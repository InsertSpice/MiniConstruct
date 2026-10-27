import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
import { mountHtml } from "./support/html.mjs";
import { Element } from "./support/dom.mjs";
import { createSavedPrompts } from "../../web/js/saved-prompts.js";
import { applyRevisionCandidate } from "../../web/js/revision.js";
import { currentFormatAudit } from "../../web/js/history-preview.js";
import { markOutputManuallyEdited } from "../../web/js/manual-edit.js";

function fixture({ runtime = false } = {}) {
  const doc = { createElement: tag => new Element(tag, doc) };
  const root = runtime ? mountHtml(readFileSync(new URL("../../web/index.html", import.meta.url), "utf8"), doc) : new Element("div", doc);
  if (!runtime) for (const id of ["save-h3-prompt", "save-h3-prompt-as-new", "saved-h3-prompts", "load-saved-h3-prompt", "delete-saved-h3-prompt", "saved-h3-prompt-status", "saved-h3-prompt-details"]) {
    const element = new Element(id.endsWith("status") ? "span" : id === "saved-h3-prompts" ? "select" : "button", doc);
    element.id = id; root.append(element);
  }
  const libraries = new Map(), calls = [], notifications = [], confirmations = [];
  const context = { saved: true, projectId: "project", generationId: "G1" };
  let output = null, fingerprint = "X", agreed = true, nextId = 0;
  const library = g => { if (!libraries.has(g)) libraries.set(g, { prompts: [], activeSavedPromptId: null, nextSaveNumber: 1 }); return libraries.get(g); };
  const response = g => ({ ...structuredClone(library(g)), prompts: library(g).prompts.map(item => ({ ...item, stale: item.sourceFingerprint !== fingerprint })).reverse() });
  const api = {
    savedH3Prompt: async (p, g) => { calls.push(["get", p, g]); return response(g); },
    saveH3Prompt: async (p, g, body) => {
      calls.push(["save", p, g, body]); const l = library(g);
      let saved = body.savedPromptId ? l.prompts.find(item => item.id === body.savedPromptId)
        : body.saveAsNew ? null : l.prompts.find(item => item.prompt === body.prompt);
      const alreadySaved = Boolean(saved) && !body.savedPromptId;
      if (body.savedPromptId) {
        if (!saved) throw new Error("Saved H3 prompt does not belong to this Generation");
        Object.assign(saved, { prompt: body.prompt, authoringPath: body.authoringPath, sourceFingerprint: fingerprint });
      }
      if (!saved && l.prompts.length >= 100) throw new Error("Delete an older entry before saving another.");
      if (!saved) { saved = { ...body, id: `id-${++nextId}`, saveNumber: l.nextSaveNumber++, sourceFingerprint: fingerprint, sha256: `hash-${nextId}`, savedAt: "2026-10-03T00:00:00Z" }; l.prompts.push(saved); }
      l.activeSavedPromptId = saved.id; return { ...response(g), alreadySaved };
    },
    loadH3Prompt: async (p, g, id) => { calls.push(["load", p, g, id]); library(g).activeSavedPromptId = id; return response(g); },
    deleteH3Prompt: async (p, g, id) => {
      calls.push(["delete", p, g, id]); const l = library(g);
      l.prompts = l.prompts.filter(item => item.id !== id);
      if (l.activeSavedPromptId === id) l.activeSavedPromptId = null;
      return response(g);
    },
  };
  const mount = () => createSavedPrompts({ root, api, getContext: () => context,
    getOutput: () => output, setOutput: value => { output = value; }, notify: message => notifications.push(message),
    confirmReplace: message => { confirmations.push(message); return agreed; } });
  const status = () => root.querySelector("#saved-h3-prompt-status").textContent;
  const click = id => root.querySelector(`#${id}`).emit("click");
  const choose = async id => { root.querySelector("#saved-h3-prompts").value = id; await root.querySelector("#saved-h3-prompts").emit("change"); };
  return { root, libraries, library, calls, notifications, confirmations, api, context, mount, status, click, choose,
    get output() { return output; }, set output(value) { output = value; },
    set fingerprint(value) { fingerprint = value; }, set agreed(value) { agreed = value; } };
}

test("reset refreshes retained library without auto-restoring historical text or cached candidate", async () => {
  const f = fixture(), ui = f.mount();
  await ui.load(); f.output = { prompt: "saved history", authoringPath: "one_shot" };
  ui.sync();
  await ui.saveCurrent(); ui.rememberCurrent();
  ui.reset(); f.output = null;
  await ui.load({ restore: false });
  assert.equal(f.output, null); assert.equal(f.library("G1").prompts.length, 1);
  await ui.loadSelected(); assert.equal(f.output.prompt, "saved history");
  assert.equal(f.output.validation, null); assert.equal(f.output.sourceFingerprint, null);
});

test("first filesystem Save adopts the collection without replacing the generated candidate", async () => {
  const f = fixture(); f.context.saved = false; f.context.projectId = null; f.context.generationId = null;
  const ui = f.mount(); f.output = { prompt: "  generated 雪\n", authoringPath: "one_shot" };
  const original = f.output;
  const state = { activeProjectType: null, outputs: [original], outputIndex: 0, assets: [], filesystemProjects: [] };
  const saved = { projectId: "project", name: "Project", sequence: true,
    response: { workspace: {}, sequence: {}, projectFingerprint: "saved" } };
  const controls = { value: "Project" };
  const sandbox = vm.createContext({ state, referenceMutation: null, document: { querySelectorAll: () => [] }, $: () => controls,
    savedPrompts: ui, api: {}, storyPlanner: { confirmContextChange: () => true, resetProjectContext() {},
      restoreStoryIdea() {}, restoreStoryCreativeDirection() {}, restoreStoryPlanningStyle() {}, restoreStoryMemory() {} },
    persistenceWorkspace: () => ({ projectName: "Project" }), captureContinuity: () => ({}), capturePlanning: () => ({}),
    characterProfileUpdates: () => [], resolveProjectSave: () => ({ create: true }), resolveProjectSaveRoute: () => "create",
    invalidateProviderContext() {}, persistFilesystemProject: async () => saved, adoptSavedMedia() {},
    rememberProjectLocation() {}, clearImportedSequenceMetadata() {}, sequenceFromProject: () => ({ generations: [], activeGenerationId: "G1" }),
    renderAssets() {}, setDirty: () => {
      Object.assign(f.context, { saved: state.activeProjectType === "filesystem", projectId: state.currentProjectId, generationId: state.activeGenerationId });
      ui.sync();
    }, refreshProjects: async () => {}, toast() {},
  });
  const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const saveFunction = app.match(/async function saveProject\([^\n]*\) \{[\s\S]*?\n\}/)[0];
  vm.runInContext(saveFunction, sandbox);
  await vm.runInContext("saveProject()", sandbox);
  assert.equal(f.calls.filter(call => call[0] === "get").length, 1);
  assert.equal(f.root.querySelector("#save-h3-prompt").disabled, false);
  assert.equal(f.output, original);
  assert.doesNotMatch(f.status(), /Loading/);
  await ui.saveCurrent(); assert.equal(f.calls.at(-1)[3].prompt, original.prompt);
});

test("same-context execution mutations preserve in-flight and loaded collections without reload loops", async () => {
  const f = fixture(), ui = f.mount();
  f.output = { prompt: "visible generated candidate" };
  let resolve;
  f.api.savedH3Prompt = () => { f.calls.push(["get"]); return new Promise(done => { resolve = done; }); };
  const pending = ui.ensureLoaded();
  for (let i = 0; i < 3; i++) {
    f.context.projectFingerprint = `execution-${i}`; ui.sync(); ui.ensureLoaded();
  }
  assert.equal(f.calls.length, 1); assert.equal(f.status(), "Loading saved prompts…");
  resolve({ prompts: [], activeSavedPromptId: null }); await pending;
  f.context.projectFingerprint = "sent"; ui.sync(); await ui.ensureLoaded();
  assert.equal(f.calls.length, 1); assert.equal(f.root.querySelector("#save-h3-prompt").disabled, false);
  await ui.saveCurrent(); assert.equal(f.calls.at(-1)[3].prompt, "visible generated candidate");
});

test("loading status belongs only to an actual current request; failed and stale loads are truthful", async () => {
  const f = fixture(), ui = f.mount();
  assert.doesNotMatch(f.status(), /Loading/);
  let resolve;
  f.api.savedH3Prompt = (p, g) => g === "G1" ? new Promise(done => { resolve = done; })
    : Promise.resolve({ prompts: [], activeSavedPromptId: null });
  const old = ui.load(); f.context.generationId = "G2"; ui.sync();
  assert.doesNotMatch(f.status(), /Loading/);
  await ui.load(); f.output = { prompt: "G2 candidate" }; ui.sync();
  resolve({ prompts: [{ id: "old", prompt: "G1 saved" }], activeSavedPromptId: "old" }); await old;
  assert.equal(f.root.querySelector("#saved-h3-prompts").children.length, 0);
  assert.equal(f.output.prompt, "G2 candidate");
  assert.equal(f.root.querySelector("#save-h3-prompt").disabled, false);
  f.api.savedH3Prompt = async () => { throw new Error("disk failure"); };
  await ui.load(); assert.match(f.status(), /Could not load.*disk failure/); assert.doesNotMatch(f.status(), /Loading/);
  f.api.savedH3Prompt = async () => ({ prompts: [], activeSavedPromptId: null });
  await ui.ensureLoaded(); assert.equal(f.root.querySelector("#save-h3-prompt").disabled, false);
});

test("toolbar and right-hand panel expose explicit saved snapshots and compact actions", () => {
  const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(html, /id="download-output"[^>]*>Save \.txt<\/button>/);
  assert.match(html, /id="save-h3-prompt"[^>]*title="Update the loaded saved prompt, or create its first save\."[^>]*>Save H3 Prompt/);
  assert.match(html, /<label for="saved-h3-prompts">Saved H3 Prompts/);
  assert.match(html, /<select id="saved-h3-prompts"/);
  assert.match(html, /id="load-saved-h3-prompt"[^>]*>Load/);
  assert.match(html, /id="delete-saved-h3-prompt"[^>]*>Delete/);
  assert.doesNotMatch(html, /Clear Saved Draft|Save H3 Draft|Load History|clear-history|history-list/);
  const css = readFileSync(new URL("../../web/css/app.css", import.meta.url), "utf8");
  assert.doesNotMatch(html.match(/<select id="saved-h3-prompts"[^>]*>/)[0], /\bsize=/);
  assert.match(html, /id="load-saved-h3-prompt" class="quiet"/);
  assert.match(html, /id="delete-saved-h3-prompt" class="quiet"/);
  assert.match(css, /\.saved-prompt-preview[^}]*white-space: normal;[^}]*overflow-wrap: anywhere/);
  assert.doesNotMatch(css.match(/\.saved-prompt-details \{[^}]+\}/)[0], /(?:min-)?height:/);
  assert.match(css, /\.saved-prompt-actions[^}]*flex-wrap: wrap/);
});

test("model generation and repair never write browser History; legacy IndexedDB remains intact", () => {
  const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.doesNotMatch(app, /addHistory|renderHistory|clearHistoryDb|listHistory|saveCompletedGenerationHistory/);
  const db = readFileSync(new URL("../../web/js/db.js", import.meta.url), "utf8");
  assert.match(db, /export async function listHistory/);
  assert.doesNotMatch(db, /deleteObjectStore/);
});

test("exact manual text appends, duplicate selects original, remount restores active safely", async () => {
  const f = fixture(); let ui = f.mount(); await ui.load();
  f.output = markOutputManuallyEdited({ prompt: "generated", sourceFingerprint: "X" }, "  <H3>\n雪\n</H3>  ");
  ui.sync(); await f.click("save-h3-prompt");
  const first = f.library("G1").prompts[0];
  assert.equal(f.calls.at(-1)[3].prompt, "  <H3>\n雪\n</H3>  ");
  f.output = { prompt: "B" }; ui.sync(); await ui.saveCurrent();
  assert.equal(f.library("G1").prompts.length, 2);
  assert.equal(f.root.querySelector("#saved-h3-prompts").children[0].value, "id-2");
  f.output = { prompt: first.prompt }; ui.sync(); await ui.saveCurrent();
  assert.equal(f.library("G1").prompts.length, 2);
  assert.match(f.notifications.at(-1), /This exact H3 prompt is already saved/);
  assert.equal(f.root.querySelector("#saved-h3-prompts").value, first.id);
  f.output = null; ui = f.mount(); await ui.load();
  assert.equal(f.output.prompt, first.prompt); assert.equal(f.output.origin, "manual_edit");
  assert.equal(f.output.sourceFingerprint, null); assert.equal(f.output.validation, null);
  assert.equal(f.status(), "Saved prompt loaded");
  assert.deepEqual(f.calls.filter(c => c[0] !== "get").map(c => c[0]), ["save", "save", "save"]);
});

test("selection is inspection only; dirty Load confirms and preserves candidate on refusal", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  f.output = { prompt: "B" }; ui.sync(); await ui.saveCurrent();
  f.output = markOutputManuallyEdited(f.output, "unsaved C"); ui.sync();
  await f.choose("id-1"); assert.equal(f.output.prompt, "unsaved C");
  assert.equal(f.library("G1").activeSavedPromptId, "id-2");
  f.agreed = false; await ui.loadSelected();
  assert.equal(f.output.prompt, "unsaved C"); assert.equal(f.calls.filter(c => c[0] === "load").length, 0);
  f.agreed = true; await ui.loadSelected();
  assert.equal(f.output.prompt, "A"); assert.equal(f.output.origin, "manual_edit");
  assert.equal(f.library("G1").activeSavedPromptId, "id-1");
  assert.deepEqual(f.calls.at(-1), ["load", "project", "G1", "id-1"]);
  assert.equal(f.confirmations.length, 2);
});

test("visible text matching any snapshot is saved, active delete leaves text and clears restore", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  for (const prompt of ["A", "B"]) { f.output = { prompt }; ui.sync(); await ui.saveCurrent(); }
  f.output = { prompt: "A" }; ui.sync(); assert.equal(f.status(), "Saved prompt loaded");
  f.output = { prompt: "B" }; await f.choose("id-2"); await ui.deleteSelected();
  assert.equal(f.output.prompt, "B"); assert.equal(f.library("G1").activeSavedPromptId, null);
  assert.equal(f.library("G1").prompts.length, 1);
  f.output = null; const reopened = f.mount(); await reopened.load();
  assert.equal(f.output, null); // Never load another saved item after active deletion.
});

test("Generation lists isolate A/B and C while memory preserves newer unsaved edits", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  for (const prompt of ["A", "B"]) { f.output = { prompt }; ui.sync(); await ui.saveCurrent(); }
  f.output = { prompt: "B edit" }; ui.sync(); ui.rememberCurrent();
  f.context.generationId = "G2"; f.output = null; await ui.load();
  assert.equal(f.status(), "No saved prompts"); assert.equal(f.root.querySelector("#saved-h3-prompts").children.length, 0);
  f.output = { prompt: "C" }; ui.sync(); await ui.saveCurrent(); ui.rememberCurrent();
  f.context.generationId = "G1"; f.output = null; await ui.load();
  assert.equal(f.output.prompt, "B edit"); assert.equal(f.status(), "Unsaved changes");
  assert.deepEqual(f.root.querySelector("#saved-h3-prompts").children.map(x => x.value), ["id-2", "id-1"]);
  f.context.generationId = "G2"; f.output = null; await ui.load();
  assert.equal(f.output.prompt, "C"); assert.equal(f.root.querySelector("#saved-h3-prompts").children.length, 1);
});

test("stale sources retain snapshots; previews are compact and timestamps/path are local", async () => {
  const f = fixture(); let ui = f.mount(); await ui.load();
  f.output = { prompt: "雪".repeat(1000), authoringPath: "story" }; ui.sync(); await ui.saveCurrent();
  f.fingerprint = "Y"; f.output = null; ui = f.mount(); await ui.load();
  assert.match(f.status(), /saved source is stale/);
  const option = f.root.querySelector("#saved-h3-prompts").children[0];
  assert.ok(option.textContent.length < 100); assert.match(option.textContent, /Save 1.*2026.*Story.*Stale/);
  assert.doesNotMatch(option.textContent, /雪/);
  const details = f.root.querySelector("#saved-h3-prompt-details");
  assert.match(details.textContent, /Save 1.*Active restore prompt.*Story.*Stale source/);
  assert.equal(details.querySelector(".saved-prompt-preview").textContent, "雪".repeat(240) + "…");
  assert.equal(f.library("G1").prompts[0].prompt.length, 1000);
});

test("late restore and a Generation switch never replace newer candidates", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  f.output = null; const loading = ui.load(); f.output = { prompt: "generated B" }; await loading;
  assert.equal(f.output.prompt, "generated B");
  f.output = null; const other = ui.load(); f.context.generationId = "G2"; await other;
  assert.equal(f.output, null);
});

test("busy provider work disables replacement; save/delete errors preserve snapshots and detail", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  f.context.candidateBusy = true; ui.sync();
  assert.equal(f.root.querySelector("#load-saved-h3-prompt").disabled, true);
  await ui.loadSelected(); assert.equal(f.calls.filter(x => x[0] === "load").length, 0);
  f.context.candidateBusy = false; ui.sync();
  f.api.saveH3Prompt = async () => { throw new Error("disk detail"); };
  await ui.saveCurrent(); assert.equal(f.notifications.at(-1), "Could not save H3 prompt: disk detail");
  f.api.deleteH3Prompt = async () => { throw new Error("delete detail"); };
  await ui.deleteSelected(); assert.equal(f.notifications.at(-1), "Could not delete saved H3 prompt: delete detail");
  assert.equal(f.output.prompt, "A"); assert.equal(f.library("G1").prompts.length, 1);
});


test("stable save numbers appear newest first while inspection details change no authority", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  for (const prompt of ["A", "B", "C"]) { f.output = { prompt }; ui.sync(); await ui.saveCurrent(); }
  await f.choose("id-2"); await ui.deleteSelected();
  f.output = { prompt: "D" }; ui.sync(); await ui.saveCurrent();
  const options = f.root.querySelector("#saved-h3-prompts").children;
  assert.deepEqual(options.map(item => item.textContent.match(/Save (\d+)/)[1]), ["4", "3", "1"]);
  await f.choose("id-1");
  assert.equal(f.output.prompt, "D"); assert.equal(f.library("G1").activeSavedPromptId, "id-4");
  const details = f.root.querySelector("#saved-h3-prompt-details");
  assert.match(details.textContent, /Save 1/); assert.doesNotMatch(details.textContent, /Active restore prompt/);
  assert.equal(f.calls.filter(call => call[0] === "load").length, 0);
});

test("Save updates loaded lineage; inspection cannot retarget; Save As New always appends", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  assert.ok(f.root.querySelector("#save-h3-prompt"));
  assert.ok(f.root.querySelector("#save-h3-prompt-as-new"));
  f.output = { prompt: "A" }; ui.sync(); await f.click("save-h3-prompt");
  await ui.loadSelected();
  f.output = markOutputManuallyEdited(f.output, "B"); ui.sync(); await f.click("save-h3-prompt");
  assert.equal(f.library("G1").prompts.length, 1);
  assert.equal(f.library("G1").prompts[0].id, "id-1");
  assert.equal(f.library("G1").prompts[0].saveNumber, 1);
  assert.equal(f.notifications.at(-1), "Updated Save 1");
  assert.match(f.root.querySelector("#saved-h3-prompt-details").textContent, /B/);
  f.output = markOutputManuallyEdited(f.output, "C"); ui.sync(); await f.click("save-h3-prompt-as-new");
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["B", "C"]);
  assert.equal(f.output.savedPromptTarget.id, "id-2");
  assert.equal(f.notifications.at(-1), "Saved as Save 2");
  await f.choose("id-1");
  f.output = markOutputManuallyEdited(f.output, "D"); ui.sync(); await ui.saveCurrent();
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["B", "D"]);
  await f.choose("id-1"); await ui.loadSelected();
  f.output = markOutputManuallyEdited(f.output, "loaded edit"); ui.sync(); await ui.saveCurrent();
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["loaded edit", "D"]);
  assert.deepEqual(f.root.querySelector("#saved-h3-prompts").children.map(o => o.value), ["id-2", "id-1"]);
  await f.click("save-h3-prompt-as-new");
  assert.equal(f.library("G1").prompts.length, 3);
  assert.equal(f.output.savedPromptTarget.id, "id-3");
  await ui.saveCurrent(); assert.equal(f.library("G1").prompts.length, 3);
});

test("delete clears only its target; capacity allows updates but disables creation and empty saves", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  for (const id of ["save-h3-prompt", "save-h3-prompt-as-new"]) assert.equal(f.root.querySelector(`#${id}`).disabled, true);
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  await ui.deleteSelected();
  assert.equal(f.output.prompt, "A"); assert.equal(f.output.savedPromptTarget, undefined);
  await ui.saveCurrent(); assert.equal(f.library("G1").prompts[0].saveNumber, 2);
  assert.equal(f.output.savedPromptTarget.id, "id-2");
  for (let i = 1; i < 100; i++) {
    f.output = { prompt: `text ${i}` }; ui.sync(); await ui.saveCurrent();
  }
  assert.equal(f.root.querySelector("#save-h3-prompt").disabled, false);
  assert.equal(f.root.querySelector("#save-h3-prompt-as-new").disabled, true);
  assert.match(f.root.querySelector("#save-h3-prompt-as-new").title, /Delete an older entry/);
  f.output = markOutputManuallyEdited(f.output, "capacity edit"); ui.sync(); await ui.saveCurrent();
  assert.equal(f.library("G1").prompts.length, 100);
  assert.equal(f.library("G1").prompts.at(-1).prompt, "capacity edit");
  f.output = { prompt: "independent candidate" }; ui.sync();
  assert.equal(f.root.querySelector("#save-h3-prompt").disabled, true);
});

test("restore retains scoped edit target; new candidates take precedence and never borrow restore authority", async () => {
  const f = fixture(); let ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  f.output = null; ui = f.mount(); await ui.load();
  assert.equal(f.output.savedPromptTarget.id, "id-1");
  f.output = markOutputManuallyEdited(f.output, "in-memory edit"); ui.rememberCurrent();
  f.context.generationId = "G2"; f.output = null; await ui.load();
  f.output = { prompt: "G2" }; ui.sync(); await ui.saveCurrent(); ui.rememberCurrent();
  f.context.generationId = "G1"; f.output = null; await ui.load();
  await ui.saveCurrent(); assert.equal(f.library("G1").prompts[0].prompt, "in-memory edit");
  assert.equal(f.library("G2").prompts[0].prompt, "G2");
  for (const operation of ["Generate", "Regenerate"]) {
    f.output = { prompt: operation }; ui.sync(); await ui.saveCurrent();
  }
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["in-memory edit", "Generate", "Regenerate"]);
  f.output = { prompt: "newer unsaved" }; await ui.load(); await ui.saveCurrent();
  assert.equal(f.library("G1").prompts.length, 4);
  // Even an incorrectly carried candidate cannot update an entry in another Generation.
  f.context.generationId = "G2"; await ui.load(); await ui.saveCurrent();
  assert.equal(f.library("G2").prompts.length, 2);
  assert.equal(f.library("G1").prompts.length, 4);
});

test("late Save and Load responses cannot attach target authority to replacement candidates", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  const originalSave = f.api.saveH3Prompt; let release;
  f.api.saveH3Prompt = async (...args) => { const response = await originalSave(...args); await new Promise(r => { release = r; }); return response; };
  const pending = ui.saveCurrent(); await Promise.resolve(); await Promise.resolve();
  f.output = { prompt: "A" }; release(); await pending;
  assert.equal(f.output.savedPromptTarget, undefined);
  const originalLoad = f.api.loadH3Prompt;
  f.api.loadH3Prompt = async (...args) => { const response = await originalLoad(...args); await new Promise(r => { release = r; }); return response; };
  const loading = ui.loadSelected(); await Promise.resolve(); await Promise.resolve();
  const generated = { prompt: "A" }; f.output = generated; release(); await loading;
  assert.equal(f.output, generated); assert.equal(f.output.savedPromptTarget, undefined);
});

test("applied Revision, Format Audit and real Repair handler preserve loaded save identity", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A", authoringPath: "one_shot" }; ui.sync(); await ui.saveCurrent(); await ui.loadSelected();
  const original = f.output;
  assert.equal(applyRevisionCandidate([original], 0, { originIndex: 0, originOutput: original, sourcePrompt: "A",
    candidatePrompt: "revision", authoringPath: "one_shot", sourceFingerprint: "revision fingerprint", validation: { findings: [] } }), true);
  ui.sync(); await ui.saveCurrent();
  assert.equal(f.library("G1").prompts[0].prompt, "revision");
  f.output = currentFormatAudit(f.output, { findings: [] });
  assert.equal(f.output.savedPromptTarget.id, "id-1");
  const state = { outputs: [f.output], outputIndex: 0, repairRunning: false };
  const sandbox = vm.createContext({ state, AbortController, providerContextEpoch: 1,
    providerActivity: { active: () => false, finish() {} }, providerAttempt: () => ({}), canStartRepair: () => true,
    providerContextCurrent: () => true, syncReferenceAuthority: async () => {}, buildGenerateRequest: () => ({}),
    $: () => ({ value: f.output.prompt }), api: { repair: async () => ({ prompt: "repaired", sourceFingerprint: "repair fingerprint", authoringPath: "one_shot" }) },
    markRevisionStale() {}, renderWarnings() {}, updateEjectButton() {}, toast: message => { assert.doesNotMatch(message, /not defined/); },
    renderOutput: () => { f.output = state.outputs[state.outputIndex]; ui.sync(); } });
  const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  vm.runInContext(app.slice(app.indexOf("async function repairCurrent()"), app.indexOf("async function showInstructions()")), sandbox);
  await sandbox.repairCurrent();
  assert.equal(f.output.prompt, "repaired"); assert.equal(f.output.savedPromptTarget.id, "id-1");
  assert.equal(f.output.sourceFingerprint, "repair fingerprint");
  await ui.saveCurrent();
  assert.equal(f.library("G1").prompts.length, 1); assert.equal(f.library("G1").prompts[0].prompt, "repaired");
});

test("real Generate and Regenerate handler create independent candidates and first saves", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "saved A" }; ui.sync(); await ui.saveCurrent();
  const state = { outputs: [f.output], outputIndex: 0, activeProjectType: "filesystem" };
  const controls = { classList: { add() {} } }, errors = [];
  let next = 0;
  const sandbox = vm.createContext({ state, AbortController, providerContextEpoch: 1,
    providerActivity: { active: () => false, finish() {} }, providerAttempt: () => ({}), generationClickIntent: () => "start",
    providerContextCurrent: () => true, syncReferenceAuthority: async () => {}, persistSettings() {}, ensureDialogueResolution: async () => {},
    buildGenerateRequest: () => ({ workspace: { variations: 1 }, executionRouting: {} }), snapshotGenerationRequest: x => x,
    $: () => controls, api: { streamGenerate: async (_request, { onEvent }) => {
      onEvent({ event: "complete", data: { variation: 0, prompt: `generated ${++next}`, sourceFingerprint: "new fingerprint" } });
    } }, renderPerformance() {}, setGenerationStatus() {}, setGenerationControls() {},
    markRevisionStale() {}, renderWarnings() {}, updateEjectButton() {}, toast: message => errors.push(message), workflowPreparation: null, renderedTakes: null,
    renderOutput: () => { f.output = state.outputs[state.outputIndex]; ui.sync(); } });
  const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  vm.runInContext(app.slice(app.indexOf('async function generate(authoringPath'), app.indexOf('function renderWarnings(')), sandbox);
  for (const action of ["Generate", "Regenerate"]) {
    // Both toolbar actions invoke this same handler; Regenerate supplies current authoringPath.
    await sandbox.generate(action === "Regenerate" ? f.output.authoringPath : "one_shot");
    assert.deepEqual(errors, []);
    assert.equal(f.output.savedPromptTarget, undefined); assert.equal(f.output.generationStatus, "complete");
    await ui.saveCurrent();
  }
  assert.deepEqual(errors, []);
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["saved A", "generated 1", "generated 2"]);
});

test("Project copy restoration scopes its target to copied ownership; reset clears authority", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "source" }; ui.sync(); await ui.saveCurrent();
  f.context.projectId = "copied-project"; f.output = null; await ui.load();
  assert.deepEqual(f.output.savedPromptTarget, { key: "copied-project/G1", id: "id-1" });
  ui.reset(); assert.equal(f.output.savedPromptTarget, undefined);
  f.output = null; await ui.load({ restore: false }); assert.equal(f.output, null);
});

test("runtime page mounts adjacent Save actions with empty, first-save, update and Save As states", async () => {
  const f = fixture({ runtime: true }), ui = f.mount(); await ui.load();
  const save = f.root.querySelector("#save-h3-prompt"), asNew = f.root.querySelector("#save-h3-prompt-as-new");
  assert.equal(save.parentElement, asNew.parentElement);
  assert.equal(save.parentElement.children.indexOf(asNew), save.parentElement.children.indexOf(save) + 1);
  assert.equal(save.disabled, true); assert.equal(asNew.disabled, true);
  f.output = { prompt: "A" }; ui.sync(); await save.emit("click");
  f.output = markOutputManuallyEdited(f.output, "B"); ui.sync(); await save.emit("click");
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["B"]);
  await asNew.emit("click");
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["B", "B"]);
  assert.equal(f.root.querySelector("#saved-h3-prompts").value, "id-2");
  assert.match(f.root.querySelector("#saved-h3-prompt-details").textContent, /Save 2.*B/);
});

test("refresh clears an externally deleted target without resurrecting it", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync(); await ui.saveCurrent();
  f.library("G1").prompts = []; f.library("G1").activeSavedPromptId = null;
  // A direct stale update fails safely; a subsequent refresh confirms deletion and clears authority.
  await ui.saveCurrent(); assert.match(f.notifications.at(-1), /does not belong/);
  await ui.load(); assert.equal(f.output.savedPromptTarget, undefined);
  await ui.saveCurrent();
  assert.equal(f.library("G1").prompts[0].saveNumber, 2);
  assert.equal(f.library("G1").prompts[0].id, "id-2");
});

test("manual edits during first Save and Save As New inherit the successful saved identity", async () => {
  const f = fixture(), ui = f.mount(); await ui.load();
  f.output = { prompt: "A" }; ui.sync();
  const save = f.api.saveH3Prompt; let release;
  f.api.saveH3Prompt = async (...args) => { const result = await save(...args); await new Promise(r => { release = r; }); return result; };
  let saving = ui.saveCurrent(); await Promise.resolve(); await Promise.resolve();
  f.output = markOutputManuallyEdited(f.output, "B"); release(); await saving;
  assert.equal(f.output.savedPromptTarget.id, "id-1");
  saving = ui.saveCurrent(true); await Promise.resolve(); await Promise.resolve();
  f.output = markOutputManuallyEdited(f.output, "C"); release(); await saving;
  assert.equal(f.output.savedPromptTarget.id, "id-2");
  f.api.saveH3Prompt = save; await ui.saveCurrent();
  assert.deepEqual(f.library("G1").prompts.map(p => p.prompt), ["A", "C"]);
});
