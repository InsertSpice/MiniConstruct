import test from "node:test";
import assert from "node:assert/strict";

import {
  createSubject, defaultSubjectIdentity, isComparisonAsset, isSubjectIdentityAsset, normalizeSubjectIdentity,
  normalizeSubjectRegistry, normalizedComparisonSubjects, subjectLabel,
  subjectIdentityHelperText, subjectIdentityNotesGuidance, subjectIdentityNotesPlaceholder,
} from "../../web/js/subject-identity.js";

test("subject identity defaults and normalization are safe and independent", () => {
  const first = defaultSubjectIdentity();
  const second = defaultSubjectIdentity();
  first.focus = "face";
  assert.deepEqual(second, { subjectId: "subject-1", focus: "general", view: "unspecified", layout: "auto" });
  assert.deepEqual(normalizeSubjectIdentity(), second);
  assert.deepEqual(normalizeSubjectIdentity({ focus: "face", view: "profile" }), { subjectId: "subject-1", focus: "face", view: "profile", layout: "auto" });
  assert.deepEqual(normalizeSubjectIdentity({ focus: "unknown", view: "wrong" }), second);
});

test("stable subject registry preserves identity grouping through reorder and creates monotonic subjects", () => {
  const assets = [
    { id: "later", kind: "image", role: "subject_identity", order: 1, subjectIdentity: { subjectId: "subject-1" } },
    { id: "earlier", kind: "image", role: "subject_identity", order: 0, subjectIdentity: { subjectId: "subject-2" } },
  ];
  const registry = normalizeSubjectRegistry([{ id: "subject-1", number: 1 }, { id: "subject-2", number: 2 }], assets, 3);
  assert.equal(subjectLabel(registry.subjects, assets[0].subjectIdentity.subjectId), "Subject 1");
  assert.equal(subjectLabel(registry.subjects, assets[1].subjectIdentity.subjectId), "Subject 2");
  const created = createSubject(registry);
  assert.deepEqual(created.subject, { id: "subject-3", number: 3 });
  assert.equal(created.nextSubjectNumber, 4);
});

test("comparison selection is role-specific and rejects dangling selections during normalization", () => {
  const subjects = [{ id: "subject-1", number: 1 }, { id: "subject-2", number: 2 }];
  const comparison = { kind: "image", role: "character_comparison_scale", comparisonSubjects: ["subject-2", "missing", "subject-2", "subject-1"] };
  assert.equal(isComparisonAsset(comparison), true);
  assert.deepEqual(normalizedComparisonSubjects(comparison, subjects), ["subject-2", "subject-1"]);
});

test("reference-sheet metadata keeps its stored view but makes its guidance sheet-aware", () => {
  const sheet = normalizeSubjectIdentity({ focus: "full_body", view: "profile", layout: "reference_sheet" });
  assert.equal(sheet.view, "profile");
  assert.match(subjectIdentityHelperText(sheet), /not target-video composition/i);
  assert.match(subjectIdentityNotesGuidance(sheet), /reference sheet/i);
  assert.match(subjectIdentityNotesPlaceholder(sheet), /turnaround/i);
});

test("role changes preserve metadata while rendering only for subject identity Pictures", () => {
  const asset = { kind: "image", role: "subject_identity", subjectIdentity: { subjectId: "subject-2", focus: "face", view: "front", layout: "single_view" } };
  assert.equal(isSubjectIdentityAsset(asset), true);
  asset.role = "environment";
  assert.equal(isSubjectIdentityAsset(asset), false);
  assert.deepEqual(asset.subjectIdentity, { subjectId: "subject-2", focus: "face", view: "front", layout: "single_view" });
  asset.role = "subject_identity";
  assert.equal(isSubjectIdentityAsset(asset), true);
});

test("contextual Notes guidance prioritizes facial and view-specific facts", () => {
  assert.match(subjectIdentityNotesPlaceholder({ focus: "face" }), /eye shape/i);
  assert.match(subjectIdentityNotesPlaceholder({ focus: "face" }), /cheek marks.*freckles.*moles.*scars/i);
  assert.match(subjectIdentityNotesPlaceholder({ view: "rear" }), /rear-view/i);
  assert.match(subjectIdentityNotesPlaceholder({ view: "profile" }), /side-view/i);
  assert.match(subjectIdentityNotesPlaceholder({ focus: "full_body" }), /need not be restated/i);
  assert.match(subjectIdentityNotesPlaceholder({ focus: "outfit" }), /gold buttons.*red bow shape.*pink shoes/i);
  assert.match(subjectIdentityHelperText({ focus: "face", view: "front" }), /Primary facial-likeness.*Front-facing/i);
  assert.match(subjectIdentityHelperText({ focus: "full_body" }), /clothing.*footwear.*accessories/i);
  assert.match(subjectIdentityHelperText({ focus: "outfit" }), /primary reference purpose.*stronger authority/i);
  assert.match(subjectIdentityNotesGuidance({ focus: "face" }), /Optional factual emphasis.*cheek marks/i);
  assert.match(subjectIdentityNotesGuidance({ focus: "outfit" }), /gold buttons.*red bow shape.*pink shoes/i);
  assert.match(subjectIdentityNotesGuidance({ view: "profile" }), /pointed nose profile/i);
});
