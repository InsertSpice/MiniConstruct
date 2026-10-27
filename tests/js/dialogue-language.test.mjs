import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";

import { api } from "../../web/js/api.js";
import { effectiveDialogueLanguage, dialogueResolutionIsCurrent, dialogueResolutionNeedsProvider, staleDialogueResolution, editedDialogueResolution } from "../../web/js/dialogue-language-state.js";
import { buildGenerationRequest } from "../../web/js/generation-request.js";

test("Dialogue Language exposes only As Written and the eleven stable H3 values", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const select = html.match(/<select id="dialogue-language"[\s\S]*?<\/select>/)?.[0] || "";
  const values = [...select.matchAll(/<option value="([^"]+)"/g)].map(match => match[1]);
  assert.deepEqual(values, ["as_written", "ar", "zh", "en", "fr", "de", "it", "ja", "ko", "pt", "ru", "es"]);
  assert.match(select, />As Written</);
  assert.doesNotMatch(html, /subject-dialogue-language|per-Subject language/i);
});

test("source/resolved controls preserve both fields and send explicit refresh requests", async () => {
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(app, /dialogueLanguage: state\.dialogueLanguage/);
  assert.match(app, /dialogueResolution: state\.dialogueResolution/);
  assert.match(app, /editedDialogueResolution/);
  assert.match(app, /markDialogueResolutionStale/);
  const originalFetch = globalThis.fetch;
  let body;
  globalThis.fetch = async (_url, options) => { body = JSON.parse(options.body); return { ok: true, json: async () => ({ resolution: null }) }; };
  try {
    await api.resolveDialogue({ workspace: { dialogue: "Kent: Run.", dialogueLanguage: "ja" }, llm: { modelId: "local" } });
    assert.equal(body.workspace.dialogue, "Kent: Run.");
    assert.equal(body.workspace.dialogueLanguage, "ja");
  } finally { globalThis.fetch = originalFetch; }
});

test("filesystem Project authority survives Generation switching and dialogue edits", () => {
  const japanese = { sourceText: "Kent: Run.", targetLanguage: "ja", resolvedText: "Kent: 走れ。", status: "current", sourceFingerprint: "f".repeat(64) };
  const project = { dialogueLanguage: "ja" };
  const first = { dialogueLanguage: "as_written", dialogue: "Kent: Run.", dialogueResolution: japanese };
  const second = { dialogueLanguage: "as_written", dialogue: "Mira: Wait.", dialogueResolution: null };
  let language = effectiveDialogueLanguage(project.dialogueLanguage, first.dialogueLanguage);
  assert.equal(language, "ja");
  language = effectiveDialogueLanguage(project.dialogueLanguage, second.dialogueLanguage);
  assert.equal(language, "ja");
  assert.equal(dialogueResolutionIsCurrent({ dialogueLanguage: language, dialogue: first.dialogue, resolution: japanese }), true);
  const staleSource = staleDialogueResolution(japanese);
  assert.equal(dialogueResolutionIsCurrent({ dialogueLanguage: language, dialogue: "Kent: Hide.", resolution: staleSource }), false);
  const staleTarget = { ...japanese, targetLanguage: "en", status: "stale" };
  assert.equal(dialogueResolutionIsCurrent({ dialogueLanguage: language, dialogue: first.dialogue, resolution: staleTarget }), false);
  const edited = editedDialogueResolution({ dialogueLanguage: language, dialogue: first.dialogue, resolution: japanese, resolvedText: "Kent: 急いで！" });
  assert.equal(edited.status, "user_edited");
  assert.equal(dialogueResolutionIsCurrent({ dialogueLanguage: language, dialogue: first.dialogue, resolution: edited }), true);
});

test("shared normal generation request carries a dirty Project language into Generate and Assemble", () => {
  const request = buildGenerationRequest({
    workspace: { dialogue: "Kent: We need to leave.", dialogueLanguage: "ja" },
    llm: { modelId: "offline" },
    filesystemProjectId: "project-1",
    sequenceDialogueLanguage: "ja",
  });
  assert.equal(request.sequenceDialogueLanguage, "ja");
  assert.equal(request.filesystemProjectId, "project-1");
});

test("Show Instructions requires provider coordination only for uncached nonblank translated dialogue", async () => {
  const input = { dialogueLanguage: "ja", dialogue: "Kent: Run!", resolution: null };
  assert.equal(await dialogueResolutionNeedsProvider({ ...input, dialogueLanguage: "as_written" }), false);
  assert.equal(await dialogueResolutionNeedsProvider({ ...input, dialogue: "  " }), false);
  const payload = `dialogue-resolution-v1\0ja\0${input.dialogue}`;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
  const sourceFingerprint = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
  const cached = { sourceText: input.dialogue, targetLanguage: "ja", resolvedText: "ケント: 走れ！", status: "current", contractVersion: 1, sourceFingerprint };
  assert.equal(await dialogueResolutionNeedsProvider({ ...input, resolution: cached }), false);
  assert.equal(await dialogueResolutionNeedsProvider({ ...input, resolution: { ...cached, resolvedText: "" } }), true);
  assert.equal(await dialogueResolutionNeedsProvider({ ...input, resolution: { ...cached, sourceFingerprint: "0".repeat(64) } }), true);
  const app = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(app, /if \(!await ensureInstructionsDialogue\(authoringPath\)\) return;/);
  assert.match(app, /const needsProvider = await dialogueResolutionNeedsProvider\(snapshot\);[\s\S]*?await refreshDialogueResolution\(authoringPath\)/);
});
