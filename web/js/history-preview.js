export const HISTORY_PREVIEW_LIMIT = 240;

export function historyPreview(prompt, limit = HISTORY_PREVIEW_LIMIT) {
  const compact = String(prompt ?? "").trim().replace(/\s+/g, " ");
  const characters = Array.from(compact);
  return characters.length > limit ? `${characters.slice(0, limit).join("")}…` : compact;
}

export function fullHistoryPrompt(entry) {
  return entry.prompt;
}
