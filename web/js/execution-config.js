// Drafts are ephemeral. The backend owns all saved execution configuration.
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
        link: isGraphLink(value) ? value : null, hint: scalarHint(value),
        kind: isGraphLink(value) ? "graph link" : value !== null && typeof value === "object" ? "structured input" : "scalar" })) };
  });
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
  let busy = false, dirty = false;
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
  const connectionStatus = status("connection-status", connections);
  const connectionActions = el("div", null, connections); connectionActions.className = "inline-row wrap";
  button("connection-new", "New connection", connectionActions, () => {
    connectionId = null; connectionName.value = ""; baseUrl.value = ""; connectionStatus.textContent = "New connection";
  });
  button("connection-save", "Save connection", connectionActions, () => act(connectionStatus, async () => {
    const saved = await api.saveExecutionConnection(connectionId, { name: connectionName.value, baseUrl: baseUrl.value });
    connectionId = saved.id; connectionName.value = saved.name; baseUrl.value = saved.baseUrl;
    connectionStatus.textContent = "Connection saved"; await loadConnections(); await onConnectionsChanged();
  }));

  const profiles = el("fieldset"); el("legend", "Workflow Profiles — how", profiles);
  const profileList = el("div", null, profiles); profileList.className = "execution-list";
  button("profile-new", "New profile", profiles, () => {
    if (dirty && !confirmDelete("Discard unsaved profile edits?")) return;
    adoptProfile(null);
  });
  const profileName = field("profile-name", "Profile name", "input", profiles); profileName.maxLength = 160;
  const description = field("description", "Description (optional)", "textarea", profiles); description.maxLength = 4000; description.rows = 2;
  const modeGroup = el("fieldset", null, profiles); el("legend", "Compatible H3 modes", modeGroup);
  const modeChoices = el("div", null, modeGroup); modeChoices.className = "inline-row wrap";
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
  options(source, [["generated_prompt", "Generated H3 prompt"], ["duration", "Duration (seconds)"], ["reference_asset", "Reference asset by kind + ordinal"]]);
  const assetKind = field("asset-kind", "Reference kind", "select", bindingForm); assetKind.parentElement.className = "reference-only";
  const ordinal = field("ordinal", "Reference ordinal (one-based)", "input", bindingForm); ordinal.parentElement.className = "reference-only"; ordinal.type = "number"; ordinal.min = "1"; ordinal.step = "1"; ordinal.value = "1";
  const presence = field("presence", "Reference presence", "select", bindingForm); presence.parentElement.className = "reference-only";
  options(presence, [["optional", "Optional"], ["required", "Required"]]);
  const targetNode = field("target-node", "Target node", "select", bindingForm);
  const targetInput = field("target-input", "Target input (scalar only)", "select", bindingForm);
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
    if (!targetNode.value || !targetInput.value) { bindingStatus.textContent = "Choose an existing scalar target input"; return false; }
    if (bindings.some((item, index) => index !== editing && item.nodeId === targetNode.value && item.inputName === targetInput.value)) {
      bindingStatus.textContent = "That target already has a binding"; return false;
    }
    const binding = { source: semantic, nodeId: targetNode.value, inputName: targetInput.value };
    if (semantic.kind === "reference_asset") {
      binding.presence = presence.value;
      const targets = Array.from(consumerChoices.querySelectorAll("input"))
        .filter(item => item.checked).map(item => JSON.parse(item.value));
      binding.routes = targets.map(item => ({ ...item, channel: item.channel || null }));
    }
    if (editing < 0) bindings.push(binding); else bindings[editing] = binding;
    editing = -1; dirty = true; renderBindings(); bindingStatus.textContent = "Draft binding saved";
    return true;
  }
  const bindingButton = button("binding-save", "Add binding", bindingEditorGroup, () => { applyBindingDraft(); });
  button("binding-cancel", "Cancel binding edit", bindingEditorGroup, () => resetBindingEditor());
  const profileStatus = status("profile-status", profiles);
  const profileActions = el("div", null, profiles); profileActions.className = "inline-row wrap";
  const body = () => ({ name: profileName.value, description: description.value,
    compatibleModes: Array.from(modeChoices.querySelectorAll("input")).filter(item => item.checked).map(item => item.value),
    ...(!profileId || workflowJson ? { workflowJson } : {}), bindings: structuredClone(bindings) });
  button("profile-validate", "Validate draft", profileActions, () => act(profileStatus, async () => {
    const report = await api.validateExecutionProfile(body(), profileId);
    profileStatus.textContent = report.valid ? "Profile is valid offline; server node availability is not checked" : report.errors.join("\n");
  }));
  button("profile-save", "Save profile", profileActions, () => act(profileStatus, async () => {
    if (editing >= 0 && !applyBindingDraft()) { profileStatus.textContent = bindingStatus.textContent; return; }
    const saved = await api.saveExecutionProfile(profileId, body()); adoptProfile(saved);
    profileStatus.textContent = "Workflow Profile saved; Validate draft checks optional-slot readiness"; await loadProfiles();
  }));
  file.addEventListener("change", () => act(importStatus, async () => {
    const selected = file.files?.[0];
    try {
      if (!selected) { importStatus.textContent = "No file selected"; return; }
      if (!capabilities) throw new Error("Load execution settings before importing");
      if (selected.size > capabilities.maxWorkflowBytes) throw new Error("Workflow exceeds the 2 MiB size limit");
      const text = await selected.text();
      const inspected = inspectWorkflow(JSON.parse(text));
      workflowJson = text; nodes = inspected; dirty = true; editing = -1; renderGraph(); renderBindings();
      fingerprint.textContent = "Draft workflow changed; fingerprint will be calculated on save";
      importStatus.textContent = `${nodes.length} nodes imported into draft`;
    } finally { file.value = ""; }
  }));
  source.addEventListener("change", sourceFields);
  presence.addEventListener("change", sourceFields);
  assetKind.addEventListener("change", sourceFields);
  targetNode.addEventListener("change", renderInputs);
  profiles.addEventListener("input", () => { dirty = true; });
  sourceFields();

  function sourceFields() {
    assetKind.parentElement.hidden = source.value !== "reference_asset";
    ordinal.parentElement.hidden = source.value !== "reference_asset";
    presence.parentElement.hidden = source.value !== "reference_asset";
    consumerGroup.hidden = source.value !== "reference_asset";
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
        : binding.source.kind === "duration" ? "Duration" : "Generated H3 prompt";
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
    profileId = profile?.id || null; profileName.value = profile?.name || ""; description.value = profile?.description || "";
    // Never serialize the browser's parsed saved graph back to the server:
    // JavaScript may round large JSON integers used by arbitrary custom nodes.
    workflowJson = ""; nodes = profile ? inspectWorkflow(profile.workflow) : [];
    bindings = structuredClone(profile?.bindings || []); editing = -1; dirty = false;
    for (const input of modeChoices.querySelectorAll("input")) input.checked = Boolean(profile?.compatibleModes.includes(input.value));
    fingerprint.textContent = profile ? `SHA-256: ${profile.fingerprint}` : "Import a workflow to begin";
    profileStatus.textContent = profile ? `Editing ${profile.name}` : "New profile";
    importStatus.textContent = ""; bindingStatus.textContent = ""; renderGraph(); renderBindings();
  }
  async function loadConnections() {
    const saved = await api.executionConnections(); connectionList.replaceChildren();
    if (!saved.length) el("p", "No saved connections", connectionList);
    saved.forEach(item => {
      const row = el("div", null, connectionList); row.className = "execution-item";
      el("span", `${item.name} · ${item.baseUrl}`, row);
      const result = status(`connection-result-${item.id}`, row);
      button(`connection-edit-${item.id}`, "Edit", row, () => {
        connectionId = item.id; connectionName.value = item.name; baseUrl.value = item.baseUrl;
        connectionStatus.textContent = `Editing ${item.name}`;
      });
      button(`connection-test-${item.id}`, "Test Connection", row, () => act(result, async () => {
        const response = await api.testExecutionConnection(item.id); result.textContent = `${response.status}: ${response.message}`;
      }));
      button(`connection-delete-${item.id}`, "Delete", row, () => act(result, async () => {
        if (!confirmDelete(`Delete connection “${item.name}”?`)) { result.textContent = "Deletion canceled"; return; }
        await api.deleteExecutionConnection(item.id);
        if (connectionId === item.id) { connectionId = null; connectionName.value = ""; baseUrl.value = ""; }
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
      el("span", `${item.name} · ${item.compatibleModes.join(", ")}`, row);
      button(`profile-edit-${item.id}`, "Edit", row, () => act(profileStatus, async () => {
        if (dirty && !confirmDelete("Discard unsaved profile edits?")) { profileStatus.textContent = "Edit canceled"; return; }
        adoptProfile(await api.executionProfile(item.id));
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
  return { refresh, get hasDraft() { return dirty; } };
}
