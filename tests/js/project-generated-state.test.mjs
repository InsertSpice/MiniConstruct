import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { Element } from "./support/dom.mjs";
import { mountHtml } from "./support/html.mjs";
import { createProjectGeneratedState } from "../../web/js/project-generated-state.js";
import { api } from "../../web/js/api.js";

function fixture() {
  const doc = { createElement: tag => new Element(tag, doc) };
  const root = mountHtml(readFileSync(new URL("../../web/index.html", import.meta.url), "utf8"), doc);
  const find = id => root.querySelector(`#${id}`), click = id => find(id).emit("click");
  for (const id of ["project-save-as-dialog", "project-clear-dialog"]) {
    find(id).showModal = () => { find(id).open = true; };
    find(id).close = () => { find(id).open = false; };
  }
  const context = { saved: true, dirty: false, name: "Variation" }, copies = [], clears = [], notices = [];
  createProjectGeneratedState({ root, getContext: () => context,
    saveAs: async options => { copies.push(options); return { saved: true }; },
    clearGeneratedState: async option => { clears.push(option); return true; }, notify: text => notices.push(text) });
  return { root, find, click, context, copies, clears, notices };
}

test("Save As defaults to Full, Fresh keeps prompts only by explicit choice", async () => {
  const s = fixture();
  await s.click("project-save-as");
  assert.equal(s.find("project-copy-mode").value, "full");
  assert.equal(s.find("project-keep-prompts").checked, false);
  assert.equal(s.find("project-keep-prompts-row").hidden, true);
  await s.click("project-copy-confirm");
  assert.deepEqual(s.copies[0], { name: "Variation", copyMode: "full", keepSavedPrompts: false });
  await s.click("project-save-as"); s.find("project-copy-mode").value = "fresh";
  await s.find("project-copy-mode").emit("change");
  assert.equal(s.find("project-keep-prompts-row").hidden, false);
  await s.click("project-copy-confirm");
  assert.equal(s.copies[1].keepSavedPrompts, false);
  await s.click("project-save-as"); s.find("project-copy-mode").value = "fresh";
  s.find("project-keep-prompts").checked = true;
  await s.click("project-copy-confirm");
  assert.equal(s.copies[2].keepSavedPrompts, true);
  await s.click("project-save-as");
  assert.equal(s.find("project-copy-mode").value, "full");
  assert.equal(s.find("project-keep-prompts").checked, false);
});

test("Clear is confirmed explicitly, preserves prompts by default, and rejects drafts", async () => {
  const s = fixture(); await s.click("project-clear-generated");
  assert.deepEqual(s.clears, []);
  assert.equal(s.find("project-clear-prompts").checked, false);
  const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
  const confirmation = html.match(/<dialog id="project-clear-dialog"[\s\S]*?<\/dialog>/)[0];
  assert.match(confirmation, /external ComfyUI output directory/);
  assert.match(confirmation, /Project authoring, Reference Assets and workflow setup are preserved/);
  await s.click("project-clear-cancel"); assert.deepEqual(s.clears, []);
  await s.click("project-clear-generated"); await s.click("project-clear-confirm");
  assert.deepEqual(s.clears, [false]);
  await s.click("project-clear-generated"); s.find("project-clear-prompts").checked = true;
  await s.click("project-clear-confirm"); assert.deepEqual(s.clears, [false, true]);
  s.context.dirty = true; await s.click("project-clear-generated");
  assert.equal(s.find("project-clear-dialog").open, false);
  assert.match(s.notices.at(-1), /Save or discard/);
  s.context.saved = false; await s.click("project-clear-generated");
  assert.match(s.notices.at(-1), /filesystem Project/);
});

test("request APIs forward copy policy and required Clear fingerprint", async () => {
  const prior = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, JSON.parse(options.body)]); return { ok: true, json: async () => ({}) }; };
  try {
    await api.saveProjectAs("p", "Fresh", undefined, undefined, undefined, undefined, undefined,
      { expectedProjectFingerprint: "f".repeat(64) }, undefined, undefined, undefined, undefined,
      { copyMode: "fresh", keepSavedPrompts: true });
    await api.clearGeneratedState("p", "f".repeat(64));
    await api.clearGeneratedState("p", "f".repeat(64), true);
    assert.equal(calls[0][1].copyMode, "fresh"); assert.equal(calls[0][1].keepSavedPrompts, true);
    assert.deepEqual(calls[1], ["/api/projects/p/clear-generated-state", { expectedProjectFingerprint: "f".repeat(64), clearSavedPrompts: false }]);
    assert.equal(calls[2][1].clearSavedPrompts, true);
  } finally { globalThis.fetch = prior; }
});

test("Fresh/reset adoption clears candidates and reloads retained history without restoring it", () => {
  const resets = [], loads = [], state = { outputs: [{ prompt: "old generated text", validation: { valid: true } }] };
  const sandbox = vm.createContext({ state, window: { history: { replaceState() {} } },
    navigationAuthority: { invalidate() {} },
    sequenceRenderAction: { reset: () => resets.push("sequence") },
    savedPrompts: { reset: () => resets.push("saved"), rememberCurrent: () => resets.push("remember"), load: options => loads.push(options) },
    renderedTakes: { reset: () => resets.push("takes"), sync() {} },
    workflowPreparation: { reset: () => resets.push("preparation"), sync() {} },
    invalidateProviderContext: () => resets.push("provider"), rememberProjectLocation() {},
    restoreWorkspace: () => { state.outputs = []; }, restoreContinuity() {}, restorePlanning() {},
    storyPlanner: { restoreStoryIdea() {}, restoreScenePlan() {}, restoreStoryCreativeDirection() {},
      restoreStoryPlanningStyle() {}, restoreStoryMemory() {} },
    sequenceFromProject: p => p.sequence, setDirty() {}, renderProjectOptions() {}, renderSequenceControls() {},
    renderContinuityUi() {}, requestReferenceManifestRefresh() {},
  });
  const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const source = app.slice(app.indexOf("function adoptProjectResponse("), app.indexOf("function renderSequenceControls("));
  vm.runInContext(source, sandbox);
  sandbox.project = { id: "fresh", name: "Fresh", projectFingerprint: "new-fingerprint", workspace: {},
    sequence: { generations: [{ generationId: "active" }], activeGenerationId: "active" } };
  vm.runInContext("adoptProjectResponse(project, { generatedReset: true })", sandbox);
  assert.deepEqual(resets, ["sequence", "takes", "preparation", "saved", "provider"]);
  assert.equal(loads.length, 1); assert.equal(loads[0].restore, false);
  assert.deepEqual(state.outputs, []); assert.equal(state.projectFingerprint, "new-fingerprint");
  assert.equal(state.activeGenerationId, "active"); assert.equal(state.contextReplacing, false);
});
