// Saved Project + sidecar are authority. No History or browser persistence here.
export function canSelectExecutionOutput(output) {
  return Boolean(output?.prompt?.trim() && output.validation?.valid && output.sourceFingerprint
    && (!output.generationStatus || output.generationStatus === "complete"));
}

export function createWorkflowPreparation({ root, api, getContext, getOutput, onRoutingChanged = () => {} }) {
  const doc = root.ownerDocument;
  let contextKey = "", epoch = 0, profileEpoch = 0, selectionEpoch = 0;
  let profiles = [], selection = null, prepared = null, busy = false, error = "";
  let routingState = null, routingEpoch = 0;
  let connections = [], connectionEpoch = 0, sendReady = false, receipt = null, sending = false, sendAttempted = false;
  const el = (tag, text, parent = root) => {
    const node = doc.createElement(tag); if (text != null) node.textContent = text; parent.append(node); return node;
  };
  const button = (id, text, action) => {
    const node = el("button", text); node.id = `preparation-${id}`; node.type = "button";
    node.addEventListener("click", action); return node;
  };
  el("h3", "ComfyUI preparation");
  el("p", "Choose a completed H3 prompt, save the Generation, and prepare a compatible workflow offline.").className = "helper";
  const status = el("p", ""); status.id = "preparation-selection"; status.setAttribute("role", "status");
  const selectedText = el("details", null); el("summary", "Selected generated prompt", selectedText);
  const promptText = el("pre", "", selectedText); promptText.className = "execution-prompt";
  const use = button("use", "Use for execution", () => act(async c => {
    const output = getOutput();
    if (!canSelectExecutionOutput(output)) throw new Error("Choose a completed valid generated H3 prompt.");
    return api.selectExecutionPrompt(c.projectId, c.generationId,
      { prompt: output.prompt, sourceFingerprint: output.sourceFingerprint,
        ...(output.referenceMap ? { referenceMap: output.referenceMap } : {}) });
  }, true));
  const clear = button("clear", "Clear selection", () => act(c => api.clearExecutionPrompt(c.projectId, c.generationId), true));
  const connectionLabel = el("label", "ComfyUI Connection");
  const connection = el("select", null, connectionLabel); connection.id = "preparation-connection";
  connection.addEventListener("change", () => { invalidateSend(); render(); });
  const label = el("label", "Workflow Profile");
  const profile = el("select", null, label); profile.id = "preparation-profile";
  profile.addEventListener("change", () => { invalidate(); render(); });
  const issues = el("p", ""); issues.id = "preparation-profile-issues"; issues.className = "helper";
  const routingSection = el("fieldset"); routingSection.id = "preparation-routing";
  el("legend", "Reference routing for this Generation", routingSection);
  el("p", "Choose routes before generating a prompt so its Audio labels match. Your master Workflow Profile stays reusable.", routingSection);
  const routingChoices = el("div", null, routingSection); routingChoices.className = "routing-choices";
  const prepare = button("prepare", "Prepare", () => act(c => {
    if (c.dirty) throw new Error("Save before preparing for ComfyUI.");
    if (!profile.value) throw new Error("Choose a compatible Workflow Profile.");
    return api.prepareWorkflow({ projectId: c.projectId, generationId: c.generationId, profileId: profile.value });
  }));
  const send = button("send", "Send to ComfyUI", sendCurrent);
  button("refresh", "Refresh preparation status", () => refresh());
  const message = el("p", ""); message.id = "preparation-message"; message.setAttribute("role", "status");
  const result = el("div", null); result.id = "preparation-result";
  const sendStatus = el("p", "NOT SENT TO COMFYUI"); sendStatus.id = "preparation-receipt";
  sendStatus.setAttribute("role", "status");

  const saved = c => Boolean(c.saved && c.projectId && c.generationId);
  function invalidateSend() { epoch++; sendReady = false; receipt = null; sendAttempted = false; error = ""; }
  function invalidate() { invalidateSend(); prepared = null; }
  function render() {
    const c = getContext();
    status.textContent = !saved(c) ? "Save a filesystem Project to select an execution prompt."
      : !selection ? "Loading selected prompt status…" : !selection.selected ? "No generated prompt selected."
        : selection.stale ? "Selected prompt is stale for the current saved Generation." : "Selected and current for the saved Generation.";
    selectedText.hidden = !selection?.selected;
    promptText.textContent = selection?.selected?.prompt || "";
    use.disabled = busy || !saved(c) || !canSelectExecutionOutput(getOutput());
    clear.disabled = busy || !selection?.selected || !saved(c);
    prepare.disabled = busy || !saved(c) || c.dirty || !selection?.selected || selection.stale || !profile.value;
    profile.disabled = busy || !saved(c);
    connection.disabled = busy || !saved(c);
    send.disabled = prepare.disabled || !sendReady || !connection.value;
    sendStatus.textContent = receipt ? `Queued in ComfyUI\nConnection: ${receipt.connectionName}\nPrompt ID: ${receipt.promptId}`
      + (receipt.queueNumber != null ? `\nQueue number: ${receipt.queueNumber}` : "")
      : sendAttempted ? sending ? "Sending to ComfyUI…" : "No queue acceptance receipt. Check the Send message above."
      : prepared ? "Prepared offline · NOT SENT TO COMFYUI" : "NOT SENT TO COMFYUI";
    issues.textContent = profiles.find(p => p.id === profile.value)?.preparationIssues?.join("\n") || "";
    message.textContent = error || (c.dirty && saved(c) ? "Save before preparing for ComfyUI." : sending ? "Sending to ComfyUI…" : busy ? "Preparing / updating…" : "");
    routingChoices.replaceChildren();
    for (const asset of routingState?.assets || []) {
      if (asset.kind === "image") continue;
      const group = el("div", null, routingChoices); group.className = "routing-asset-group";
      el("p", `${asset.label} · ${asset.filename}`, group).className = "routing-asset-title";
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
    if (routingState?.referenceMap) el("p", routingState.referenceMap.map(ref => {
      const asset = routingState.assets.find(item => item.id === ref.assetId);
      return `${ref.label}: ${asset?.filename || "reference"}${ref.channel === "embedded_audio" ? " (embedded soundtrack)" : ""}`;
    }).join(" · "), routingChoices);
    result.replaceChildren();
    if (!prepared) return;
    el("p", `${prepared.profileName} · ${prepared.mode}`, result);
    for (const row of prepared.resolutions) {
      el("p", `${row.label} · ${row.value}`
        + (row.presence ? ` · ${row.presence} slot ${row.status.toUpperCase()}` : ""), result);
      for (const route of row.routes || []) el("p", `${route.channel.replaceAll("_", " ")} ${route.status.toUpperCase()}`, result);
    }
    for (const warning of prepared.warnings) el("p", warning, result).className = "helper";
    el("strong", "Prepared workflow is ready according to profile semantics.", result);
    const details = el("details", null, result); el("summary", "Inspect prepared workflow and asset requirements", details);
    el("pre", JSON.stringify(prepared, null, 2), details).className = "execution-prompt";
  }
  async function act(action, selectionAction = false) {
    if (busy || !saved(getContext())) return;
    invalidate(); const ticket = epoch; const c = { ...getContext() }; busy = true; render();
    try {
      const response = await action(c);
      if (ticket !== epoch) return;
      if (selectionAction) { selectionEpoch++; selection = response; } else { prepared = response; sendReady = true; }
    } catch (e) { if (ticket === epoch) error = e.message; }
    finally { busy = false; render(); }
  }
  function renderProfiles() {
    const prior = profile.value; profile.replaceChildren();
    const compatible = profiles.filter(p => p.compatibleModes.includes(getContext().mode));
    for (const p of compatible) { const option = el("option", `${p.name} · ${p.compatibleModes.join(", ")}`, profile); option.value = p.id; }
    profile.value = compatible.some(p => p.id === prior) ? prior : compatible[0]?.id || "";
  }
  async function refreshProfiles() {
    invalidate(); const ticket = ++profileEpoch; render();
    try { const response = await api.executionProfiles(); if (ticket !== profileEpoch) return; invalidate(); profiles = response; renderProfiles(); }
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
    sync();
    if (busy || send.disabled) return;
    const c = { ...getContext() }, ticket = epoch;
    sendReady = false; receipt = null; sendAttempted = true; error = ""; busy = true; sending = true; render();
    try {
      const response = await api.sendExecution({ projectId: c.projectId, generationId: c.generationId,
        profileId: profile.value, connectionId: connection.value });
      if (ticket !== epoch) return;
      if (response.submitted !== true || !response.promptId) throw new Error("Submission status is unknown. Check ComfyUI before sending again.");
      receipt = response;
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
    const c = getContext(); const key = JSON.stringify([c.saved, c.projectId, c.generationId, c.mode, c.dirty]);
    if (key !== contextKey) {
      contextKey = key; invalidate(); selection = null; routingState = null; routingEpoch++;
      renderProfiles(); void refreshSelection(); void refreshRouting();
    }
    render();
  }
  async function refresh() { sync(); await Promise.allSettled([refreshSelection(), refreshProfiles(), refreshRouting(), refreshConnections()]); }
  return { sync, refresh, refreshProfiles, refreshSelection, refreshConnections, get prepared() { return prepared; },
    get routingReady() { return !saved(getContext()) || (routingState !== null && !busy); },
    get routing() { return structuredClone(routingState?.routing || { assets: {} }); } };
}
