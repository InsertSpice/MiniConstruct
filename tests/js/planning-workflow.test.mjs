import assert from "node:assert/strict";
import test from "node:test";
import {
  applyScopedPlan, fillMissingPlan, mergeRecommendations, planningFromProject, RELATIONSHIP_OPTIONS, safeRecommendations, recommendationAssetLabel,
} from "../../web/js/planning-workflow.js";

const current = {
  creativeRequest: "Manual action",
  incomingState: "",
  intendedOutgoingState: "Manual ending",
  planning: { relationshipToPrevious: null, cameraIntent: "" },
};
const proposal = {
  creativeRequest: "Assistant action",
  incomingState: "Assistant start",
  intendedOutgoingState: "Assistant ending",
  relationshipToPrevious: "continue_previous_shot",
  cameraIntent: "Low-angle track",
};

test("recommendation labels use filename basenames and roles with ID fallback", () => {
  assert.equal(recommendationAssetLabel({ filename: "station_environment.png", role: "environment" }, "asset-id"),
    "station_environment.png · environment");
  for (const filename of ["C:\\private\\station.png", "/private/station.png", "assets/pictures/station.png"]) {
    assert.equal(recommendationAssetLabel({ filename, role: "environment" }, "asset-id"), "station.png · environment");
  }
  assert.equal(recommendationAssetLabel({ filename: "station.png" }, "asset-id"), "station.png");
  assert.equal(recommendationAssetLabel({ filename: "  ", role: "environment" }, "asset-id"), "asset-id · environment");
  for (const asset of [null, {}, { path: "assets/pictures/blob.png", image: { data_url: "data:image/png;base64,secret" } },
    { filename: "blob:private-payload" }, { filename: "data:image/png;base64,secret" }]) {
    assert.equal(recommendationAssetLabel(asset, "asset-id"), "asset-id");
  }
  assert.equal(recommendationAssetLabel({ filename: "<img src=x onerror=alert(1)>" }, "asset-id"),
    "<img src=x onerror=alert(1)>");
});

test("planning metadata has the four closed relationship choices", () => {
  assert.deepEqual(RELATIONSHIP_OPTIONS.map(([value]) => value), [
    "continue_previous_shot", "new_shot_preserve_continuity", "match_previous_ending_frame", "independent",
  ]);
});

test("fill missing plan preserves manual values", () => {
  const result = fillMissingPlan(current, proposal);
  assert.equal(result.creativeRequest, "Manual action");
  assert.equal(result.incomingState, "Assistant start");
  assert.equal(result.intendedOutgoingState, "Manual ending");
  assert.equal(result.planning.relationshipToPrevious, "continue_previous_shot");
  assert.equal(result.planning.cameraIntent, "Low-angle track");
});

test("scoped plan revision changes only selected fields", () => {
  const result = applyScopedPlan(current, proposal, ["camera_intent", "incoming_state"]);
  assert.equal(result.creativeRequest, current.creativeRequest);
  assert.equal(result.incomingState, proposal.incomingState);
  assert.equal(result.intendedOutgoingState, current.intendedOutgoingState);
  assert.equal(result.planning.relationshipToPrevious, null);
  assert.equal(result.planning.cameraIntent, proposal.cameraIntent);
});

test("planning response defaults and recommendations are safe", () => {
  assert.deepEqual(planningFromProject({}), { relationshipToPrevious: null, cameraIntent: "" });
  const safe = safeRecommendations([
    { source: "previous_generation", assetId: "audio-previous", recommendation: "Reuse" },
    { source: "current_generation", assetId: "shared", recommendation: "Use current" },
    { source: "fabricated_generation", assetId: "x", recommendation: "Ignore" },
    { source: "previous_generation", assetId: "a", recommendation: "<unsafe>" },
  ]);
  assert.equal(safe.length, 3);
  assert.equal(safe[0].source, "previous_generation");
  assert.equal(safe[0].assetId, "audio-previous");
});

test("camera-only revision preserves previous Picture and Audio recommendations", () => {
  const merged = mergeRecommendations([
    { source: "previous_generation", assetId: "picture-previous", recommendation: "Keep identity" },
    { source: "previous_generation", assetId: "audio-previous", recommendation: "Keep voice timbre" },
  ], [
    { source: "current_generation", assetId: "picture-current", recommendation: "Use a tighter camera" },
  ]);
  assert.deepEqual(merged, [
    { source: "previous_generation", assetId: "picture-previous", recommendation: "Keep identity" },
    { source: "previous_generation", assetId: "audio-previous", recommendation: "Keep voice timbre" },
    { source: "current_generation", assetId: "picture-current", recommendation: "Use a tighter camera" },
  ]);
});

test("same source-qualified key is replaced without duplication", () => {
  const merged = mergeRecommendations([
    { source: "current_generation", assetId: "shared", recommendation: "Old advice" },
  ], [
    { source: "current_generation", assetId: "shared", recommendation: "Updated advice" },
  ]);
  assert.deepEqual(merged, [
    { source: "current_generation", assetId: "shared", recommendation: "Updated advice" },
  ]);
});

test("same asset ID with different sources remains distinct", () => {
  const merged = mergeRecommendations([
    { source: "previous_generation", assetId: "shared", recommendation: "Previous advice" },
  ], [
    { source: "current_generation", assetId: "shared", recommendation: "Current advice" },
  ]);
  assert.equal(merged.length, 2);
  assert.deepEqual(merged.map(item => [item.source, item.assetId]), [
    ["previous_generation", "shared"], ["current_generation", "shared"],
  ]);
});

test("invalid incoming recommendations are filtered during merge", () => {
  const merged = mergeRecommendations([
    { source: "previous_generation", assetId: "audio-previous", recommendation: "Keep voice" },
  ], [
    { source: "fabricated_generation", assetId: "x", recommendation: "Ignore" },
    { source: "current_generation", assetId: "", recommendation: "Ignore" },
    { source: "current_generation", assetId: "picture-current", recommendation: "Use current" },
  ]);
  assert.deepEqual(merged, [
    { source: "previous_generation", assetId: "audio-previous", recommendation: "Keep voice" },
    { source: "current_generation", assetId: "picture-current", recommendation: "Use current" },
  ]);
});
