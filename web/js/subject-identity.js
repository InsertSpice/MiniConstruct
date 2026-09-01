export const IDENTITY_FOCUS_OPTIONS = [
  ["general", "General identity", "Overall recognizable identity and current appearance; consistent visible wardrobe may contribute to current appearance."],
  ["face", "Facial identity", "Primary facial-likeness reference for defining facial traits."],
  ["full_body", "Full-body / appearance", "Overall body proportions, silhouette, current clothing, footwear, accessories, and complete visible character appearance."],
  ["outfit", "Outfit / clothing", "Wardrobe is the primary reference purpose; use for stronger authority, fine detail, or disambiguation."],
  ["detail", "Detail reference", "A specialized identity detail described in Notes."],
];

export const IDENTITY_VIEW_OPTIONS = [
  ["unspecified", "Unspecified", "No particular viewpoint is specified."],
  ["front", "Front", "Front-facing appearance."],
  ["three_quarter", "Three-quarter", "Three-quarter facial or body appearance."],
  ["profile", "Profile / side", "Side-view facial silhouette, hair, and visible design."],
  ["rear", "Rear / back", "Rear hair, clothing, and silhouette."],
];

export const REFERENCE_LAYOUT_OPTIONS = [
  ["auto", "Auto", "With vision, the writer may recognize a clear reference sheet; without vision it remains conservative."],
  ["single_view", "Single view", "One primary viewpoint; View metadata remains active."],
  ["reference_sheet", "Reference sheet", "Multiple complementary depictions of the assigned Subject; never target-video composition."],
];

const validFocus = new Set(IDENTITY_FOCUS_OPTIONS.map(([value]) => value));
const validView = new Set(IDENTITY_VIEW_OPTIONS.map(([value]) => value));
const validLayout = new Set(REFERENCE_LAYOUT_OPTIONS.map(([value]) => value));

export function defaultSubjectIdentity() {
  return { subjectId: "subject-1", focus: "general", view: "unspecified", layout: "auto" };
}

export function normalizeSubjectIdentity(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    subjectId: typeof source.subjectId === "string" && source.subjectId.trim() ? source.subjectId : "subject-1",
    focus: validFocus.has(source.focus) ? source.focus : "general",
    view: validView.has(source.view) ? source.view : "unspecified",
    layout: validLayout.has(source.layout) ? source.layout : "auto",
  };
}

export function normalizeSubjectRegistry(subjects, assets, nextSubjectNumber = 1) {
  const seen = new Set(); const normalized = []; let next = Math.max(1, Number.isInteger(nextSubjectNumber) ? nextSubjectNumber : 1);
  for (const record of Array.isArray(subjects) ? subjects : []) {
    if (!record || typeof record.id !== "string" || !record.id.trim() || seen.has(record.id)) continue;
    const number = Number.isInteger(record.number) && record.number > 0 ? record.number : next;
    if (normalized.some(subject => subject.number === number)) continue;
    seen.add(record.id); normalized.push({ id: record.id, number }); next = Math.max(next, number + 1);
  }
  for (const asset of (Array.isArray(assets) ? assets : []).filter(asset => asset?.kind === "image" && asset.role === "subject_identity")) {
    const identity = asset.subjectIdentity = normalizeSubjectIdentity(asset.subjectIdentity);
    if (!seen.has(identity.subjectId)) {
      const number = identity.subjectId === "subject-1" && !normalized.length ? 1 : next++;
      normalized.push({ id: identity.subjectId, number }); seen.add(identity.subjectId);
    }
  }
  normalized.sort((a, b) => a.number - b.number || a.id.localeCompare(b.id));
  return { subjects: normalized, nextSubjectNumber: Math.max(next, ...normalized.map(subject => subject.number + 1), 1) };
}

export function createSubject(registry) {
  const number = Math.max(1, registry.nextSubjectNumber || 1);
  return { subject: { id: `subject-${number}`, number }, nextSubjectNumber: number + 1 };
}

export function subjectLabel(subjects, id) { const subject = (subjects || []).find(item => item.id === id); return subject ? `Subject ${subject.number}` : "Subject"; }

export function isSubjectIdentityAsset(asset) {
  return asset?.kind === "image" && asset.role === "subject_identity";
}

export function isComparisonAsset(asset) { return asset?.kind === "image" && asset.role === "character_comparison_scale"; }

export function normalizedComparisonSubjects(asset, subjects) {
  const known = new Set((subjects || []).map(subject => subject.id));
  return [...new Set((Array.isArray(asset?.comparisonSubjects) ? asset.comparisonSubjects : []).filter(id => known.has(id)))];
}

export function subjectIdentityNotesPlaceholder(value) {
  const identity = normalizeSubjectIdentity(value);
  if (identity.layout === "reference_sheet") return "Optional: factual sheet details or constraints, e.g. front, three-quarter and rear turnaround of the same character. Do not restate every visible panel.";
  if (identity.focus === "face") return "Optional: note subtle or important facial traits such as eye shape/iris design, brows, cheek marks, freckles, moles, scars, bangs, or facial proportions.";
  if (identity.focus === "full_body") return "Optional: add only important full-character details; plainly visible clothing need not be restated.";
  if (identity.focus === "outfit") return "Optional: describe important outfit details, e.g. These gold buttons, red bow shape, and pink shoes are important outfit details.";
  if (identity.view === "rear") return "Optional: describe important rear-view hair, clothing or silhouette details.";
  if (identity.view === "profile") return "Optional: describe side-view facial silhouette, hair or other profile details.";
  if (identity.focus === "detail") return "Optional: describe the specialized identity detail this reference provides.";
  return "Optional: describe identity facts or constraints this reference should preserve.";
}

export function subjectIdentityHelperText(value) {
  const identity = normalizeSubjectIdentity(value);
  const focus = IDENTITY_FOCUS_OPTIONS.find(([item]) => item === identity.focus)[2];
  if (identity.layout === "reference_sheet") return `${focus} This is complementary evidence for one Subject; its panels, labels, repeated depictions, and expressions are not target-video composition or a required sequence.`;
  const view = IDENTITY_VIEW_OPTIONS.find(([item]) => item === identity.view)[2];
  return identity.view === "unspecified" ? focus : `${focus} ${view}`;
}

export function subjectIdentityNotesGuidance(value) {
  const identity = normalizeSubjectIdentity(value);
  if (identity.layout === "reference_sheet") return "Optional factual emphasis for a manually designated reference sheet. State only known identity, turnaround, wardrobe, material, prop, or expression-range facts; its panels and annotations must not be recreated in the target video.";
  let example = "Example: State the identity detail and why it matters.";
  if (identity.focus === "face") example = "Example: Small paired cheek marks under both eyes are an important identity trait.";
  else if (identity.focus === "full_body") example = "Example: Use Notes only for important full-character details; plainly visible clothing does not need to be restated.";
  else if (identity.focus === "outfit") example = "Example: These gold buttons, red bow shape, and pink shoes are important outfit details.";
  else if (identity.view === "profile") example = "Example: The pointed nose profile and long side bang are important.";
  else if (identity.view === "rear") example = "Example: Hair reaches mid-back and splits into two tapered sections.";
  return `Optional factual emphasis for subtle or especially important identity details that vision may underweight. State the feature and why it matters; MiniConstruct handles the prompting. ${example}`;
}
