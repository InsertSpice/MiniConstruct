async function request(path, body, method = "POST") {
  const options = { method, headers: {} };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(`/api/${path}`, options);
  let data;
  try { data = await response.json(); } catch { data = {}; }
  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("; ")
      : data.detail || `Request failed (${response.status})`;
    throw new Error(detail);
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
    const detail = Array.isArray(data.detail)
      ? data.detail.map(item => `${(item.loc || []).slice(1).join(".")}: ${item.msg}`).join("; ")
      : data.detail || `Request failed (${response.status})`;
    throw new Error(detail);
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

export const api = {
  executionRouting: (p, g) => request(selectionPath(p, g).replace(/selection$/, "routing"), undefined, "GET"),
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
  selectExecutionPrompt: (p, g, body) => request(selectionPath(p, g), body, "PUT"),
  clearExecutionPrompt: (p, g) => request(selectionPath(p, g), undefined, "DELETE"),
  prepareWorkflow: body => request("execution/prepare", body),
  sendExecution: body => request("execution/send", body),
  executionCapabilities: () => request("execution/capabilities", undefined, "GET"),
  executionConnections: () => request("execution/connections", undefined, "GET"),
  saveExecutionConnection: (id, body) => request(`execution/connections${id ? `/${encodeURIComponent(id)}` : ""}`, body, id ? "PUT" : "POST"),
  deleteExecutionConnection: id => request(`execution/connections/${encodeURIComponent(id)}`, undefined, "DELETE"),
  testExecutionConnection: id => request(`execution/connections/${encodeURIComponent(id)}/test`),
  executionProfiles: () => request("execution/profiles", undefined, "GET"),
  executionProfile: id => request(`execution/profiles/${encodeURIComponent(id)}`, undefined, "GET"),
  saveExecutionProfile: (id, body) => request(`execution/profiles${id ? `/${encodeURIComponent(id)}` : ""}`, body, id ? "PUT" : "POST"),
  deleteExecutionProfile: id => request(`execution/profiles/${encodeURIComponent(id)}`, undefined, "DELETE"),
  validateExecutionProfile: (body, id = null) => request(`execution/profiles${id ? `/${encodeURIComponent(id)}` : ""}/validate`, body),
  buildStoryPlan: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/build`, body),
  reviseStoryPlan: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/revise`, body),
  applyStoryPlan: (id, body) => request(`projects/${encodeURIComponent(id)}/story-plan/apply`, body),
  listProjects: () => request("projects", undefined, "GET"),
  createProject: (workspace, continuity, planning, storyIdea) => request("projects", { workspace, ...(storyIdea !== undefined ? { storyIdea } : {}), ...(continuity ? { continuity } : {}), ...(planning ? { planning } : {}) }),
  loadProject: id => request(`projects/${encodeURIComponent(id)}`, undefined, "GET"),
  deleteProject: id => request(`projects/${encodeURIComponent(id)}`, undefined, "DELETE"),
  saveProject: (id, workspace, continuity, planning, storyIdea) => request(`projects/${encodeURIComponent(id)}`, {
    workspace, ...(storyIdea !== undefined ? { storyIdea } : {}), ...(continuity ? { continuity } : {}), ...(planning ? { planning } : {}),
  }, "PUT"),
  saveProjectAs: (id, name, workspace, continuity, planning, storyIdea) => request(`projects/${encodeURIComponent(id)}/save-as`, {
    name, ...(storyIdea !== undefined ? { storyIdea } : {}), ...(workspace ? { workspace } : {}), ...(continuity ? { continuity } : {}), ...(planning ? { planning } : {}),
  }),
  createGeneration: (id, insertIndex) => request(`projects/${encodeURIComponent(id)}/generations`, insertIndex == null ? {} : { insertIndex }),
  duplicateGeneration: (projectId, generationId) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/duplicate`, undefined, "POST"),
  activateGeneration: (projectId, generationId) => request(`projects/${encodeURIComponent(projectId)}/active-generation/${encodeURIComponent(generationId)}`, undefined, "PUT"),
  reorderGenerations: (projectId, generationIds) => request(`projects/${encodeURIComponent(projectId)}/generation-order`, { generationIds }, "PUT"),
  deleteGeneration: (projectId, generationId) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}`, undefined, "DELETE"),
  inferPlan: (projectId, generationId, body) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/infer-plan`, body),
  revisePlan: (projectId, generationId, body) => request(`projects/${encodeURIComponent(projectId)}/generations/${encodeURIComponent(generationId)}/revise-plan`, body),
  models: settings => request("models", settings),
  discoverEndpoints: body => request("discover-endpoints", body),
  test: settings => request("test-connection", settings),
  eject: settings => request("model-management/eject", settings),
  assemble: body => request("assemble", body),
  referenceManifest: body => request("reference-manifest", body),
  generate: body => request("generate", body),
  streamGenerate: (body, options) => streamRequest("generate-stream", body, options),
  streamRevision: (body, options) => streamRequest("revise-stream", body, options),
  validate: body => request("validate", body),
  repair: body => request("repair", body),
  validateProject: body => request("validate-project", body),
};
