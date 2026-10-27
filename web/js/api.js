function failureDetail(data, status) {
  const detail = data.detail;
  if (Array.isArray(detail)) return detail.map(item => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("; ");
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (detail && typeof detail === "object" && Array.isArray(detail.fields)) {
    return detail.fields.map(item => `${item.path || "request"}: ${item.message || "invalid value"}`).join("; ") || detail.message;
  }
  return typeof detail === "string" ? detail : `Request failed (${status})`;
}

function requestFailure(data, status) {
  const error = new Error(failureDetail(data, status));
  error.status = status;
  const metadata = data.detail && typeof data.detail === "object" ? data.detail : data;
  if (typeof metadata.rawResponse === "string" && typeof metadata.category === "string" && metadata.category.startsWith("story_")) {
    error.rawResponse = metadata.rawResponse;
  }
  if (metadata.rawResponseWithheld === true && typeof metadata.category === "string" && metadata.category.startsWith("story_")) {
    error.rawResponseWithheld = true;
  }
  return error;
}

async function request(path, body, method = "POST", signal) {
  const options = { method, headers: {}, signal };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`/api/${path}`, options);
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    throw requestFailure(data, response.status);
  }
  return data;
}

async function streamRequest(path, body, { signal, onEvent }) {
  const response = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    let data = {};
    try { data = await response.json(); } catch { /* response was not JSON */ }
    throw requestFailure(data, response.status);
  }
  if (!response.body) throw new Error("This browser did not expose the generation stream.");
  const { createEventStreamParser } = await import("./streaming.js");
  const parser = createEventStreamParser(onEvent);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    parser.push(decoder.decode());
    parser.finish();
  } finally {
    reader.releaseLock();
  }
}

const selectionPath = (p, g) => `execution/projects/${encodeURIComponent(p)}/generations/${encodeURIComponent(g)}/selection`;
const savedPromptPath = (p, g) => selectionPath(p, g).replace(/selection$/, "saved-prompt");

export const takeBasePath = (p, g) => `projects/${encodeURIComponent(p)}/generations/${encodeURIComponent(g)}`;
async function uploadTake(p, g, file, provenance, takeId = null) {
  if (file.size > 8 * 1024 ** 3) throw new Error("Take upload exceeds 8 GiB");
  const query = new URLSearchParams({ filename: file.name, ...provenance });
  const path = `${takeBasePath(p, g)}/takes${takeId ? `/${encodeURIComponent(takeId)}/media` : ""}`;
  const response = await fetch(`/api/${path}?${query}`, {
    method: takeId ? "PUT" : "POST", headers: { "Content-Type": file.type || "application/octet-stream" }, body: file,
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "Take import metadata is invalid.");
  return data;
}

export const api = {
  draftContinuation: (p, g, body, signal) => request(`${takeBasePath(p, g)}/draft-continuation`, body, "POST", signal),
  validateContinuationDraft: (p, g, body) => request(`${takeBasePath(p, g)}/draft-continuation/validate`, body),
  analyzeSelectedTake: (p, g, body, signal) => request(`${takeBasePath(p, g)}/selected-take/analyze`, body, "POST", signal),
  correctObservation: (p, g, id, body) => request(`${takeBasePath(p, g)}/takes/${encodeURIComponent(id)}/observation`, body, "PUT"),
  observationEvidenceUrl: (p, g, id, name) => `/api/${takeBasePath(p, g)}/takes/${encodeURIComponent(id)}/evidence/${encodeURIComponent(name)}`,
  generationOutputs: (p, g) => request(`${takeBasePath(p, g)}/outputs`, undefined, "GET"),
  foregroundRenders: (p, watched, discovery) => {
    const params = new URLSearchParams({ ...(watched ? { generationId: watched.generationId, submissionId: watched.id } : {}),
      ...(discovery?.refresh ? { refresh: "true", cursor: String(discovery.cursor || 0) } : {}) });
    return request(`projects/${encodeURIComponent(p)}/foreground-renders${params.size ? `?${params}` : ""}`, undefined, "GET");
  },
  reconcileRenderSubmission: (p, g, id) => request(`${takeBasePath(p, g)}/render-submissions/${encodeURIComponent(id)}/reconcile`),
  recoverRenderSubmission: (p, g, id) => request(`${takeBasePath(p, g)}/render-submissions/${encodeURIComponent(id)}/recover`),
  retryRenderSubmission: (p, g, id) => request(`${takeBasePath(p, g)}/render-submissions/${encodeURIComponent(id)}/retry`),
  cancelRenderSubmission: (p, g, id) => request(`${takeBasePath(p, g)}/render-submissions/${encodeURIComponent(id)}/cancel`),
  watchRenderSubmission: (p, g, id) => {
    const path = `/api/${takeBasePath(p, g)}/render-submissions/${encodeURIComponent(id)}/live`;
    return new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}${path}`);
  },
  importRenderOutput: (p, g, id, outputId) => request(`${takeBasePath(p, g)}/render-submissions/${encodeURIComponent(id)}/outputs/${encodeURIComponent(outputId)}/import`),
  importTake: (p, g, file, provenance) => uploadTake(p, g, file, provenance),
  reattachTake: (p, g, id, file) => uploadTake(p, g, file, {}, id),
  takeDetail: (p, g, id) => request(`${takeBasePath(p, g)}/takes/${encodeURIComponent(id)}`, undefined, "GET"),
  takeMediaUrl: (p, g, id) => `/api/${takeBasePath(p, g)}/takes/${encodeURIComponent(id)}/media`,
  selectTake: (p, g, id) => request(`${takeBasePath(p, g)}/selected-take/${encodeURIComponent(id)}`, undefined, "PUT"),
  clearSelectedTake: (p, g) => request(`${takeBasePath(p, g)}/selected-take`, undefined, "DELETE"),
  deleteTake: (p, g, id) => request(`${takeBasePath(p, g)}/takes/${encodeURIComponent(id)}`, undefined, "DELETE"),
  executionRouting: (p, g) => request(selectionPath(p, g).replace(/selection$/, "routing"), undefined, "GET"),
    workflowControls: (p, g, profile) => request(selectionPath(p, g).replace(/selection$/, `profiles/${encodeURIComponent(profile)}/controls`), undefined, "GET"),
    saveWorkflowControls: (p, g, profile, scope, values) => request(selectionPath(p, g).replace(/selection$/, `profiles/${encodeURIComponent(profile)}/controls/${scope}`), { values }, "PUT"),
    clearWorkflowControls: (p, g, profile, scope) => request(selectionPath(p, g).replace(/selection$/, `profiles/${encodeURIComponent(profile)}/controls/${scope}`), undefined, "DELETE"),
  continuationStatus: (p, g) => request(selectionPath(p, g).replace(/selection$/, "continuation"), undefined, "GET"),
  saveContinuationSettings: (p, g, body) => request(selectionPath(p, g).replace(/selection$/, "continuation/settings"), body, "PUT"),
  decideContinuation: (p, g, id, body) => request(selectionPath(p, g).replace(/selection$/, `continuation/${encodeURIComponent(id)}`), body, "PUT"),
  removeContinuation: (p, g, id) => request(selectionPath(p, g).replace(/selection$/, `continuation/${encodeURIComponent(id)}`), undefined, "DELETE"),
  clearUnavailableContinuations: (p, g) => request(selectionPath(p, g).replace(/selection$/, "continuation/unavailable"), undefined, "DELETE"),
  saveRendererSeedSettings: (p, g, body) => request(selectionPath(p, g).replace(/selection$/, "renderer-seed"), body, "PUT"),
  saveExecutionRouting: (p, g, body) => request(selectionPath(p, g).replace(/selection$/, "routing"), body, "PUT"),
  uploadMedia: async (file, kind) => {
    const limits = { audio: 128, video: 512 };
    if (!limits[kind]) throw new Error("Choose an Audio or Video file.");
    if (file.size > limits[kind] * 1024 * 1024) throw new Error(`${kind} file exceeds ${limits[kind]} MiB limit.`);
    const response = await fetch(`/api/media/${kind}`, {
      method: "POST", headers: { "Content-Type": file.type || `${kind}/unknown` }, body: file,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Media upload failed. Reattach the file.");
    return data;
  },
  executionSelection: (p, g) => request(selectionPath(p, g), undefined, "GET"),
  savedH3Prompt: (p, g) => request(savedPromptPath(p, g), undefined, "GET"),
  saveH3Prompt: (p, g, body) => request(savedPromptPath(p, g), body, "PUT"),
  loadH3Prompt: (p, g, id) => request(`${savedPromptPath(p, g)}/${encodeURIComponent(id)}/load`, undefined, "POST"),
  deleteH3Prompt: (p, g, id) => request(`${savedPromptPath(p, g)}/${encodeURIComponent(id)}`, undefined, "DELETE"),
  selectExecutionPrompt: (p, g, body) => request(selectionPath(p, g), body, "PUT"),
  adoptHistoryExecutionPrompt: (p, g, body) => request(`${selectionPath(p, g)}/adopt-history`, body, "POST"),
  adoptManualEditExecutionPrompt: (p, g, body) => request(`${selectionPath(p, g)}/adopt-manual-edit`, body, "POST"),
  clearExecutionPrompt: (p, g) => request(selectionPath(p, g), undefined, "DELETE"),
  updateExecutionDuration: (p, g, body) => request(`${selectionPath(p, g).replace(/selection$/, "execution-duration")}`, body, "PUT"),
  updateExecutionSetup: (p, g, body) => request(`${selectionPath(p, g).replace(/selection$/, "execution-setup")}`, body, "PUT"),
  syncReferenceAssets: (p, g, body) => request(`${takeBasePath(p, g)}/reference-assets`, body, "PUT"),
  prepareWorkflow: body => request("execution/prepare", body),
  sequenceRenderReadiness: projectId => request(`execution/projects/${encodeURIComponent(projectId)}/sequence-render-readiness`, undefined, "GET"),
  sendExecution: body => request("execution/send", body),
  executionCapabilities: () => request("execution/capabilities", undefined, "GET"),
  executionConnections: () => request("execution/connections", undefined, "GET"),
  saveExecutionConnection: (id, body) => request(`execution/connections${id ? `/${encodeURIComponent(id)}` : ""}`, body, id ? "PUT" : "POST"),
  deleteExecutionConnection: id => request(`execution/connections/${encodeURIComponent(id)}`, undefined, "DELETE"),
  testExecutionConnection: id => request(`execution/connections/${encodeURIComponent(id)}/test`),
  localExecutionInputStatus: id => request(`execution/connections/${encodeURIComponent(id)}/local-input-status`, undefined, "GET"),
  executionProfiles: () => request("execution/profiles", undefined, "GET"),
  executionProfile: id => request(`execution/profiles/${encodeURIComponent(id)}`, undefined, "GET"),
  duplicateExecutionProfileDraft: id => request(`execution/profiles/${encodeURIComponent(id)}/duplicate-draft`, undefined, "GET"),
  executionEnumCapabilities: (id, controlId, connectionId) => request(`execution/profiles/${encodeURIComponent(id)}/controls/${encodeURIComponent(controlId)}/enum-capabilities`, { connectionId }),
  executionAspectRatioCapabilities: (id, nodeId, inputName, connectionId) => request(`execution/profiles/${encodeURIComponent(id)}/aspect-ratio-capabilities`, { nodeId, inputName, connectionId }),
  saveExecutionProfile: (id, body) => request(`execution/profiles${id ? `/${encodeURIComponent(id)}` : ""}`, body, id ? "PUT" : "POST"),
  deleteExecutionProfile: id => request(`execution/profiles/${encodeURIComponent(id)}`, undefined, "DELETE"),
  validateExecutionProfile: (body, id = null) => request(`execution/profiles${id ? `/${encodeURIComponent(id)}` : ""}/validate`, body),
  buildInitialRange: (id, body, signal) => request(`projects/${encodeURIComponent(id)}/story-plan/initial-range`, body, "POST", signal),
  applyInitialRange: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/initial-range/apply`, body),
  planStoryNext: (id, body, signal) => request(`projects/${encodeURIComponent(id)}/story-plan/next`, body, "POST", signal),
  applyStoryNext: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/next/apply`, body),
  planStoryInsert: (id, body, signal) => request(`projects/${encodeURIComponent(id)}/story-plan/insert`, body, "POST", signal),
  applyStoryInsert: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/insert/apply`, body),
  planStoryReplan: (id, body, signal) => request(`projects/${encodeURIComponent(id)}/story-plan/replan`, body, "POST", signal),
  applyStoryReplan: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/replan/apply`, body),
  buildStoryPlan: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/build`, body),
  reviseStoryPlan: (id, body, signal) => request(`projects/${encodeURIComponent(id)}/story-plan/revise`, body, "POST", signal),
  applyStoryPlan: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/apply`, body),
  listProjects: () => request("projects", undefined, "GET"),
  createProject: (workspace, continuity, planning, storyIdea, storyMemory, storyCreativeDirection, sequenceSubjects, characterProfiles, sequenceNextSubjectNumber, dialogueLanguage, storyPlanningStyle) => request("projects", { workspace, ...(dialogueLanguage ? { dialogueLanguage } : {}), ...(storyIdea !== undefined ? { storyIdea } : {}), ...(storyCreativeDirection !== undefined ? { storyCreativeDirection } : {}), ...(storyPlanningStyle !== undefined ? { storyPlanningStyle } : {}), ...(storyMemory !== undefined ? { storyMemory } : {}), ...(sequenceSubjects !== undefined && sequenceSubjects !== null ? { sequenceSubjects } : {}), ...(sequenceNextSubjectNumber !== undefined && sequenceNextSubjectNumber !== null ? { sequenceNextSubjectNumber } : {}), ...(characterProfiles ? { characterProfiles } : {}), ...(continuity ? { continuity } : {}), ...(planning ? { planning } : {}) }),
  loadProject: id => request(`projects/${encodeURIComponent(id)}`, undefined, "GET"),
  clearGeneratedState: (id, expectedProjectFingerprint, clearSavedPrompts = false) => request(`projects/${encodeURIComponent(id)}/clear-generated-state`, { expectedProjectFingerprint, clearSavedPrompts }),
  deleteProject: id => request(`projects/${encodeURIComponent(id)}`, undefined, "DELETE"),
  saveProject: (id, workspace, continuity, planning, storyIdea, storyMemory, memoryAuthority = {}, storyCreativeDirection, characterProfiles, dialogueLanguage, storyPlanningStyle) => request(`projects/${encodeURIComponent(id)}`, {
    ...memoryAuthority, workspace, ...(dialogueLanguage ? { dialogueLanguage } : {}), ...(storyIdea !== undefined ? { storyIdea } : {}), ...(storyCreativeDirection !== undefined ? { storyCreativeDirection } : {}), ...(storyPlanningStyle !== undefined ? { storyPlanningStyle } : {}), ...(storyMemory !== undefined ? { storyMemory } : {}), ...(characterProfiles ? { characterProfiles } : {}), ...(continuity ? { continuity } : {}), ...(planning ? { planning } : {}),
  }, "PUT"),
  saveProjectAs: (id, name, workspace, continuity, planning, storyIdea, storyMemory, memoryAuthority = {}, storyCreativeDirection, characterProfiles, dialogueLanguage, storyPlanningStyle, copyOptions = {}) => request(`projects/${encodeURIComponent(id)}/save-as`, {
    ...memoryAuthority, ...copyOptions, name, ...(dialogueLanguage ? { dialogueLanguage } : {}), ...(storyIdea !== undefined ? { storyIdea } : {}), ...(storyCreativeDirection !== undefined ? { storyCreativeDirection } : {}), ...(storyPlanningStyle !== undefined ? { storyPlanningStyle } : {}), ...(storyMemory !== undefined ? { storyMemory } : {}), ...(characterProfiles ? { characterProfiles } : {}), ...(workspace ? { workspace } : {}), ...(continuity ? { continuity } : {}), ...(planning ? { planning } : {}),
  }),
  createGeneration: (id, insertIndex) => request(`projects/${encodeURIComponent(id)}/generations`, insertIndex == null ? {} : { insertIndex }),
  continueSelectedTake: (projectId, generationId, authority) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/continue-selected-take`, authority),
  duplicateGeneration: (projectId, generationId) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/duplicate`, undefined, "POST"),
  activateGeneration: (projectId, generationId, expectedProjectFingerprint) => request(`projects/${encodeURIComponent(projectId)}/active-generation/${encodeURIComponent(generationId)}`, expectedProjectFingerprint ? { expectedProjectFingerprint } : undefined, "PUT"),
  reorderGenerations: (projectId, generationIds) => request(`projects/${encodeURIComponent(projectId)}/generation-order`, { generationIds }, "PUT"),
  deleteGeneration: (projectId, generationId) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}`, undefined, "DELETE"),
  nameSequenceSubject: (projectId, subjectId, narrativeName) => request(`projects/${encodeURIComponent(projectId)}/subjects`, { subjectId, narrativeName }, "PATCH"),
  deleteSequenceSubject: (projectId, subjectId) => request(`projects/${encodeURIComponent(projectId)}/subjects`, { subjectId }, "DELETE"),
  inferPlan: (projectId, generationId, body, signal) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/infer-plan`, body, "POST", signal),
  revisePlan: (projectId, generationId, body, signal) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/revise-plan`, body, "POST", signal),
  models: settings => request("models", settings),
  discoverEndpoints: body => request("discover-endpoints", body),
  test: settings => request("test-connection", settings),
  eject: settings => request("model-management/eject", settings),
  assemble: body => request("assemble", body),
  resolveDialogue: (body, signal) => request("dialogue/resolve", body, "POST", signal),
  referenceManifest: body => request("reference-manifest", body),
  generate: body => request("generate", body),
  streamGenerate: (body, options) => streamRequest("generate-stream", body, options),
  streamRevision: (body, options) => streamRequest("revise-stream", body, options),
  validate: body => request("validate", body),
  repair: (body, signal) => request("repair", body, "POST", signal),
  validateProject: body => request("validate-project", body),
};
