import test from "node:test";
import assert from "node:assert/strict";

import {
  CAMERA_FIELDS, defaultCreativeControls, normalizeCreativeControls,
  PERFORMANCE_ENERGY_LEVELS, PERFORMANCE_STYLE_LEVELS, TONE_LEVELS,
  rangeForSemantic, resetCameraControls, resetCreativeControls, resetTonePerformance, semanticFromRange,
} from "../../web/js/creative-controls.js";


test("defaults normalize old workspaces without shared mutable state", () => {
  const first = defaultCreativeControls();
  const second = defaultCreativeControls();
  first.camera.arc = "avoid";
  first.tonePerformance.drama = "strong";
  assert.equal(second.camera.arc, "auto");
  assert.equal(second.tonePerformance.drama, "auto");
  assert.deepEqual(normalizeCreativeControls(), second);
});


test("normalization preserves music description and valid control values", () => {
  const controls = normalizeCreativeControls({
    music: { mode: "on", description: "dark pulse" },
    camera: { arc: "avoid", pedestal: "prefer", invalid: "prefer" },
    visualStyle: { preset: "animated_2d_anime", custom: "keep this custom style" },
    subjectIdentityFidelity: { level: "strict" },
    tonePerformance: { drama: "strong", performanceStyle: "expressive", performanceEnergy: "calm" },
  });
  assert.equal(controls.music.description, "dark pulse");
  assert.equal(controls.camera.arc, "avoid");
  assert.equal(controls.camera.pedestal, "prefer");
  assert.equal(controls.visualStyle.preset, "animated_2d_anime");
  assert.equal(controls.subjectIdentityFidelity.level, "strict");
  assert.equal(controls.tonePerformance.drama, "strong");
  assert.equal(controls.tonePerformance.performanceStyle, "expressive");
  assert.equal(controls.tonePerformance.performanceEnergy, "calm");
  assert.equal(Object.keys(controls.camera).length, CAMERA_FIELDS.length);
});


test("camera and all-controls resets preserve only the allowed inactive music description", () => {
  const source = normalizeCreativeControls({
    music: { mode: "on", description: "keep me" }, camera: { arc: "avoid" },
    visualStyle: { preset: "custom", custom: "keep this too" }, subjectIdentityFidelity: { level: "strong" },
    tonePerformance: { tension: "moderate", performanceEnergy: "energetic" },
  });
  const cameraReset = resetCameraControls(source);
  assert.equal(cameraReset.camera.arc, "auto");
  assert.equal(cameraReset.music.mode, "on");
  const allReset = resetCreativeControls(source);
  assert.equal(allReset.music.mode, "auto");
  assert.equal(allReset.music.description, "keep me");
  assert.equal(allReset.visualStyle.preset, "auto");
  assert.equal(allReset.visualStyle.custom, "keep this too");
  assert.equal(allReset.subjectIdentityFidelity.level, "auto");
  assert.equal(allReset.tonePerformance.tension, "auto");
  const toneReset = resetTonePerformance(source);
  assert.equal(toneReset.tonePerformance.performanceEnergy, "auto");
  assert.equal(toneReset.camera.arc, "avoid");
});


test("legacy fidelity and missing visual style normalize safely", () => {
  const controls = normalizeCreativeControls({ referenceFidelity: { level: "strong" } });
  assert.equal(controls.subjectIdentityFidelity.level, "strong");
  assert.equal(controls.visualStyle.preset, "auto");
  assert.deepEqual(controls.tonePerformance, defaultCreativeControls().tonePerformance);
});


test("semantic slider mappings preserve Auto centers and restore values", () => {
  assert.equal(semanticFromRange(TONE_LEVELS, 3), "strong");
  assert.equal(rangeForSemantic(TONE_LEVELS, "intense"), 4);
  assert.equal(semanticFromRange(PERFORMANCE_STYLE_LEVELS, 2), "auto");
  assert.equal(rangeForSemantic(PERFORMANCE_ENERGY_LEVELS, "auto"), 2);
  assert.equal(semanticFromRange(PERFORMANCE_ENERGY_LEVELS, 0), "calm");
});
