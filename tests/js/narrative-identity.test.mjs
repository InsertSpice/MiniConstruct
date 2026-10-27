import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";
import { Element } from "./support/dom.mjs";
import { api } from "../../web/js/api.js";
import {
  availableSubjects, materializeSubject, normalizeSubjectRegistry, subjectSelectorOptions,
  subjectDisplayLabel, subjectLabel, subjectDeleteConfirmation, adoptNarrativeResponse,
} from "../../web/js/subject-identity.js";
import { groupReferenceAssets } from "../../web/js/reference-asset-groups.js";
import { shouldMarkWorkspaceDirty } from "../../web/js/sequence-workflow.js";

const sequence = { subjects: [
  { id: "opaque/id", number: 1, narrativeName: "Alice", narrativeAliases: [] },
  { id: "other ?/#", number: 3, narrativeName: "Bob", narrativeAliases: ["Robert"] },
  { id: "unnamed", number: 4, narrativeName: null, narrativeAliases: [] },
], nextSubjectNumber: 5 };

test("names label Identity, Comparison, dormant groups and deletion while IDs bind", () => {
  const local = [{ id: "opaque/id", number: 1 }];
  const available = availableSubjects(local, sequence);
  assert.equal(subjectDisplayLabel(available[0]), "Alice (Subject 1)");
  assert.equal(subjectLabel(available, "other ?/#"), "Bob (Subject 3)");
  assert.equal(subjectDisplayLabel(available[2]), "Subject 4");
  assert.deepEqual(subjectSelectorOptions(local, sequence).slice(0, 3), [
    { value: "opaque/id", label: "Alice (Subject 1)" },
    { value: "other ?/#", label: "Bob (Subject 3)" },
    { value: "unnamed", label: "Subject 4" },
  ]);
  const groups = groupReferenceAssets({ subjects: available, assets: [] }).subjectGroups;
  assert.equal(groups.length, 3); // Aliases do not create Subject entries.
  assert.equal(groups[1].label, "Bob (Subject 3)");
  assert.equal(groups[1].structuralUse, false);
  assert.match(subjectDeleteConfirmation(available[1]), /^Delete Bob \(Subject 3\).*does not renumber/);
  materializeSubject(local, "other ?/#", sequence);
  assert.deepEqual(local, [{ id: "opaque/id", number: 1 }, { id: "other ?/#", number: 3 }]);
  const normalized = normalizeSubjectRegistry(available, [{ kind: "image", role: "subject_identity",
    subjectIdentity: { subjectId: "other ?/#" } }], 5, sequence);
  assert.deepEqual(normalized.subjects, [{ id: "other ?/#", number: 3 }]);
});

test("rename API uses an opaque ID JSON body and the canonical result", async () => {
  const previous = globalThis.fetch;
  let call;
  globalThis.fetch = async (url, options) => {
    call = { url, options };
    return { ok: true, status: 200, json: async () => ({ sequence }) };
  };
  try {
    const response = await api.nameSequenceSubject("project/id", "other ?/#", "Bob");
    assert.deepEqual(response.sequence, sequence);
    assert.equal(call.url, "/api/projects/project%2Fid/subjects");
    assert.equal(call.options.method, "PATCH");
    assert.deepEqual(JSON.parse(call.options.body), { subjectId: "other ?/#", narrativeName: "Bob" });
  } finally { globalThis.fetch = previous; }
});

test("canonical rename refresh retains edits and history; late responses cannot cross Projects", () => {
  const state = { currentProjectId: "p", activeGenerationId: "g", activeProjectType: "filesystem",
    dirty: true, assets: [{ id: "unsaved" }], subjects: [{ id: "opaque/id", number: 1 }],
    outputs: [{ prompt: "old Robert output", sourceFingerprint: "historical" }] };
  const before = structuredClone(state);
  assert.equal(adoptNarrativeResponse(state, { sequence, projectFingerprint: "new-authority", workspace: { creativeRequest: "overwrite" } },
    { projectId: "p", generationId: "g" }), true);
  assert.deepEqual(state, { ...before, projectSequence: sequence, projectFingerprint: "new-authority" });
  assert.equal(subjectLabel(availableSubjects(state.subjects, state.projectSequence), "other ?/#"), "Bob (Subject 3)");
  assert.equal(adoptNarrativeResponse(state, { sequence: {} }, { projectId: "old", generationId: "g" }), false);
  assert.equal(adoptNarrativeResponse(state, { sequence: {} }, { projectId: "p", generationId: "other" }), false);
  assert.equal(state.projectSequence, sequence);
});

test("name input is a Sequence operation and does not make the Generation dirty", () => {
  const target = { closest: selector => [".left-rail", ".subject-narrative-name"].includes(selector) ? {} : null };
  assert.equal(shouldMarkWorkspaceDirty(target), false);
});

test("actual app rename handler obeys the established saved/dirty guard before API mutation", async () => {
  const source = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const guard = source.match(/function structuralSequenceReady\(\) \{[\s\S]*?\n\}/)[0];
  const handler = source.match(/async function renameSequenceSubject\(subject, name\) \{[\s\S]*?\n\}/)[0];
  let calls = 0;
  const state = { currentProjectId: "p", activeGenerationId: "g", activeProjectType: "filesystem", dirty: true, subjects: [] };
  const context = vm.createContext({ state, toast: () => {}, navigationAuthority: {
    begin: (projectId, generationId) => ({ projectId, generationId }),
    ownsContext: (ticket, projectId, generationId) => ticket.projectId === projectId && ticket.generationId === generationId,
    invalidate() {},
  },
    api: { nameSequenceSubject: async () => { calls++; return { sequence }; } },
    storyPlanner: { confirmContextChange: () => true, clearDraft() {} },
    adoptNarrativeResponse, renderAssets() {}, workflowPreparation: { refreshSelection() {} }, markRevisionStale() {},
  });
  vm.runInContext(`${guard}\n${handler}`, context);
  await context.renameSequenceSubject(sequence.subjects[0], "Alice");
  assert.equal(calls, 0);
  state.dirty = false; state.activeProjectType = "none";
  await context.renameSequenceSubject(sequence.subjects[0], "Alice");
  assert.equal(calls, 0);
  state.activeProjectType = "filesystem";
  await context.renameSequenceSubject(sequence.subjects[0], "   ");
  assert.equal(calls, 0);
  await context.renameSequenceSubject(sequence.subjects[0], "Alice");
  assert.equal(calls, 1);
  assert.equal(state.projectSequence, sequence);
  assert.match(source, /subjectSet.append\(new Option\(option.label, option.value/);
  assert.match(source, /subjectLabel\(comparisonSubjects, subject.id\)/);
  assert.match(source, /save.addEventListener\("click", \(\) => renameSequenceSubject/);
  assert.match(source, /filesystemProjectId: state.activeProjectType === "filesystem"/);
});

test("actual grouped name editor appears after initial save and deliberately rejects clearing", async () => {
  const source = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  const renderer = source.match(/function renderAssets\([^\n]*\) \{[\s\S]*?\n\}/)[0];
  const doc = { createElement: tag => new Element(tag, doc) };
  const container = doc.createElement("div");
  const state = { mode: "Ref2VA", activeProjectType: "none", projectSequence: null,
    subjects: sequence.subjects.map(({ id, number }) => ({ id, number })) };
  const calls = [];
  const context = vm.createContext({ state, document: doc, $: () => container, visibleAssets: () => [],
    availableSubjects, groupReferenceAssets, renderCreativeControlsUi() {}, applyCanonicalAssetLabels() {},
    renameSequenceSubject: (subject, name) => calls.push([subject.id, name]), deleteSequenceSubject() {},
  });
  vm.runInContext(renderer, context);
  context.renderAssets({ refreshManifest: false });
  assert.equal(container.querySelectorAll("input").length, 0);
  // The first save adopts canonical Project ownership before rendering the controls.
  const saveSource = source.match(/async function saveProject\([^\n]*\) \{[\s\S]*?\n\}/)[0];
  assert.ok(saveSource.indexOf('state.activeProjectType = "filesystem"') < saveSource.indexOf("renderAssets();"));
  state.activeProjectType = "filesystem"; state.projectSequence = sequence;
  sequence.subjects[0].characterProfile = { roleBackground: "Legacy conservator", personality: "Careful", notes: "Current notes" };
  context.renderAssets({ refreshManifest: false });
  assert.equal(container.querySelectorAll(".subject-narrative-name").length, 3);
  assert.match(container.textContent, /Alice \(Subject 1\)/);
  assert.match(container.textContent, /Bob \(Subject 3\)/);
  assert.doesNotMatch(container.textContent, /Previous names/);
  assert.match(container.textContent, /Saved names cannot be cleared/);
  const profiles = container.querySelectorAll(".character-profile");
  assert.equal(profiles.length, 3);
  assert.equal(Boolean(profiles[0].open), false); // collapsed by default
  assert.match(profiles[0].textContent, /Stable character notes.*not current plot state/i);
  const profileFields = profiles[0].querySelectorAll("textarea");
  assert.equal(profileFields.length, 1);
  assert.match(profiles[0].textContent, /Character Profile/);
  assert.equal(profileFields[0].value, "Current notes");
  profileFields[0].value = "Archive conservator"; await profileFields[0].emit("input");
  assert.equal(sequence.subjects[0].characterProfile.notes, "Archive conservator");
  assert.equal(sequence.subjects[0].characterProfile.roleBackground, "Legacy conservator");
  assert.equal(sequence.subjects[0].characterProfile.personality, "Careful");
  const updatesSource = source.match(/function characterProfileUpdates\(\) \{[\s\S]*?\n\}/)[0];
  vm.runInContext(updatesSource, context);
  const savedProfile = context.characterProfileUpdates()[0].characterProfile;
  assert.equal(savedProfile.roleBackground, "Legacy conservator");
  assert.equal(savedProfile.notes, "Archive conservator");
  const editors = container.querySelectorAll(".subject-narrative-name");
  const input = editors[1].querySelector("input"), button = editors[1].querySelector("button");
  input.value = ""; await input.emit("input");
  assert.equal(button.disabled, true);
  input.value = "Captain Reynolds"; await input.emit("input");
  assert.equal(button.disabled, false);
  await button.emit("click");
  assert.deepEqual(calls, [["other ?/#", "Captain Reynolds"]]);
  assert.equal(editors[2].querySelector("button").disabled, true);
  assert.deepEqual(state.subjects, sequence.subjects.map(({ id, number }) => ({ id, number })));
});
