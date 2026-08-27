import assert from "node:assert/strict";
import test from "node:test";

import { highlightPrompt, tokenizePrompt } from "../../web/js/highlighter.js";

const sample = `subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
[Shot 1] A scene begins. [Shot 2] At 00:04.200, <Subject 1> (S1) sees <Picture 1>, <Video 1>, and <Audio 1>.
[reference generation + audio reference] fully_preserved, partially_preserved, attribute_transfer, weak_reference, fully_copy, partially_copy, reference.
<d>[English] Preserve & <script>alert("x")</script>.</d>`;

test("highlights all H3 structural categories and round-trips exact text", () => {
  const tokens = tokenizePrompt(sample);
  const byType = new Map();
  for (const token of tokens) {
    if (token.type !== "text") byType.set(token.type, [...(byType.get(token.type) || []), token.text]);
  }
  assert.deepEqual(tokens.map(token => token.text).join(""), sample);
  assert.ok(byType.get("section").includes("subject_definitions:"));
  assert.ok(byType.get("section").includes("non_diegetic_music:"));
  assert.ok(byType.get("shot").includes("[Shot 1]"));
  assert.ok(byType.get("shot").includes("[Shot 2] At 00:04.200,"));
  assert.deepEqual(byType.get("reference"), ["<Subject 1>", "<Picture 1>", "<Video 1>", "<Audio 1>"]);
  assert.deepEqual(byType.get("speaker"), ["(S1)"]);
  assert.deepEqual(byType.get("task"), ["[reference generation + audio reference]"]);
  assert.deepEqual(byType.get("retention"), [
    "fully_preserved", "partially_preserved", "attribute_transfer", "weak_reference",
    "fully_copy", "partially_copy", "reference",
  ]);
  assert.deepEqual(byType.get("dialogue-tag"), ["<d>", "</d>"]);
});

test("ordinary, incomplete, and bracketed prose remain harmless text", () => {
  const prose = tokenizePrompt("A [normal bracket] and retention_anal with <Subject are ordinary text.");
  assert.equal(prose.filter(token => token.type !== "text").length, 0);
  assert.equal(prose.map(token => token.text).join(""), "A [normal bracket] and retention_anal with <Subject are ordinary text.");
});

test("rendering uses textContent, never generated HTML", () => {
  const container = {
    children: [],
    replaceChildren() { this.children = []; },
    append(child) { this.children.push(child); },
  };
  globalThis.document = {
    createTextNode(text) { return { textContent: text }; },
    createElement() {
      return { className: "", textContent: "" };
    },
  };
  const malicious = '<script>alert("x")</script> & "quoted"';
  highlightPrompt(container, malicious);
  assert.equal(container.children.map(child => child.textContent).join(""), malicious);
  assert.equal(container.children.some(child => child.className === "syntax-reference"), false);
});
