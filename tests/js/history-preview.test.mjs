import assert from "node:assert/strict";
import test from "node:test";

import { fullHistoryPrompt, HISTORY_PREVIEW_LIMIT, historyPreview } from "../../web/js/history-preview.js";

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
