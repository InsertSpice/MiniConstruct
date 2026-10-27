export const HISTORY_PREVIEW_LIMIT = 240;

export function historyPreview(prompt, limit = HISTORY_PREVIEW_LIMIT) {
  const compact = String(prompt ?? "").trim().replace(/\s+/g, " ");
  const characters = Array.from(compact);
  return characters.length > limit ? `${characters.slice(0, limit).join("")}…` : compact;
}

export function fullHistoryPrompt(entry) {
  return entry.prompt;
}

const fingerprintPattern = /^[0-9a-f]{64}$/;

export function historyRecordFromOutput(output, metadata) {
  const trusted = fingerprintPattern.test(output.sourceFingerprint || "")
    && (!output.generationStatus || output.generationStatus === "complete");
  return {
    ...metadata, prompt: output.prompt, validation: output.validation,
    ...(output.authoringPath ? { authoringPath: output.authoringPath } : {}),
    ...(trusted ? { sourceFingerprint: output.sourceFingerprint, generationStatus: "complete",
      ...(Array.isArray(output.referenceMap) ? { referenceMap: output.referenceMap } : {}) } : {}),
  };
}

export function historyOutput(entry) {
  const trusted = entry.generationStatus === "complete" && fingerprintPattern.test(entry.sourceFingerprint || "");
  return {
    prompt: fullHistoryPrompt(entry), validation: entry.validation, origin: "history",
    historyItem: true,
    sourceFingerprint: trusted ? entry.sourceFingerprint : null,
    ...(entry.authoringPath ? { authoringPath: entry.authoringPath } : {}),
    ...(typeof entry.mode === "string" ? { historyMode: entry.mode } : {}),
    ...(trusted && Array.isArray(entry.referenceMap) ? { referenceMap: entry.referenceMap } : {}),
  };
}

export function currentFormatAudit(output, audit) {
  return { ...output, currentAudit: audit };
}

export function showHistoryOutput(outputs, entry) {
  const next = [...outputs];
  let index = next.findIndex(output => output.origin === "history");
  if (index < 0) index = next.length;
  next[index] = historyOutput(entry);
  return { outputs: next, index };
}
