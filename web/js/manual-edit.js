export function markOutputManuallyEdited(output, prompt) {
  if (!output) return output;
  const edited = { ...output, prompt, sourceFingerprint: null, origin: "manual_edit", validation: null };
  delete edited.referenceMap;
  delete edited.currentAudit;
  delete edited.historyItem;
  delete edited.historyMode;
  return edited;
}
