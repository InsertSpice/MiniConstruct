import assert from "node:assert/strict";
import test from "node:test";

import { api } from "../../web/js/api.js";
import { migrateLegacyProject } from "../../web/js/project-migration.js";
import {
  continuityFromProject, freshUnsavedContinuity, freshUnsavedWorkspace, openFilesystemProjectSafely, openLegacyProjectSafely, persistFilesystemProject,
  deleteFilesystemProjectAndReset, deleteLegacyProjectAndReset, loadProjectSources, prepareImportedWorkspace,
  resetUnsavedProjectState,
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

test("create, Save, and Save As forward canonical Story idea outside Workspace", async () => {
  const bodies = [], originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    const body = JSON.parse(options.body); bodies.push(body);
    return { ok: true, json: async () => ({ id: "saved", storyIdea: body.storyIdea }) };
  };
  try {
    const idea = "  Exact\nidea  ";
    await api.createProject(workspace, undefined, undefined, idea);
    await api.saveProject("saved", workspace, undefined, undefined, idea);
    await api.saveProjectAs("saved", "Fork", workspace, undefined, undefined, idea);
    assert.ok(bodies.every(body => body.storyIdea === idea && !Object.hasOwn(body.workspace, "storyIdea")));
  } finally { globalThis.fetch = originalFetch; }
});

const workspace = {
  schemaVersion: 1,
  projectId: null,
  projectName: "Browser Project",
  creativeRequest: "A cyclist crosses a wet street.",
  assets: [{ id: "image-1", kind: "image", image: { data_url: "data:image/png;base64,AA==" } }],
};

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

test("New preparation is blank and resets Project ownership state", () => {
  const fresh = freshUnsavedWorkspace();
  assert.equal(fresh.projectId, null);
  assert.equal(fresh.projectName, "Untitled Project");
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
