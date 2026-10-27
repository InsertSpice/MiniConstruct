import assert from "node:assert/strict";
import test from "node:test";
import { Element } from "./support/dom.mjs";
import { createRenderedTakes } from "../../web/js/rendered-takes.js";
import { api as realApi } from "../../web/js/api.js";
import { beginProviderActivity, createProviderActivity } from "../../web/js/provider-activity.js";

const effective = summary => ({ summary, environment: "Room", subjectStates: [{ subjectId: "kent", visibility: "visible",
  appearance: "", wardrobe: "red jacket on", condition: "", positionPose: "standing", heldObjects: "", currentAction: "turning" }],
  propsObjects: ["door open"], actionMomentum: "turning", cameraState: "medium shot", lightingConditions: "daylight",
  plannedOutcomeAssessment: { status: "conflicts", explanation: "jacket remains on" }, unresolvedVisibleEntities: [] });
const record = (id, summary) => ({ takeId: id, takeSha256: id, analyzedAt: "today", analyzerModel: "vision",
  subjectLabels: { kent: "Kent / Subject 1" }, status: "model_observed", modelObservedState: effective(summary), effectiveObservedState: effective(summary) });
function setup(options = {}) {
  const doc = { createElement: tag => new Element(tag, doc) }, root = new Element("div", doc);
  const context = { saved: true, projectId: "p", generationId: "g1", projectFingerprint: "fp" }, settings = { supportsVision: true };
  const state = { takes: ["a", "b", "c"].map(id => ({ id, sha256: id, originalFilename: `${id}.mp4`,
    sourceKind: "external_unknown", promptProvenance: "unknown", availability: "available", byteSize: 1024 })),
    selectedTakeId: null, takeObservations: [], renderSubmissions: [], projectFingerprint: "fp" };
  let allow = true, pending = null;
  const calls = [], analysisSignals = [], mutations = [], receipt = () => structuredClone(state);
  const api = { ...realApi,
    foregroundRenders: async () => ({ candidate: null, watched: null }),
    generationOutputs: async () => context.generationId === "g1" ? receipt() : { takes: [], renderSubmissions: [], selectedTakeId: null },
    executionSelection: async () => ({ selected: null }),
    selectTake: async (p, g, id) => { state.selectedTakeId = id; return receipt(); },
    analyzeSelectedTake: async (p, g, body, signal) => {
      analysisSignals.push(signal);
      const id = state.selectedTakeId; calls.push(["analyze", body]);
      if (pending) await pending;
      state.takeObservations = state.takeObservations.filter(o => o.takeId !== id).concat(record(id, `Observed ${id}`));
      return receipt();
    },
    correctObservation: async (p, g, id, body) => {
      calls.push(["correct", id, body]); const o = state.takeObservations.find(o => o.takeId === id);
      o.effectiveObservedState = structuredClone(body.reset ? o.modelObservedState : body.effectiveObservedState);
      o.status = body.reset ? "model_observed" : "user_edited"; return receipt();
    },
  };
  const providerActivity = options.providerActivity || createProviderActivity();
  const ui = createRenderedTakes({ root, api, providerActivity, getContext: () => context, getSettings: () => settings,
    setTimer: () => null, clearTimer: () => {},
    confirmDelete: () => allow, onMutation: (r, c) => mutations.push([r, c]), ...options });
  const find = id => root.querySelector(`#${id}`);
  const inspect = async id => { find("takes-inspected").value = id; await find("takes-inspected").emit("change"); };
  const click = async id => {
    const action = id.match(/^takes-(?:select|preview|delete|provenance)-(.+)$/);
    if (action && find("takes-inspected").value !== action[1]) await inspect(action[1]);
    assert.equal(find(id).disabled, false); await find(id).emit("click");
  };
  return { root, context, settings, state, api, calls, analysisSignals, mutations, providerActivity, ui, find, click, inspect,
    setAllow: value => allow = value, setPending: value => pending = value };
}

test("no selection, vision requirement and external unknown analysis", async () => {
  const s = setup(); await s.ui.refresh();
  assert.match(s.root.textContent, /Select a Take to establish observed continuity/);
  await s.click("takes-select-a"); s.settings.supportsVision = false; s.ui.sync();
  assert.equal(s.find("observation-analyze").disabled, true); assert.match(s.root.textContent, /enable Supports Vision/);
  s.settings.supportsVision = true; s.ui.sync(); await s.click("observation-analyze");
  assert.equal(s.calls.length, 1); assert.equal(s.calls[0][1].expectedProjectFingerprint, "fp");
  assert.equal(s.find("observation-summary").value, "Observed a");
  assert.equal(s.root.querySelectorAll("img").length, 2);
  assert.match(s.root.querySelector("img").src, /takes\/a\/evidence\/endpoint/);
  assert.match(s.root.textContent, /Kent \/ Subject 1/);
  assert.equal(s.find("observation-assessment").value, "conflicts");
  assert.equal(s.find("observation-visibility-kent").value, "visible");
});

test("pending Story authority guards observation analysis and corrections", async () => {
  let allowed = false;
  const s = setup({ canChangeObservedState: () => allowed });
  s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "Original")]; await s.ui.refresh();
  await s.click("observation-analyze");
  assert.equal(s.calls.length, 0);
  assert.match(s.find("takes-status").textContent, /Wait for the Story proposal request/);
  s.find("observation-summary").value = "Changed"; await s.find("observation-summary").emit("input");
  await s.click("observation-save");
  assert.equal(s.calls.length, 0);
  allowed = true; await s.click("observation-save");
  assert.equal(s.calls.length, 1);
  assert.equal(s.state.takeObservations[0].effectiveObservedState.summary, "Changed");
});

test("H3 activity blocks Analyze Selected Take with no request and a visible conflict", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; await s.ui.refresh();
  const token = s.providerActivity.begin({ id: "h3-story", label: "Generate from Story", owner: "app", cancel: () => {} });
  await s.click("observation-analyze");
  assert.equal(s.calls.length, 0);
  assert.match(s.root.textContent, /Cannot start Selected Take while Generate from Story/);
  s.providerActivity.finish(token);
});

test("Analyze Selected Take owns shared activity, allows no duplicate, and passes AbortSignal", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "Prior state")]; await s.ui.refresh();
  let resolve; s.setPending(new Promise(done => { resolve = done; }));
  const running = s.click("observation-analyze");
  assert.equal(s.providerActivity.active().id, "analyze-selected-take");
  assert.equal(s.providerActivity.active().verb, "Analyzing");
  assert.equal(s.find("observation-analyze").disabled, false);
  await s.click("observation-analyze");
  assert.equal(s.calls.length, 1);
  assert.match(s.root.textContent, /Analyze Selected Take is already running/);
  const signal = s.analysisSignals[0];
  let dialogueCalls = 0, dialogueConflict = "";
  const dialogueToken = beginProviderActivity(s.providerActivity, { id: "dialogue-translation", label: "Spoken Output", verb: "Translating", cancel: () => {} }, message => dialogueConflict = message);
  if (dialogueToken) dialogueCalls++;
  assert.equal(dialogueCalls, 0);
  assert.match(dialogueConflict, /while Analyze Selected Take/);
  s.providerActivity.cancelActive();
  assert.equal(signal.aborted, true);
  assert.equal(s.providerActivity.active(), null);
  resolve(); await running;
  assert.equal(s.find("observation-summary").value, "Prior state");
});

test("analysis failure is owned by the observation panel and retry remains available", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; await s.ui.refresh();
  const analyze = s.api.analyzeSelectedTake;
  s.api.analyzeSelectedTake = async () => { throw new Error("subjectStates[0].subjectId could not be resolved"); };
  await s.click("observation-analyze");
  assert.match(s.root.textContent, /Analysis failed: subjectStates\[0\]\.subjectId/);
  assert.match(s.root.textContent, /has not been analyzed/);
  s.api.analyzeSelectedTake = analyze; await s.click("observation-analyze");
  assert.equal(s.find("observation-summary").value, "Observed a");
});

test("corrections preserve model, explicit re-analysis confirmation and reset", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "Model jacket on")]; await s.ui.refresh();
  assert.equal(s.find("observation-save").disabled, true);
  s.find("observation-summary").value = "User shirt only"; await s.find("observation-summary").emit("input");
  assert.equal(s.find("observation-save").disabled, false); assert.match(s.root.textContent, /Unsaved edits/);
  await s.click("observation-save"); assert.match(s.root.textContent, /Saved — user-edited state is active/);
  assert.equal(s.state.takeObservations[0].modelObservedState.summary, "Model jacket on");
  assert.equal(s.state.takeObservations[0].effectiveObservedState.summary, "User shirt only");
  s.setAllow(false); await s.click("observation-analyze"); assert.equal(s.calls.length, 1);
  await s.click("observation-reset"); assert.equal(s.calls[1][2].reset, true);
  assert.match(s.root.textContent, /Model-observed/);
  s.setAllow(true); await s.click("observation-analyze"); assert.equal(s.calls[2][1].replaceCorrections, false);
});

test("same-identity Save keeps a later draft through an unrelated render", async () => {
  const s = setup(); s.state.selectedTakeId = "a";
  const observation = record("a", "Already saved"); observation.status = "user_edited";
  s.state.takeObservations = [observation]; await s.ui.refresh();
  assert.equal(s.find("observation-save").disabled, true);
  s.find("observation-summary").value = "Second correction"; await s.find("observation-summary").emit("input");
  await s.click("takes-preview-a");
  assert.equal(s.find("observation-summary").value, "Second correction");
});

test("same-identity no-op Reset keeps a later draft through sync", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "Model baseline")]; await s.ui.refresh();
  assert.equal(s.find("observation-reset").disabled, true);
  s.find("observation-summary").value = "Second correction"; await s.find("observation-summary").emit("input");
  s.ui.sync();
  assert.equal(s.find("observation-summary").value, "Second correction");
});

test("switching selected Takes and Generations never leaks previous observation", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "A state"), record("b", "B state")]; await s.ui.refresh();
  await s.click("takes-select-b"); assert.equal(s.find("observation-summary").value, "B state");
  await s.click("takes-select-c"); assert.equal(s.find("observation-summary"), null);
  assert.match(s.root.textContent, /has not been analyzed/);
  s.context.generationId = "g2"; s.ui.sync(); await s.ui.refresh(); assert.equal(s.find("observation-summary"), null);
  s.context.generationId = "g1"; s.ui.sync(); await s.ui.refresh();
  assert.equal(s.find("observation-summary"), null);
});

test("loading, late response and missing raw media", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; await s.ui.refresh();
  let finish; s.setPending(new Promise(resolve => { finish = resolve; }));
  const running = s.click("observation-analyze");
  assert.equal(s.find("observation-analyze").disabled, false); assert.match(s.root.textContent, /Working/);
  s.context.generationId = "g2"; s.ui.sync(); await s.ui.refresh(); finish(); await running;
  assert.equal(s.find("observation-summary"), null); assert.equal(s.mutations.length, 1);
  s.context.generationId = "g1"; s.state.takes[0].availability = "missing"; s.ui.sync(); await s.ui.refresh();
  assert.match(s.root.textContent, /Source media is missing/); assert.equal(s.find("observation-analyze").disabled, true);
  assert.equal(s.find("observation-summary").value, "Observed a");
});

test("observation commands use current Project fingerprint while local output stays F1", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "Model")]; await s.ui.refresh();
  s.context.projectFingerprint = "F2"; s.state.projectFingerprint = "F1"; await s.click("observation-analyze");
  assert.equal(s.calls[0][1].expectedProjectFingerprint, "F2");
  s.find("observation-summary").value = "Corrected"; await s.find("observation-summary").emit("input");
  await s.click("observation-save"); assert.equal(s.calls[1][2].expectedProjectFingerprint, "F2");
  await s.click("observation-reset"); assert.equal(s.calls[2][2].expectedProjectFingerprint, "F2");
});

test("fingerprint-only sync preserves identity and an in-progress draft", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "Model")]; await s.ui.refresh();
  s.find("observation-summary").value = "Draft survives"; await s.find("observation-summary").emit("input");
  s.context.projectFingerprint = "F2"; s.ui.sync();
  assert.equal(s.find("observation-summary").value, "Draft survives");
  assert.equal(s.root.querySelectorAll(".take-card").length, 1);
});

test("unrelated renders preserve drafts, navigation isolates them, and failed save keeps text", async () => {
  const s = setup(); s.state.selectedTakeId = "a"; s.state.takeObservations = [record("a", "A model"), record("b", "B model")]; await s.ui.refresh();
  s.find("observation-summary").value = "A draft"; await s.find("observation-summary").emit("input");
  const beforeInspection = structuredClone(s.state);
  await s.inspect("b");
  assert.equal(s.state.selectedTakeId, "a");
  assert.deepEqual(s.state, beforeInspection); // No observation, fingerprint or continuity authority changed.
  assert.equal(s.find("observation-summary").value, "A draft");
  assert.match(s.root.querySelector(".observed-end-state").textContent, /Source Take: a.mp4/);
  await s.click("takes-preview-a"); assert.equal(s.find("observation-summary").value, "A draft");
  s.find("takes-source").value = "external_unknown"; await s.find("takes-source").emit("change");
  s.find("takes-file").files = [{ name: "future.mp4" }]; await s.find("takes-file").emit("change");
  assert.equal(s.find("observation-summary").value, "A draft");
  s.api.correctObservation = async () => { throw new Error("save failed"); };
  await s.click("observation-save"); assert.equal(s.find("observation-summary").value, "A draft");
  assert.match(s.root.textContent, /Could not save observed-state edits: save failed/);
  await s.click("takes-select-b"); assert.equal(s.find("observation-summary").value, "B model");
  await s.click("takes-select-a"); assert.equal(s.find("observation-summary").value, "A model");
  s.context.generationId = "g2"; s.ui.sync(); await s.ui.refresh();
  s.context.generationId = "g1"; s.ui.sync(); await s.ui.refresh();
  assert.equal(s.find("observation-summary").value, "A model");
});
