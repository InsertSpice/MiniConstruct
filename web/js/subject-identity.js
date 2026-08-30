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

const validFocus = new Set(IDENTITY_FOCUS_OPTIONS.map(([value]) => value));
const validView = new Set(IDENTITY_VIEW_OPTIONS.map(([value]) => value));

export function defaultSubjectIdentity() {
  return { focus: "general", view: "unspecified" };
}

export function normalizeSubjectIdentity(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    focus: validFocus.has(source.focus) ? source.focus : "general",
    view: validView.has(source.view) ? source.view : "unspecified",
  };
}

export function isSubjectIdentityAsset(asset) {
  return asset?.kind === "image" && asset.role === "subject_identity";
}

export function subjectIdentityNotesPlaceholder(value) {
  const identity = normalizeSubjectIdentity(value);
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
  const view = IDENTITY_VIEW_OPTIONS.find(([item]) => item === identity.view)[2];
  return identity.view === "unspecified" ? focus : `${focus} ${view}`;
}

export function subjectIdentityNotesGuidance(value) {
  const identity = normalizeSubjectIdentity(value);
  let example = "Example: State the identity detail and why it matters.";
  if (identity.focus === "face") example = "Example: Small paired cheek marks under both eyes are an important identity trait.";
  else if (identity.focus === "full_body") example = "Example: Use Notes only for important full-character details; plainly visible clothing does not need to be restated.";
  else if (identity.focus === "outfit") example = "Example: These gold buttons, red bow shape, and pink shoes are important outfit details.";
  else if (identity.view === "profile") example = "Example: The pointed nose profile and long side bang are important.";
  else if (identity.view === "rear") example = "Example: Hair reaches mid-back and splits into two tapered sections.";
  return `Optional factual emphasis for subtle or especially important identity details that vision may underweight. State the feature and why it matters; MiniConstruct handles the prompting. ${example}`;
}
