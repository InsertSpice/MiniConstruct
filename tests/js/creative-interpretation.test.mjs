import test from "node:test";
import assert from "node:assert/strict";
import { snapshotGenerationRequest } from "../../web/js/streaming.js";
import { captureWorkspaceCreativeState, restoreWorkspaceCreativeState } from "../../web/js/workspace-creative-state.js";

import {
  CREATIVE_INTERPRETATION_OPTIONS, DEFAULT_CREATIVE_INTERPRETATION,
  normalizeCreativeInterpretation, renderCreativeInterpretation,
} from "../../web/js/creative-interpretation.js";


test("Creative Interpretation defaults old workspace state to Balanced", () => {
  assert.equal(DEFAULT_CREATIVE_INTERPRETATION, "balanced");
  assert.equal(normalizeCreativeInterpretation(), "balanced");
  assert.equal(normalizeCreativeInterpretation("unknown"), "balanced");
});


test("Creative Interpretation exposes exactly Conservative, Balanced, and Expansive", () => {
  assert.deepEqual(CREATIVE_INTERPRETATION_OPTIONS, [
    ["conservative", "Conservative"], ["balanced", "Balanced"], ["expansive", "Expansive"],
  ]);
  assert.equal(normalizeCreativeInterpretation("conservative"), "conservative");
  assert.equal(normalizeCreativeInterpretation("expansive"), "expansive");
});


test("Creative Interpretation rendering restores the selected control", () => {
  const control = { innerHTML: "" };
  renderCreativeInterpretation({ querySelector: selector => {
    assert.equal(selector, "#creative-interpretation-controls");
    return control;
  } }, "expansive");
  assert.match(control.innerHTML, /data-creative-interpretation="conservative" aria-pressed="false">Conservative/);
  assert.match(control.innerHTML, /data-creative-interpretation="balanced" aria-pressed="false">Balanced/);
  assert.match(control.innerHTML, /control-choice active" data-creative-interpretation="expansive" aria-pressed="true">Expansive/);
  assert.equal((control.innerHTML.match(/aria-pressed="true"/g) || []).length, 1);
  assert.equal((control.innerHTML.match(/aria-pressed="false"/g) || []).length, 2);
});


test("workspace creative state capture and restoration round-trip without shared mutation", () => {
  const liveState = {
    creativeInterpretation: "expansive",
    creativeControls: { music: { mode: "off", description: "quiet" }, camera: { arc: "prefer" } },
  };
  const captured = captureWorkspaceCreativeState(liveState);
  liveState.creativeInterpretation = "conservative";
  liveState.creativeControls.music.mode = "on";

  const restored = restoreWorkspaceCreativeState(captured);
  assert.equal(restored.creativeInterpretation, "expansive");
  assert.equal(restored.creativeControls.music.mode, "off");
  assert.equal(restored.creativeControls.music.description, "quiet");
  assert.equal(restored.creativeControls.camera.arc, "prefer");
  captured.creativeControls.camera.arc = "avoid";
  assert.equal(restored.creativeControls.camera.arc, "prefer");

  const legacy = restoreWorkspaceCreativeState({});
  assert.equal(legacy.creativeInterpretation, "balanced");
  assert.equal(legacy.creativeControls.music.mode, "auto");
  assert.equal(legacy.creativeControls.camera.arc, "auto");
  assert.equal(legacy.creativeControls.subjectIdentityFidelity.level, "auto");
});


test("generation and History snapshots retain the selected Creative Interpretation", () => {
  const request = {
    workspace: { creativeInterpretation: "conservative", creativeControls: { music: { mode: "off" } } },
    llm: { seedMode: "backend_default" },
  };
  const snapshot = snapshotGenerationRequest(request);
  request.workspace.creativeInterpretation = "expansive";
  request.workspace.creativeControls.music.mode = "on";
  assert.equal(snapshot.workspace.creativeInterpretation, "conservative");
  assert.equal(snapshot.workspace.creativeControls.music.mode, "off");
});
