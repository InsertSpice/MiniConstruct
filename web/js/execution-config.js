import { H3_ASPECT_RATIO_CHOICES, aspectRatioToken } from "./aspect-ratios.js";

// Drafts are ephemeral. The backend owns all saved execution configuration.
// The server fills typed defaults. Every explicitly submitted value must survive;
// arrays (including sourceLinks) must also retain their length and order.
function containsSubmitted(actual, submitted) {
  if (submitted === null || typeof submitted !== "object") return actual === submitted;
  if (!actual || typeof actual !== "object" || Array.isArray(actual) !== Array.isArray(submitted)) return false;
  if (Array.isArray(submitted) && actual.length !== submitted.length) return false;
  return Object.keys(submitted).every(key => Object.hasOwn(actual, key) && containsSubmitted(actual[key], submitted[key]));
}

export function isGraphLink(value) {
  return Array.isArray(value) && value.length === 2 && typeof value[0] === "string"
    && Number.isInteger(value[1]) && value[1] >= 0;
}

export function scalarHint(value) {
  if (value !== null && typeof value === "object") return "";
  const text = String(value).replace(/\s+/g, " ");
  if (/data:|base64/i.test(text)) return "[payload omitted]";
  return text.length > 64 ? `${text.slice(0, 61)}…` : text;
}

export function inspectWorkflow(graph) {
  if (!graph || Array.isArray(graph) || typeof graph !== "object" || !Object.keys(graph).length)
    throw new Error("Workflow must be a non-empty API-format object keyed by node IDs");
  if (Array.isArray(graph.nodes) || Array.isArray(graph.links))
    throw new Error("This is an editor workflow. Export an API-format workflow from ComfyUI");
  return Object.entries(graph).map(([id, node]) => {
    if (!node || typeof node !== "object" || typeof node.class_type !== "string" || !node.class_type.trim()
      || !node.inputs || Array.isArray(node.inputs) || typeof node.inputs !== "object")
      throw new Error("Each API workflow node requires class_type text and an inputs object");
    const title = typeof node._meta?.title === "string" ? node._meta.title : "";
    const hints = Object.entries(node.inputs).filter(([key, value]) => !/secret|password|token|key/i.test(key)
      && (value === null || typeof value !== "object")).slice(0, 2).map(([key, value]) => `${key}=${scalarHint(value)}`).join(" · ");
    return { id, classType: node.class_type, title: title.slice(0, 100), label: `${id} · ${node.class_type}${title ? ` · ${title.slice(0, 100)}` : ""}${hints ? ` · ${hints}` : ""}`,
      inputs: Object.entries(node.inputs).map(([name, value]) => ({ name,
        value,
        link: isGraphLink(value) ? value : null, hint: scalarHint(value),
        kind: isGraphLink(value) ? "graph link" : value !== null && typeof value === "object" ? "structured input" : "scalar" })) };
  });
}

export function discoverControlLeaves(nodes) {
  const leaves = [];
  const walk = (node, inputName, value, path = []) => {
    if (path.length > 4 || leaves.length >= 4096 || isGraphLink(value)) return;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      for (const [key, child] of Object.entries(value).slice(0, 128)) walk(node, inputName, child, [...path, key]);
    } else if (["boolean", "number", "string"].includes(typeof value) && (typeof value !== "number" || Number.isFinite(value))) {
      leaves.push({ nodeId: node.id, inputName, path, value,
        label: `${node.id} · ${node.title || node.classType} / ${inputName}${path.length ? ` › ${path.join(" › ")}` : ""}` });
    }
  };
  for (const node of nodes) for (const input of node.inputs) walk(node, input.name, input.value);
  return leaves;
}

const MAX_DISCOVERY_NODES = 512;
const MAX_DISCOVERY_EDGES = 2048;

function nodeDisplay(node) {
  return node.title || node.classType || node.id;
}

/**
 * Find exact downstream input links for an optional reference. The traversal
 * only reads API-format graph-link values, keeps node IDs opaque, and carries
 * one bounded human-readable route for each destination.
 */
export function discoverDeactivationTargets(graph, loaderId, { maxNodes = MAX_DISCOVERY_NODES,
  maxEdges = MAX_DISCOVERY_EDGES } = {}) {
  const nodes = Array.isArray(graph) ? graph : inspectWorkflow(graph);
  const byId = new Map(nodes.map(node => [node.id, node]));
  if (!byId.has(loaderId)) return [];
  const outgoing = new Map(nodes.map(node => [node.id, []]));
  for (const destination of nodes) for (const input of destination.inputs) {
    if (!input.link || !byId.has(input.link[0])) continue;
    outgoing.get(input.link[0]).push({ sourceNodeId: input.link[0], nodeId: destination.id,
      inputName: input.name, outputIndex: input.link[1] });
  }
  const downstream = new Set([loaderId]);
  const fullQueue = [loaderId]; let fullScanned = 0;
  while (fullQueue.length) {
    const current = fullQueue.shift();
    if (++fullScanned > maxNodes) throw new Error("Downstream graph discovery exceeded its safety bound; simplify the workflow branch.");
    for (const edge of outgoing.get(current) || []) if (!downstream.has(edge.nodeId)) {
      downstream.add(edge.nodeId); fullQueue.push(edge.nodeId);
    }
  }
  const isBoundary = nodeId => {
    const node = byId.get(nodeId);
    return node.inputs.some(input => input.link && !downstream.has(input.link[0]));
  };
  const candidates = [], seenTargets = new Set(), visited = new Set([loaderId]);
  const queue = [{ id: loaderId, path: [loaderId] }]; let scanned = 0;
  while (queue.length) {
    const current = queue.shift();
    if (++scanned > maxNodes) throw new Error("Downstream graph discovery exceeded its safety bound; simplify the workflow branch.");
    for (const edge of outgoing.get(current.id) || []) {
      if (candidates.length >= maxEdges) throw new Error("Downstream graph discovery found too many destinations; simplify the workflow branch.");
      const key = `${edge.sourceNodeId}\u0000${edge.nodeId}\u0000${edge.inputName}\u0000${edge.outputIndex}`;
      const route = [...current.path, edge.nodeId];
      if (isBoundary(edge.nodeId) && !seenTargets.has(key)) {
        seenTargets.add(key);
        candidates.push({ ...edge, path: route });
      }
      if (!isBoundary(edge.nodeId) && !visited.has(edge.nodeId)) {
        visited.add(edge.nodeId); queue.push({ id: edge.nodeId, path: route });
      }
    }
  }
  return candidates.map(candidate => ({ ...candidate,
    route: (candidate.path.length > 6 ? ["…", ...candidate.path.slice(-5)] : candidate.path).map(id => {
      const node = byId.get(id); return node ? nodeDisplay(node) : id;
    }) }));
}

export function createExecutionConfig({ root, api, onProfilesChanged = () => {}, onConnectionsChanged = () => {}, confirmDelete = message => globalThis.confirm(message) }) {
  const doc = root.ownerDocument;
  let capabilities = null, connectionId = null, profileId = null, workflowJson = "", nodes = [], bindings = [], editing = -1;
  let controls = [], controlEditing = -1, presets = [], presetEditing = -1;
  let busy = false, dirty = false;
  let duplicateDraft = null;
  const el = (tag, text, parent = root) => {
    const result = doc.createElement(tag);
    if (text != null) result.textContent = text;
    parent.append(result); return result;
  };
  const field = (id, label, tag, parent) => {
    const input = el(tag, null, el("label", label, parent)); input.id = `execution-${id}`; return input;
  };
  const button = (id, label, parent, action) => {
    const result = el("button", label, parent); result.type = "button"; result.id = `execution-${id}`;
    result.addEventListener("click", action); return result;
  };
  const status = (id, parent) => {
    const result = el("p", "", parent); result.id = `execution-${id}`; result.className = "helper";
    result.setAttribute("role", "status"); result.setAttribute("aria-live", "polite"); return result;
  };
  const options = (select, items) => {
    select.replaceChildren();
    for (const [value, label, disabled = false] of items) {
      const option = el("option", label, select); option.value = value; option.disabled = disabled;
    }
    select.value = items.find(item => !item[2])?.[0] || "";
  };
  async function act(region, action) {
    if (busy) return;
    busy = true; region.textContent = "Working…";
    const controls = Array.from(root.querySelectorAll("button,input,select,textarea"));
    const disabled = controls.map(item => item.disabled);
    controls.forEach(item => { item.disabled = true; });
    try { await action(); }
    catch (error) { region.textContent = error.message; }
    finally { controls.forEach((item, index) => { item.disabled = disabled[index]; }); busy = false; }
  }
  el("p", "Machine-local configuration for self-hosted ComfyUI. Profiles describe mappings; workflows cannot be executed here.").className = "helper";
  const loadStatus = status("load-status", root);
  button("reload", "Refresh saved lists", root, () => refresh());
  const connections = el("fieldset"); el("legend", "ComfyUI Connections — where", connections);
  const connectionList = el("div", null, connections); connectionList.className = "execution-list";
  const connectionForm = el("div", null, connections); connectionForm.className = "field-grid two";
  const connectionName = field("connection-name", "Connection name", "input", connectionForm); connectionName.maxLength = 160;
  const baseUrl = field("base-url", "Base URL", "input", connectionForm); baseUrl.type = "url"; baseUrl.placeholder = "http://127.0.0.1:8188";
  const localInput = field("local-input-directory", "Local ComfyUI input root (optional)", "input", connectionForm);
  localInput.maxLength = 4096;
  el("p", "Use ComfyUI's actual input root only when it is directly accessible to the MiniConstruct backend. Exact file bytes are checked on Send. Leave blank for normal remote HTTP verification; invalid mappings do not enable local reuse.", connections).className = "helper";
  const connectionStatus = status("connection-status", connections);
  const connectionActions = el("div", null, connections); connectionActions.className = "inline-row wrap";
  button("connection-new", "New connection", connectionActions, () => {
    connectionId = null; connectionName.value = ""; baseUrl.value = ""; localInput.value = ""; connectionStatus.textContent = "New connection";
  });
  button("connection-save", "Save connection", connectionActions, () => act(connectionStatus, async () => {
    const saved = await api.saveExecutionConnection(connectionId, { name: connectionName.value, baseUrl: baseUrl.value, localInputDirectory: localInput.value.trim() || null });
    connectionId = saved.id; connectionName.value = saved.name; baseUrl.value = saved.baseUrl;
    localInput.value = saved.localInputDirectory || "";
    connectionStatus.textContent = "Connection saved";
    if (api.localExecutionInputStatus) connectionStatus.textContent += ` · ${(await api.localExecutionInputStatus(saved.id)).message}`;
    await loadConnections(); await onConnectionsChanged();
  }));

  const profiles = el("fieldset"); el("legend", "Workflow Profiles — how", profiles);
  el("p", "A Workflow Profile is a reusable imported ComfyUI API graph plus mappings from MiniConstruct inputs to graph inputs. Create separate profiles for substantially different workflows, such as Ref2VA and I2VA; both remain saved.", profiles).className = "helper";
  el("p", "Manage profiles here. To execute, close setup and choose Workflow Profile under Generated H3 Prompt → ComfyUI Execution Settings. Edit only opens the editor; it does not activate a profile. No Load button or repeated import is needed.", profiles).className = "helper";
  const profileList = el("div", null, profiles); profileList.className = "execution-list";
  button("profile-new", "New profile", profiles, () => {
    if (dirty && !confirmDelete("Discard unsaved profile edits?")) return;
    adoptProfile(null);
  });
  const setupSteps = el("ol", null, profiles); setupSteps.id = "execution-profile-steps";
  for (const step of ["Name the profile and select its actual supported H3 modes (required).",
    "Import API-format workflow JSON exported from ComfyUI (required for a new profile).",
    "Add a Generated H3 prompt binding to the graph input that accepts the prompt (required).",
    "Map required media, including the first-frame Picture for I2VA, to the appropriate loader and consumer.",
    "Optionally map duration, aspect ratio and renderer seed, and expose Workflow Controls.",
    "Validate draft, resolve reported issues, then Save profile. Select it in ComfyUI Execution Settings to Prepare / Send."]) el("li", step, setupSteps);
  const profileName = field("profile-name", "Profile name (required)", "input", profiles); profileName.maxLength = 160;
  const description = field("description", "Description (optional)", "textarea", profiles); description.maxLength = 4000; description.rows = 2;
  const modeGroup = el("fieldset", null, profiles); el("legend", "Compatible H3 modes", modeGroup);
  el("p", "Required: declare only modes this imported graph and its bindings actually support. Multiple modes are allowed, but these checkboxes do not combine workflows or convert a Ref2VA graph to I2VA. Separate profiles are recommended for different workflows.", modeGroup).className = "helper";
  const modeChoices = el("div", null, modeGroup); modeChoices.className = "inline-row wrap";
  const i2vaHelp = el("p", "I2VA needs an actual I2VA-compatible exported workflow. In H3 Setup choose I2VA and attach a Picture with the first-frame anchor role. Map Reference asset → Picture → ordinal 1 to its loader's filename input; choose Required when the workflow always needs it. Inspect the node titles, scalar input hints and discovered consumer routes below to confirm the correct destination. Custom-node wiring cannot be inferred reliably: select the loader and consumer used by your workflow. Import alone does not establish correct bindings. The ordinary H3 prompt writer and Prepare / Send pipeline stay the same.", profiles);
  i2vaHelp.id = "execution-i2va-help"; i2vaHelp.className = "helper"; i2vaHelp.hidden = true;
  modeChoices.addEventListener("change", () => { i2vaHelp.hidden = !Array.from(modeChoices.querySelectorAll("input")).some(input => input.value === "I2VA" && input.checked); });
  const file = field("workflow-file", "Import / replace API-format workflow JSON (maximum 2 MiB)", "input", profiles);
  file.type = "file"; file.accept = ".json,application/json";
  const importStatus = status("import-status", profiles);
  const fingerprint = el("p", "", profiles); fingerprint.id = "execution-fingerprint"; fingerprint.className = "helper execution-fingerprint";
  el("p", "Replacement keeps draft bindings. Fix missing targets before saving; the saved graph stays intact if validation fails.", profiles).className = "helper";
  el("p", "Keep your normal visual ComfyUI workflow as the master. Import its API-format copy here; you do not need to load that copy back into ComfyUI. Node IDs below come from the API JSON, so you do not need to find them on the canvas. Descriptive Comfy node titles and filename hints help distinguish loaders.", profiles).className = "helper";
  const inspection = el("details", null, profiles); el("summary", "Inspect imported nodes and inputs", inspection);
  const nodeList = el("div", null, inspection); nodeList.id = "execution-nodes"; nodeList.className = "execution-node-list";
  const bindingGroup = el("fieldset", null, profiles); el("legend", "Existing semantic bindings", bindingGroup);
  const bindingList = el("div", null, bindingGroup); bindingList.id = "execution-bindings";
  const bindingEditorGroup = el("fieldset", null, profiles); bindingEditorGroup.id = "execution-binding-editor";
  el("legend", "Add / edit semantic binding", bindingEditorGroup);
  const bindingForm = el("div", null, bindingEditorGroup); bindingForm.className = "field-grid two";
  const source = field("source", "MiniConstruct input", "select", bindingForm);
  options(source, [["generated_prompt", "Generated H3 prompt"], ["duration", "Duration (seconds)"], ["aspect_ratio", "Aspect Ratio (H3 Setup)"], ["renderer_seed", "Renderer seed (controlled on Send)"], ["reference_asset", "Reference asset by kind + ordinal"]]);
  const assetKind = field("asset-kind", "Reference kind", "select", bindingForm); assetKind.parentElement.className = "reference-only";
  const ordinal = field("ordinal", "Reference ordinal (one-based)", "input", bindingForm); ordinal.parentElement.className = "reference-only"; ordinal.type = "number"; ordinal.min = "1"; ordinal.step = "1"; ordinal.value = "1";
  const presence = field("presence", "Reference presence", "select", bindingForm); presence.parentElement.className = "reference-only";
  options(presence, [["optional", "Optional"], ["required", "Required"]]);
  const targetNode = field("target-node", "Target node", "select", bindingForm);
  const targetInput = field("target-input", "Target input (scalar only)", "select", bindingForm);
  const aspectMapping = el("fieldset", null, bindingEditorGroup); aspectMapping.id = "execution-aspect-mapping";
  aspectMapping.className = "aspect-ratio-mapping reference-only";
  el("legend", "Renderer value mapping", aspectMapping);
  el("p", "H3 Setup supplies the ratio. Choose the Enum capability connection below, populate mappings, review, then Save profile. Manual values remain available; verified enums accept only declared choices.", aspectMapping).className = "helper";
  let aspectEnumChoices = null, aspectExtraMappings = {}, aspectProposal = null;
  const aspectReview = el("div", null, aspectMapping);
  const aspectSnapshot = () => JSON.stringify([profileId, workflowJson, editing, enumConnection.value, source.value, targetNode.value,
    targetInput.value, aspectMappingInputs.map(([ratio, input]) => [ratio, input.value]), aspectExtraMappings, aspectEnumChoices]);
  button("aspect-populate", "Populate mappings from ComfyUI", aspectMapping, () => act(bindingStatus, async () => {
    aspectProposal = null; aspectReview.replaceChildren(); aspectApply.hidden = true;
    if (!profileId || workflowJson) throw new Error("Save the imported workflow first, then select the exact target for this binding.");
    if (!enumConnection.value) throw new Error("Choose an Enum capability connection first.");
    if (!targetNode.value || !targetInput.value) throw new Error("Choose the exact renderer target node and scalar input.");
    const snapshot = aspectSnapshot();
    const result = await api.executionAspectRatioCapabilities(profileId, targetNode.value, targetInput.value, enumConnection.value);
    if (snapshot !== aspectSnapshot() || result.profileId !== profileId || result.target.nodeId !== targetNode.value
      || result.target.inputName !== targetInput.value || result.classType !== nodes.find(node => node.id === targetNode.value)?.classType)
      throw new Error("The binding target or draft changed. Populate mappings again.");
    const current = { ...aspectExtraMappings, ...Object.fromEntries(aspectMappingInputs.filter(([, input]) => input.value.trim()).map(([ratio, input]) => [ratio, input.value])) };
    const proposed = { ...current }, conflicts = [];
    let manualReviews = 0;
    for (const [ratio, value] of Object.entries(result.valueMap)) {
      if (H3_ASPECT_RATIO_CHOICES.includes(ratio) && !current[ratio]) { proposed[ratio] = value; el("p", `${ratio}: add ${value}`, aspectReview); }
    }
    for (const [ratio, value] of Object.entries(current)) {
      const requestedRatio = aspectRatioToken(ratio), rendererRatio = aspectRatioToken(value);
      const mismatch = requestedRatio && rendererRatio && requestedRatio !== rendererRatio;
      if (result.choices.includes(value) && !mismatch) {
        if (requestedRatio && !rendererRatio) {
          manualReviews++;
          el("p", `${ratio}: current renderer label “${value}” is declared, but numeric ratio equivalence cannot be verified. Manual mapping preserved; review this renderer-specific label.`, aspectReview);
        }
        continue;
      }
      const replacement = result.valueMap[ratio];
      const label = el("label", null, aspectReview);
      const check = el("input", null, label); check.type = "checkbox";
      const reason = mismatch ? `declares ratio ${rendererRatio}, which conflicts with H3 Setup ${ratio}` : "is not declared by ComfyUI";
      el("span", `${ratio}: current “${value}” ${reason}. ${replacement ? `Replace with “${replacement}”` : "Remove unsupported mapping"} (explicit decision required).`, label);
      conflicts.push({ ratio, replacement, check });
    }
    el("p", `Verified choices: ${result.choices.join(", ")}`, aspectReview);
    aspectProposal = { snapshot, proposed, conflicts, choices: result.choices };
    aspectApply.hidden = false;
    bindingStatus.textContent = manualReviews && !conflicts.length
      ? "Review renderer-specific labels whose ratio equivalence could not be verified, then Apply detected mappings. Nothing has been saved."
      : Object.keys(proposed).length === Object.keys(current).length && !conflicts.length
      ? "Mappings are up to date. Apply to retain verified enum choices for safe offline Prepare."
      : "Review additions and conflicts, then Apply detected mappings. Nothing has been saved.";
  }));
  const aspectApply = button("aspect-apply", "Apply detected mappings", aspectMapping, () => act(bindingStatus, async () => {
    const proposal = aspectProposal;
    if (!proposal || proposal.snapshot !== aspectSnapshot()) throw new Error("The binding draft changed. Populate mappings again.");
    if (proposal.conflicts.some(item => !item.check.checked)) throw new Error("Decide each conflicting mapping before applying; cancel by populating again or leaving the editor.");
    for (const { ratio, replacement } of proposal.conflicts) {
      if (replacement) proposal.proposed[ratio] = replacement; else delete proposal.proposed[ratio];
    }
    aspectExtraMappings = { ...proposal.proposed };
    for (const [ratio, input] of aspectMappingInputs) { input.value = proposal.proposed[ratio] || ""; delete aspectExtraMappings[ratio]; }
    aspectEnumChoices = [...proposal.choices];
    if (!applyBindingDraft()) return;
    aspectProposal = null; aspectReview.replaceChildren(); aspectApply.hidden = true;
    bindingStatus.textContent = "Mappings applied to Profile draft. Click Save profile to persist.";
  }));
  aspectApply.hidden = true;
  const aspectMappingTable = el("div", null, aspectMapping); aspectMappingTable.className = "aspect-ratio-mapping-table";
  el("strong", "H3 value", aspectMappingTable); el("strong", "Renderer value", aspectMappingTable);
  const aspectMappingInputs = [];
  for (const [index, ratio] of H3_ASPECT_RATIO_CHOICES.entries()) {
    const ratioLabel = el("label", ratio, aspectMappingTable);
    const input = el("input", null, aspectMappingTable); input.type = "text"; input.maxLength = 256;
    input.id = `execution-aspect-map-${index}`; input.setAttribute("aria-label", `Renderer value for H3 ${ratio}`);
    ratioLabel.setAttribute("for", input.id);
    aspectMappingInputs.push([ratio, input]);
  }
  const editorHeading = el("h3", "Add semantic binding", bindingEditorGroup); editorHeading.id = "execution-binding-editor-heading"; editorHeading.className = "compact-heading";
  const consumerGroup = el("fieldset", null, bindingEditorGroup); consumerGroup.id = "execution-consumer-group"; consumerGroup.className = "reference-only"; el("legend", "Reference route capacity — configure once", consumerGroup);
  el("p", "MiniConstruct follows the private reference branch to its first safe boundary. Check the boundary destinations that belong to this reference slot; intermediate adapters and downstream generation nodes remain in the prepared graph.", consumerGroup).className = "helper";
  const consumerStatus = el("p", "", consumerGroup); consumerStatus.id = "execution-consumer-status";
  const consumerChoices = el("div", null, consumerGroup); consumerChoices.id = "execution-consumers";
  const requiredHelper = el("p", "This workflow always requires this reference file. Its individual routes are still selected per Generation.", bindingEditorGroup);
  requiredHelper.id = "execution-required-helper"; requiredHelper.className = "helper reference-only";
  const bindingStatus = status("binding-status", bindingEditorGroup);
  function applyBindingDraft() {
    const semantic = { kind: source.value };
    if (semantic.kind === "reference_asset") {
      semantic.assetKind = assetKind.value; semantic.ordinal = Number(ordinal.value);
      if (!Number.isSafeInteger(semantic.ordinal) || semantic.ordinal < 1) { bindingStatus.textContent = "Reference ordinal must be a positive integer"; return false; }
    }
    if (semantic.kind === "aspect_ratio") {
      semantic.valueMap = { ...aspectExtraMappings, ...Object.fromEntries(aspectMappingInputs
        .filter(([, input]) => input.value.trim())
        .map(([ratio, input]) => [ratio, input.value])) };
      if (aspectEnumChoices !== null) semantic.enumChoices = [...aspectEnumChoices];
    }
    if (!targetNode.value || !targetInput.value) { bindingStatus.textContent = "Choose an existing scalar target input"; return false; }
    if (bindings.some((item, index) => index !== editing && item.nodeId === targetNode.value && item.inputName === targetInput.value)) {
      bindingStatus.textContent = "That target already has a binding"; return false;
    }
    const wasEditing = editing >= 0;
    const binding = { source: semantic, nodeId: targetNode.value, inputName: targetInput.value };
    if (semantic.kind === "reference_asset") {
      binding.presence = presence.value;
      const targets = Array.from(consumerChoices.querySelectorAll("input"))
        .filter(item => item.checked).map(item => JSON.parse(item.value));
      binding.routes = targets.map(item => ({ ...item, channel: item.channel || null }));
    }
    if (editing < 0) bindings.push(binding); else bindings[editing] = binding;
    editing = -1; dirty = true; renderBindings(); bindingStatus.textContent = wasEditing
      ? "Binding updated in Profile draft — click Save profile to persist."
      : "Binding added to Profile draft — click Save profile to persist.";
    return true;
  }
  const bindingButton = button("binding-save", "Add binding", bindingEditorGroup, () => { applyBindingDraft(); });
  button("binding-cancel", "Cancel binding edit", bindingEditorGroup, () => resetBindingEditor());
  const profileStatus = status("profile-status", profiles);
  const enumConnection = field("enum-connection", "Enum capability connection", "select", profiles);
  el("p", "Sync choices from ComfyUI reads the exact saved node class/input into the Profile draft. Review the choices and click Save profile to persist. Prepare stays offline.", profiles).className = "helper";
  const controlGroup = el("fieldset", null, profiles); el("legend", "Exposed Workflow Controls", controlGroup);
  const controlList = el("div", null, controlGroup); controlList.id = "execution-control-list";
  const controlForm = el("div", null, controlGroup); controlForm.className = "field-grid two";
  const controlId = field("control-id", "Control ID", "input", controlForm);
  controlId.parentElement.className = "workflow-control-editor-field";
  el("span", 'Stable internal key used by Project/Generation overrides and presets. Prefer descriptive IDs such as "steps" or "turbo_enabled".', controlId.parentElement).className = "helper";
  const controlLabel = field("control-label", "Label", "input", controlForm);
  const controlTarget = field("control-target", "Workflow input or dictionary leaf", "select", controlForm);
  const controlType = field("control-type", "Type", "select", controlForm);
  options(controlType, [["boolean", "Boolean"], ["integer", "Integer"], ["float", "Float"], ["enum", "Choices"]]);
  const controlDefaultField = el("div", null, controlForm); controlDefaultField.className = "workflow-control-editor-field workflow-control-default-field";
  const controlDefaultTitle = el("label", "Profile default", controlDefaultField); controlDefaultTitle.setAttribute("for", "execution-control-default");
  const controlDefaultSwitch = el("label", null, controlDefaultField); controlDefaultSwitch.className = "mini-switch";
  let controlDefault = el("input", null, controlDefaultSwitch); controlDefault.id = "execution-control-default";
  controlDefault.className = "mini-switch-input"; controlDefault.setAttribute("role", "switch"); controlDefault.setAttribute("aria-label", "Profile default");
  el("span", "", controlDefaultSwitch).className = "mini-switch-track";
  const controlDefaultSwitchState = el("span", "Off", controlDefaultSwitch); controlDefaultSwitchState.className = "mini-switch-state";
  const controlDefaultBoolean = controlDefault;
  const controlDefaultNumber = el("input", null, controlDefaultField); controlDefaultNumber.id = "execution-control-default-number"; controlDefaultNumber.type = "number";
  const controlDefaultChoice = el("select", null, controlDefaultField); controlDefaultChoice.id = "execution-control-default-choice";
  el("span", "Used when neither Project defaults nor this Generation provide an override.", controlDefaultField).className = "helper";
  controlDefaultBoolean.addEventListener("change", () => { controlDefaultSwitchState.textContent = controlDefaultBoolean.checked ? "On" : "Off"; });
  const controlMinimum = field("control-minimum", "Minimum (optional)", "input", controlForm);
  const controlMaximum = field("control-maximum", "Maximum (optional)", "input", controlForm);
  const controlStep = field("control-step", "Step (optional)", "input", controlForm);
  const controlChoices = field("control-choices", "Choices", "textarea", controlForm);
  controlChoices.rows = 3;
  controlChoices.parentElement.className = "workflow-control-editor-field workflow-control-choices-field";
  el("span", "One exact renderer value per line. Used only for Choice controls. Examples: res_multistep, euler.", controlChoices.parentElement).className = "helper";
  const numericFields = [controlMinimum.parentElement, controlMaximum.parentElement, controlStep.parentElement];
  const controlChoiceOptions = () => controlChoices.value.split("\n").map(value => value.trim()).filter(Boolean);
  const updateControlDefaultInput = () => {
    const selectedLeaf = discoverControlLeaves(nodes).find(item => {
      try { const leaf = JSON.parse(controlTarget.value); return item.nodeId === leaf.nodeId && item.inputName === leaf.inputName
        && JSON.stringify(item.path) === JSON.stringify(leaf.path); } catch { return false; }
    });
    const type = controlType.value;
    const boolean = type === "boolean";
    controlDefault = boolean ? controlDefaultBoolean : type === "enum" ? controlDefaultChoice : controlDefaultNumber;
    controlDefault.id = "execution-control-default";
    for (const input of [controlDefaultBoolean, controlDefaultNumber, controlDefaultChoice]) {
      if (input !== controlDefault) input.id = input === controlDefaultBoolean ? "execution-control-default-boolean"
        : input === controlDefaultNumber ? "execution-control-default-number" : "execution-control-default-choice";
      input.hidden = input !== controlDefault;
    }
    if (controlDefault.tagName.toLowerCase() !== "select") controlDefault.type = boolean ? "checkbox" : "number";
    controlDefaultSwitch.hidden = !boolean;
    controlDefaultTitle.setAttribute("for", controlDefault.id);
    controlDefaultSwitchState.textContent = controlDefaultBoolean.checked ? "On" : "Off";
    if (type === "enum") {
      const prior = controlDefault.value;
      options(controlDefaultChoice, controlChoiceOptions().map(value => [value, value]));
      if (controlChoiceOptions().includes(prior)) controlDefaultChoice.value = prior;
      else if (selectedLeaf && controlChoiceOptions().includes(String(selectedLeaf.value))) controlDefaultChoice.value = String(selectedLeaf.value);
    }
    numericFields.forEach(wrapper => { wrapper.hidden = !["integer", "float"].includes(type); });
    controlChoices.parentElement.hidden = type !== "enum";
  };
  updateControlDefaultInput();
  controlType.addEventListener("change", updateControlDefaultInput);
  controlChoices.addEventListener("input", updateControlDefaultInput);
  const controlStatus = status("control-status", controlGroup);
  const controlSave = button("control-save", "Add control", controlGroup, () => {
    try {
      const leaf = JSON.parse(controlTarget.value);
      const type = controlType.value;
      const selectedLeaf = discoverControlLeaves(nodes).find(item => item.nodeId === leaf.nodeId && item.inputName === leaf.inputName
        && JSON.stringify(item.path) === JSON.stringify(leaf.path));
      const booleanDefault = type === "boolean";
      const parse = text => (type === "enum" ? typeof selectedLeaf?.value : type) === "boolean" ? text === "true"
        : type === "integer" || type === "float" || (type === "enum" && typeof selectedLeaf?.value === "number") ? Number(text) : text;
      const item = { id: controlId.value.trim(), label: controlLabel.value.trim(),
        target: { nodeId: leaf.nodeId, inputName: leaf.inputName, path: leaf.path }, type,
        defaultValue: booleanDefault ? controlDefault.checked : parse(controlDefault.value) };
      if (type === "enum") item.choices = controlChoices.value.split("\n").map(v => v.trim()).filter(Boolean).map(parse);
      if (["integer", "float"].includes(type)) for (const [key, input] of [["minimum", controlMinimum], ["maximum", controlMaximum], ["step", controlStep]]) {
        if (input.value.trim()) item[key] = Number(input.value);
      }
      if (!item.id || !item.label || !controlTarget.value) throw new Error("Enter an ID, label and workflow target.");
      if (["integer", "float"].includes(type) && (!controlDefault.value.trim() || !Number.isFinite(item.defaultValue)))
        throw new Error("Enter a finite numeric Profile default.");
      const previous = controls[controlEditing];
      if (controlEditing < 0) {
        controls.push(item);
        for (const preset of presets) preset.values[item.id] = item.defaultValue;
      } else {
        controls[controlEditing] = item;
        if (previous.id !== item.id) presetStatus.textContent = `Control ID changed from ${previous.id} to ${item.id}; update affected presets before Save.`;
      }
      const statusText = controlEditing < 0 ? "Control added to Profile draft — click Save profile to persist."
        : "Control updated in Profile draft — click Save profile to persist.";
      controlEditing = -1; dirty = true; renderControls(); renderPresets(); controlStatus.textContent = statusText;
    } catch (error) { controlStatus.textContent = error.message; }
  });
  button("control-cancel", "Cancel edit", controlGroup, () => { controlEditing = -1; renderControls(); });
  function renderControls() {
    updateControlDefaultInput();
    const leaves = discoverControlLeaves(nodes);
    const prior = controlTarget.value;
    options(controlTarget, leaves.map(leaf => [JSON.stringify({ nodeId: leaf.nodeId, inputName: leaf.inputName, path: leaf.path }), leaf.label]));
    if (leaves.some(leaf => JSON.stringify({ nodeId: leaf.nodeId, inputName: leaf.inputName, path: leaf.path }) === prior)) controlTarget.value = prior;
    else if (controlEditing < 0) inferControlTarget();
    controlList.replaceChildren();
    controls.forEach((item, index) => {
      const row = el("div", null, controlList); row.className = "execution-item";
      const title = el("strong", item.label, row); title.className = "control-row-title";
      const metadata = el("span", `ID: ${item.id} · Target: ${item.target.nodeId} / ${item.target.inputName}${item.target.path?.length ? ` › ${item.target.path.join(" › ")}` : ""}`, row);
      metadata.className = "control-row-metadata";
      if (item.type === "enum") {
        el("span", `Saved/draft choices: ${(item.choices || []).join(", ")}`, row);
        button(`control-sync-${index}`, "Sync choices from ComfyUI", row, () => act(controlStatus, async () => {
          if (!profileId || workflowJson) throw new Error("Save the imported workflow before synchronizing its exact node capabilities.");
          if (controlEditing >= 0) throw new Error("Update or cancel the open control edit before synchronizing choices.");
          if (!enumConnection.value) throw new Error("Choose an Enum capability connection first.");
          const result = await api.executionEnumCapabilities(profileId, item.id, enumConnection.value);
          if (result.profileId !== profileId || result.controlId !== item.id
            || result.target.nodeId !== item.target.nodeId || result.target.inputName !== item.target.inputName
            || JSON.stringify(result.target.path || []) !== JSON.stringify(item.target.path || [])
            || result.classType !== nodes.find(node => node.id === item.target.nodeId)?.classType)
            throw new Error("The saved target differs from this draft. Save or reload the Profile before synchronizing.");
          if (!Array.isArray(result.choices) || !result.choices.includes(item.defaultValue)
            || presets.some(preset => !result.choices.includes(preset.values[item.id])))
            throw new Error("Verified choices no longer support this draft's default or presets. Review those values before synchronizing.");
          controls[index] = { ...item, choices: structuredClone(result.choices) };
          dirty = true; renderControls(); renderPresets();
          controlStatus.textContent = `${result.classType} / ${item.target.inputName}: ${result.choices.join(", ")}. ${result.message}`;
        }));
      }
      button(`control-edit-${index}`, "Edit", row, () => {
        controlEditing = index; controlId.value = item.id; controlLabel.value = item.label;
        controlTarget.value = JSON.stringify({ nodeId: item.target.nodeId, inputName: item.target.inputName, path: item.target.path || [] });
        controlType.value = item.type; controlChoices.value = (item.choices || []).join("\n"); updateControlDefaultInput();
        if (controlDefault.type === "checkbox") controlDefault.checked = item.defaultValue;
        else controlDefault.value = String(item.defaultValue);
        controlMinimum.value = item.minimum ?? ""; controlMaximum.value = item.maximum ?? "";
        controlStep.value = item.step ?? "";
        controlSave.textContent = "Update control";
      });
      button(`control-remove-${index}`, "Remove", row, () => { controls.splice(index, 1); for (const preset of presets) delete preset.values[item.id]; controlEditing = -1; dirty = true; renderControls(); renderPresets(); });
    });
    if (controlEditing < 0) controlSave.textContent = "Add control";
  }
  function inferControlTarget() {
    const leaf = discoverControlLeaves(nodes).find(item => JSON.stringify({ nodeId: item.nodeId, inputName: item.inputName, path: item.path }) === controlTarget.value);
    if (!leaf) return;
    controlType.value = typeof leaf.value === "boolean" ? "boolean" : typeof leaf.value === "number" ? Number.isInteger(leaf.value) ? "integer" : "float" : "enum";
    updateControlDefaultInput();
    if (controlDefault.type === "checkbox") controlDefault.checked = Boolean(leaf.value);
    else controlDefault.value = String(leaf.value);
    controlChoices.value = controlType.value === "enum" ? String(leaf.value) : "";
    updateControlDefaultInput();
  }
  controlTarget.addEventListener("change", inferControlTarget);
  const presetGroup = el("section", null, profiles); presetGroup.id = "execution-presets";
  el("h3", "Workflow Control Presets", presetGroup);
  const presetList = el("div", null, presetGroup);
  const presetName = field("preset-name", "Preset name", "input", presetGroup);
  const presetFields = el("div", null, presetGroup); presetFields.className = "execution-preset-fields";
  const presetStatus = status("preset-status", presetGroup);
  const presetSave = button("preset-save", "Add preset", presetGroup, () => {
    try {
      const name = presetName.value.trim();
      if (!name || name.length > 80) throw new Error("Enter a preset name of at most 80 characters.");
      if (presets.some((preset, index) => index !== presetEditing && preset.name.toLowerCase() === name.toLowerCase())) throw new Error("Preset names must be unique.");
      const wasEditing = presetEditing >= 0;
      const values = {};
      for (const control of controls) {
        const input = presetFields.querySelector(`#execution-preset-value-${control.id}`);
        const value = control.type === "boolean" ? input.checked : control.type === "enum"
          ? control.choices.find(choice => String(choice) === input.value) : Number(input.value);
        if (value === undefined || (["integer", "float"].includes(control.type) && (!input.value.trim() || !Number.isFinite(value) || (control.type === "integer" && !Number.isInteger(value))
          || (control.minimum != null && value < control.minimum) || (control.maximum != null && value > control.maximum)))) throw new Error(`${control.label}: invalid value.`);
        values[control.id] = value;
      }
      const preset = { name, values };
      if (presetEditing < 0) presets.push(preset); else presets[presetEditing] = preset;
      presetEditing = -1; dirty = true; renderPresets(); presetStatus.textContent = wasEditing
        ? "Preset updated in Profile draft — click Save profile to persist."
        : "Preset added to Profile draft — click Save profile to persist.";
    } catch (error) { presetStatus.textContent = error.message; }
  });
  button("preset-cancel", "Cancel edit", presetGroup, () => { presetEditing = -1; renderPresets(); });
  function renderPresets() {
    presetGroup.hidden = !controls.length;
    presetList.replaceChildren(); presetFields.replaceChildren();
    for (const [index, preset] of presets.entries()) {
      const row = el("div", null, presetList); row.className = "execution-item";
      el("span", preset.name, row);
      button(`preset-edit-${index}`, "Edit", row, () => { presetEditing = index; renderPresets(); });
      button(`preset-remove-${index}`, "Remove", row, () => { presets.splice(index, 1); presetEditing = -1; dirty = true; renderPresets(); });
    }
    const draft = presetEditing < 0 ? null : presets[presetEditing];
    presetName.value = draft?.name || "";
    presetSave.textContent = draft ? "Update preset" : "Add preset";
    for (const control of controls) {
      const wrapper = el("div", null, presetFields);
      wrapper.className = control.type === "boolean" ? "preset-field preset-boolean-row" : "preset-field";
      const value = draft && Object.hasOwn(draft.values, control.id) ? draft.values[control.id] : control.defaultValue;
      let input;
      if (control.type === "boolean") {
        const name = el("label", control.label, wrapper); name.className = "workflow-control-label";
        const label = el("label", null, wrapper); label.className = "mini-switch";
        input = el("input", null, label); input.type = "checkbox"; input.className = "mini-switch-input";
        input.setAttribute("role", "switch"); input.setAttribute("aria-label", control.label);
        el("span", "", label).className = "mini-switch-track";
        const state = el("span", value ? "On" : "Off", label); state.className = "mini-switch-state";
        input.addEventListener("change", () => { state.textContent = input.checked ? "On" : "Off"; });
      } else {
        el("label", control.label, wrapper);
        input = el(control.type === "enum" ? "select" : "input", null, wrapper);
      }
      input.id = `execution-preset-value-${control.id}`;
      if (control.type === "boolean") wrapper.children[0].setAttribute("for", input.id);
      if (control.type === "boolean") { input.checked = Boolean(value); }
      else if (control.type === "enum") { for (const choice of control.choices) { const option = el("option", String(choice), input); option.value = String(choice); } input.value = String(value); }
      else { input.type = "number"; input.value = String(value); input.step = String(control.step ?? (control.type === "integer" ? 1 : "any"));
        if (control.minimum != null) input.min = String(control.minimum);
        if (control.maximum != null) input.max = String(control.maximum); }
    }
    const ids = new Set(controls.map(control => control.id));
    const invalid = presets.filter(preset => new Set(Object.keys(preset.values)).size !== ids.size || Object.keys(preset.values).some(id => !ids.has(id)));
    presetStatus.textContent = invalid.length ? `Invalid presets: ${invalid.map(preset => preset.name).join(", ")}. Edit them before Save.` : "";
  }
  const nativeDetails = el("details", null, profiles);
  el("summary", "Native H3 continuation (optional advanced profile mapping)", nativeDetails);
  el("p", "For workflow authors: paste the nativeContinuation capability JSON with semantic inputs mapped to this graph. Leave blank for existing fallback behavior. Validate checks required mappings and frame grids. A separate picture-free I2VA successor may explicitly declare pictureFreeI2va after renderer validation; it needs independent dimensions, no image anchors, and always_extend. See docs/i2va-native-continuation.md for the contract.", nativeDetails).className = "helper";
  const nativeMapping = field("native-continuation", "Native continuation capability JSON", "textarea", nativeDetails);
  nativeMapping.rows = 8;
  const profileActions = el("div", null, profiles); profileActions.className = "inline-row wrap";
  function parseNativeCapabilityDraft() {
    const text = nativeMapping.value.trim();
    if (!text) return null;
    try { return JSON.parse(text); }
    catch { throw new Error("Native continuation capability JSON is invalid JSON."); }
  }
  function draftBody() {
    return { ...structuredClone(duplicateDraft || {}), name: profileName.value, description: description.value,
      compatibleModes: Array.from(modeChoices.querySelectorAll("input")).filter(item => item.checked).map(item => item.value),
      ...(!profileId || workflowJson ? { workflowJson } : {}), bindings: structuredClone(bindings),
      nativeContinuation: parseNativeCapabilityDraft(), exposedControls: structuredClone(controls), workflowControlPresets: structuredClone(presets) };
  }
  button("profile-validate", "Validate draft", profileActions, () => act(profileStatus, async () => {
    const report = await api.validateExecutionProfile(draftBody(), profileId);
    profileStatus.textContent = report.valid ? "Profile is valid offline; server node availability is not checked" : report.errors.join("\n");
  }));
  button("profile-save", "Save profile", profileActions, () => act(profileStatus, async () => {
    if (editing >= 0 && !applyBindingDraft()) { profileStatus.textContent = bindingStatus.textContent; return; }
    renderPresets();
    if (presets.some(preset => Object.keys(preset.values).length !== controls.length || controls.some(control => !Object.hasOwn(preset.values, control.id)))) { profileStatus.textContent = presetStatus.textContent; return; }
    const creating = !profileId;
    const submitted = draftBody();
    const saved = await api.saveExecutionProfile(profileId, submitted);
    if (!saved.id || (profileId && saved.id !== profileId)
      || !containsSubmitted(saved.nativeContinuation, submitted.nativeContinuation)
      || !containsSubmitted(saved.exposedControls, submitted.exposedControls)
      || !containsSubmitted(saved.workflowControlPresets, submitted.workflowControlPresets)) {
      throw new Error("Profile save could not be verified: the server response disagrees with the submitted Profile declarations. Your draft is retained.");
    }
    const canonical = await api.executionProfile(saved.id);
    if (canonical.id !== saved.id || !containsSubmitted(canonical.nativeContinuation, saved.nativeContinuation)
      || !containsSubmitted(saved.nativeContinuation, canonical.nativeContinuation)
      || !containsSubmitted(canonical.exposedControls, saved.exposedControls)
      || !containsSubmitted(saved.exposedControls, canonical.exposedControls)
      || !containsSubmitted(canonical.workflowControlPresets, saved.workflowControlPresets)
      || !containsSubmitted(saved.workflowControlPresets, canonical.workflowControlPresets)) {
      throw new Error("Profile save could not be verified: the reloaded Profile declarations disagree with the saved response. Your draft is retained.");
    }
    adoptProfile(canonical);
    profileStatus.textContent = creating ? "New Workflow Profile created; select it in ComfyUI Execution Settings when ready" : "Workflow Profile saved; Validate draft checks optional-slot readiness"; await loadProfiles();
  }));
  button("profile-cancel", "Cancel profile edits", profileActions, () => {
    adoptProfile(null);
    profileStatus.textContent = "Profile draft discarded; saved profiles unchanged";
  });
  file.addEventListener("change", () => act(importStatus, async () => {
    const selected = file.files?.[0];
    try {
      if (!selected) { importStatus.textContent = "No file selected"; return; }
      if (!capabilities) throw new Error("Load execution settings before importing");
      if (selected.size > capabilities.maxWorkflowBytes) throw new Error("Workflow exceeds the 2 MiB size limit");
      const text = await selected.text();
      const inspected = inspectWorkflow(JSON.parse(text));
      workflowJson = text; nodes = inspected; dirty = true; editing = -1; renderGraph(); renderBindings(); renderControls();
      fingerprint.textContent = "Draft workflow changed; fingerprint will be calculated on save";
      importStatus.textContent = `${nodes.length} nodes imported into draft. Next: map the Generated H3 prompt and required media inputs, then Validate draft. Import does not verify these bindings.`;
    } finally { file.value = ""; }
  }));
  source.addEventListener("change", sourceFields);
  presence.addEventListener("change", sourceFields);
  assetKind.addEventListener("change", sourceFields);
  targetNode.addEventListener("change", renderInputs);
  for (const target of [targetNode, targetInput]) target.addEventListener("change", () => {
    aspectEnumChoices = null; aspectProposal = null; aspectReview.replaceChildren(); aspectApply.hidden = true;
    if (source.value === "aspect_ratio") bindingStatus.textContent = "Target changed. Populate mappings to verify this exact input's choices.";
  });
  profiles.addEventListener("input", () => { dirty = true; });
  sourceFields();

  function sourceFields() {
    assetKind.parentElement.hidden = source.value !== "reference_asset";
    ordinal.parentElement.hidden = source.value !== "reference_asset";
    presence.parentElement.hidden = source.value !== "reference_asset";
    consumerGroup.hidden = source.value !== "reference_asset";
    aspectMapping.hidden = source.value !== "aspect_ratio";
    const aspectSource = editing >= 0 && bindings[editing]?.source?.kind === "aspect_ratio" ? bindings[editing].source : null;
    aspectProposal = null; aspectReview.replaceChildren(); aspectApply.hidden = true;
    aspectEnumChoices = aspectSource?.enumChoices || null;
    aspectExtraMappings = Object.fromEntries(Object.entries(aspectSource?.valueMap || {}).filter(([ratio]) => !H3_ASPECT_RATIO_CHOICES.includes(ratio)));
    for (const [ratio, input] of aspectMappingInputs) input.value = aspectSource?.valueMap?.[ratio] || "";
    requiredHelper.hidden = source.value !== "reference_asset" || presence.value !== "required";
    renderConsumers();
  }
  function renderInputs() {
    const inputs = nodes.find(item => item.id === targetNode.value)?.inputs || [];
    options(targetInput, inputs.map(item => [item.name, `${item.name}${item.kind !== "scalar" ? ` (${item.kind} — preserved)` : ` · ${item.hint}`}`, item.kind !== "scalar"]));
    renderConsumers();
  }
  function renderConsumers(selected = []) {
    consumerChoices.replaceChildren(); let count = 0;
    const loader = nodes.find(item => item.id === targetNode.value);
    let candidates = [];
    try { candidates = loader ? discoverDeactivationTargets(nodes, targetNode.value) : []; }
    catch (error) { consumerStatus.textContent = error.message; return; }
    for (const candidate of candidates) {
      const value = { nodeId: candidate.nodeId, inputName: candidate.inputName,
        expectedSourceNodeId: candidate.sourceNodeId, expectedOutputIndex: candidate.outputIndex };
      const label = el("label", null, consumerChoices);
      const check = el("input", null, label); check.type = "checkbox"; check.value = JSON.stringify(value);
      check.checked = selected.some(item => item.nodeId === value.nodeId && item.inputName === value.inputName
        && (item.expectedSourceNodeId || targetNode.value) === value.expectedSourceNodeId
        && (item.expectedOutputIndex ?? item.outputIndex) === value.expectedOutputIndex);
      const existing = selected.find(item => item.nodeId === value.nodeId && item.inputName === value.inputName);
      const channel = el("select", null, label);
      channel.setAttribute("aria-label", "Reference route channel");
      const choices = assetKind.value === "video"
        ? [["", "Classify this route once"], ["video_frames", "Video frames"], ["embedded_audio", "Embedded video audio"]]
        : assetKind.value === "audio" ? [["audio", "Standalone audio"]] : [["image", "Picture"]];
      options(channel, choices);
      channel.value = existing?.channel || choices[0][0];
      const updateChannel = () => { check.value = JSON.stringify({ ...value, channel: channel.value || null }); };
      channel.addEventListener("change", updateChannel); updateChannel();
      el("span", `${candidate.route.join(" → ")} → ${candidate.inputName} · node ${candidate.nodeId} · from ${loader?.classType || "mapped loader"} output ${candidate.outputIndex}`, label); count++;
    }
    consumerStatus.textContent = count > 1 ? `${count} destinations found. This reference cannot safely be optional until its destinations are explicitly accounted for.`
      : count === 1 ? "1 destination found. Check it only if it belongs to this reference slot."
        : "MiniConstruct could not determine a safe optional-reference boundary for this branch. Make the reference Required or simplify/configure the workflow.";
  }
  function renderGraph() {
    options(targetNode, nodes.map(item => [item.id, item.label])); renderInputs(); nodeList.replaceChildren();
    for (const node of nodes) {
      el("strong", node.label, nodeList);
      el("p", node.inputs.map(item => `${item.name}: ${item.kind}${item.kind === "scalar" ? ` (${item.hint})` : ""}`).join(" · "), nodeList);
    }
  }
  function renderBindings() {
    bindingButton.textContent = editing < 0 ? "Add binding" : "Update binding";
    if (editing < 0) editorHeading.textContent = "Add semantic binding";
    bindingList.replaceChildren();
    bindings.forEach((binding, index) => {
      const row = el("div", null, bindingList); row.className = `execution-item binding-row${editing === index ? " editing" : ""}`;
      const semantic = binding.source.kind === "reference_asset"
        ? `${binding.source.assetKind === "image" ? "Picture" : binding.source.assetKind === "audio" ? "Audio" : "Video"} ${binding.source.ordinal}`
        : binding.source.kind === "duration" ? "Duration"
          : binding.source.kind === "aspect_ratio" ? "Aspect Ratio (H3 Setup)"
            : binding.source.kind === "renderer_seed" ? "Renderer seed" : "Generated H3 prompt";
      const found = nodes.find(item => item.id === binding.nodeId)?.inputs.find(item => item.name === binding.inputName && item.kind === "scalar");
      const destinations = binding.routes || binding.deactivationTargets || (binding.controlledConsumers || []).map(item => ({
        nodeId: item.nodeId, inputName: item.inputName, expectedSourceNodeId: binding.nodeId, expectedOutputIndex: item.outputIndex }));
      el("span", `${semantic} → ${binding.nodeId} / ${binding.inputName}${binding.source.kind === "reference_asset" ? ` · ${binding.presence || "optional"} · ${destinations.length} deactivation destinations` : ""}${found ? "" : " — missing or non-scalar target; edit or remove"}`, row);
      button(`binding-edit-${index}`, "Edit binding", row, () => {
        editing = index; source.value = binding.source.kind; presence.value = binding.presence || "optional"; sourceFields();
        assetKind.value = binding.source.assetKind || "image"; ordinal.value = String(binding.source.ordinal || 1);
        targetNode.value = binding.nodeId; renderInputs(); targetInput.value = found ? binding.inputName : "";
        renderConsumers(destinations);
        editorHeading.textContent = `Editing binding: ${semantic} → ${binding.nodeId} / ${binding.inputName}`;
        bindingButton.textContent = "Update binding";
        renderBindings();
      });
      button(`binding-remove-${index}`, "Remove binding", row, () => { bindings.splice(index, 1); resetBindingEditor(); dirty = true; renderBindings(); });
    });
  }
  function resetBindingEditor() {
    editing = -1; source.value = "generated_prompt"; assetKind.value = capabilities?.assetKinds?.[0] || "image";
    ordinal.value = "1"; presence.value = "optional"; targetNode.value = ""; targetInput.replaceChildren();
    bindingStatus.textContent = ""; bindingButton.textContent = "Add binding"; editorHeading.textContent = "Add semantic binding";
    sourceFields(); renderBindings();
  }
  function adoptProfile(profile) {
    duplicateDraft = null;
    nativeMapping.value = profile?.nativeContinuation ? JSON.stringify(profile.nativeContinuation, null, 2) : "";
    profileId = profile?.id || null; profileName.value = profile?.name || ""; description.value = profile?.description || "";
    // Never serialize the browser's parsed saved graph back to the server:
    // JavaScript may round large JSON integers used by arbitrary custom nodes.
    workflowJson = ""; nodes = profile ? inspectWorkflow(profile.workflow) : [];
    bindings = structuredClone(profile?.bindings || []); editing = -1; dirty = false;
    controls = structuredClone(profile?.exposedControls || []); controlEditing = -1;
    presets = structuredClone(profile?.workflowControlPresets || []); presetEditing = -1;
    for (const input of modeChoices.querySelectorAll("input")) input.checked = Boolean(profile?.compatibleModes.includes(input.value));
    i2vaHelp.hidden = !profile?.compatibleModes.includes("I2VA");
    fingerprint.textContent = profile ? `SHA-256: ${profile.fingerprint}` : "Import a workflow to begin";
    profileStatus.textContent = profile ? `Editing ${profile.name}` : "New profile";
    importStatus.textContent = ""; bindingStatus.textContent = ""; renderGraph(); renderBindings(); renderControls(); renderPresets();
  }
  async function loadConnections() {
    const saved = await api.executionConnections(); connectionList.replaceChildren();
    const priorEnumConnection = enumConnection.value;
    options(enumConnection, [["", "Choose connection for explicit capability sync"], ...saved.map(item => [item.id, item.name])]);
    if (saved.some(item => item.id === priorEnumConnection)) enumConnection.value = priorEnumConnection;
    if (!saved.length) el("p", "No saved connections", connectionList);
    saved.forEach(item => {
      const row = el("div", null, connectionList); row.className = "execution-item";
      el("span", `${item.name} · ${item.baseUrl}`, row);
      const result = status(`connection-result-${item.id}`, row);
      button(`connection-edit-${item.id}`, "Edit", row, () => {
        connectionId = item.id; connectionName.value = item.name; baseUrl.value = item.baseUrl;
        localInput.value = item.localInputDirectory || "";
        connectionStatus.textContent = `Editing ${item.name}`;
      });
      button(`connection-test-${item.id}`, "Test Connection", row, () => act(result, async () => {
        const response = await api.testExecutionConnection(item.id); result.textContent = `${response.status}: ${response.message}`;
      }));
      button(`connection-delete-${item.id}`, "Delete", row, () => act(result, async () => {
        if (!confirmDelete(`Delete connection “${item.name}”?`)) { result.textContent = "Deletion canceled"; return; }
        await api.deleteExecutionConnection(item.id);
        if (connectionId === item.id) { connectionId = null; connectionName.value = ""; baseUrl.value = ""; localInput.value = ""; }
        await loadConnections(); await onConnectionsChanged(); connectionStatus.textContent = "Connection deleted";
      }));
    });
  }
  async function loadProfiles() {
    const saved = await api.executionProfiles(); profileList.replaceChildren();
    await onProfilesChanged();
    if (!saved.length) el("p", "No saved Workflow Profiles", profileList);
    saved.forEach(item => {
      const row = el("div", null, profileList); row.className = "execution-item";
      el("span", `${item.name} · Compatible H3 modes: ${item.compatibleModes.join(", ")} · Saved reusable execution configuration`, row);
      button(`profile-edit-${item.id}`, "Edit", row, () => act(profileStatus, async () => {
        if (dirty && !confirmDelete("Discard unsaved profile edits?")) { profileStatus.textContent = "Edit canceled"; return; }
        adoptProfile(await api.executionProfile(item.id));
      }));
      button(`profile-duplicate-${item.id}`, "Duplicate", row, () => act(profileStatus, async () => {
        if (dirty && !confirmDelete("Discard unsaved profile edits?")) { profileStatus.textContent = "Duplication canceled"; return; }
        const draft = await api.duplicateExecutionProfileDraft(item.id);
        adoptProfile({ ...draft, workflow: JSON.parse(draft.workflowJson) });
        duplicateDraft = structuredClone(draft);
        workflowJson = draft.workflowJson;
        dirty = true;
        fingerprint.textContent = "Independent new Profile draft; fingerprint will be calculated on save";
        profileStatus.textContent = `Duplicating ${item.name} — new Profile; rename and Save profile to create it`;
      }));
      button(`profile-delete-${item.id}`, "Delete", row, () => act(profileStatus, async () => {
        if (!confirmDelete(`Delete Workflow Profile “${item.name}”?`)) { profileStatus.textContent = "Deletion canceled"; return; }
        await api.deleteExecutionProfile(item.id); if (profileId === item.id) adoptProfile(null);
        await loadProfiles(); profileStatus.textContent = "Workflow Profile deleted";
      }));
    });
  }
  async function refresh() {
    return act(loadStatus, async () => {
      if (!capabilities) {
        capabilities = await api.executionCapabilities();
        for (const mode of capabilities.compatibleModes) {
          const label = el("label", null, modeChoices); label.className = "check";
          const input = el("input", null, label); input.type = "checkbox"; input.value = mode; input.id = `execution-mode-${mode}`;
          el("span", mode, label);
        }
        options(assetKind, capabilities.assetKinds.map(kind => [kind, kind === "image" ? "Picture" : kind === "audio" ? "Audio" : "Video"]));
      }
      await loadConnections(); await loadProfiles(); loadStatus.textContent = "Saved configuration loaded";
    });
  }
  return { refresh, discardDuplicateDraft() { if (duplicateDraft) adoptProfile(null); }, get hasDraft() { return dirty; } };
}
