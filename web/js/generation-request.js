export function buildGenerationRequest({ workspace, llm, filesystemProjectId = null, generationId = null, sequenceDialogueLanguage, executionRouting = { assets: {} }, authoringPath = "one_shot" }) {
  return { workspace, llm, filesystemProjectId, generationId, sequenceDialogueLanguage, executionRouting, authoringPath };
}
