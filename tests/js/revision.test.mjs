import test from "node:test";
import assert from "node:assert/strict";

import {
  applyRevisionCandidate, assessRevisionFindings, createRevisionSnapshot, createSelectionSnapshot,
  reconstructsPrompt, revisionIsStale, selectionFromHighlighted, selectionFromTextarea,
} from "../../web/js/revision.js";


test("raw textarea snapshot preserves multiline selection and repeated text", () => {
  const prompt = "same\n[Shot 1] first\nsame\n[Shot 2] second\nsame";
  const start = prompt.indexOf("same", 10);
  const end = prompt.indexOf("\nsame", start + 1);
  const snapshot = selectionFromTextarea({ selectionStart: start, selectionEnd: end }, prompt);
  assert.equal(snapshot.selectedText, "same\n[Shot 2] second");
  assert.equal(snapshot.beforeSelection + "REVISED" + snapshot.afterSelection, "same\n[Shot 1] first\nREVISED\nsame");
  assert.ok(reconstructsPrompt(snapshot));
});


test("UTF-16 selection handles emoji and Japanese without offset conversion", () => {
  const prompt = "🎬 前置き\n[Shot 1] 彼女は言う <d>[Japanese] 行こう。</d>\n終わり";
  const selected = "[Shot 1] 彼女は言う <d>[Japanese] 行こう。</d>";
  const start = prompt.indexOf(selected);
  const snapshot = createSelectionSnapshot(prompt, start, start + selected.length);
  assert.equal(snapshot.selectedText, selected);
  assert.equal(snapshot.beforeSelection, "🎬 前置き\n");
  assert.ok(reconstructsPrompt(snapshot));
});


test("one snapshot can cover two adjacent shots with surrounding blank lines untouched", () => {
  const prompt = "[Shot 1] before\n\n[Shot 2] middle A\n[Shot 3] middle B\n\n[Shot 4] after";
  const selected = "[Shot 2] middle A\n[Shot 3] middle B";
  const start = prompt.indexOf(selected);
  const snapshot = createSelectionSnapshot(prompt, start, start + selected.length);
  assert.equal(snapshot.beforeSelection, "[Shot 1] before\n\n");
  assert.equal(snapshot.afterSelection, "\n\n[Shot 4] after");
  assert.equal(snapshot.beforeSelection + "REVISED" + snapshot.afterSelection, "[Shot 1] before\n\nREVISED\n\n[Shot 4] after");
});


test("highlighted mapping uses DOM range position rather than searching text", () => {
  const prompt = "same first same second";
  const node = { prefix: "same first ", text: "same second" };
  const root = {
    contains: candidate => candidate === node,
    ownerDocument: { createRange: () => ({
      setEnd(target, offset) { this.target = target; this.offset = offset; },
      selectNodeContents() {},
      toString() { return this.target.prefix + this.target.text.slice(0, this.offset); },
    }) },
  };
  const selection = {
    rangeCount: 1, isCollapsed: false,
    getRangeAt: () => ({ startContainer: node, endContainer: node, startOffset: 0, endOffset: 4, toString: () => "same" }),
  };
  const snapshot = selectionFromHighlighted(root, selection, prompt);
  assert.equal(snapshot.beforeSelection, "same first ");
  assert.equal(snapshot.selectedText, "same");
});


test("apply changes only the originating active output", () => {
  const outputs = [{ prompt: "before selected after", validation: null }, { prompt: "other", validation: null }];
  const selection = createSelectionSnapshot(outputs[0].prompt, 7, 15);
  const revision = createRevisionSnapshot(outputs, 0, selection, "change", {}, {});
  Object.assign(revision, { candidatePrompt: "before replacement after", validation: { valid: true, findings: [] } });
  assert.equal(applyRevisionCandidate(outputs, 0, revision), true);
  assert.equal(outputs[0].prompt, "before replacement after");
  assert.equal(outputs[1].prompt, "other");
  assert.equal(outputs[0].revised, true);
});


test("stale source or variation prevents apply", () => {
  const outputs = [{ prompt: "before selected after" }, { prompt: "other" }];
  const revision = createRevisionSnapshot(outputs, 0, createSelectionSnapshot(outputs[0].prompt, 7, 15), "change", {}, {});
  revision.candidatePrompt = "candidate";
  assert.equal(revisionIsStale(outputs, 1, revision), true);
  assert.equal(applyRevisionCandidate(outputs, 1, revision), false);
  outputs[0].prompt = "manual edit";
  assert.equal(applyRevisionCandidate(outputs, 0, revision), false);
});


test("retry snapshot remains anchored to the original source and selection", () => {
  const outputs = [{ prompt: "A selected B" }];
  const selection = createSelectionSnapshot(outputs[0].prompt, 2, 10);
  const revision = createRevisionSnapshot(outputs, 0, selection, "first instruction", { mode: "T2VA" }, { modelId: "writer" });
  revision.preview = "failed preview";
  revision.instruction = "edited retry instruction";
  assert.equal(revision.sourcePrompt, "A selected B");
  assert.equal(revision.selection.selectedText, "selected");
  assert.equal(revision.preview, "failed preview");
});


test("request snapshot strips UI-only selection metadata", () => {
  const outputs = [{ prompt: "one two three" }];
  const selection = { ...createSelectionSnapshot(outputs[0].prompt, 4, 7), outputIndex: 0 };
  const revision = createRevisionSnapshot(outputs, 0, selection, "revise", {}, {});
  assert.deepEqual(Object.keys(revision.selection).sort(), [
    "afterSelection", "beforeSelection", "fullPrompt", "selectedText",
  ]);
});


test("revision assessment permits workspace divergence and recognizes pre-existing findings", () => {
  const shotMismatch = {
    severity: "ERROR", category: "workspace_consistency", code: "shot_count",
    message: "Requested 4 shots, but the output contains 3.",
  };
  const malformed = {
    severity: "ERROR", category: "structural", code: "missing_shot_time",
    message: "Shot 2 requires an MM:SS.mmm cut time.",
  };
  const changedMalformed = { ...malformed, message: "Shot 3 requires an MM:SS.mmm cut time." };
  const mismatchOnly = assessRevisionFindings({ findings: [shotMismatch] }, { findings: [] });
  assert.equal(mismatchOnly.newStructural.length, 0);
  assert.equal(mismatchOnly.workspaceMismatches.length, 1);

  const preexisting = assessRevisionFindings({ findings: [malformed] }, { findings: [malformed] });
  assert.equal(preexisting.newStructural.length, 0);
  assert.equal(preexisting.preexistingStructural.length, 1);

  const introduced = assessRevisionFindings({ findings: [changedMalformed] }, { findings: [malformed] });
  assert.equal(introduced.newStructural.length, 1);
});
