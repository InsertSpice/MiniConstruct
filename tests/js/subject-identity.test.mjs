import test from "node:test";
import assert from "node:assert/strict";

import {
  defaultSubjectIdentity, isSubjectIdentityAsset, normalizeSubjectIdentity,
  subjectIdentityHelperText, subjectIdentityNotesGuidance, subjectIdentityNotesPlaceholder,
} from "../../web/js/subject-identity.js";

test("subject identity defaults and normalization are safe and independent", () => {
  const first = defaultSubjectIdentity();
  const second = defaultSubjectIdentity();
  first.focus = "face";
  assert.deepEqual(second, { focus: "general", view: "unspecified" });
  assert.deepEqual(normalizeSubjectIdentity(), second);
  assert.deepEqual(normalizeSubjectIdentity({ focus: "face", view: "profile" }), { focus: "face", view: "profile" });
  assert.deepEqual(normalizeSubjectIdentity({ focus: "unknown", view: "wrong" }), second);
});

test("role changes preserve metadata while rendering only for subject identity Pictures", () => {
  const asset = { kind: "image", role: "subject_identity", subjectIdentity: { focus: "face", view: "front" } };
  assert.equal(isSubjectIdentityAsset(asset), true);
  asset.role = "environment";
  assert.equal(isSubjectIdentityAsset(asset), false);
  assert.deepEqual(asset.subjectIdentity, { focus: "face", view: "front" });
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
