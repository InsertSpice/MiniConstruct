import assert from "node:assert/strict";
import test from "node:test";
import { requestFactLines } from "../../web/js/generation-diagnostics.js";
import { buildGenerationRequest } from "../../web/js/generation-request.js";
import { storyPlannerSettings } from "../../web/js/story-planning.js";

test("generation diagnostics expose the actual request facts concisely", () => {
  const lines = requestFactLines({
    requestSnapshot: { authoringPath: "story", llm: { reasoningMode: "off", maxTokens: 4096 } },
    diagnostics: { authoringPath: "story", reasoningMode: "off", assembledTextChars: 42722,
      estimatedInputTokens: 12000, contextWindow: 65536, maxOutputTokens: 4096 },
  });
  assert.deepEqual(lines, [
    "Authoring path: Story",
    "Reasoning: Off",
    "Text characters: 42,722",
    "Estimated input tokens: 12,000 (includes image reserve)",
    "Backend context capacity: 65,536 tokens · Max output: 4,096 tokens",
  ]);
});

test("Generate from Story preserves the global H3 reasoning setting from Main Prompt", () => {
  const workspace = {};
  for (const reasoningMode of ["off", "on"]) {
    const llm = { modelId: "same-model", reasoningMode };
    const main = buildGenerationRequest({ workspace, llm, authoringPath: "one_shot" });
    const story = buildGenerationRequest({ workspace, llm, authoringPath: "story" });
    assert.equal(story.authoringPath, "story");
    assert.equal(story.llm.reasoningMode, main.llm.reasoningMode);
  }
});

test("Story Planner reasoning on cannot change Generate from Story reasoning off", () => {
  const globalH3 = { modelId: "same-model", reasoningMode: "off" };
  const planner = storyPlannerSettings(globalH3, { timeoutSeconds: 600, reasoning: true });
  const request = buildGenerationRequest({ workspace: {}, llm: globalH3, authoringPath: "story" });
  assert.equal(planner.reasoningMode, "on");
  assert.equal(request.llm.reasoningMode, "off");
});
