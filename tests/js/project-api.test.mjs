import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { api } from "../../web/js/api.js";
import { migrateLegacyProject } from "../../web/js/project-migration.js";
import {
  continuityFromProject, freshUnsavedContinuity, freshUnsavedWorkspace, openFilesystemProjectSafely, openLegacyProjectSafely, persistFilesystemProject,
  deleteFilesystemProjectAndReset, deleteLegacyProjectAndReset, loadProjectSources, prepareImportedWorkspace,
  importedSequenceMetadata, clearImportedSequenceMetadata, resetUnsavedProjectState,
  projectIdFromLocation, projectLocation,
} from "../../web/js/project-workflow.js";

test("refresh URL retains only the selected Project identity and preserves other URL state", async () => {
  const url = projectLocation("http://localhost:8000/?panel=story#sequence", "saved-id");
  assert.equal(projectIdFromLocation(url), "saved-id");
  assert.equal(new URL(url).searchParams.get("panel"), "story");
  assert.equal(new URL(url).hash, "#sequence");
  const reopened = await openFilesystemProjectSafely({ id: projectIdFromLocation(url),
    loadProject: async id => ({ id, storyIdea: "Exact saved idea" }) });
  assert.equal(reopened.project.storyIdea, "Exact saved idea");
  assert.equal(projectIdFromLocation(projectLocation(url, null)), null);
});

test("create, Save, and Save As forward canonical Story direction and Planning Style outside Workspace", async () => {
  const bodies = [], originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body); bodies.push(body);
    return { ok: true, json: async () => ({ id: "saved", storyIdea: body.storyIdea }) };
  };
  try {
    const idea = "  Exact\nidea  ";
    const direction = { toneMood: "Uneasy", performanceEnergy: "Restrained", pacingRhythm: "Patient", beatDurationDensity: "One beat" };
    const style = { creativity: "exploratory", scenePlanDetail: "detailed" };
    await api.createProject(workspace, undefined, undefined, idea, undefined, direction, undefined, undefined, undefined, undefined, style);
    await api.saveProject("saved", workspace, undefined, undefined, idea, undefined, {}, direction, undefined, undefined, style);
    await api.saveProjectAs("saved", "Fork", workspace, undefined, undefined, idea, undefined, {}, direction, undefined, undefined, style);
    assert.ok(bodies.every(body => body.storyIdea === idea && body.storyCreativeDirection.toneMood === "Uneasy"
      && body.storyPlanningStyle.creativity === "exploratory"
      && !Object.hasOwn(body.workspace, "storyIdea") && !Object.hasOwn(body.workspace, "storyCreativeDirection")
      && !Object.hasOwn(body.workspace, "storyPlanningStyle")));
  } finally { globalThis.fetch = originalFetch; }
});

const workspace = {
  schemaVersion: 1,
  projectId: null,
  projectName: "Browser Project",
  creativeRequest: "A cyclist crosses a wet street.",
  assets: [{ id: "image-1", kind: "image", image: { data_url: "data:image/png;base64,AA==" } }],
};

test("portable import preserves an explicit empty canonical cast through create and clears adoption state", async () => {
  const appSource = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(appSource, /importedSequenceMetadata\(payload, validated\)/);
  assert.match(appSource, /Array\.isArray\(state\.importedSequenceSubjects\)/);
  assert.match(appSource, /clearImportedSequenceMetadata\(state\)/);
  const envelope = { workspace, sequenceSubjects: [], sequenceNextSubjectNumber: 9 };
  const validated = { ...envelope, sequenceSubjects: [], sequenceNextSubjectNumber: 9 };
  const imported = importedSequenceMetadata(envelope, validated);
  assert.deepEqual(imported, { sequenceSubjects: [], sequenceNextSubjectNumber: 9 });

  const state = { ...imported };
  const originalFetch = globalThis.fetch;
  let body;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return { ok: true, status: 200, json: async () => ({ id: "imported", name: workspace.projectName, workspace }) };
  };
  try {
    const saved = await persistFilesystemProject({
      projectId: null, workspace, sequenceSubjects: state.sequenceSubjects,
      sequenceNextSubjectNumber: state.sequenceNextSubjectNumber,
      createProject: api.createProject, saveProject: async () => { throw new Error("save should not be called"); },
    });
    assert.equal(saved.projectId, "imported");
  } finally { globalThis.fetch = originalFetch; }
  assert.deepEqual(body.sequenceSubjects, []);
  assert.equal(body.sequenceNextSubjectNumber, 9);
  const adoptedState = { importedSequenceSubjects: state.sequenceSubjects,
    importedSequenceNextSubjectNumber: state.sequenceNextSubjectNumber };
  clearImportedSequenceMetadata(adoptedState);
  assert.equal(adoptedState.importedSequenceSubjects, null);
  assert.equal(adoptedState.importedSequenceNextSubjectNumber, null);
});

test("legacy portable envelopes without canonical fields retain the Workspace-only path", async () => {
  const legacy = importedSequenceMetadata({ workspace }, { sequenceSubjects: [], sequenceNextSubjectNumber: null });
  assert.deepEqual(legacy, { sequenceSubjects: null, sequenceNextSubjectNumber: null });
  let body;
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return { ok: true, status: 200, json: async () => ({ id: "legacy-import", workspace }) };
  };
  try {
    await persistFilesystemProject({ projectId: null, workspace,
      sequenceSubjects: legacy.sequenceSubjects, sequenceNextSubjectNumber: legacy.sequenceNextSubjectNumber,
      createProject: api.createProject, saveProject: async () => { throw new Error("save should not be called"); } });
  } finally { globalThis.fetch = originalFetch; }
  assert.equal(Object.hasOwn(body, "sequenceSubjects"), false);
  assert.equal(Object.hasOwn(body, "sequenceNextSubjectNumber"), false);
});

test("normal Save and Save As send explicit selected memory UUID and loaded Project authority", async () => {
  const bodies = [], original = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    bodies.push(JSON.parse(options.body));
    return { ok: true, json: async () => ({ id: "project" }) };
  };
  const memory = { recentSummary: "G3 history", durableFacts: [], openThreads: [] };
  const authority = { storyMemoryGenerationId: "g3", expectedProjectFingerprint: "loaded" };
  try {
    await api.saveProject("project", workspace, undefined, undefined, undefined, memory, authority);
    await api.saveProjectAs("project", "Fork", workspace, undefined, undefined, undefined, memory, authority);
    await api.saveProject("project", workspace, undefined, undefined, undefined, undefined, authority);
  } finally { globalThis.fetch = original; }
  for (const body of bodies) {
    assert.equal(body.storyMemoryGenerationId, "g3");
    assert.equal(body.expectedProjectFingerprint, "loaded");
  }
  assert.deepEqual(bodies[0].storyMemory, memory);
  assert.deepEqual(bodies[1].storyMemory, memory);
  assert.equal(Object.hasOwn(bodies[2], "storyMemory"), false);
});

test("filesystem Project API uses metadata listing, open, create, and active-ID save routes", async () => {
  const calls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ id: "backend-id", name: "Browser Project", workspace }) };
  };
  try {
    await api.listProjects();
    await api.loadProject("backend-id");
    await api.createProject(workspace);
    await api.saveProject("backend-id", workspace);
    await api.deleteProject("backend-id");
  } finally { globalThis.fetch = originalFetch; }

  assert.deepEqual(calls.map(call => [call.options.method, call.url]), [
    ["GET", "/api/projects"], ["GET", "/api/projects/backend-id"],
    ["POST", "/api/projects"], ["PUT", "/api/projects/backend-id"], ["DELETE", "/api/projects/backend-id"],
  ]);
  assert.deepEqual(JSON.parse(calls[2].options.body).workspace, workspace);
  assert.deepEqual(JSON.parse(calls[3].options.body).workspace, workspace);
});

test("Generation activation forwards the loaded Project fingerprint as a concurrency guard", async () => {
  const originalFetch = globalThis.fetch; let call;
  globalThis.fetch = async (url, options) => {
    call = { url, method: options.method, body: JSON.parse(options.body) };
    return { ok: true, json: async () => ({}) };
  };
  try {
    await api.activateGeneration("project", "generation", "a".repeat(64));
    assert.deepEqual(call, { url: "/api/projects/project/active-generation/generation", method: "PUT",
      body: { expectedProjectFingerprint: "a".repeat(64) } });
  } finally { globalThis.fetch = originalFetch; }
});

test("Sequence Subject deletion uses the canonical Subject ID endpoint", async () => {
  const calls = [], originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options });
    return { ok: true, status: 200, json: async () => ({ sequence: { subjects: [] } }) };
  };
  try {
    await api.deleteSequenceSubject("project/id", "stable subject/id");
  } finally { globalThis.fetch = originalFetch; }
  assert.deepEqual(calls.map(call => [call.options.method, call.url]), [
    ["DELETE", "/api/projects/project%2Fid/subjects"],
  ]);
  assert.deepEqual(JSON.parse(calls[0].options.body), { subjectId: "stable subject/id" });
});

test("New preparation is blank and resets Project ownership state", () => {
  const fresh = freshUnsavedWorkspace();
  assert.equal(fresh.projectId, null);
  assert.equal(fresh.projectName, "Untitled Project");
  assert.equal(fresh.aspectRatio, "4:3");
  assert.equal(fresh.creativeRequest, "");
  const state = resetUnsavedProjectState({
    activeProjectType: "filesystem", currentProjectId: "old-id", currentProjectSavedName: "Old",
    currentLegacyProjectId: "legacy-id",
  });
  assert.deepEqual(state, {
    activeProjectType: "none", currentProjectId: null, currentProjectSavedName: null, currentLegacyProjectId: null,
  });
  assert.equal(freshUnsavedWorkspace().projectId, null);
});

test("Variations helper explains prompt candidates and explicit saving without changing generation behavior", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(html, /how many separate H3 prompts the writer generates/);
  assert.match(html, /Candidates appear under Variation 1, Variation 2/);
  assert.match(html, /do not render videos or queue ComfyUI/);
  assert.match(html, /Save any candidates you want to keep to Saved H3 Prompts/);
});

test("continuity defaults safely and loads only the active response fields", () => {
  assert.deepEqual(freshUnsavedContinuity(), { sequenceContext: "", incomingState: "", intendedOutgoingState: "" });
  assert.deepEqual(continuityFromProject({ continuity: { sequenceContext: "Rule", incomingState: "Start", intendedOutgoingState: "End" } }), {
    sequenceContext: "Rule", incomingState: "Start", intendedOutgoingState: "End",
  });
  assert.deepEqual(continuityFromProject({}), { sequenceContext: "", incomingState: "", intendedOutgoingState: "" });
});

test("filesystem Project list failures remain errors instead of becoming an empty successful list", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 503, json: async () => ({ detail: "Projects service unavailable" }) });
  try {
    await assert.rejects(() => api.listProjects(), /Projects service unavailable/);
  } finally { globalThis.fetch = originalFetch; }
});

test("filesystem save submits active continuity separately from the Workspace", async () => {
  const originalFetch = globalThis.fetch;
  let body;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return { ok: true, status: 200, json: async () => ({}) };
  };
  try {
    await api.saveProject("project-id", workspace, {
      sequenceContext: "Shared rule",
      incomingState: "Start",
      intendedOutgoingState: "End",
    });
  } finally { globalThis.fetch = originalFetch; }
  assert.deepEqual(body, {
    workspace,
    continuity: { sequenceContext: "Shared rule", incomingState: "Start", intendedOutgoingState: "End" },
  });
});

test("filesystem save forwards the complete typed Visual Handoff planning policy", async () => {
  const originalFetch = globalThis.fetch;
  let body;
  globalThis.fetch = async (_url, options) => {
    body = JSON.parse(options.body);
    return { ok: true, status: 200, json: async () => ({}) };
  };
  const planning = {
    relationshipToPrevious: "continue_previous_shot",
    cameraIntent: "Pan with the subject.",
    visualHandoff: { strategy: "endpoint_then_immediate_cut", tailSeconds: 1.75, immediateCutMilliseconds: 240 },
  };
  try {
    await api.saveProject("project-id", workspace, undefined, planning);
  } finally { globalThis.fetch = originalFetch; }
  assert.deepEqual(body.planning, planning);
  assert.equal(Object.hasOwn(body.workspace, "planning"), false);
});

test("filesystem and legacy Project discovery fail independently", async () => {
  const filesystemUnavailable = await loadProjectSources({
    listFilesystem: async () => { throw new Error("filesystem unavailable"); },
    listLegacy: async () => [{ id: "legacy-1", name: "Legacy" }],
  });
  assert.equal(filesystemUnavailable.filesystemOk, false);
  assert.deepEqual(filesystemUnavailable.legacyProjects, [{ id: "legacy-1", name: "Legacy" }]);

  const legacyUnavailable = await loadProjectSources({
    listFilesystem: async () => ({ projects: [{ id: "filesystem-1", name: "Filesystem" }] }),
    listLegacy: async () => { throw new Error("IndexedDB unavailable"); },
  });
  assert.equal(legacyUnavailable.legacyOk, false);
  assert.deepEqual(legacyUnavailable.filesystemProjects, [{ id: "filesystem-1", name: "Filesystem" }]);
});

test("filesystem workflow adopts backend identity and keeps failed save state untouched", async () => {
  let createCalls = 0;
  const created = await persistFilesystemProject({
    projectId: null, workspace, createProject: async value => { createCalls += 1; return { id: "new-backend-id", name: value.projectName, workspace: value }; },
    saveProject: async () => { throw new Error("save should not be called"); },
  });
  assert.equal(createCalls, 1);
  assert.equal(created.projectId, "new-backend-id");

  const currentWorkspace = structuredClone(workspace);
  await assert.rejects(() => persistFilesystemProject({
    projectId: "backend-id", workspace: currentWorkspace,
    createProject: async () => { throw new Error("create should not be called"); },
    saveProject: async () => { throw new Error("server unavailable"); },
  }), /server unavailable/);
  assert.deepEqual(currentWorkspace, workspace);

  const failedLoad = await openFilesystemProjectSafely({
    id: "missing", currentWorkspace, loadProject: async () => { throw new Error("not found"); },
  });
  assert.equal(failedLoad.ok, false);
  assert.equal(failedLoad.workspace, currentWorkspace);

  const failedLegacyLoad = await openLegacyProjectSafely({
    id: "missing-legacy", currentWorkspace, loadProject: async () => { throw new Error("legacy not found"); },
  });
  assert.equal(failedLegacyLoad.ok, false);
  assert.equal(failedLegacyLoad.workspace, currentWorkspace);
});

test("legacy migration posts a new filesystem Project without changing the IndexedDB source", async () => {
  const legacy = { id: "legacy-id", name: "Legacy Browser Project", workspace: structuredClone(workspace) };
  legacy.workspace.projectId = "legacy-id";
  let posted;
  const migration = await migrateLegacyProject(legacy, async value => {
    posted = value;
    return { id: "new-filesystem-id", name: value.projectName, workspace: { ...value, projectId: "new-filesystem-id" } };
  });
  assert.equal(posted.projectId, null);
  assert.equal(posted.projectName, "Legacy Browser Project");
  assert.equal(posted.assets[0].image.data_url, workspace.assets[0].image.data_url);
  assert.equal(migration.created.id, "new-filesystem-id");
  assert.equal(legacy.id, "legacy-id");
  assert.equal(legacy.workspace.projectId, "legacy-id");
});

test("portable import explicitly clears prior Project ownership before first filesystem save", () => {
  const imported = prepareImportedWorkspace({ projectId: "old-filesystem-id", projectName: "Imported", assets: [] });
  assert.equal(imported.projectId, null);
  assert.equal(imported.projectName, "Imported");
});

test("legacy deletion removes the deleted record from cache only after deletion succeeds", async () => {
  const legacyProjects = [
    { id: "legacy-id", name: "Legacy Project" },
    { id: "other-id", name: "Other Project" },
  ];
  let resetCalls = 0;
  await deleteLegacyProjectAndReset({
    id: "legacy-id",
    deleteProject: async () => {},
    legacyProjects,
    reset: () => { resetCalls += 1; },
  });
  assert.deepEqual(legacyProjects, [{ id: "other-id", name: "Other Project" }]);
  assert.equal(resetCalls, 1);

  const cacheAfterSuccess = [...legacyProjects];
  await assert.rejects(() => deleteLegacyProjectAndReset({
    id: "other-id",
    deleteProject: async () => { throw new Error("IndexedDB unavailable"); },
    legacyProjects,
    reset: () => { throw new Error("reset should not run"); },
  }), /IndexedDB unavailable/);
  assert.deepEqual(legacyProjects, cacheAfterSuccess);
  assert.equal(resetCalls, 1);
});

test("filesystem deletion removes its cache entry and resets only after backend success", async () => {
  const filesystemProjects = [
    { id: "filesystem-id", name: "Filesystem Project" },
    { id: "other-id", name: "Other Project" },
  ];
  let deletedId;
  let resetCalls = 0;
  await deleteFilesystemProjectAndReset({
    id: "filesystem-id",
    deleteProject: async id => { deletedId = id; },
    filesystemProjects,
    reset: () => { resetCalls += 1; },
  });
  assert.equal(deletedId, "filesystem-id");
  assert.deepEqual(filesystemProjects, [{ id: "other-id", name: "Other Project" }]);
  assert.equal(resetCalls, 1);

  const cacheAfterSuccess = [...filesystemProjects];
  await assert.rejects(() => deleteFilesystemProjectAndReset({
    id: "other-id",
    deleteProject: async () => { throw new Error("server unavailable"); },
    filesystemProjects,
    reset: () => { throw new Error("reset should not run"); },
  }), /server unavailable/);
  assert.deepEqual(filesystemProjects, cacheAfterSuccess);
  assert.equal(resetCalls, 1);
});
