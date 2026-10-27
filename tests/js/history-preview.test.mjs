import assert from "node:assert/strict";
import test from "node:test";

import { fullHistoryPrompt, HISTORY_PREVIEW_LIMIT, historyPreview, historyRecordFromOutput, historyOutput, showHistoryOutput, currentFormatAudit } from "../../web/js/history-preview.js";

test("History validation remains recorded while current audit is separate", () => {
  const recorded = { valid: false, findings: [{ code: "dialogue_changed" }] };
  const audit = { valid: true, findings: [{ code: "structure_ok" }] };
  const entry = historyRecordFromOutput({ prompt: "Japanese H3", validation: recorded }, { mode: "T2VA" });
  const opened = historyOutput(entry);
  const checked = currentFormatAudit(opened, audit);
  assert.deepEqual(entry.validation, recorded);
  assert.deepEqual(checked.validation, recorded);
  assert.deepEqual(checked.currentAudit, audit);
  assert.equal(historyRecordFromOutput(checked, { mode: "T2VA" }).validation, recorded);
});

test("editing a currently audited History prompt clears its audit", async () => {
  const { markOutputManuallyEdited } = await import("../../web/js/manual-edit.js");
  const opened = historyOutput({ prompt: "old H3", validation: { valid: false } });
  const checked = currentFormatAudit(opened, { valid: true, findings: [] });
  const edited = markOutputManuallyEdited(checked, "new H3");
  assert.equal(edited.currentAudit, undefined);
  assert.equal(edited.validation, null);
  assert.equal(opened.validation.valid, false);
});

test("History previews keep short prompts readable without mutation", () => {
  const prompt = "subject_definitions:\nA quiet scene.";
  assert.equal(historyPreview(prompt), "subject_definitions: A quiet scene.");
  assert.equal(prompt, "subject_definitions:\nA quiet scene.");
});

test("History previews compact and truncate long prompts without tail text", () => {
  const tail = " UNIQUELY-DEEP-TAIL";
  const prompt = `${"a".repeat(HISTORY_PREVIEW_LIMIT + 30)}${tail}`;
  const preview = historyPreview(prompt);
  assert.equal(preview.length, HISTORY_PREVIEW_LIMIT + 1);
  assert.ok(preview.endsWith("…"));
  assert.ok(!preview.includes("UNIQUELY-DEEP-TAIL"));
  assert.equal(prompt.endsWith(tail), true);
});

test("History previews preserve Unicode boundaries and restore full prompt", () => {
  const prompt = `${"日本語🙂 ".repeat(80)}late-tail`;
  const preview = historyPreview(prompt, 20);
  assert.ok(preview.endsWith("…"));
  assert.ok(!preview.includes("late-tail"));
  const entry = { prompt, validation: {} };
  assert.equal(fullHistoryPrompt(entry), prompt);
  assert.equal(entry.prompt, prompt);
});

test("completed History retains exact prompt provenance without replacing fresh variations", () => {
  const fingerprint = "a".repeat(64), referenceMap = [{ label: "Picture 1", assetId: "image-1", channel: "image" }];
  const fresh = { prompt: "exact H3", validation: { valid: true }, sourceFingerprint: fingerprint,
    referenceMap, generationStatus: "complete" };
  const entry = historyRecordFromOutput(fresh, { mode: "Ref2VA", generationId: "generation-a" });
  assert.equal(entry.sourceFingerprint, fingerprint);
  assert.deepEqual(entry.referenceMap, referenceMap);
  const other = { prompt: "other variation", sourceFingerprint: "b".repeat(64) };
  const shown = showHistoryOutput([fresh, other], entry);
  assert.equal(shown.index, 2);
  assert.deepEqual(shown.outputs.slice(0, 2), [fresh, other]);
  assert.equal(shown.outputs[2].prompt, fresh.prompt);
  assert.equal(shown.outputs[2].sourceFingerprint, fingerprint);
  assert.deepEqual(showHistoryOutput(shown.outputs, { ...entry, prompt: "second History item" }).outputs.slice(0, 2), [fresh, other]);
});

test("legacy History stays readable without fabricated provenance", () => {
  const legacy = historyOutput({ prompt: "old exact text", validation: { valid: true } });
  assert.equal(legacy.prompt, "old exact text");
  assert.equal(legacy.sourceFingerprint, null);
  assert.equal(historyOutput({ prompt: "untrusted", sourceFingerprint: "a".repeat(64) }).sourceFingerprint, null);
});

test("Story authoring path survives History and manual edits", async () => {
  const { markOutputManuallyEdited } = await import("../../web/js/manual-edit.js");
  const output = { prompt: "Story H3", validation: { valid: true }, authoringPath: "story",
    sourceFingerprint: "a".repeat(64), generationStatus: "complete" };
  const entry = historyRecordFromOutput(output, { mode: "T2VA" });
  assert.equal(entry.authoringPath, "story");
  assert.equal(historyOutput(entry).authoringPath, "story");
  const edited = markOutputManuallyEdited(output, "Edited Story H3");
  assert.equal(edited.authoringPath, "story");
  assert.equal(edited.origin, "manual_edit");
});
