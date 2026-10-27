import { formatLocalTimestamp } from "./local-time.js";

// Presentation order only. IDs remain the sole authority for provenance/actions.
export function renderSubmissionLabels(submissions = []) {
  const ordered = [...submissions].sort((left, right) =>
    String(left.createdAt).localeCompare(String(right.createdAt)) || String(left.id).localeCompare(String(right.id)));
  return new Map(ordered.map((submission, index) =>
    [submission.id, `ComfyUI send ${index + 1} · ${formatLocalTimestamp(submission.createdAt)}`]));
}
