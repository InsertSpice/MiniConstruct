import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { captureWorkspaceCreativeState, restoreWorkspaceCreativeState } from "../../web/js/workspace-creative-state.js";

import {
  CAMERA_FIELDS, defaultCreativeControls, normalizeCreativeControls, renderH3PromptDetailControl, selectH3PromptDetail,
  PERFORMANCE_ENERGY_LEVELS, PERFORMANCE_STYLE_LEVELS, TONE_LEVELS,
  rangeForSemantic, renderCreativeControls, resetCameraControls, resetCreativeControls, resetTonePerformance, semanticFromRange,
} from "../../web/js/creative-controls.js";


test("Story-authored Tone and Performance restore in the shared H3 controls", async () => {
  const saved = { creativeControls: normalizeCreativeControls({
    camera: { zoom: "prefer" }, music: { mode: "off" },
    tonePerformance: { drama: "strong", tension: "moderate", performanceStyle: "expressive" },
  }) };
  const restored = restoreWorkspaceCreativeState(saved);
  assert.equal(restored.creativeControls.tonePerformance.drama, "strong");
  assert.equal(restored.creativeControls.tonePerformance.tension, "moderate");
  assert.equal(restored.creativeControls.camera.zoom, "prefer");
  assert.deepEqual(captureWorkspaceCreativeState(restored).creativeControls, saved.creativeControls);
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(html, /<summary>H3 Creative Controls <span class="muted">shared by Story and Main Prompt<\/span>/);
  assert.equal((html.match(/id="creative-controls"/g) || []).length, 1);
  assert.ok(html.indexOf('id="creative-interpretation-controls"') < html.indexOf('id="creative-controls"'));
});


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
  assert.equal(allReset.music.mode, "off");
  assert.equal(allReset.music.description, "keep me");
  assert.equal(allReset.visualStyle.preset, "auto");
  assert.equal(allReset.visualStyle.custom, "keep this too");
  assert.equal(allReset.subjectIdentityFidelity.level, "auto");
  assert.equal(allReset.tonePerformance.tension, "auto");
  const toneReset = resetTonePerformance(source);
  assert.equal(toneReset.tonePerformance.performanceEnergy, "auto");
  assert.equal(toneReset.camera.arc, "avoid");
});


test("Reset Creative Controls restores Prompt Detail state and selected UI value", async () => {
  const detailed = normalizeCreativeControls({
    promptDetail: "detailed", music: { mode: "on", description: "keep for later" },
    camera: { arc: "avoid" }, visualStyle: { preset: "watercolor", custom: "keep custom" },
    subjectIdentityFidelity: { level: "strong" }, tonePerformance: { drama: "strong" },
  });
  const state = resetCreativeControls(detailed);
  assert.equal(state.promptDetail, "standard");
  assert.equal(state.music.mode, "off");
  assert.equal(state.music.description, "keep for later");
  assert.equal(state.camera.arc, "auto");
  assert.equal(state.visualStyle.preset, "auto");
  assert.equal(state.visualStyle.custom, "keep custom");
  assert.equal(state.subjectIdentityFidelity.level, "auto");
  assert.equal(state.tonePerformance.drama, "auto");

  const elements = new Map();
  const root = { querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, { innerHTML: "" });
    return elements.get(selector);
  } };
  renderH3PromptDetailControl(root, state);
  const html = elements.get("#h3-prompt-detail-controls").innerHTML;
  assert.match(html, /data-prompt-detail="standard" aria-pressed="true">Standard/);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);

  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(app, /function renderCreativeControlsUi\(\) \{[\s\S]*?renderH3PromptDetailUi\(\);/);
  assert.match(app, /state\.creativeControls = resetCreativeControls\(state\.creativeControls\); renderCreativeControlsUi\(\); setDirty\(\);/);
});


test("legacy fidelity and missing visual style normalize safely", () => {
  const controls = normalizeCreativeControls({ referenceFidelity: { level: "strong" } });
  assert.equal(controls.subjectIdentityFidelity.level, "strong");
  assert.equal(controls.visualStyle.preset, "auto");
  assert.deepEqual(controls.tonePerformance, defaultCreativeControls().tonePerformance);
});


test("H3 Prompt Detail defaults to Standard and restores independently for each Generation", async () => {
  const oldWorkspace = restoreWorkspaceCreativeState({});
  assert.equal(oldWorkspace.creativeControls.promptDetail, "standard");
  const conciseGeneration = restoreWorkspaceCreativeState({ creativeControls: { promptDetail: "concise" } });
  const detailedGeneration = restoreWorkspaceCreativeState({ creativeControls: { promptDetail: "detailed" } });
  assert.equal(conciseGeneration.creativeControls.promptDetail, "concise");
  assert.equal(detailedGeneration.creativeControls.promptDetail, "detailed");
  assert.equal(selectH3PromptDetail(oldWorkspace.creativeControls, "concise").promptDetail, "concise");
  assert.equal(selectH3PromptDetail(oldWorkspace.creativeControls, "detailed").promptDetail, "detailed");

  const savedWorkspace = { creativeControls: structuredClone(detailedGeneration.creativeControls) };
  const reloadedWorkspace = restoreWorkspaceCreativeState(JSON.parse(JSON.stringify(savedWorkspace)));
  const duplicateGeneration = restoreWorkspaceCreativeState(structuredClone(savedWorkspace));
  assert.equal(captureWorkspaceCreativeState(reloadedWorkspace).creativeControls.promptDetail, "detailed");
  assert.equal(duplicateGeneration.creativeControls.promptDetail, "detailed");

  const elements = new Map();
  const root = { querySelector(selector) {
    if (!elements.has(selector)) elements.set(selector, { innerHTML: "" });
    return elements.get(selector);
  } };
  renderH3PromptDetailControl(root, oldWorkspace.creativeControls);
  assert.match(elements.get("#h3-prompt-detail-controls").innerHTML, /data-prompt-detail="standard" aria-pressed="true">Standard/);
  renderH3PromptDetailControl(root, reloadedWorkspace.creativeControls);
  const html = elements.get("#h3-prompt-detail-controls").innerHTML;
  assert.match(html, /data-prompt-detail="detailed" aria-pressed="true">Detailed/);
  assert.equal((html.match(/aria-pressed="true"/g) || []).length, 1);
  const document = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(document, /H3 Prompt Detail <span class="muted">Main Prompt only<\/span>/);
  assert.match(document, /It does not grant additional creative license\. Story generation uses Scene Plan Detail instead\./);
});


test("semantic slider mappings preserve Auto centers and restore values", () => {
  assert.equal(semanticFromRange(TONE_LEVELS, 3), "strong");
  assert.equal(rangeForSemantic(TONE_LEVELS, "intense"), 4);
  assert.equal(semanticFromRange(PERFORMANCE_STYLE_LEVELS, 2), "auto");
  assert.equal(rangeForSemantic(PERFORMANCE_ENERGY_LEVELS, "auto"), 2);
  assert.equal(semanticFromRange(PERFORMANCE_ENERGY_LEVELS, 0), "calm");
});


test("segmented Creative Controls expose the same selected state visually and accessibly", () => {
  const elements = new Map();
  const element = () => ({
    innerHTML: "", value: "", textContent: "", dataset: {},
    append() {}, replaceChildren() {}, setAttribute() {},
    classList: { toggle() {} },
  });
  const root = {
    ownerDocument: { createElement: element },
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
  };
  renderCreativeControls(root, normalizeCreativeControls({
    music: { mode: "off" },
    camera: { arc: "prefer" },
    subjectIdentityFidelity: { level: "strict" },
  }), true);

  const groups = ["#music-mode-controls", "#camera-control-rows", "#subject-identity-fidelity-controls"];
  for (const selector of groups) {
    const html = elements.get(selector).innerHTML;
    assert.equal((html.match(/class="control-choice active"/g) || []).length, selector === "#camera-control-rows" ? CAMERA_FIELDS.length : 1);
    assert.equal((html.match(/aria-pressed="true"/g) || []).length, selector === "#camera-control-rows" ? CAMERA_FIELDS.length : 1);
  }
  assert.match(elements.get("#music-mode-controls").innerHTML, /data-creative-value="off" aria-pressed="true"/);
  assert.match(elements.get("#camera-control-rows").innerHTML, /data-creative-field="camera\.arc" data-creative-value="prefer" aria-pressed="true"/);
  assert.match(elements.get("#subject-identity-fidelity-controls").innerHTML, /data-creative-value="strict" aria-pressed="true"/);
});


test("camera rows keep each action label paired with its three-state control", () => {
  const elements = new Map();
  const element = () => ({
    innerHTML: "", value: "", textContent: "", dataset: {},
    append() {}, replaceChildren() {}, setAttribute() {},
    classList: { toggle() {} },
  });
  const root = {
    ownerDocument: { createElement: element },
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector, element());
      return elements.get(selector);
    },
  };
  renderCreativeControls(root, defaultCreativeControls(), false);

  const html = elements.get("#camera-control-rows").innerHTML;
  assert.equal((html.match(/class="camera-control-row"/g) || []).length, CAMERA_FIELDS.length);
  for (const [key, label] of CAMERA_FIELDS) {
    assert.match(html, new RegExp(`<span>${label}<\\/span><div class="segmented">[\\s\\S]*data-creative-field="camera\\.${key}"`));
  }
});
