import test from "node:test";
import assert from "node:assert/strict";

import { groupReferenceAssets, subjectIdentityPresentation } from "../../web/js/reference-asset-groups.js";

const subjects = [{ id: "stable-a", number: 1 }, { id: "stable-b", number: 2 }];
const identity = (id, subjectId, focus = "general", layout = "single_view", order = 0) => ({
  id, kind: "image", role: "subject_identity", order,
  subjectIdentity: { subjectId, focus, layout, view: "front" },
});

const grouped = assets => groupReferenceAssets({ assets, subjects });
const subject = (result, number) => result.subjectGroups.find(group => group.subject.number === number);
const group = (groups, label) => groups.find(item => item.label === label);

test("Subject identity Pictures use stable Subject sections and metadata-derived categories", () => {
  const sheet = identity("sheet", "stable-a", "face", "reference_sheet", 4);
  const outfit = identity("outfit", "stable-a", "outfit", "single_view", 1);
  const result = grouped([sheet, outfit]);
  assert.deepEqual(group(subject(result, 1).groups, "Identity").assets.map(asset => asset.id), ["sheet"]);
  assert.deepEqual(group(subject(result, 1).groups, "Appearance").assets.map(asset => asset.id), ["outfit"]);
  assert.deepEqual(subject(result, 2).groups, []); // Dormant cast members remain presentation-only.
  assert.equal(subjectIdentityPresentation(sheet.subjectIdentity).metadata, "Reference Sheet · Facial identity");
  assert.equal(subjectIdentityPresentation(outfit.subjectIdentity).metadata, "Single View · Outfit / clothing");
  assert.equal(subjectIdentityPresentation(identity("body", "stable-a", "full_body").subjectIdentity).category, "Appearance");
});

test("shared, environment, video, and audio references never duplicate into Subjects", () => {
  const comparison = { id: "comparison", kind: "image", role: "character_comparison_scale", comparisonSubjects: ["stable-a", "stable-b"], order: 0 };
  const environment = { id: "yard", kind: "image", role: "environment", order: 1 };
  const anchors = ["first_frame_anchor", "keyframe_anchor", "last_frame_anchor"].map((role, order) => ({ id: role, kind: "image", role, order: order + 2 }));
  const video = { id: "video", kind: "video", role: "continuation_source", order: 0 };
  const audio = { id: "voice", kind: "audio", role: "voice_timbre", order: 0 };
  const result = grouped([comparison, environment, ...anchors, video, audio]);
  assert.deepEqual(result.environment.assets.map(asset => asset.id), ["yard"]);
  assert.deepEqual(group(result.sharedGroups, "Character Comparison / Scale").assets.map(asset => asset.id), ["comparison"]);
  assert.deepEqual(group(result.sharedGroups, "Frame / Keyframe Anchors").assets.map(asset => asset.id), anchors.map(asset => asset.id));
  assert.deepEqual(group(result.sharedGroups, "Video").assets.map(asset => asset.id), ["video"]);
  assert.deepEqual(group(result.sharedGroups, "Audio").assets.map(asset => asset.id), ["voice"]);
  assert.deepEqual(result.subjectGroups.map(item => item.groups), [[], []]);
});

test("comparison-only Subjects are marked structurally active without duplicating the shared card", () => {
  const comparison = { id: "comparison", kind: "image", role: "character_comparison_scale", comparisonSubjects: ["stable-a", "stable-b"], order: 0 };
  const result = grouped([comparison]);
  assert.deepEqual(result.subjectGroups.map(item => item.structuralUse), [true, true]);
  assert.deepEqual(result.subjectGroups.map(item => item.groups), [[], []]);
  assert.equal(group(result.sharedGroups, "Character Comparison / Scale").assets.length, 1);
});

test("fully dormant Subjects remain distinguishable from comparison participation", () => {
  const result = grouped([]);
  assert.deepEqual(result.subjectGroups.map(item => item.structuralUse), [false, false]);
});

test("storyboard and continuity roles remain in the shared visual area without changing H3 labels", () => {
  const storyboard = { id: "board", kind: "image", role: "storyboard_composition", order: 3, h3Label: "Picture 9" };
  const continuity = { id: "continuity", kind: "image", role: "continuity_state", order: 1, h3Label: "Picture 4" };
  const style = { id: "style", kind: "image", role: "style_appearance", order: 2, h3Label: "Picture 5" };
  const result = grouped([storyboard, continuity, style]);
  assert.deepEqual(group(result.sharedGroups, "Storyboard / Composition").assets.map(asset => asset.id), ["board"]);
  assert.deepEqual(group(result.sharedGroups, "Continuity / General Visual").assets.map(asset => asset.id), ["continuity", "style"]);
  assert.deepEqual(group(result.sharedGroups, "Continuity / General Visual").assets.map(asset => asset.h3Label), ["Picture 4", "Picture 5"]);
  assert.equal(storyboard.h3Label, "Picture 9");
});

test("role or Subject edits only change derived placement, never IDs or canonical order", () => {
  const asset = identity("unchanged-id", "stable-a", "face", "single_view", 7);
  const first = grouped([asset]);
  asset.subjectIdentity.subjectId = "stable-b";
  const second = grouped([asset]);
  asset.role = "environment";
  const third = grouped([asset]);
  assert.equal(group(subject(first, 1).groups, "Identity").assets[0].id, "unchanged-id");
  assert.equal(group(subject(second, 2).groups, "Identity").assets[0].id, "unchanged-id");
  assert.equal(third.environment.assets[0].id, "unchanged-id");
  assert.equal(asset.order, 7);
});

test("standalone subject lists group without Sequence metadata and retain canonical sort within a section", () => {
  const localSubjects = [{ id: "local", number: 3 }];
  const later = identity("later", "local", "general", "single_view", 4);
  const earlier = identity("earlier", "local", "face", "single_view", 1);
  const result = groupReferenceAssets({ assets: [later, earlier], subjects: localSubjects });
  assert.deepEqual(result.subjectGroups.map(item => item.label), ["Subject 3"]);
  assert.deepEqual(group(result.subjectGroups[0].groups, "Identity").assets.map(asset => asset.id), ["earlier", "later"]);
  assert.deepEqual(localSubjects, [{ id: "local", number: 3 }]);
});
