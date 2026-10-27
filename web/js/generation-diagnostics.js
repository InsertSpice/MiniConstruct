export function requestFactLines(session) {
  const diagnostic = session.diagnostics || {};
  const authoringPath = diagnostic.authoringPath || session.requestSnapshot.authoringPath;
  const reasoningMode = diagnostic.reasoningMode || session.requestSnapshot.llm.reasoningMode;
  const reasoning = reasoningMode === "on" ? "On" : reasoningMode === "off" ? "Off" : "Backend default";
  return [
    `Authoring path: ${authoringPath === "story" ? "Story" : "Main Prompt"}`,
    `Reasoning: ${reasoning}`,
    `Text characters: ${(diagnostic.assembledTextChars ?? 0).toLocaleString()}`,
    `Estimated input tokens: ${diagnostic.estimatedInputTokens?.toLocaleString() ?? "waiting"} (includes image reserve)`,
    `Backend context capacity: ${diagnostic.contextWindow?.toLocaleString() ?? "unknown"} tokens · Max output: ${diagnostic.maxOutputTokens?.toLocaleString() ?? session.requestSnapshot.llm.maxTokens?.toLocaleString() ?? "unknown"} tokens`,
  ];
}
