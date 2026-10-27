import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { Element } from "./support/dom.mjs";
import { groupReferenceAssets } from "../../web/js/reference-asset-groups.js";

import {
  availableSubjects, materializeSubject, subjectSelectorOptions,
  createSubject, defaultSubjectIdentity, isComparisonAsset, isSubjectIdentityAsset, normalizeSubjectIdentity,
  normalizeSubjectRegistry, normalizedComparisonSubjects, subjectLabel,
  structuralSubjectIds,
  subjectIdentityHelperText, subjectIdentityNotesGuidance, subjectIdentityNotesPlaceholder,
} from "../../web/js/subject-identity.js";
import { groupInputMapEntries, inputMapEmptyMessage, inputMapLabelForAsset, inputMapRefreshStatus } from "../../web/js/input-map.js";

const cast = { subjects: [{ id: "subject-1", number: 1 }, { id: "subject-2", number: 2 }], nextSubjectNumber: 3 };

test("Sequence selector offers dormant Subjects without materializing them", () => {
  const local = [{ id: "subject-2", number: 2 }];
  assert.deepEqual(subjectSelectorOptions(local, cast), [
    { value: "subject-1", label: "Subject 1" }, { value: "subject-2", label: "Subject 2" },
    { value: "__new_subject__", label: "+ New Subject" },
  ]);
  assert.deepEqual(local, [{ id: "subject-2", number: 2 }]);
  assert.deepEqual(normalizeSubjectRegistry(local, [], 3, cast).subjects, []);
});

test("selecting a dormant Subject copies exact canonical identity without editing the cast", () => {
  const sequence = structuredClone(cast), local = [{ id: "subject-2", number: 2 }];
  const subject = materializeSubject(local, "subject-1", sequence);
  const asset = { kind: "image", role: "subject_identity", subjectIdentity: { subjectId: subject.id } };
  assert.deepEqual(normalizeSubjectRegistry(local, [asset], 3, sequence).subjects, [cast.subjects[0]]);
  assert.notEqual(local[1], sequence.subjects[0]);
  assert.deepEqual(sequence, cast);
});

test("default identity resolves canonical Subject 1 while only Subject 2 is local", () => {
  const asset = { kind: "image", role: "subject_identity", subjectIdentity: defaultSubjectIdentity(cast) };
  const result = normalizeSubjectRegistry([cast.subjects[1]], [asset], 3, cast);
  assert.deepEqual(result, { subjects: [cast.subjects[0]], nextSubjectNumber: 3 });
  assert.equal(asset.subjectIdentity.subjectId, "subject-1");
});

test("Project creation uses Sequence watermark and accounts for unsaved allocations", () => {
  const sequence = { subjects: [...cast.subjects, { id: "subject-4", number: 4 }], nextSubjectNumber: 5 };
  const registry = { subjects: [cast.subjects[1]], nextSubjectNumber: 3 };
  const first = createSubject(registry, sequence);
  assert.equal(first.subject.number, 5);
  assert.equal(first.nextSubjectNumber, 6);
  assert.notEqual(createSubject(registry, sequence).subject.id, first.subject.id);
  registry.subjects.push(first.subject); registry.nextSubjectNumber = first.nextSubjectNumber;
  assert.equal(createSubject(registry, sequence).subject.number, 6);
  assert.equal(sequence.nextSubjectNumber, 5); // Proposals remain local until atomic save.
  assert.equal(createSubject({ subjects: [cast.subjects[1]], nextSubjectNumber: 100 }, sequence).subject.number, 5);
  assert.equal(normalizeSubjectRegistry([cast.subjects[1]], [], 100, sequence).nextSubjectNumber, 5);
});

test("imported ID spellings do not determine visible numbers or cause ID reuse", () => {
  const sequence = { subjects: [{ id: "actual-subject-one", number: 1 }, { id: "subject-1", number: 2 }], nextSubjectNumber: 3 };
  assert.equal(defaultSubjectIdentity(sequence).subjectId, "actual-subject-one");
  assert.equal(defaultSubjectIdentity({ subjects: [{ id: "subject-1", number: 2 }, { id: "stable-first", number: 1 }], nextSubjectNumber: 3 }).subjectId, "stable-first");
  const imported = { subjects: [{ id: "subject-5", number: 1 }], nextSubjectNumber: 5 };
  const created = createSubject({ subjects: [], nextSubjectNumber: 1 }, sequence);
  assert.equal(created.subject.number, 3);
  assert.notEqual(created.subject.id, "subject-1");
  assert.deepEqual(normalizeSubjectRegistry([], [{ kind: "image", role: "subject_identity",
    subjectIdentity: { subjectId: "subject-5" } }], 1, imported).subjects, imported.subjects);
});

test("Project normalizer rejects divergent identity and materializes comparison Subjects", () => {
  assert.throws(() => availableSubjects([{ id: "subject-1", number: 3 }], cast), /conflicts/);
  assert.throws(() => availableSubjects([{ id: "other", number: 1 }], cast), /already used/);
  const comparison = { kind: "image", role: "character_comparison_scale", comparisonSubjects: ["subject-1", "subject-2"] };
  assert.deepEqual(normalizeSubjectRegistry([], [comparison], 1, cast), cast);
});

test("comparison choices include dormant canonical Subjects and materialize exact records", () => {
  const local = [{ id: "stable-b", number: 2 }];
  const sequence = { subjects: [{ id: "stable-a", number: 1 }, { id: "stable-b", number: 2 }], nextSubjectNumber: 3 };
  const choices = availableSubjects(local, sequence);
  assert.deepEqual(choices, sequence.subjects);
  assert.equal(subjectLabel(choices, "stable-a"), "Subject 1");
  assert.equal(subjectLabel(choices, "stable-b"), "Subject 2");
  const comparison = { kind: "image", role: "character_comparison_scale", comparisonSubjects: [] };
  materializeSubject(local, choices[0].id, sequence);
  comparison.comparisonSubjects.push(choices[0].id, "stable-b");
  assert.deepEqual(local.sort((a, b) => a.number - b.number), sequence.subjects);
  assert.deepEqual(normalizedComparisonSubjects(comparison, choices), ["stable-a", "stable-b"]);
  comparison.comparisonSubjects = [];
  assert.deepEqual(normalizeSubjectRegistry(local, [comparison], 3, sequence).subjects, []);
});

test("standalone legacy normalization and last-reference removal retain their behavior", () => {
  const asset = { kind: "image", role: "subject_identity", subjectIdentity: defaultSubjectIdentity() };
  const local = normalizeSubjectRegistry([cast.subjects[1]], [asset], 3);
  assert.equal(local.subjects.find(s => s.id === "subject-1").number, 3);
  assert.deepEqual(normalizeSubjectRegistry(local.subjects, [], local.nextSubjectNumber), local);
  assert.deepEqual(normalizeSubjectRegistry(cast.subjects, [], 3, cast), { subjects: [], nextSubjectNumber: 3 });
});

test("Project structural participation prunes only semantic Picture associations", () => {
  const assets = [
    { kind: "image", role: "subject_identity", subjectIdentity: { subjectId: "subject-1" } },
    { kind: "image", role: "character_comparison_scale", comparisonSubjects: ["subject-2"] },
    { kind: "image", role: "environment", subjectIdentity: { subjectId: "ignored-picture" } },
    { kind: "audio", role: "voice_timbre", subjectIdentity: { subjectId: "ignored-audio" } },
  ];
  assert.deepEqual([...structuralSubjectIds(assets)].sort(), ["subject-1", "subject-2"]);
  assert.deepEqual(normalizeSubjectRegistry(cast.subjects, assets, 3, cast).subjects, cast.subjects);
  assert.deepEqual(normalizeSubjectRegistry(cast.subjects, assets.slice(2), 3, cast).subjects, []);
});

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

test("input map presentation groups backend labels without renumbering them", () => {
  const groups = groupInputMapEntries([
    { id: "audio", kind: "audio", h3Label: "Audio 4", filename: "voice.wav" },
    { id: "image", kind: "image", h3Label: "Picture 7", filename: "sheet.png" },
    { id: "video", kind: "video", h3Label: "Video 2", filename: "motion.mp4" },
  ]);
  assert.deepEqual(groups.map(group => group.label), ["Pictures", "Videos", "Audio"]);
  assert.deepEqual(groups.map(group => group.entries[0].h3Label), ["Picture 7", "Video 2", "Audio 4"]);
});

test("input map messages distinguish pending, loaded-empty, no-assets, and failed states", () => {
  assert.equal(inputMapEmptyMessage({ assetCount: 2, status: "pending" }), "Updating H3 input identifiers…");
  assert.equal(inputMapEmptyMessage({ assetCount: 2, status: "loaded" }), "No H3 reference inputs are active for this mode.");
  assert.equal(inputMapEmptyMessage({ assetCount: 0, status: "loaded" }), "Add reference assets to see their H3 input identifiers.");
  assert.equal(inputMapEmptyMessage({ assetCount: 2, status: "failed" }), "Could not update H3 input identifiers.");
});

test("canonical labels are looked up by stable asset identity and absent entries clear", () => {
  const entries = [{ id: "asset-a", h3Label: "Picture 2" }];
  assert.equal(inputMapLabelForAsset(entries, "asset-a"), "Picture 2");
  assert.equal(inputMapLabelForAsset(entries, "missing"), "");
});

test("loaded wiring stays loaded during background refresh", () => {
  assert.equal(inputMapRefreshStatus(false), "pending");
  assert.equal(inputMapRefreshStatus(true), "loaded");
});

test("actual new-Picture and dormant-Subject attachment buttons express different identity choices", async () => {
  const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const doc = { createElement: tag => new Element(tag, doc) };
  const assets = doc.createElement("div"), actions = doc.createElement("div"), helper = doc.createElement("p");
  const picked = [];
  const state = { mode: "Ref2VA", subjects: [], activeProjectType: "filesystem",
    projectSequence: { subjects: [{ id: "old-character", number: 1 }], nextSubjectNumber: 2 } };
  const context = { state, document: doc, availableSubjects, groupReferenceAssets,
    $: selector => selector === "#asset-list" ? assets : selector === "#asset-actions" ? actions : helper,
    visibleAssets: () => [], renderCreativeControlsUi() {}, applyCanonicalAssetLabels() {},
    chooseAsset: (...args) => picked.push(args) };
  const renderer = app.match(/function renderAssets\([^\n]*\) \{[\s\S]*?\n\}/)[0];
  const actionRenderer = app.match(/function renderAssetActions\([^\n]*\) \{[\s\S]*?\n\}/)[0];
  runInNewContext(renderer + actionRenderer, context);
  context.renderAssets({ refreshManifest: false });
  context.renderAssetActions();
  assert.equal(assets.querySelectorAll("details").find(section => section.className === "reference-subject-group")?.dataset.subjectId, "old-character");
  const attach = assets.querySelectorAll("button").find(button => button.textContent === "Add identity Picture");
  await attach.emit("click");
  assert.deepEqual(picked[0], ["image", null, "old-character"]);
  const create = actions.querySelectorAll("button").find(button => button.textContent === "+ New Subject Picture");
  await create.emit("click");
  assert.deepEqual(picked[1], ["image"]);
  assert.equal(state.projectSequence.nextSubjectNumber, 2);
  assert.deepEqual(state.subjects, []);
});
