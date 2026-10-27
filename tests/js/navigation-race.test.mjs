import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { createNavigationAuthority } from "../../web/js/navigation-authority.js";

const source = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const project = (id, generation = `${id}-g1`) => ({ id, name: id, projectFingerprint: `${id}-fingerprint`,
  workspace: { projectName: id }, sequence: { activeGenerationId: generation, generations: [{ generationId: generation }] } });

function fixture() {
  const state = { currentProjectId: "A", activeGenerationId: "A-g1", activeProjectType: "filesystem",
    dirty: false, projectFingerprint: "A-fingerprint", filesystemProjects: [], legacyProjects: [] };
  const calls = [], loads = new Map(), activations = new Map(), operations = [];
  const element = { value: "" };
  const sandbox = {
    state, navigationAuthority: createNavigationAuthority(),
    api: {
      activateGeneration: (id, generation) => { const d = deferred(); activations.set(generation, d); calls.push(["activate", id, generation]); return d.promise; },
      loadProject: id => { const d = deferred(); loads.set(id, d); calls.push(["load", id]); return d.promise; },
    },
    storyPlanner: { confirmContextChange: () => true, hasPendingRequest: false, clearDraft() {}, resetProjectContext() {} },
    invalidateProviderContext() {}, renderSequenceControls() {}, renderProjectOptions() {}, setDirty() {},
    renderContinuityUi() {}, toast: message => calls.push(["toast", message]),
    $: selector => selector === "#project-select" ? element : { value: "" },
    projectSelectionValue: () => state.currentProjectId || "",
    captureWorkspace: () => ({}),
    openFilesystemProjectSafely: async ({ id, loadProject }) => ({ ok: true, project: await loadProject(id) }),
    openLegacyProjectSafely: async ({ id, loadProject }) => ({ ok: true, project: await loadProject(id) }),
    applyFreshUnsavedWorkspace: () => { state.currentProjectId = null; state.activeProjectType = "unsaved"; state.activeGenerationId = "ephemeral"; state.dirty = true; },
    structuralSequenceReady: () => true,
    sequenceFromProject: p => p.sequence,
    restoreWorkspace: w => { state.workspace = w; }, restoreContinuity() {}, restorePlanning() {},
    restoreScenePlan() {}, requestReferenceManifestRefresh() {},
    savedPrompts: { rememberCurrent() {}, load() {} }, workflowPreparation: { sync() {} }, renderedTakes: { sync() {} },
    invalidateProviderContext() {}, rememberProjectLocation() {},
    renderSequenceControls() {}, renderContinuityUi() {}, renderProjectOptions() {}, setDirty() {},
    restoreWorkspace: w => { state.workspace = w; }, restoreContinuity() {}, restorePlanning() {},
    storyPlanner: { confirmContextChange: () => true, hasPendingRequest: false, clearDraft() {}, resetProjectContext() {},
      restoreStoryIdea() {}, restoreScenePlan() {}, restoreStoryCreativeDirection() {}, restoreStoryPlanningStyle() {}, restoreStoryMemory() {} },
    sequenceFromProject: p => p.sequence,
    rememberProjectLocation() {}, requestReferenceManifestRefresh() {},
    document: { querySelectorAll: () => [] }, window: { history: { replaceState() {} } },
  };
  sandbox.adoptProjectResponse = p => {
    sandbox.navigationAuthority.invalidate();
    state.currentProjectId = p.id; state.currentProjectSavedName = p.name; state.activeProjectType = "filesystem";
    state.activeGenerationId = p.sequence.activeGenerationId; state.sequenceGenerations = p.sequence.generations;
    state.projectFingerprint = p.projectFingerprint; state.workspace = p.workspace; state.adoptions = (state.adoptions || 0) + 1;
  };
  sandbox.runOperation = () => { const d = deferred(); operations.push(d); return d.promise; };
  const cut = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
  vm.createContext(sandbox);
  vm.runInContext(cut("async function switchGeneration(", "function structuralSequenceReady("), sandbox);
  vm.runInContext(cut("function structuralSequenceReady(", "function addGeneration("), sandbox);
  vm.runInContext(cut("async function newProject(", "async function loadSelectedProject("), sandbox);
  vm.runInContext(cut("async function loadSelectedProject(", "async function removeCurrentProject("), sandbox);
  return { sandbox, state, calls, loads, activations, operations, element };
}

test("Project B load supersedes an in-flight Generation switch from Project A", async () => {
  const f = fixture();
  const oldSwitch = f.sandbox.switchGeneration("A-g2");
  f.element.value = "B";
  const newLoad = f.sandbox.loadSelectedProject();
  f.loads.get("B").resolve(project("B", "B-g1")); await newLoad;
  f.activations.get("A-g2").resolve(project("A", "A-g2")); await oldSwitch;
  assert.equal(f.state.currentProjectId, "B"); assert.equal(f.state.activeGenerationId, "B-g1");
  assert.equal(f.state.adoptions, 1);
});

test("newer Generation intent wins with either response order", async () => {
  for (const order of [["A-g3", "A-g2"], ["A-g2", "A-g3"]]) {
    const f = fixture();
    const two = f.sandbox.switchGeneration("A-g2"), three = f.sandbox.switchGeneration("A-g3");
    for (const id of order) f.activations.get(id).resolve(project("A", id));
    await Promise.all([two, three]);
    assert.equal(f.state.activeGenerationId, "A-g3"); assert.equal(f.state.adoptions, 1);
  }
});

test("newest overlapping Project load survives reversed responses", async () => {
  const f = fixture();
  f.element.value = "A"; const a = f.sandbox.loadSelectedProject();
  f.element.value = "B"; const b = f.sandbox.loadSelectedProject();
  f.loads.get("B").resolve(project("B")); await b;
  f.loads.get("A").resolve(project("A")); await a;
  assert.equal(f.state.currentProjectId, "B"); assert.equal(f.state.adoptions, 1);
});

test("New Project invalidates a delayed Project load", async () => {
  const f = fixture();
  f.element.value = "A"; const pending = f.sandbox.loadSelectedProject();
  await f.sandbox.newProject();
  f.loads.get("A").resolve(project("A")); await pending;
  assert.equal(f.state.currentProjectId, null); assert.equal(f.state.activeProjectType, "unsaved");
});

test("stale Sequence response is not adopted after a context switch", async () => {
  const f = fixture();
  const sequence = f.sandbox.runSequenceOperation(() => f.sandbox.runOperation(), "changed");
  f.element.value = "B"; const load = f.sandbox.loadSelectedProject();
  f.loads.get("B").resolve(project("B")); await load;
  f.operations[0].resolve(project("A", "A-g2")); await sequence;
  assert.equal(f.state.currentProjectId, "B"); assert.equal(f.state.adoptions, 1);
});

test("current non-stale Generation switch still adopts normally", async () => {
  const f = fixture();
  const pending = f.sandbox.switchGeneration("A-g2");
  f.activations.get("A-g2").resolve(project("A", "A-g2"));
  assert.equal(await pending, true); assert.equal(f.state.activeGenerationId, "A-g2"); assert.equal(f.state.adoptions, 1);
});
