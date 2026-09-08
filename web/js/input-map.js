const GROUPS = [
  ["image", "Pictures"],
  ["video", "Videos"],
  ["audio", "Audio"],
];

// Presentation only: the backend supplies each entry's canonical h3Label.
export function groupInputMapEntries(entries) {
  const source = Array.isArray(entries) ? entries : [];
  return GROUPS
    .map(([kind, label]) => ({ kind, label, entries: source.filter(entry => entry?.kind === kind) }))
    .filter(group => group.entries.length);
}

export function inputMapLabelForAsset(entries, assetId) {
  return (Array.isArray(entries) ? entries : []).find(entry => entry?.id === assetId)?.h3Label || "";
}

export function inputMapRefreshStatus(hasSuccessfulManifest) {
  return hasSuccessfulManifest ? "loaded" : "pending";
}

export function inputMapEmptyMessage({ assetCount = 0, status = "pending" } = {}) {
  if (status === "pending") return "Updating H3 input identifiers…";
  if (status === "failed") return "Could not update H3 input identifiers.";
  return assetCount ? "No H3 reference inputs are active for this mode." : "Add reference assets to see their H3 input identifiers.";
}
