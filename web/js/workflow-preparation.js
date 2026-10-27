// Saved Project + sidecar are authority. No History or browser persistence here.
import { renderSubmissionLabels } from "./render-submission-label.js";
export function canSelectExecutionOutput(output) {
  return Boolean(output?.prompt?.trim());
}

export function currentExecutionOutput(output, generation, outputs, repairRunning) {
  if (repairRunning || (generation && generation.outputs !== outputs)) return null;
  return output || null;
}

export function executionCandidateState(context, output, selection) {
  const currentFingerprint = selection?.currentFingerprints
    ? selection.currentFingerprints[output?.authoringPath || "one_shot"] : selection?.currentFingerprint;
  if (!context.saved || !context.projectId || !context.generationId)
    return { eligible: false, reason: "Save a filesystem Project first." };
  if (context.dirty) return { eligible: Boolean(output?.prompt?.trim()), reason: "The current Generation has unsaved edits. Selection is checked against its saved version." };
  if (!output?.prompt?.trim())
    return { eligible: false, reason: "There is no H3 prompt to select." };
  if (output.origin === "manual_edit")
    return { eligible: true, reason: "Manually edited text can be selected exactly as shown." };
  if (output.historyItem && output.historyMode && output.historyMode !== context.mode)
    return { eligible: true, reason: "This History item has a different H3 mode; execution remains available." };
  if (!output.sourceFingerprint)
    return { eligible: true, reason: output.origin === "history"
      ? "Older History item has no saved generation provenance; it can be adopted."
      : "This result has no trusted generation provenance; it can be adopted." };
  if (!currentFingerprint)
    return { eligible: true, reason: "The saved Generation fingerprint is unavailable; this prompt can be adopted." };
  if (output.sourceFingerprint !== currentFingerprint)
    return { eligible: true, reason: output.origin === "history"
      ? "This History prompt belongs to a different or stale authoring snapshot."
      : "This prompt was generated for earlier saved authoring; explicit selection adopts its exact text." };
  return { eligible: true, reason: "Generated prompt ready for execution." };
}

export function historyAdoptionState(context, output, selection = null) {
  const currentFingerprint = selection?.currentFingerprints
    ? selection.currentFingerprints[output?.authoringPath || "one_shot"] : selection?.currentFingerprint;
  if (!output?.historyItem && output?.origin !== "history") return { visible: false, eligible: false, reason: "" };
  if (!context.saved || !context.projectId || !context.generationId)
    return { visible: true, eligible: false, reason: "Save a filesystem Project first." };
  if (context.dirty)
    return { visible: true, eligible: Boolean(output?.prompt?.trim()), reason: "Unsaved edits remain local. Adoption is checked against the saved Generation." };
  if (output.origin === "manual_edit")
    return { visible: true, eligible: true, reason: "This edited History prompt can be adopted exactly as shown." };
  if (!output.prompt?.trim()) return { visible: true, eligible: false, reason: "This History item has no prompt text." };
  if (output.historyMode && output.historyMode !== context.mode)
    return { visible: true, eligible: true, reason: "This History item's H3 mode differs; adoption remains available." };
  if (output.sourceFingerprint && !currentFingerprint)
    return { visible: true, eligible: true, reason: "The server will check the saved Generation fingerprint." };
  if (output.sourceFingerprint === currentFingerprint)
    return { visible: false, eligible: false, reason: "" };
  const reason = !output.sourceFingerprint
    ? "This History item predates saved generation provenance. Adopt binds its exact text for execution."
    : "This prompt belongs to an older Generation snapshot. Adopt binds its exact text for execution.";
  return { visible: true, eligible: true, reason };
}

// Structured entries own severity; legacy/unrecognized diagnostics remain warnings.
function preparationDiagnostics(prepared) {
  const entries = new Map();
  const rank = { info: 0, warning: 1, error: 2 };
  const add = (message, severity) => {
    severity = Object.hasOwn(rank, severity) ? severity : "warning";
    if (!entries.has(message) || rank[severity] > rank[entries.get(message)]) entries.set(message, severity);
  };
  for (const item of prepared.diagnostics || []) add(item.message, item.severity);
  for (const message of prepared.warnings || []) if (!entries.has(message)) add(message, "warning");
  for (const message of prepared.information || []) if (!entries.has(message)) add(message, "info");
  return [...entries].map(([message, severity]) => ({ message, severity }));
}

export function createWorkflowPreparation({ root, primaryRoot = root, detailsRoot = root, api, getContext, getOutput, syncReferences = null, onRoutingChanged = () => {}, onSelectionChanged = () => {}, onSubmitted = () => {}, onProjectMutation = () => {} }) {
  const doc = primaryRoot.ownerDocument;
  let mountRoot = primaryRoot;
  let contextKey = "", controlsIdentityKey = "", epoch = 0, profileEpoch = 0, profileDetailEpoch = 0, selectionEpoch = 0;
  let resetEpoch = 0;
  let profiles = [], profileDetailPending = Promise.resolve(), selection = null, prepared = null, busy = false, error = "", notice = "";
  let routingState = null, routingEpoch = 0;
  let referenceSelectionRequired = false;
  let controlState = null, controlStateKey = "", controlEpoch = 0, controlProfileId = "";
  let controlDraft = null, controlRows = new Map(), controlDefinitionKey = "", presetDefinitionKey = "";
  let continuation = null, continuationEpoch = 0, inspectedAttemptId = null;
  let seedDraft = null, nativeDraft = null, nativeIdentity = "";
  let explicitProfileId = null;
  let connections = [], connectionEpoch = 0, sendReady = false, receipt = null, sending = false, sendAttempted = false;
  const el = (tag, text, parent = mountRoot) => {
    const node = doc.createElement(tag); if (text != null) node.textContent = text; parent.append(node); return node;
  };
  function preservePaneScroll(update) {
    const pane = primaryRoot.closest(".right-stage");
    const scrollTop = pane?.scrollTop;
    try { return update(); }
    finally { if (pane && scrollTop != null) pane.scrollTop = scrollTop; }
  }
  const button = (id, text, action, parent = mountRoot) => {
    const node = el("button", text, parent); node.id = `preparation-${id}`; node.type = "button";
    node.addEventListener("click", action); return node;
  };
  el("h3", "ComfyUI Execution");
  el("p", "Validation findings are advisory and do not prevent explicit execution. Prepare uses the visible H3 prompt when present.").className = "helper";
  const compactLayout = el("div"); compactLayout.className = "execution-primary-layout";
  const primaryActions = el("section", null, compactLayout); primaryActions.id = "preparation-primary-actions"; primaryActions.className = "execution-primary-actions";
  const primaryActionRow = el("div", null, primaryActions); primaryActionRow.className = "execution-action-grid execution-primary-action-row";
  const selectedRegion = el("section", null, compactLayout); selectedRegion.className = "execution-selected-region";
  const status = el("p", "", selectedRegion); status.id = "preparation-selection"; status.setAttribute("role", "status");
  const selectedText = el("details", null, selectedRegion); selectedText.id = "preparation-selected";
  el("summary", "Selected execution prompt", selectedText);
  const promptText = el("pre", "", selectedText); promptText.className = "execution-prompt";
  async function selectVisible(c, output) {
    const currentFingerprint = selection?.currentFingerprints
      ? selection.currentFingerprints[output.authoringPath || "one_shot"] : selection?.currentFingerprint;
    if ((output.historyItem || output.origin === "history")
        && (!output.sourceFingerprint || output.sourceFingerprint !== currentFingerprint))
      return api.adoptHistoryExecutionPrompt(c.projectId, c.generationId, { prompt: output.prompt,
        ...(output.authoringPath ? { authoringPath: output.authoringPath } : {}) });
    if (output.origin === "manual_edit" || !output.sourceFingerprint)
      return api.adoptManualEditExecutionPrompt(c.projectId, c.generationId, { prompt: output.prompt,
        ...(output.authoringPath ? { authoringPath: output.authoringPath } : {}) });
    return api.selectExecutionPrompt(c.projectId, c.generationId,
      { prompt: output.prompt, sourceFingerprint: output.sourceFingerprint,
        ...(output.authoringPath ? { authoringPath: output.authoringPath } : {}),
        ...(output.referenceMap ? { referenceMap: output.referenceMap } : {}) });
  }
  const use = button("use", "Use for execution", () => act(async c => {
    const output = getOutput();
    const candidate = executionCandidateState(c, output, selection);
    if (!candidate.eligible) throw new Error(candidate.reason);
    return selectVisible(c, output);
  }, true), primaryActionRow); use.className = "primary";
  const adopt = button("adopt", "Adopt for execution", () => act(async c => {
    const output = getOutput();
    const candidate = historyAdoptionState(c, output, selection);
    if (!candidate.eligible) throw new Error(candidate.reason);
    return api.adoptHistoryExecutionPrompt(c.projectId, c.generationId, { prompt: output.prompt,
      ...(output.authoringPath ? { authoringPath: output.authoringPath } : {}) });
  }, true), primaryActionRow); adopt.className = "primary";
  const clear = button("clear", "Clear selection", () => act(c => api.clearExecutionPrompt(c.projectId, c.generationId), true), primaryActionRow);
  const candidateHint = el("p", "", primaryActions); candidateHint.id = "preparation-candidate-hint"; candidateHint.className = "helper";
  const prepare = button("prepare", "Prepare", () => act(async c => {
    const resetTicket = resetEpoch;
    if (referenceSelectionRequired) throw new Error("Reference Assets changed. Explicitly Use for execution before Prepare.");
    if (!profile.value) throw new Error("Choose a compatible Workflow Profile.");
    await syncExecutionSetup(c);
    const output = getOutput();
    if (output?.prompt?.trim()) {
      if (!selection?.selected || selection.stale || selection.selected.prompt !== output.prompt
          || (selection.selected.authoringPath || "one_shot") !== (output.authoringPath || "one_shot")) {
        const selected = await selectVisible(c, output);
        if (resetTicket !== resetEpoch) throw new Error("Generated state was cleared during preparation.");
        selection = selected;
        selectionEpoch++; onSelectionChanged();
      }
    } else if (!selection?.selected) throw new Error("There is no H3 prompt to prepare.");
    const result = await api.prepareWorkflow({ projectId: c.projectId, generationId: c.generationId, profileId: profile.value });
    if (resetTicket !== resetEpoch) throw new Error("Generated state was cleared during preparation.");
    if (selection && result.currentFingerprint) selection = { ...selection, currentFingerprint: result.currentFingerprint,
      stale: selection.selected?.sourceFingerprint !== result.currentFingerprint };
    return result;
  }), primaryActionRow); prepare.className = "primary";
  const send = button("send", "Send to ComfyUI", sendCurrent, primaryActionRow); send.className = "primary";
  const prepareHint = el("p", "", primaryActions); prepareHint.id = "preparation-prepare-hint"; prepareHint.className = "helper";
  button("refresh", "Refresh preparation status", () => refresh(), primaryActionRow);
  const message = el("p", "", primaryActions); message.id = "preparation-message"; message.setAttribute("role", "status");
  const sendStatus = el("p", "No active prepared send", primaryActions); sendStatus.id = "preparation-receipt";
  sendStatus.setAttribute("role", "status");
  mountRoot = detailsRoot;
  const settingsSection = el("section"); settingsSection.id = "preparation-settings"; settingsSection.className = "execution-settings";
  el("h3", "ComfyUI Execution Settings", settingsSection);
  const group = (id, title) => {
    const section = el("section", null, settingsSection); section.id = `preparation-${id}`;
    section.className = "execution-settings-group";
    const heading = el("h4", title, section); heading.id = `${section.id}-heading`;
    section.setAttribute("aria-labelledby", heading.id); return section;
  };
  const connectionGroup = group("connection-profile", "Connection & Workflow Profile");
  const connectionLabel = el("label", "ComfyUI Connection", connectionGroup);
  const connection = el("select", null, connectionLabel); connection.id = "preparation-connection";
  connection.addEventListener("change", () => { invalidateSend(); render(); });
  const label = el("label", "Workflow Profile", connectionGroup);
  const profile = el("select", null, label); profile.id = "preparation-profile";
  // Browsing is deliberately ephemeral. The explicit action below is the only
  // route that makes a profile the Generation's saved continuity preference.
  profile.addEventListener("change", () => { explicitProfileId = profile.value; setControlProfile(profile.value); invalidate(); refreshControls(); profileDetailPending = refreshProfileDetails(); render(); });
  el("p", "This Workflow Profile is used by the current Prepare and Send. Saved profiles can be reused whenever you switch H3 modes; no re-import or Load action is needed.", connectionGroup).className = "helper";
  const applyProfile = button("apply-profile", "Save as native-continuation profile", () => saveContinuation({ profileId: profile.value }), connectionGroup);
  el("p", "Saving a native-continuation preference is optional for an ordinary render. It changes continuation setup, not just the current execution choice.", connectionGroup).className = "helper";
  const profileStatus = el("p", "", connectionGroup); profileStatus.id = "preparation-profile-status"; profileStatus.className = "helper";
  const controlDisclosure = el("details", null, settingsSection); controlDisclosure.id = "preparation-workflow-controls"; controlDisclosure.className = "execution-settings-group";
  const controlSummary = el("summary", "Workflow Controls", controlDisclosure); controlSummary.id = "preparation-workflow-controls-summary";
  const controlCount = el("span", "", controlSummary); controlCount.className = "workflow-controls-count";
  const controlContent = el("div", null, controlDisclosure); controlContent.className = "workflow-controls-content";
  const controlScopeLabel = el("label", "Edit scope", controlContent);
  const controlScope = el("select", null, controlScopeLabel); controlScope.id = "preparation-control-scope";
  const controlHelp = el("p", "Effective values are applied during Prepare/Send. Precedence: Profile default → Project default → Generation override.", controlContent);
  controlHelp.className = "helper workflow-control-runtime-help";
  for (const [id, label] of [["project", "Project defaults"], ["generation", "This Generation"]]) {
    const option = el("option", label, controlScope); option.value = id;
  }
  controlScope.value = "project";
  const presetRow = el("div", null, controlContent); presetRow.className = "inline-row wrap";
  const presetLabel = el("label", "Preset", presetRow);
  const presetSelect = el("select", null, presetLabel); presetSelect.id = "preparation-preset";
  const presetApply = el("button", "Apply preset", presetRow); presetApply.type = "button"; presetApply.id = "preparation-preset-apply";
  el("span", "Selecting a preset does nothing until Apply preset is clicked.", presetRow).className = "helper workflow-control-preset-help";
  const presetMatch = el("p", "Custom", controlContent); presetMatch.id = "preparation-preset-match"; presetMatch.className = "helper";
  const clearScope = el("button", "Clear Project defaults", controlContent); clearScope.type = "button"; clearScope.id = "preparation-control-clear";
  const controlFields = el("div", null, controlContent);
  const resolutionSummary = el("div", null, controlFields); resolutionSummary.id = "preparation-resolution-summary"; resolutionSummary.className = "workflow-resolution-summary";
  resolutionSummary.hidden = true;
  const resolutionHeading = el("strong", "Resolution", resolutionSummary);
  const resolutionAspectLabel = el("span", "Aspect Ratio", resolutionSummary);
  const resolutionAspect = el("span", "", resolutionSummary); resolutionAspect.id = "preparation-resolution-aspect"; resolutionAspect.className = "workflow-resolution-aspect";
  const resolutionOrigin = el("span", "H3 Setup", resolutionSummary); resolutionOrigin.className = "workflow-resolution-origin";
  const controlLoading = el("p", "Loading saved Workflow Controls…", controlFields); controlLoading.className = "helper";
  controlLoading.hidden = true;
  const controlRowsRoot = el("div", null, controlFields); controlRowsRoot.id = "preparation-control-rows"; controlRowsRoot.className = "workflow-control-rows";
  const controlWarnings = el("p", "", controlFields); controlWarnings.className = "helper workflow-control-warnings";
  const discardControlDraft = el("button", "Discard unsaved changes", controlContent);
  discardControlDraft.type = "button"; discardControlDraft.id = "preparation-control-discard"; discardControlDraft.hidden = true;
  controlScope.addEventListener("change", () => { controlDraft = null; renderControls(); });
  discardControlDraft.addEventListener("click", () => { controlDraft = null; renderControls(); });
  presetApply.addEventListener("click", () => {
    const selected = profiles.find(item => item.id === profile.value);
    const preset = selected?.workflowControlPresets?.find(item => item.name === presetSelect.value);
    if (preset) saveControlMap(structuredClone(preset.values), `Applied “${preset.name}” ${controlScope.value === "project" ? "as Project defaults" : "to this Generation"}.`, { resetDraft: true });
  });
  clearScope.addEventListener("click", () => saveControlMap({}, controlScope.value === "project" ? "Cleared Project defaults." : "Cleared Generation overrides.", { resetDraft: true }));
  const seedSection = el("details", null, settingsSection); seedSection.className = "execution-settings-group renderer-seed-settings";
  const seedSummary = el("summary", "Renderer Seed", seedSection);
  el("p", "Controls the ComfyUI video renderer, separately from the prompt-writing LLM seed.", seedSection).className = "helper";
  const seedRow = el("div", null, seedSection); seedRow.className = "renderer-seed-row";
  const seedLabel = el("label", "Mode", seedRow);
  const seedMode = el("select", null, seedLabel); seedMode.id = "preparation-seed-mode";
  for (const [value, title] of [["workflow_default", "Workflow default"], ["random", "Random each Send"], ["fixed", "Fixed"]]) {
    const option = el("option", title, seedMode); option.value = value;
  }
  const fixedLabel = el("label", "Fixed renderer seed", seedRow);
  const seedValue = el("input", null, fixedLabel); seedValue.id = "preparation-seed-value";
  seedValue.type = "number"; seedValue.min = "0"; seedValue.max = "2147483647"; seedValue.step = "1";
  const seedSave = el("button", "Save renderer seed", seedRow); seedSave.type = "button";
  seedSave.id = "preparation-seed-save";
  const seedHelp = el("p", "", seedSection); seedHelp.className = "helper";
  seedMode.addEventListener("change", () => { seedDraft = { mode: seedMode.value, fixedSeed: seedValue.value }; invalidate(); render(); });
  seedValue.addEventListener("input", () => { seedDraft = { mode: seedMode.value, fixedSeed: seedValue.value }; invalidate(); render(); });
  seedSave.addEventListener("click", () => saveRendererSeed());
  const issues = el("p", "", connectionGroup); issues.id = "preparation-profile-issues"; issues.className = "helper";
  const referenceGroup = group("references", "References & Routing");
  const referenceSummary = el("p", "", referenceGroup); referenceSummary.id = "preparation-reference-summary";
  const routingSection = el("fieldset", null, referenceGroup); routingSection.id = "preparation-routing";
  el("legend", "Reference routing for this Generation", routingSection);
  el("p", "Choose routes before generating a prompt so its Audio labels match. Your master Workflow Profile stays reusable.", routingSection);
  const routingChoices = el("div", null, routingSection); routingChoices.className = "routing-choices";
  const preparedGroup = group("prepared-workflow", "Prepared Workflow");
  const preparationStatus = el("p", "", primaryActions); preparationStatus.id = "preparation-status";
  preparationStatus.setAttribute("role", "status"); preparationStatus.setAttribute("aria-live", "polite");
  preparationStatus.setAttribute("aria-atomic", "true");
  const result = el("div", null, preparedGroup); result.id = "preparation-result";
  const nativeSection = el("section", null, settingsSection); nativeSection.id = "preparation-native-section"; nativeSection.className = "native-continuation execution-settings-group";
  nativeSection.setAttribute("aria-labelledby", "preparation-native-heading");
  el("h4", "Native Continuation", nativeSection).id = "preparation-native-heading";
  const nativeContent = el("div", null, nativeSection); nativeContent.id = "preparation-native";

  const saved = c => Boolean(c.saved && c.projectId && c.generationId);
  const currentContext = c => {
    const now = getContext();
    return c.projectId === now.projectId && c.generationId === now.generationId
      && c.navigationTicket?.id === now.navigationTicket?.id;
  };
  async function syncExecutionSetup(c) {
    const resetTicket = resetEpoch;
    const duration = Number(c.durationSeconds);
    if (!Number.isFinite(duration) || duration < 4 || duration > 15)
      throw new Error("Set H3 Setup duration from 4 to 15 seconds before Prepare.");
    const setupReceipt = await api.updateExecutionSetup(c.projectId, c.generationId, {
      durationSeconds: duration, aspectRatio: c.aspectRatio, expectedProjectFingerprint: c.projectFingerprint,
      ...(c.projectEditFingerprint ? { expectedProjectEditFingerprint: c.projectEditFingerprint } : {}),
    });
    if (resetTicket !== resetEpoch) throw new Error("Generated state was cleared during preparation.");
    onProjectMutation(setupReceipt, c);
    if (!currentContext(c)) throw new Error("The active Generation changed during preparation.");
    if (api.executionSelection) {
      const selected = await api.executionSelection(c.projectId, c.generationId);
      if (resetTicket !== resetEpoch) throw new Error("Generated state was cleared during preparation.");
      if (!currentContext(c)) throw new Error("The active Generation changed during preparation.");
      selection = selected;
      onSelectionChanged();
    }
  }
  function invalidateSend() { epoch++; sendReady = false; receipt = null; sendAttempted = false; error = ""; notice = ""; }
  function invalidate() { invalidateSend(); prepared = null; result.replaceChildren(); preparationStatus.textContent = ""; }
  const controlsRequestKey = (c, profileId = profile.value) => JSON.stringify([c.projectId, c.generationId, profileId]);
  const controlsDraftKey = (c, profileId = profile.value, scope = controlScope.value) => JSON.stringify([c.projectId, c.generationId, profileId, scope]);
  const profileControlDefinitions = item => JSON.stringify({ controls: item?.exposedControls || [], presets: item?.workflowControlPresets || [] });
  function clearControlDraft() { controlDraft = null; }
  function setControlProfile(id) {
    if (id === controlProfileId) return;
    controlProfileId = id;
    seedDraft = null;
    profileDetailEpoch++;
    clearControlDraft();
  }
  function draftForCurrentScope(c = getContext(), selectedId = profile.value) {
    const key = controlsDraftKey(c, selectedId);
    if (!controlDraft || controlDraft.key !== key) controlDraft = { key, profileId: selectedId, values: Object.create(null), dirty: new Set() };
    return controlDraft;
  }
  function currentControlState(c = getContext(), selectedId = profile.value) {
    return controlStateKey === controlsRequestKey(c, selectedId) ? controlState : null;
  }
  function controlItem(id, c = getContext(), selectedId = profile.value) {
    return currentControlState(c, selectedId)?.controls.find(item => item.id === id) || null;
  }
  function persistedControlValue(declaration, item, scope) {
    if (scope === "project") return item?.hasProjectOverride ? item.projectOverride : declaration.defaultValue;
    return item?.value ?? declaration.defaultValue;
  }
  function controlSource(item, scope) {
    if (!item) return "Loading saved value";
    if (scope === "project") return item.hasProjectOverride ? "Project default" : "Profile default";
    return item.source === "generation_override" ? "This Generation"
      : item.source === "project_default" ? "Project default" : "Profile default";
  }
  function controlValueFromDraft(declaration, draft) {
    const raw = draft.values[declaration.id];
    if (declaration.type === "boolean") return Boolean(raw);
    if (declaration.type === "enum") return declaration.choices.find(choice => String(choice) === String(raw));
    return Number(raw);
  }
  function updateControlInputValue(row, value) {
    if (row.declaration.type === "boolean") {
      row.input.checked = Boolean(value);
      row.switchState.textContent = row.input.checked ? "On" : "Off";
    } else {
      row.input.value = value == null ? "" : String(value);
    }
  }
  function updatePresetMatch(selected, state, c) {
    if (!state) { presetMatch.textContent = "Custom"; return; }
    const scope = controlScope.value, draft = draftForCurrentScope(c);
    const values = Object.fromEntries(state.controls.map(item => {
      const declaration = selected.exposedControls.find(control => control.id === item.id);
      return [item.id, draft.dirty.has(item.id) ? controlValueFromDraft(declaration, draft)
        : persistedControlValue(declaration, item, scope)];
    }));
    const presets = selected.workflowControlPresets || [];
    const match = presets.find(preset => Object.keys(preset.values).length === state.controls.length
      && state.controls.every(item => Object.is(preset.values[item.id], values[item.id])));
    presetMatch.textContent = match ? `Matches preset: ${match.name}` : "Custom";
  }
  function buildControlRows(selected, aspectBinding) {
    const signature = JSON.stringify([selected.id, selected.exposedControls || []]);
    if (signature === controlDefinitionKey) return;
    controlDefinitionKey = signature;
    controlRows.clear();
    controlRowsRoot.replaceChildren();
    const relatedNode = aspectBinding?.nodeId;
    const declarations = selected.exposedControls || [];
    for (const declaration of declarations) {
      const row = el("div", null, controlRowsRoot); row.className = "workflow-control-row";
      if (relatedNode && declaration.target?.nodeId === relatedNode) row.className += " workflow-control-resolution-related";
      let input, switchState = null;
      const labelCell = el("div", null, row); labelCell.className = "workflow-control-label-cell";
      const label = el("label", declaration.label, labelCell); label.className = "workflow-control-label";
      const valueCell = el("div", null, row); valueCell.className = "workflow-control-value-cell";
      if (declaration.type === "boolean") {
        const switchLabel = el("label", null, valueCell); switchLabel.className = "mini-switch workflow-control-switch";
        input = el("input", null, switchLabel); input.type = "checkbox"; input.className = "mini-switch-input";
        input.setAttribute("role", "switch"); input.setAttribute("aria-label", declaration.label);
        el("span", "", switchLabel).className = "mini-switch-track";
        switchState = el("span", "Off", switchLabel); switchState.className = "mini-switch-state";
      } else {
        if (declaration.type === "enum") {
          input = el("select", null, valueCell);
          for (const choice of declaration.choices || []) { const option = el("option", String(choice), input); option.value = String(choice); }
        } else {
          input = el("input", null, valueCell); input.type = "number";
          input.step = String(declaration.step ?? (declaration.type === "integer" ? 1 : "any"));
          if (declaration.minimum != null) input.min = String(declaration.minimum);
          if (declaration.maximum != null) input.max = String(declaration.maximum);
        }
      }
      input.id = `preparation-control-${declaration.id}`;
      label.setAttribute("for", input.id);
      const source = el("span", "", row); source.className = "helper workflow-control-source";
      const unsaved = el("span", "Unsaved change", row); unsaved.className = "helper workflow-control-unsaved is-clean";
      const save = el("button", "", row); save.type = "button"; save.className = "workflow-control-save";
      save.addEventListener("click", () => saveControl(declaration.id));
      const clear = el("button", "Inherit", row); clear.type = "button"; clear.className = "workflow-control-inherit"; clear.hidden = true;
      clear.addEventListener("click", () => saveControl(declaration.id, undefined));
      const edit = () => {
        if (input.disabled) return;
        const draft = draftForCurrentScope();
        const rawValue = declaration.type === "boolean" ? input.checked : input.value;
        draft.values[declaration.id] = rawValue;
        const item = controlItem(declaration.id);
        const sameAsSaved = Object.is(controlValueFromDraft(declaration, draft), persistedControlValue(declaration, item, controlScope.value));
        if (sameAsSaved) { draft.dirty.delete(declaration.id); delete draft.values[declaration.id]; }
        else draft.dirty.add(declaration.id);
        if (!draft.dirty.size) clearControlDraft();
        updateControlRows();
      };
      input.addEventListener("input", edit);
      input.addEventListener("change", edit);
      input.addEventListener("blur", () => { if (!controlDraft?.dirty.has(declaration.id)) updateControlRows(); });
      controlRows.set(declaration.id, { row, declaration, input, source, unsaved, save, clear, switchState, label, labelCell, valueCell });
    }
  }
  function updateControlRows() {
    return preservePaneScroll(updateControlRowsInPlace);
  }
  function updateControlRowsInPlace() {
    const c = getContext(), selected = profiles.find(item => item.id === profile.value);
    if (!selected?.exposedControls?.length) return;
    const state = currentControlState(c);
    const scope = controlScope.value;
    const draft = draftForCurrentScope(c);
    for (const [id, row] of controlRows) {
      const item = state?.controls.find(control => control.id === id);
      // This one redundant legacy editor is superseded by the visible H3 Setup control.
      row.row.hidden = Boolean(item?.h3SetupControlled);
      const dirty = draft.dirty.has(id);
      const value = dirty ? draft.values[id] : persistedControlValue(row.declaration, item, scope);
      if (row.input !== doc.activeElement) updateControlInputValue(row, value);
      row.input.disabled = busy || !saved(c) || !state;
      row.source.textContent = controlSource(item, scope);
      row.unsaved.className = `helper workflow-control-unsaved${dirty ? "" : " is-clean"}`;
      row.save.textContent = scope === "project" ? "Save as Project default" : "Save for this Generation";
      row.save.disabled = row.input.disabled || !dirty;
      const explicit = scope === "project" ? Boolean(item?.hasProjectOverride) : Boolean(item?.hasGenerationOverride);
      row.clear.hidden = !explicit;
      row.clear.disabled = row.input.disabled;
      if (row.switchState) row.switchState.textContent = row.input.checked ? "On" : "Off";
    }
    const anyDirty = [...draft.dirty].some(id => controlRows.has(id));
    discardControlDraft.hidden = !anyDirty;
    presetApply.disabled = busy || !saved(c) || !(selected.workflowControlPresets || []).length || !state;
    clearScope.disabled = busy || !saved(c) || !state;
    updatePresetMatch(selected, state, c);
    const preparedMessages = new Set(prepared ? preparationDiagnostics(prepared).map(item => item.message) : []);
    const warnings = (state?.warnings || []).filter(text => !preparedMessages.has(text));
    controlWarnings.textContent = warnings.join("\n");
    controlWarnings.hidden = !warnings.length;
    controlLoading.hidden = Boolean(state);
  }
  function renderControls() {
    return preservePaneScroll(renderControlsInPlace);
  }
  function renderControlsInPlace() {
    const selected = profiles.find(item => item.id === profile.value);
    const aspectBinding = (selected?.bindings || []).find(binding => binding.source?.kind === "aspect_ratio");
    const hasControls = Boolean(selected?.exposedControls?.length);
    controlDisclosure.hidden = !selected || (!hasControls && !aspectBinding);
    const state = currentControlState(getContext(), selected?.id);
    const count = state ? state.controls.filter(item => !item.h3SetupControlled).length : selected?.exposedControls?.length || 0;
    controlCount.textContent = ` (${count})`;
    controlScopeLabel.hidden = controlHelp.hidden = !hasControls;
    presetRow.hidden = !selected?.workflowControlPresets?.length;
    presetMatch.hidden = clearScope.hidden = discardControlDraft.hidden = !hasControls;
    controlLoading.hidden = controlRowsRoot.hidden = controlWarnings.hidden = !hasControls;
    if (controlDisclosure.hidden) return;
    const c = getContext();
    resolutionSummary.hidden = !aspectBinding;
    if (aspectBinding) resolutionAspect.textContent = c.aspectRatio === "auto" ? "Automatic" : c.aspectRatio || "Automatic";
    buildControlRows(selected, aspectBinding);
    for (const row of controlRows.values())
      row.row.className = `workflow-control-row${aspectBinding && row.declaration.target?.nodeId === aspectBinding.nodeId ? " workflow-control-resolution-related" : ""}`;
    const presets = selected.workflowControlPresets || [];
    const presetSignature = JSON.stringify([selected.id, presets]);
    if (presetSignature !== presetDefinitionKey) {
      const prior = presetSelect.value;
      presetSelect.replaceChildren();
      for (const preset of presets) { const option = el("option", preset.name, presetSelect); option.value = preset.name; }
      if (presets.some(preset => preset.name === prior)) presetSelect.value = prior;
      presetDefinitionKey = presetSignature;
    }
    presetRow.hidden = !presets.length;
    clearScope.textContent = controlScope.value === "project" ? "Clear Project defaults" : "Clear Generation overrides";
    updateControlRows();
  }
  async function refreshControls() {
    const c = { ...getContext() }, selectedId = profile.value, requestKey = controlsRequestKey(c, selectedId), ticket = ++controlEpoch;
    if (!saved(c) || !selectedId || !api.workflowControls) {
      controlState = null; controlStateKey = ""; renderControls(); return;
    }
    try {
      const response = await api.workflowControls(c.projectId, c.generationId, selectedId);
      if (ticket !== controlEpoch || requestKey !== controlsRequestKey(getContext(), profile.value)) return;
      controlState = response; controlStateKey = requestKey;
    } catch (e) { if (ticket === controlEpoch && requestKey === controlsRequestKey(getContext(), profile.value)) error = e.message; }
    render();
  }
  async function refreshProfileDetails() {
    const selectedId = profile.value;
    if (!selectedId || !api.executionProfile) return;
    const activeProfile = profiles.find(item => item.id === selectedId);
    if (!activeProfile || Array.isArray(activeProfile.bindings)) return;
    const c = { ...getContext() }, requestKey = controlsRequestKey(c, selectedId), ticket = ++profileDetailEpoch;
    try {
      const detail = await api.executionProfile(selectedId);
      if (ticket !== profileDetailEpoch || requestKey !== controlsRequestKey(getContext(), profile.value)) return;
      const index = profiles.findIndex(item => item.id === selectedId);
      if (index < 0) return;
      const prior = profileControlDefinitions(profiles[index]);
      profiles[index] = { ...profiles[index], bindings: detail.bindings || [] };
      if (prior !== profileControlDefinitions(profiles[index]) && controlDraft?.profileId === selectedId) clearControlDraft();
      render();
    } catch (e) {
      if (ticket === profileDetailEpoch && requestKey === controlsRequestKey(getContext(), profile.value)) {
        error = e.message; render();
      }
    }
  }
  async function saveControl(id, overrideValue) {
    const c = { ...getContext() }, scope = controlScope.value, selectedId = profile.value;
    const state = currentControlState(c, selectedId);
    if (busy || !state) return;
    const declaration = profiles.find(item => item.id === selectedId)?.exposedControls.find(item => item.id === id);
    const draft = draftForCurrentScope(c, selectedId);
    const value = overrideValue === undefined && arguments.length > 1 ? undefined
      : draft.dirty.has(id) ? controlValueFromDraft(declaration, draft)
        : controlItem(id, c, selectedId)?.value;
    const values = Object.fromEntries(state.controls.filter(item => scope === "project" ? item.hasProjectOverride : item.hasGenerationOverride)
      .map(item => [item.id, scope === "project" ? item.projectOverride : item.generationOverride]));
    if (value === undefined) delete values[id]; else values[id] = value;
    return saveControlMap(values, "", { savedIds: [id] });
  }
  async function saveControlMap(values, successMessage = "", { savedIds = [], resetDraft = false } = {}) {
    const c = { ...getContext() }, scope = controlScope.value, selectedId = profile.value;
    if (busy || !currentControlState(c, selectedId)) return;
    const requestKey = controlsRequestKey(c, selectedId), draftKey = controlsDraftKey(c, selectedId, scope);
    busy = true; invalidate(); render();
    try {
      const response = await api.saveWorkflowControls(c.projectId, c.generationId, selectedId, scope, values);
      if (requestKey !== controlsRequestKey(getContext(), profile.value)) return;
      controlState = response; controlStateKey = requestKey;
      if (resetDraft) clearControlDraft();
      else if (controlDraft?.key === draftKey) {
        for (const id of savedIds) { controlDraft.dirty.delete(id); delete controlDraft.values[id]; }
        if (!controlDraft.dirty.size) clearControlDraft();
      }
      error = "";
      if (successMessage) notice = successMessage;
    } catch (e) { if (requestKey === controlsRequestKey(getContext(), profile.value)) error = e.message; }
    finally { busy = false; render(); }
  }
  function render() {
    return preservePaneScroll(renderInPlace);
  }
  function renderInPlace() {
    const c = getContext();
    const output = getOutput();
    renderControls();
    const candidate = executionCandidateState(c, output, selection);
    const adoption = historyAdoptionState(c, output, selection);
    const selectedMatchesCandidate = Boolean(selection?.selected && output?.prompt === selection.selected.prompt
      && (output?.authoringPath || "one_shot") === (selection.selected.authoringPath || "one_shot"));
    status.textContent = !saved(c) ? "Save a filesystem Project to select an execution prompt."
      : !selection ? "Loading selected prompt status…" : !selection.selected ? "No generated prompt selected."
        : selection.stale ? "Selected prompt was created for different saved authoring. Prepare will still use its exact text."
          : selection.selected.sourceKind === "adopted_history" ? "History prompt adopted for execution."
            : selection.selected.sourceKind === "manual_edit" ? "Manual edit bound for execution."
            : "Selected execution prompt is current for the saved Generation.";
    if (selection?.selected && output?.prompt != null && !selection.stale && !selectedMatchesCandidate)
      status.textContent = "Current candidate differs from Selected execution prompt.";
    candidateHint.textContent = adoption.visible ? adoption.reason : candidate.reason;
    selectedText.hidden = !selection?.selected;
    promptText.textContent = selection?.selected?.prompt || "";
    use.textContent = selection?.selected && output?.prompt != null && !selectedMatchesCandidate
      ? "Update execution selection" : "Use for execution";
    adopt.hidden = !adoption.visible;
    clear.disabled = busy || !selection?.selected || !saved(c);
    profile.disabled = busy || !saved(c);
    connection.disabled = busy || !saved(c);
    prepareHint.textContent = !saved(c) ? "Prepare unavailable: save a filesystem Project first."
      : !output?.prompt?.trim() && !selection?.selected ? "There is no H3 prompt to prepare."
        : !profile.value ? "Prepare unavailable: choose a Workflow Profile." : "Prepare checks saved Project authority and Workflow Profile compatibility.";
    sendStatus.textContent = receipt ? `Queued in ComfyUI\nConnection: ${receipt.connectionName}\nPrompt ID: ${receipt.promptId}`
      + (receipt.queueNumber != null ? `\nQueue number: ${receipt.queueNumber}` : "")
      + (receipt.rendererSeed != null ? `\nRenderer seed: ${receipt.rendererSeed}` : "")
      : sendAttempted ? sending ? "Submitting to ComfyUI…" : "No queue acceptance receipt. Check the Send message above."
      : prepared ? "Prepared offline · not sent to ComfyUI" : "No active prepared send";
    issues.textContent = profiles.find(p => p.id === profile.value)?.preparationIssues?.join("\n") || "";
    const appliedProfile = continuation?.settings?.profileId;
    const compatibleCount = profiles.filter(p => p.compatibleModes.includes(c.mode)).length;
    profileStatus.textContent = !profile.value ? `No compatible Workflow Profile for ${c.mode}. Open Execution / ComfyUI → New profile, import a compatible API workflow, map its inputs, validate and save.`
      : `${compatibleCount === 1 ? "Only compatible profile selected" : "Selected for Prepare / Send"}: ${profiles.find(p => p.id === profile.value)?.name}. ` + (appliedProfile === profile.value ? "Also saved for native continuity."
        : appliedProfile ? `Selected Profile is for execution only. Applied continuity Profile: ${profiles.find(p => p.id === appliedProfile)?.name || appliedProfile}.`
          : "Selected Profile is for execution only; no continuity Profile applied.");
    issues.hidden = !issues.textContent;
    applyProfile.hidden = !api.saveContinuationSettings || continuation?.settings?.profileId === profile.value;
    applyProfile.disabled = busy || !saved(c) || !profile.value;
    const seedBound = Boolean(profiles.find(p => p.id === profile.value)?.rendererSeedBinding);
    const seed = seedDraft || continuation?.rendererSeedSettings || { mode: seedBound ? "random" : "workflow_default", fixedSeed: null };
    const effectiveSeed = continuation?.rendererSeedSettings || { mode: seedBound ? "random" : "workflow_default" };
    seedSummary.textContent = `Renderer Seed · ${effectiveSeed.mode === "fixed" ? `Fixed ${effectiveSeed.fixedSeed}` : effectiveSeed.mode === "random" ? "Random each Send" : "Workflow default"}${seedDraft ? " · Unsaved changes" : ""}`;
    seedMode.value = seed.mode;
    seedValue.value = seed.fixedSeed ?? "";
    fixedLabel.hidden = seed.mode !== "fixed";
    seedMode.disabled = busy || c.dirty || !saved(c);
    for (const option of seedMode.children) option.disabled = !seedBound && option.value !== "workflow_default";
    seedValue.disabled = busy || c.dirty || !seedBound;
    seedSave.disabled = busy || c.dirty || !saved(c) || !profile.value || !api.saveRendererSeedSettings || (!seedBound && seed.mode !== "workflow_default");
    seedHelp.textContent = seedBound ? "Workflow default keeps the imported value. Random and Fixed update only the mapped renderer_seed input."
      : "Add a renderer_seed binding to this Workflow Profile to control renderer seeds.";
    renderContinuation(c);
    message.textContent = error || notice || (sending ? "Submitting to ComfyUI…" : busy && !receipt ? "Preparing / updating…" : c.dirty && saved(c) ? "Unsaved edits remain local; execution actions use the saved Generation." : "");
    routingChoices.replaceChildren();
    for (const asset of routingState?.assets || []) {
      if (asset.kind === "image") continue;
      const group = el("div", null, routingChoices); group.className = "routing-asset-group";
      el("p", `${asset.label} · ${asset.filename}`, group).className = "routing-asset-title";
      if (asset.origin === "visual_handoff") {
        el("p", "Visual handoff · video frames only. Change its policy beside Relationship to Previous.", group);
        continue;
      }
      const prefs = routingState.routing.assets[asset.id] || {};
      for (const [key, title, fallback] of asset.kind === "video"
        ? [["useFrames", "Use video frames", true], ["useEmbeddedAudio", "Use embedded video audio", false]]
        : [["useAudio", "Use standalone audio reference", true]]) {
        const label = el("label", title, group), input = el("input", null, label);
        input.type = "checkbox"; input.checked = prefs[key] ?? fallback;
        input.disabled = busy || c.dirty;
        input.addEventListener("change", () => updateRouting(asset.id, key, input.checked));
      }
    }
    const references = routingState?.referenceMap || prepared?.referenceMap || [];
    const referenceCounts = [
      ["Pictures", "image"], ["Audio", "audio", "embedded_audio"], ["Video", "video_frames"],
    ].map(([label, ...channels]) => `${new Set(references.filter(ref => channels.includes(ref.channel)).map(ref => ref.assetId + ref.channel)).size} ${label}`);
    referenceSummary.textContent = routingState || prepared ? `Active References: ${referenceCounts.join(" · ")}` : "Reference routing has not been loaded.";
    routingSection.hidden = !routingState?.assets?.some(asset => asset.kind !== "image");
    const preparedMessages = new Set(prepared ? preparationDiagnostics(prepared).map(item => item.message) : []);
    const routingWarnings = [...new Set([...(routingState?.warnings || []), ...(routingState?.visualHandoff?.warnings || [])])].filter(text => !preparedMessages.has(text));
    for (const warning of routingWarnings) el("p", warning, routingChoices);
    if (routingWarnings.length) routingSection.hidden = false;
    const inspectorOpen = result.querySelector(".preparation-inspector")?.open || false;
    const informationOpen = result.querySelector(".preparation-details")?.open || false;
    const diagnostics = prepared ? preparationDiagnostics(prepared) : [];
    const blocked = diagnostics.some(item => item.severity === "error");
    const warnings = diagnostics.filter(item => item.severity === "warning");
    const information = diagnostics.filter(item => item.severity === "info");
    preparationStatus.textContent = blocked || (!prepared && error) ? "Preparation failed"
      : !prepared ? "" : !sendReady ? "Prepared offline \u00b7 Prepare again before Send"
        : warnings.length ? "Prepared \u2014 Review warnings" : "Prepared \u2014 Ready to Send";
    result.replaceChildren();
    if (!prepared) { el("p", "Prepare to check the current H3 prompt and workflow readiness.", result); return; }
    const promptState = selection?.stale ? "stale" : selection?.selected ? "current" : "not selected";
    el("p", `${prepared.mode} · H3 prompt: ${promptState}${c.durationSeconds == null ? "" : ` · Duration: ${c.durationSeconds} seconds`}`, result);
    const native = prepared.nativeContinuation;
    const rendererMode = prepared.rendererSeedSettings?.mode || (prepared.rendererSeedControlled ? "random" : "workflow_default");
    el("p", `${native?.consume ? "Native continuation: Consuming predecessor" : "Native continuation: Not consuming"} · Renderer seed: ${rendererMode === "random" ? "fresh random value on Send" : rendererMode === "fixed" ? `fixed ${prepared.rendererSeedSettings.fixedSeed}` : "workflow default"}`, result);
    if (blocked) {
      const errors = el("section", null, result); errors.className = "preparation-errors";
      for (const item of diagnostics.filter(item => item.severity === "error")) el("p", item.message, errors);
    }
    if (warnings.length) {
      const warningRegion = el("section", null, result); warningRegion.className = "preparation-warnings";
      el("h5", "Warnings to review", warningRegion);
      for (const item of warnings) el("p", item.message, warningRegion);
    }
    if (information.length) {
      const infoDetails = el("details", null, result); infoDetails.className = "preparation-details";
      infoDetails.open = informationOpen;
      el("summary", `Preparation details (${information.length})`, infoDetails);
      for (const item of information) el("p", item.message, infoDetails).className = "helper";
    }
    const details = el("details", null, result); details.className = "preparation-inspector"; details.open = inspectorOpen;
    el("summary", "Inspect prepared workflow and asset requirements", details);
    if (native?.consume) el("p", `Source Generation: G${native.segmentIndex - 1} · Accepted execution: ${String(native.sourceId).slice(0, 8)} · Context: ${native.contextFrames} frames · Load slot: ${native.loadSegmentIndex} · Save slot: ${native.saveSegmentIndex}`, details);
    el("pre", JSON.stringify(Object.fromEntries(Object.entries(prepared).filter(([key]) => !["diagnostics", "warnings", "information"].includes(key))), null, 2), details).className = "execution-prompt";
  }

  function renderContinuation(c) {
    const identity = JSON.stringify([c.projectId, c.generationId, profile.value]);
    if (identity !== nativeIdentity) { nativeIdentity = identity; nativeDraft = null; }
    const openSettings = nativeContent.querySelector(".native-settings")?.querySelector("details")?.open || false;
    const openAttempt = nativeContent.querySelector(".native-attempt")?.querySelector("details")?.open || false;
    const previousAttempt = nativeContent.querySelector("#preparation-native-attempt")?.value;
    const capability = profiles.find(p => p.id === profile.value)?.nativeContinuation;
    nativeSection.hidden = !capability && !continuation?.executions?.length;
    nativeContent.replaceChildren();
    if (capability) {
      if (capability.pictureFreeI2va) el("p", "Picture-free I2VA successor Profile: no first-frame Picture. Explicit Native-only I2VA continuation and a compatible accepted predecessor are required; this Profile cannot start a new clip.", nativeContent).className = "helper";
      const active = continuation?.settings?.profileId;
      el("p", active === profile.value ? "Active continuity Profile for this Generation."
        : active ? "Browsing a different Profile; use the button above to apply it for continuity."
          : "This Profile is not yet applied for continuity.", nativeContent).className = "helper";
      el("p", continuation?.resolution?.reason || "Apply this profile before generating a prompt to resolve native continuity.", nativeContent);
      const settings = el("section", null, nativeContent); settings.className = "native-settings";
      el("h4", "Continuation settings", settings);
      const activeFrames = continuation?.settings?.contextFrames ?? capability.contextFrames;
      const activeAudio = capability.audioContinuity && (continuation?.settings?.audioEnabled ?? capability.audioEnabled);
      el("p", `Context: ${activeFrames} frames · Audio continuation: ${capability.audioContinuity ? activeAudio ? "Enabled" : "Disabled" : "Unsupported"}`, settings);
      const edit = el("details", null, settings); edit.open = openSettings; el("summary", "Edit settings", edit);
      const contextLabel = el("label", "Requested context (frames)", edit);
      const frames = el("input", null, contextLabel); frames.type = "number"; frames.min = "1"; frames.step = "1";
      frames.value = nativeDraft?.frames ?? String(activeFrames);
      frames.id = "preparation-native-frames";
      frames.disabled = busy || c.dirty;
      const audioLabel = el("label", "Enable audio continuity", edit);
      const audio = el("input", null, audioLabel); audio.type = "checkbox";
      audio.checked = nativeDraft?.audio ?? activeAudio;
      audio.id = "preparation-native-audio";
      audio.disabled = busy || c.dirty || !capability.audioContinuity;
      el("p", `Video continuity supported · Audio ${capability.audioContinuity ? "supported" : "unsupported"}. Context grid: ${capability.contextGrid.minimum} + n × ${capability.contextGrid.step} (${capability.contextGrid.alignment}).`, edit);
      let out = null;
      if (capability.inputs.source_out_frame) {
        const outLabel = el("label", "Source out-point (frame; blank uses full state)", edit);
        out = el("input", null, outLabel); out.type = "number"; out.min = "0"; out.step = "1";
        out.value = nativeDraft?.out ?? (continuation?.settings?.sourceOutFrame == null ? "" : String(continuation.settings.sourceOutFrame));
        out.id = "preparation-native-out";
        out.disabled = busy || c.dirty;
      }
      const rememberDraft = () => { nativeDraft = { frames: frames.value, audio: audio.checked, out: out?.value }; };
      frames.addEventListener("input", rememberDraft); audio.addEventListener("change", rememberDraft); out?.addEventListener("input", rememberDraft);
      const save = el("button", "Save continuation settings", edit); save.type = "button";
      save.disabled = busy || c.dirty;
      save.addEventListener("click", () => saveContinuation({ profileId: profile.value,
        contextFrames: Number(frames.value), audioEnabled: audio.checked,
        sourceOutFrame: out?.value ? Number(out.value) : null }));
    }
    const executions = continuation?.executions || [];
    const labels = renderSubmissionLabels(continuation?.renderSubmissions || []);
    const latest = [...executions].sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)) || String(b.id).localeCompare(String(a.id)))[0];
    if (executions.length && !executions.some(record => record.id === inspectedAttemptId)) {
      inspectedAttemptId = executions.find(record => record.id === continuation?.acceptedId)?.id || latest?.id || null;
    }
    // Only the canonical renderSubmissionId joins renderer provenance to a native attempt.
    const submissionFor = record => record.renderSubmission?.id === record.renderSubmissionId ? record.renderSubmission
      : continuation?.renderSubmissions?.find(item => item.id === record.renderSubmissionId);
    const filenamesFor = record => [...new Set(submissionFor(record)?.outputFilenames || [])];
    if (executions.length) {
      const selectorRegion = el("div", null, nativeContent); selectorRegion.className = "native-selector";
      const label = el("label", "Native continuation attempt", selectorRegion); label.setAttribute("for", "preparation-native-attempt");
      const selector = el("select", null, selectorRegion); selector.id = "preparation-native-attempt";
      selector.disabled = busy;
      for (const record of [...executions].reverse()) {
        const submission = submissionFor(record), filenames = filenamesFor(record);
        const accepted = record.confirmed ?? (continuation.acceptedId === record.id);
        const state = accepted ? `✓ ACCEPTED${continuation.acceptedId === record.id ? " (Default)" : ""}` : record.status[0].toUpperCase() + record.status.slice(1);
        const send = submission ? labels.get(submission.id) || "Recorded ComfyUI send" : "Submission record missing";
        const output = filenames.length === 1 ? filenames[0] : filenames.length > 1 ? `${filenames.length} outputs` : submission?.jobStatus;
        const option = el("option", `${state} · ${send}${output ? ` · ${output}` : ""}`, selector); option.value = record.id;
      }
      selector.value = inspectedAttemptId;
      selector.addEventListener("change", () => {
        inspectedAttemptId = selector.value; render();
        nativeContent.querySelector("#preparation-native-attempt")?.focus?.();
      });
    }
    const removableUnavailable = executions.filter(record => record.status === "unavailable" && continuation.acceptedId !== record.id);
    if (removableUnavailable.length > 1) {
      const clearUnavailable = el("button", "Clear unavailable attempts", nativeContent); clearUnavailable.type = "button"; clearUnavailable.className = "text-button";
      clearUnavailable.disabled = busy || c.dirty || !api.clearUnavailableContinuations;
      clearUnavailable.addEventListener("click", () => changeContinuation(
        context => api.clearUnavailableContinuations(context.projectId, context.generationId), "Unavailable native attempts removed."));
    }
    const record = executions.find(record => record.id === inspectedAttemptId);
    if (record) {
      const submission = submissionFor(record), accepted = record.confirmed ?? (continuation.acceptedId === record.id);
      const card = el("article", null, nativeContent); card.className = "native-attempt";
      el("strong", submission ? labels.get(submission.id) || "Recorded ComfyUI send" : "Submission record missing", card);
      const badges = el("p", null, card); badges.className = "native-attempt-badges";
      if (latest?.id === record.id) el("span", "Latest", badges).className = "native-attempt-badge";
      if (accepted) el("span", "Accepted", badges).className = "native-attempt-badge";
      if (accepted && continuation.acceptedId === record.id) el("span", "Default", badges).className = "native-attempt-badge";
      const renderStatus = { queued: "Queued", running: "Running", completed: "Completed", failed: "Failed",
        cancelled: "Cancelled", unknown: "Unknown" }[submission?.jobStatus] || "Unknown";
      el("p", `Render: ${renderStatus}`, card);
      const filenames = filenamesFor(record);
      if (filenames.length) {
        const visible = filenames.slice(0, 3).join(" · ");
        el("p", `${filenames.length === 1 ? "Output" : "Outputs"}: ${visible}${filenames.length > 3 ? ` · +${filenames.length - 3} more` : ""}`, card);
      }
      el("p", `Native state: ${record.status[0].toUpperCase()}${record.status.slice(1)}`, card);
      if (record.status === "pending") el("p", "This attempt becomes Available after its exact render completes successfully. Removing it does not cancel the ComfyUI job.", card).className = "helper";
      if (record.status === "available" && !accepted) el("p", "Available is not Accepted. Accept native state to allow later native continuation to use it.", card).className = "helper";
      if (accepted && continuation.acceptedId !== record.id) el("p", `Accepted for a matching Selected Take. ${continuation.acceptedId ? "Another attempt is the default when no Take is selected." : "No default native attempt is selected."}`, card).className = "helper";
      const details = el("details", null, card); if (previousAttempt === inspectedAttemptId) details.open = openAttempt; el("summary", "Technical details", details);
      if (submission) el("p", `ComfyUI Prompt ID: ${submission.comfyPromptId}`, details);
      el("p", `Continuation execution ID: ${record.id}`, details);
      el("p", `RenderSubmission ID: ${record.renderSubmissionId}`, details);
      const actions = el("div", null, card); actions.className = "native-attempt-actions";
      if (accepted || record.status === "available") {
        const action = el("button", accepted ? "Revoke acceptance" : "Accept native state", actions); action.type = "button";
        action.disabled = busy || c.dirty || !api.decideContinuation;
        action.addEventListener("click", () => decideContinuation(record.id, "available", !accepted));
      }
      if (!accepted) {
        const remove = el("button", "Remove native attempt", actions); remove.type = "button";
        remove.title = "Remove only native-continuation bookkeeping. Renderer job, submission, Takes, media and external native artifacts remain.";
        remove.disabled = busy || c.dirty || !api.removeContinuation;
        remove.addEventListener("click", () => changeContinuation(
          context => api.removeContinuation(context.projectId, context.generationId, record.id), "Native attempt removed. Renderer submission and Takes remain."));
      }
    }
    el("p", "Successful exact render completion makes a pending native attempt Available. Accept native state explicitly confirms its saved artifact and makes it the default. A Selected Take requires its exact attempt to be Accepted; selecting a Take never accepts native state. Revoke acceptance removes that attempt's confirmation.", nativeContent).className = "helper";
  }
  async function refreshContinuation() {
    if (!api.continuationStatus) return;
    const c = { ...getContext() }, ticket = ++continuationEpoch, key = contextKey;
    if (!saved(c)) { continuation = null; return; }
    try {
      const response = await api.continuationStatus(c.projectId, c.generationId);
      if (ticket !== continuationEpoch || key !== contextKey) return;
      continuation = response;
      renderProfiles();
      // Continuation authority also affects the current handoff warning state.
      // Refresh its manifest even when status was refreshed outside a button action.
      onRoutingChanged();
    } catch (e) { if (ticket === continuationEpoch && key === contextKey) error = e.message; }
    render();
  }
  async function changeContinuation(action, successMessage = "") {
    if (busy || !saved(getContext())) return;
    const c = { ...getContext() }, key = contextKey, ticket = ++continuationEpoch;
    invalidate(); busy = true; render();
    try {
      const response = await action(c);
      if (key !== contextKey || ticket !== continuationEpoch) return;
      continuation = response;
      await Promise.allSettled([refreshSelection(), refreshRouting()]);
      onRoutingChanged();
      if (successMessage) { notice = successMessage; error = ""; }
    } catch (e) { if (key === contextKey && ticket === continuationEpoch) error = e.message; }
    finally { busy = false; render(); }
  }
  async function saveContinuation(settings) {
    if (!api.saveContinuationSettings) return;
    return changeContinuation(async c => {
      const response = await api.saveContinuationSettings(c.projectId, c.generationId,
        { ...(continuation?.settings || {}), ...settings });
      if (currentContext(c)) nativeDraft = null;
      return response;
    });
  }
  async function decideContinuation(id, status, accept = true) {
    return changeContinuation(c => api.decideContinuation(c.projectId, c.generationId, id, { status, accept }));
  }
  async function saveRendererSeed() {
    if (!api.saveRendererSeedSettings) return;
    const controlled = Boolean(profiles.find(p => p.id === profile.value)?.rendererSeedBinding);
    const mode = seedMode.value;
    if (mode !== "workflow_default" && !controlled) {
      error = "Add a renderer_seed binding to this Workflow Profile to control renderer seeds."; render(); return;
    }
    const raw = seedValue.value;
    if (mode === "fixed" && (!/^\d+$/.test(raw) || Number(raw) > 2147483647)) {
      error = "Enter a fixed renderer seed from 0 to 2147483647."; render(); return;
    }
    const settings = { mode, fixedSeed: mode === "fixed" ? Number(raw) : null };
    await changeContinuation(c => api.saveRendererSeedSettings(c.projectId, c.generationId,
      { profileId: profile.value, settings }), "Renderer seed preference saved.");
    if (!error) seedDraft = null;
    render();
  }
  async function act(action, selectionAction = false) {
    if (busy) { error = "An execution action is already in progress."; render(); return; }
    if (!saved(getContext())) { error = "Save a filesystem Project before using execution actions."; render(); return; }
    invalidate();
    const resetTicket = resetEpoch;
    let ticket = epoch; const context = { ...getContext() }; busy = true; render();
    try {
      if (syncReferences) await syncReferences();
      if (resetTicket !== resetEpoch) return;
      if (!currentContext(context))
        throw new Error("The active Generation changed before execution selection or preparation.");
      sync(); invalidate(); ticket = epoch; selectionEpoch++;
      const c = { ...getContext() };
      const response = await action(c);
      if (ticket !== epoch) return;
      if (selectionAction) { selectionEpoch++; selection = response; if (response.selected) referenceSelectionRequired = false; onSelectionChanged(); } else { prepared = response; sendReady = !preparationDiagnostics(response).some(item => item.severity === "error"); }
    } catch (e) { if (ticket === epoch) { invalidate(); error = e.message; } }
    finally { busy = false; render(); }
  }
  function renderProfiles() {
    const compatible = profiles.filter(p => p.compatibleModes.includes(getContext().mode));
    const previous = profile.value;
    const prior = [explicitProfileId, continuation?.settings?.profileId, previous]
      .find(id => compatible.some(p => p.id === id));
    profile.replaceChildren();
    for (const p of compatible) { const option = el("option", `${p.name} · ${p.compatibleModes.join(", ")}`, profile); option.value = p.id; }
    profile.value = compatible.some(p => p.id === prior) ? prior : compatible[0]?.id || "";
    if (previous !== profile.value) invalidate();
    setControlProfile(profile.value);
    refreshControls();
    profileDetailPending = refreshProfileDetails();
  }
  async function refreshProfiles() {
    invalidate(); const ticket = ++profileEpoch; render();
    try {
      const response = await api.executionProfiles(); if (ticket !== profileEpoch) return;
      invalidate();
      const oldSelected = profiles.find(item => item.id === profile.value);
      const newSelected = response.find(item => item.id === profile.value);
      if (oldSelected && newSelected && profileControlDefinitions(oldSelected) !== profileControlDefinitions(newSelected)
          && controlDraft?.profileId === profile.value) clearControlDraft();
      if (oldSelected && newSelected && JSON.stringify(oldSelected.exposedControls || []) !== JSON.stringify(newSelected.exposedControls || [])) {
        controlEpoch++; controlStateKey = "";
      }
      profiles = response; renderProfiles(); await profileDetailPending;
    }
    catch (e) { if (ticket === profileEpoch) { profiles = []; renderProfiles(); error = e.message; } }
    render();
  }
  async function refreshSelection() {
    const c = { ...getContext() }; const key = contextKey; const ticket = ++selectionEpoch;
    if (!saved(c)) { selection = null; render(); return; }
    // Project adoption updates Workspace and Generation synchronously in stages.
    // Let that finish before sending a request for an already superseded context.
    await Promise.resolve();
    const current = getContext();
    if (key !== contextKey || ticket !== selectionEpoch || c.projectId !== current.projectId
        || c.generationId !== current.generationId) return;
    try {
      const response = await api.executionSelection(c.projectId, c.generationId);
      if (key !== contextKey || ticket !== selectionEpoch) return;
      if (JSON.stringify(selection) !== JSON.stringify(response)) invalidate();
      selection = response;
    } catch (e) { if (key === contextKey && ticket === selectionEpoch) { invalidate(); selection = null; error = e.message; } }
    render();
  }
  async function sendCurrent() {
    const resetTicket = resetEpoch;
    sync();
    if (busy) { error = "An execution action is already in progress."; render(); return; }
    if (!saved(getContext())) { error = "Save a filesystem Project before Send."; render(); return; }
    if (!sendReady || !prepared) { error = "Prepare the current selection before Send."; render(); return; }
    if (!connection.value) { error = "Choose a ComfyUI Connection before Send."; render(); return; }
    const ticket = epoch;
    const c = { ...getContext() };
    sendReady = false; receipt = null; sendAttempted = true; error = ""; busy = true; sending = true; render();
    try {
      // Prepare already synchronized References and execution setup. Send only
      // validates that saved execution against its fingerprint; it must not write
      // browser drafts or acquire a new Project-wide edit authority.
      if (!currentContext(c))
        throw new Error("The active Generation changed before Send.");
      const response = await api.sendExecution({ projectId: c.projectId, generationId: c.generationId,
        profileId: profile.value, connectionId: connection.value,
        ...(prepared?.executionFingerprint ? { expectedExecutionFingerprint: prepared.executionFingerprint } : {}) });
      if (resetTicket !== resetEpoch) return;
      if (response.submitted === true && response.promptId) onSubmitted(response, c);
      if (ticket !== epoch) return;
      if (response.submitted !== true || !response.promptId) throw new Error("Submission status is unknown. Check ComfyUI before sending again.");
      receipt = response;
      sending = false;
      notice = [response.provenanceWarning, response.stagingWarning].filter(Boolean).join(" ") || notice;
      render(); // Show the actual queue acceptance before auxiliary requests.
      await refreshContinuation();
    } catch (e) {
      if (ticket === epoch) error = e instanceof TypeError
        ? "Connection to MiniConstruct was lost. Submission status is unknown. Check ComfyUI before sending again."
        : e.message;
    } finally { busy = false; sending = false; render(); }
  }
  async function refreshConnections() {
    invalidateSend(); const ticket = ++connectionEpoch; render();
    try {
      const response = api.executionConnections ? await api.executionConnections() : [];
      if (ticket !== connectionEpoch) return;
      connections = response;
    } catch (e) { if (ticket !== connectionEpoch) return; connections = []; error = e.message; }
    // A Prepare may have finished while the configuration read was in flight.
    // Never transfer its readiness to a newly returned Connection context.
    epoch++; sendReady = false; receipt = null; sendAttempted = false;
    const prior = connection.value; connection.replaceChildren();
    const empty = el("option", "Choose a Connection", connection); empty.value = "";
    for (const item of connections) { const option = el("option", item.name, connection); option.value = item.id; }
    connection.value = connections.some(item => item.id === prior) ? prior : connections.length === 1 ? connections[0].id : "";
    render();
  }
  async function refreshRouting() {
    if (!api.executionRouting) return;
    const c = { ...getContext() }, ticket = ++routingEpoch, key = contextKey;
    if (!saved(c)) { routingState = null; return; }
    try {
      const response = await api.executionRouting(c.projectId, c.generationId);
      if (ticket !== routingEpoch || key !== contextKey) return;
      routingState = response; invalidate(); onRoutingChanged();
    } catch (e) { if (ticket === routingEpoch && key === contextKey) error = e.message; }
    render();
  }
  async function updateRouting(id, key, value) {
    if (busy || !routingState) return;
    const c = { ...getContext() }, ticket = ++routingEpoch, currentKey = contextKey;
    const next = structuredClone(routingState.routing);
    next.assets[id] = { ...(next.assets[id] || {}), [key]: value };
    if (key === "useFrames" && !value) next.assets[id].useEmbeddedAudio = false;
    invalidate(); busy = true; render();
    try {
      const response = await api.saveExecutionRouting(c.projectId, c.generationId, next);
      if (ticket !== routingEpoch || currentKey !== contextKey) return;
      routingState = response; onRoutingChanged();
    } catch (e) { if (ticket === routingEpoch && currentKey === contextKey) error = e.message; }
    finally { busy = false; render(); }
  }
  function sync() {
    const c = getContext(); const key = JSON.stringify([c.saved, c.projectId, c.generationId, c.mode,
      c.durationSeconds, c.aspectRatio]);
    const identity = JSON.stringify([c.saved, c.projectId, c.generationId]);
    const pending = [];
    if (identity !== controlsIdentityKey) {
      explicitProfileId = null;
      referenceSelectionRequired = false;
      controlsIdentityKey = identity;
      inspectedAttemptId = null;
      controlEpoch++;
      controlState = null; controlStateKey = "";
      clearControlDraft();
    }
    if (key !== contextKey) {
      contextKey = key; invalidate(); selection = null; routingState = null; routingEpoch++; continuation = null; continuationEpoch++;
      renderProfiles(); pending.push(refreshSelection(), refreshRouting(), refreshContinuation(), refreshControls());
    }
    render(); return pending;
  }
  async function refresh() {
    const pending = sync();
    if (!pending.length) pending.push(refreshSelection(), refreshRouting(), refreshContinuation());
    await Promise.allSettled([...pending, refreshProfiles(), refreshConnections()]);
  }
  async function prepareSavedSelection(profileId) {
    const resetTicket = resetEpoch;
    await refresh();
    if (resetTicket !== resetEpoch) throw new Error("Generated state was cleared before preparation.");
    const c = getContext();
    if (!profiles.some(item => item.id === profileId && item.compatibleModes.includes(c.mode)))
      throw new Error("The saved Workflow Profile is no longer compatible with this Generation.");
    profile.value = profileId;
    explicitProfileId = profileId;
    setControlProfile(profileId);
    await refreshControls();
    if (resetTicket !== resetEpoch) throw new Error("Generated state was cleared before preparation.");
    await act(async context => {
      if (referenceSelectionRequired) throw new Error("Reference Assets changed. Explicitly Use for execution before Prepare.");
      await syncExecutionSetup(context);
      return api.prepareWorkflow({ projectId: context.projectId,
        generationId: context.generationId, profileId });
    });
    return prepared;
  }
  function invalidateReferences() {
    invalidate(); selection = null; selectionEpoch++; referenceSelectionRequired = true;
    notice = "Reference Assets changed. Generate a new prompt, then explicitly Use for execution.";
    render(); return Promise.all([refreshRouting(), refreshSelection()]);
  }
  function reset() {
    ++resetEpoch;
    invalidate(); ++selectionEpoch; ++routingEpoch; ++continuationEpoch; ++controlEpoch;
    contextKey = controlsIdentityKey = ""; selection = routingState = continuation = null;
    inspectedAttemptId = null; controlState = null; controlStateKey = ""; clearControlDraft();
    explicitProfileId = null;
    seedDraft = nativeDraft = null; nativeIdentity = ""; busy = sending = false; render();
  }
  return { sync, refresh, reset, invalidateReferences, prepareSavedSelection, refreshProfiles, refreshSelection, refreshConnections, refreshContinuation, get prepared() { return prepared; },
    get routingReady() { return !saved(getContext()) || (routingState !== null && !busy); },
    get routing() { return structuredClone(routingState?.routing || { assets: {} }); } };
}
