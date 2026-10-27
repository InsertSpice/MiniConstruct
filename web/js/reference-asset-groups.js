import { normalizeSubjectIdentity, structuralSubjectIds, subjectDisplayLabel } from "./subject-identity.js";

const identityFocusLabels = {
  general: "General identity",
  face: "Facial identity",
  full_body: "Full-body / appearance",
  outfit: "Outfit / clothing",
  detail: "Detail reference",
};

const referenceLayoutLabels = {
  auto: "Auto",
  single_view: "Single View",
  reference_sheet: "Reference Sheet",
};

const sharedRoleGroups = {
  character_comparison_scale: "Character Comparison / Scale",
  first_frame_anchor: "Frame / Keyframe Anchors",
  keyframe_anchor: "Frame / Keyframe Anchors",
  last_frame_anchor: "Frame / Keyframe Anchors",
  storyboard_composition: "Storyboard / Composition",
  continuity_state: "Continuity / General Visual",
  general_visual: "Continuity / General Visual",
  style_appearance: "Continuity / General Visual",
};

export function subjectIdentityPresentation(value) {
  const identity = normalizeSubjectIdentity(value);
  return {
    category: identity.layout === "reference_sheet" || !["full_body", "outfit"].includes(identity.focus)
      ? "Identity" : "Appearance",
    metadata: `${referenceLayoutLabels[identity.layout]} · ${identityFocusLabels[identity.focus]}`,
  };
}

function canonicalOrder(items) {
  return [...items].sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || String(a.id).localeCompare(String(b.id)));
}

function appendSharedAsset(groups, label, asset) {
  let group = groups.get(label);
  if (!group) {
    group = { label, assets: [] };
    groups.set(label, group);
  }
  group.assets.push(asset);
}

/**
 * Derives the semantic authoring layout from canonical assets. It never mutates
 * assets, subjects, IDs, or their persisted order.
 */
export function groupReferenceAssets({ assets = [], subjects = [] } = {}) {
  const sortedSubjects = [...subjects]
    .filter(subject => subject?.id && Number.isInteger(subject.number))
    .sort((a, b) => a.number - b.number || a.id.localeCompare(b.id));
  const structurallyUsed = structuralSubjectIds(assets);
  const subjectGroups = sortedSubjects.map(subject => ({
    subject,
    label: subjectDisplayLabel(subject),
    structuralUse: structurallyUsed.has(subject.id),
    groups: new Map([["Identity", { label: "Identity", assets: [] }], ["Appearance", { label: "Appearance", assets: [] }]]),
  }));
  const subjectById = new Map(subjectGroups.map(group => [group.subject.id, group]));
  const environment = [];
  const shared = new Map();

  for (const asset of canonicalOrder(assets)) {
    if (asset?.kind === "image" && asset.role === "subject_identity") {
      const identity = normalizeSubjectIdentity(asset.subjectIdentity);
      const subjectGroup = subjectById.get(identity.subjectId);
      if (subjectGroup) subjectGroup.groups.get(subjectIdentityPresentation(identity).category).assets.push(asset);
      else appendSharedAsset(shared, "Unassigned Subject Identity", asset);
    } else if (asset?.kind === "image" && asset.role === "environment") {
      environment.push(asset);
    } else if (asset?.kind === "video") {
      appendSharedAsset(shared, "Video", asset);
    } else if (asset?.kind === "audio") {
      appendSharedAsset(shared, "Audio", asset);
    } else {
      appendSharedAsset(shared, sharedRoleGroups[asset?.role] || "Continuity / General Visual", asset);
    }
  }

  return {
    subjectGroups: subjectGroups.map(group => ({
      ...group,
      groups: [...group.groups.values()].filter(item => item.assets.length),
    })),
    environment: { label: "Environment", assets: environment },
    sharedGroups: [...shared.values()],
    hasAssets: assets.length > 0,
  };
}
