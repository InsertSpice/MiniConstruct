import { freshVisualHandoff } from "./visual-handoff.js";

export const RELATIONSHIP_OPTIONS = [
  ["continue_previous_shot", "Continue previous shot"],
  ["new_shot_preserve_continuity", "New shot — preserve continuity"],
  ["match_previous_ending_frame", "Match previous ending frame"],
  ["independent", "Independent / new scene"],
];

export const PLAN_SCOPES = [
  ["main_prompt", "Main Prompt / idea"],
  ["dialogue", "Exact Dialogue / Lyrics"],
  ["incoming_state", "Incoming State"],
  ["intended_outgoing_state", "Intended Outgoing State"],
  ["relationship_to_previous", "Relationship to Previous"],
  ["camera_intent", "Shot Plan / Camera Intent"],
];

export function freshPlanning() {
  return { scenePlan: "", relationshipToPrevious: null, cameraIntent: "", visualHandoff: freshVisualHandoff() };
}

export function planningFromProject(project) {
  const planning = project?.planning || {};
  return {
    scenePlan: planning.scenePlan || "",
    relationshipToPrevious: planning.relationshipToPrevious ?? null,
    cameraIntent: planning.cameraIntent || "",
    visualHandoff: { ...freshVisualHandoff(), ...planning.visualHandoff },
  };
}

export function storyShotMismatchMessage({ hasSavedPlanning, plannedShotCount, actualShotCount }) {
  const scenePlanExists = typeof hasSavedPlanning === "string"
    ? Boolean(hasSavedPlanning.trim())
    : hasSavedPlanning === true;
  const planned = Number(plannedShotCount);
  const actualText = String(actualShotCount ?? "").trim();
  if (!scenePlanExists || plannedShotCount == null || !Number.isInteger(planned) || planned < 1
    || !actualText || Number(actualText) === planned) return "";
  const shotWord = planned === 1 ? "shot" : "shots";
  return `Saved planning requests ${planned} ${shotWord}. H3 Setup currently requests ${actualText}.`;
}

export function renderStoryShotMismatch(advisory, authority) {
  advisory.textContent = storyShotMismatchMessage(authority);
  advisory.hidden = !advisory.textContent;
}

const isMissing = value => value == null || (typeof value === "string" && !value.trim());

export function fillMissingPlan({ creativeRequest, dialogue, incomingState, intendedOutgoingState, planning }, proposal) {
  const result = {
    creativeRequest: creativeRequest || "",
    // Infer from Previous is a planning-only operation. Dialogue remains the
    // current canonical Workspace value even if a model echoes another value.
    dialogue: dialogue ?? "",
    incomingState: incomingState || "",
    intendedOutgoingState: intendedOutgoingState || "",
    planning: { ...planning },
  };
  if (isMissing(result.creativeRequest) && !isMissing(proposal?.creativeRequest)) result.creativeRequest = proposal.creativeRequest;
  if (isMissing(result.incomingState) && !isMissing(proposal?.incomingState)) result.incomingState = proposal.incomingState;
  if (isMissing(result.intendedOutgoingState) && !isMissing(proposal?.intendedOutgoingState)) result.intendedOutgoingState = proposal.intendedOutgoingState;
  if (isMissing(result.planning.relationshipToPrevious) && !isMissing(proposal?.relationshipToPrevious)) {
    result.planning.relationshipToPrevious = proposal.relationshipToPrevious;
  }
  if (isMissing(result.planning.cameraIntent) && !isMissing(proposal?.cameraIntent)) result.planning.cameraIntent = proposal.cameraIntent;
  return result;
}

export function applyScopedPlan(current, proposal, scopes) {
  const selected = new Set(scopes || []);
  const result = {
    creativeRequest: current.creativeRequest || "",
    dialogue: current.dialogue ?? "",
    incomingState: current.incomingState || "",
    intendedOutgoingState: current.intendedOutgoingState || "",
    planning: { ...(current.planning || freshPlanning()) },
  };
  if (selected.has("main_prompt")) result.creativeRequest = proposal.creativeRequest;
  if (selected.has("dialogue")) result.dialogue = proposal.dialogue;
  if (selected.has("incoming_state")) result.incomingState = proposal.incomingState;
  if (selected.has("intended_outgoing_state")) result.intendedOutgoingState = proposal.intendedOutgoingState;
  if (selected.has("relationship_to_previous")) result.planning.relationshipToPrevious = proposal.relationshipToPrevious;
  if (selected.has("camera_intent")) result.planning.cameraIntent = proposal.cameraIntent;
  return result;
}

export function safeRecommendations(recommendations) {
  return (recommendations || [])
    .filter(item => item && ["previous_generation", "current_generation"].includes(item.source))
    .filter(item => typeof item.assetId === "string" && item.assetId && typeof item.recommendation === "string")
    .map(item => ({ source: item.source, assetId: item.assetId, recommendation: item.recommendation }));
}

// Use author-facing metadata only; never display stored payload paths or URLs.
export function recommendationAssetLabel(asset, assetId) {
  const filename = typeof asset?.filename === "string" ? asset.filename.trim() : "";
  const basename = /^(?:blob|data):/i.test(filename) ? "" : filename.split(/[\\/]/).pop();
  const role = typeof asset?.role === "string" ? asset.role.trim() : "";
  return [basename || assetId, role].filter(Boolean).join(" · ");
}

export function mergeRecommendations(existing, incoming) {
  const result = [];
  const indexes = new Map();
  const addOrReplace = item => {
    const key = `${item.source}\u0000${item.assetId}`;
    const index = indexes.get(key);
    if (index === undefined) {
      indexes.set(key, result.length);
      result.push(item);
    } else {
      result[index] = item;
    }
  };
  for (const item of safeRecommendations(existing)) addOrReplace(item);
  for (const item of safeRecommendations(incoming)) addOrReplace(item);
  return result;
}
