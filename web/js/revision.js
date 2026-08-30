export function createSelectionSnapshot(fullPrompt, start, end) {
  const source = String(fullPrompt ?? "");
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > source.length || start >= end) return null;
  const snapshot = {
    fullPrompt: source,
    beforeSelection: source.slice(0, start),
    selectedText: source.slice(start, end),
    afterSelection: source.slice(end),
  };
  return snapshot.selectedText.trim() && reconstructsPrompt(snapshot) ? snapshot : null;
}

export function reconstructsPrompt(snapshot) {
  return snapshot.beforeSelection + snapshot.selectedText + snapshot.afterSelection === snapshot.fullPrompt;
}

export function findingSignature(finding) {
  return [
    finding.category || "structural",
    finding.severity || "ERROR",
    finding.code || "",
    finding.message || "",
  ].join("\u0000");
}

export function assessRevisionFindings(candidateValidation, originalValidation) {
  const candidate = candidateValidation?.findings || [];
  const originalErrors = new Set(
    (originalValidation?.findings || [])
      .filter(finding => finding.severity === "ERROR")
      .map(findingSignature),
  );
  const errors = candidate.filter(finding => finding.severity === "ERROR");
  const workspaceMismatches = errors.filter(finding => finding.category === "workspace_consistency");
  const structuralErrors = errors.filter(finding => finding.category !== "workspace_consistency");
  const preexistingStructural = structuralErrors.filter(finding => originalErrors.has(findingSignature(finding)));
  const newStructural = structuralErrors.filter(finding => !originalErrors.has(findingSignature(finding)));
  return { workspaceMismatches, structuralErrors, preexistingStructural, newStructural };
}

export function selectionFromTextarea(textarea, fullPrompt) {
  return createSelectionSnapshot(fullPrompt, textarea.selectionStart, textarea.selectionEnd);
}

export function selectionFromHighlighted(root, selection, fullPrompt) {
  if (!selection || selection.rangeCount !== 1 || selection.isCollapsed) return null;
  const selectedRange = selection.getRangeAt(0);
  if (!root.contains(selectedRange.startContainer) || !root.contains(selectedRange.endContainer)) return null;
  const prefix = root.ownerDocument.createRange();
  prefix.selectNodeContents(root);
  prefix.setEnd(selectedRange.startContainer, selectedRange.startOffset);
  const start = prefix.toString().length;
  const selectedText = selectedRange.toString();
  const snapshot = createSelectionSnapshot(fullPrompt, start, start + selectedText.length);
  return snapshot?.selectedText === selectedText ? snapshot : null;
}

export function createRevisionSnapshot(outputs, outputIndex, selection, instruction, workspace, llm) {
  const originOutput = outputs[outputIndex];
  return {
    originIndex: outputIndex,
    originOutput,
    sourcePrompt: originOutput?.prompt ?? "",
    selection: structuredClone({
      fullPrompt: selection.fullPrompt,
      beforeSelection: selection.beforeSelection,
      selectedText: selection.selectedText,
      afterSelection: selection.afterSelection,
    }),
    instruction,
    workspace: structuredClone(workspace),
    llm: structuredClone(llm),
  };
}

export function revisionIsStale(outputs, activeIndex, revision) {
  return !revision
    || activeIndex !== revision.originIndex
    || outputs[revision.originIndex] !== revision.originOutput
    || revision.originOutput?.prompt !== revision.sourcePrompt;
}

export function applyRevisionCandidate(outputs, activeIndex, revision) {
  if (!revision.candidatePrompt || revisionIsStale(outputs, activeIndex, revision)) return false;
  revision.originOutput.prompt = revision.candidatePrompt;
  revision.originOutput.validation = revision.validation;
  revision.originOutput.revised = true;
  return true;
}
