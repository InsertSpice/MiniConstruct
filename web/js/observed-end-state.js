// Only the selected Take's hash-bound observation is displayed or edited.
export function renderObservedEndState({ root, state, context, busy, api, settings, act, confirm, draftStore, analysisError, onAnalyzeStart, onAnalyzeError }) {
  root.replaceChildren();
  const doc = root.ownerDocument;
  const el = (tag, text, parent = root) => {
    const node = doc.createElement(tag); if (text != null) node.textContent = text; parent.append(node); return node;
  };
  const button = (id, title, action, parent = root) => {
    const node = el("button", title, parent); node.id = `observation-${id}`; node.type = "button";
    node.disabled = busy; node.addEventListener("click", action); return node;
  };
  el("h3", "Observed End State");
  const take = state?.takes.find(t => t.id === state.selectedTakeId);
  if (!take) { el("p", "Select a Take to establish observed continuity."); return; }
  const observation = state.takeObservations?.find(o => o.takeId === take.id && o.takeSha256 === take.sha256);
  el("p", `Source Take: ${take.originalFilename}`);
  el("p", "Analysis uses the selected video and saved Generation authoring. Prompt provenance is optional.").className = "helper";
  if (busy) el("p", "Working… Video extraction and vision analysis may take a moment.").setAttribute("role", "status");
  if (take.availability === "missing") el("p", "Source media is missing. Existing observations remain usable; reattach exact original bytes to analyze again.");
  if (settings.supportsVision !== true) el("p", "Select a vision-capable model and enable Supports Vision in LLM settings to analyze this Take.");
  const observationIdentity = observation && JSON.stringify([observation.takeId, observation.takeSha256,
    observation.analyzedAt, observation.status, observation.modelObservedState, observation.effectiveObservedState]);
  const draftKey = observation && JSON.stringify([context.projectId, context.generationId, take.id, observationIdentity]);
  if (!observation || draftStore.key !== draftKey) {
    draftStore.key = draftKey || "";
    draftStore.value = observation ? structuredClone(observation.effectiveObservedState) : null;
    draftStore.dirty = false;
    draftStore.editError = "";
  }
  const currentProjectFingerprint = context.projectFingerprint ?? state.projectFingerprint;
  button("analyze", observation ? "Re-analyze" : "Analyze Selected Take", async () => {
    const replaceCorrections = observation?.status === "user_edited";
    if (replaceCorrections && !await confirm("Re-analyze this Take and replace your saved corrections with the new model observation?")) return;
    const request = onAnalyzeStart?.();
    if (request === false) return;
    return act(c => api.analyzeSelectedTake(c.projectId, c.generationId, {
      llm: settings, expectedProjectFingerprint: c.projectFingerprint ?? currentProjectFingerprint, replaceCorrections,
    }, request?.signal), { onError: message => { if (!request?.cancelled?.()) onAnalyzeError?.(message); }, onSettled: request?.finish });
  }).disabled = take.availability === "missing" || settings.supportsVision !== true;
  if (analysisError) {
    const failure = el("p", `Analysis failed: ${analysisError}`);
    failure.className = "observation-analysis-error";
    failure.setAttribute("role", "alert");
  }
  if (!observation) { el("p", "This Selected Take has not been analyzed. Continuity currently falls back to planned state."); return; }
  el("p", `${observation.analyzedAt} · ${observation.status === "user_edited" ? "User-edited" : "Model-observed"} · ${observation.analyzerModel}`);
  const evidence = el("div"); evidence.className = "observation-evidence";
  for (const [name, title] of [["endpoint", "Endpoint frame"], ["contact-sheet", "Tail contact sheet"]]) {
    const figure = el("figure", null, evidence), img = el("img", null, figure);
    img.alt = title; img.loading = "lazy";
    img.src = api.observationEvidenceUrl(context.projectId, context.generationId, take.id, name);
    el("figcaption", title, figure);
    img.addEventListener("error", () => { img.hidden = true; el("p", "Evidence preview unavailable. Reattach missing source media or explicitly re-analyze to regenerate it.", figure); });
  }
  const draft = draftStore.value;
  const sameState = (left, right) => JSON.stringify(left) === JSON.stringify(right);
  let saveButton, resetButton, editStatus;
  const updateEditUi = () => {
    draftStore.dirty = !sameState(draft, observation.effectiveObservedState);
    if (saveButton) saveButton.disabled = busy || !draftStore.dirty;
    if (resetButton) resetButton.disabled = busy || observation.status === "model_observed" || sameState(observation.effectiveObservedState, observation.modelObservedState);
    if (!editStatus) return;
    editStatus.textContent = draftStore.editError ? `Could not save observed-state edits: ${draftStore.editError}`
      : draftStore.dirty ? "Unsaved edits"
        : observation.status === "user_edited" ? "Saved — user-edited state is active"
          : "Original model observation active";
    editStatus.className = draftStore.editError ? "observation-analysis-error" : "helper";
    editStatus.setAttribute("role", draftStore.editError ? "alert" : "status");
  };
  const markDraft = () => { draftStore.editError = ""; updateEditUi(); };
  const fields = el("fieldset"); fields.disabled = busy;
  el("legend", "Effective Observed End State", fields);
  const field = (parent, object, key, title, list = false) => {
    const label = el("label", title, parent), input = el("textarea", null, label);
    input.id = `observation-${key}`; input.rows = 2; input.maxLength = list ? 9600 : 800;
    input.value = list ? object[key].join("\n") : object[key]; input.disabled = busy;
    input.addEventListener("input", () => { object[key] = list ? input.value.split("\n").map(v => v.trim()).filter(Boolean) : input.value; markDraft(); });
  };
  field(fields, draft, "summary", "Summary"); field(fields, draft, "environment", "Environment");
  const subjects = el("details", null, fields); subjects.open = true; el("summary", "Subjects and visibility", subjects);
  function subjectFields(subject) {
    const group = el("fieldset", null, subjects); el("legend", observation.subjectLabels?.[subject.subjectId] || `Subject: ${subject.subjectId}`, group);
    const label = el("label", "Visibility", group), select = el("select", null, label);
    select.id = `observation-visibility-${subject.subjectId}`; select.disabled = busy;
    for (const value of ["visible", "partially_visible", "not_visible", "uncertain"]) { const option = el("option", value.replaceAll("_", " "), select); option.value = value; }
    select.value = subject.visibility; select.addEventListener("change", () => { subject.visibility = select.value; markDraft(); });
    for (const [key, title] of [["appearance", "Appearance"], ["wardrobe", "Wardrobe"], ["condition", "Condition"], ["positionPose", "Position / pose"], ["heldObjects", "Held objects"], ["currentAction", "Current action"]])
      field(group, subject, key, title);
  }
  draft.subjectStates.forEach(subjectFields);
  const missing = (observation.offeredSubjectIds || []).filter(id => !draft.subjectStates.some(s => s.subjectId === id));
  if (missing.length) {
    const label = el("label", "Add an omitted Subject state", subjects), choose = el("select", null, label);
    choose.disabled = busy;
    for (const id of missing) { const option = el("option", observation.subjectLabels?.[id] || id, choose); option.value = id; }
    choose.value = missing[0];
    const add = el("button", "Add Subject State", label); add.type = "button"; add.disabled = busy; add.id = "observation-add-subject";
    add.addEventListener("click", () => {
      if (!choose.value || draft.subjectStates.some(s => s.subjectId === choose.value)) return;
      const subject = { subjectId: choose.value, visibility: "uncertain", appearance: "", wardrobe: "", condition: "", positionPose: "", heldObjects: "", currentAction: "" };
      draft.subjectStates.push(subject); subjectFields(subject);
      const remaining = missing.filter(id => !draft.subjectStates.some(s => s.subjectId === id));
      choose.replaceChildren();
      for (const id of remaining) { const option = el("option", observation.subjectLabels?.[id] || id, choose); option.value = id; }
      choose.value = remaining[0] || ""; add.disabled = !remaining.length;
      markDraft();
    });
  }
  field(fields, draft, "propsObjects", "Props / Objects (one per line)", true);
  field(fields, draft, "actionMomentum", "Action / Motion at Cut");
  field(fields, draft, "cameraState", "Camera at Cut"); field(fields, draft, "lightingConditions", "Lighting / Conditions");
  const assessment = el("label", "Planned vs Observed", fields), select = el("select", null, assessment);
  select.id = "observation-assessment"; select.disabled = busy;
  for (const value of ["matches", "partial", "conflicts", "not_verifiable"]) { const option = el("option", value.replaceAll("_", " "), select); option.value = value; }
  select.value = draft.plannedOutcomeAssessment.status;
  select.addEventListener("change", () => { draft.plannedOutcomeAssessment.status = select.value; markDraft(); });
  field(fields, draft.plannedOutcomeAssessment, "explanation", "Assessment explanation");
  field(fields, draft, "unresolvedVisibleEntities", "Unresolved visible entities (one per line)", true);
  const controls = el("div"); controls.className = "observation-edit-controls";
  editStatus = el("p", "", controls); editStatus.id = "observation-edit-status";
  saveButton = button("save", "Save Observed State Edits", () => act(c => api.correctObservation(c.projectId, c.generationId, take.id, {
    expectedProjectFingerprint: c.projectFingerprint ?? currentProjectFingerprint, effectiveObservedState: draft,
  }), { onSuccess: () => { draftStore.dirty = false; draftStore.editError = ""; }, onError: message => { draftStore.editError = message; } }), controls);
  resetButton = button("reset", "Restore Original Model Observation", () => act(c => api.correctObservation(c.projectId, c.generationId, take.id, {
    expectedProjectFingerprint: c.projectFingerprint ?? currentProjectFingerprint, reset: true,
  }), { onSuccess: () => { draftStore.dirty = false; draftStore.editError = ""; }, onError: message => { draftStore.editError = message; } }), controls);
  el("p", "Saved observed-state edits affect later same-scene continuity. They do not rewrite Planned Continuity or Story Memory.", controls).className = "helper";
  updateEditUi();
  const original = el("details"); el("summary", "Inspect original model observation", original);
  el("pre", JSON.stringify(observation.modelObservedState, null, 2), original).className = "execution-prompt";
}
