import { freshVisualHandoff, renderVisualHandoff, visiblePromptWarnings } from "./visual-handoff.js";
import { readMediaReference, mediaPayloadStatus, metadataOnlyWorkspace, adoptSavedMedia } from "./media-assets.js";
import { createReferenceAuthority, replacePicture } from "./reference-authority.js";
import { api } from "./api.js";
import { H3_ASPECT_RATIO_CHOICES } from "./aspect-ratios.js";
import {
  deleteProject as deleteLegacyProject, getProject as getLegacyProject,
  listProjects as listLegacyProjects,
} from "./db.js";
import { snapshotGenerationRequest, generationClickIntent } from "./streaming.js";
import { currentFormatAudit } from "./history-preview.js";
import { markOutputManuallyEdited } from "./manual-edit.js";
import { createSavedPrompts } from "./saved-prompts.js";
import { highlightPrompt } from "./highlighter.js";
import {
  defaultCreativeControls, renderCreativeControls, renderH3PromptDetailControl, selectH3PromptDetail,
  resetCameraControls, resetCreativeControls, resetTonePerformance,
  PERFORMANCE_ENERGY_LEVELS, PERFORMANCE_STYLE_LEVELS, TONE_LEVELS, semanticFromRange, semanticLabel,
} from "./creative-controls.js";
import {
  DEFAULT_CREATIVE_INTERPRETATION, normalizeCreativeInterpretation, renderCreativeInterpretation,
} from "./creative-interpretation.js";
import { captureWorkspaceCreativeState, restoreWorkspaceCreativeState } from "./workspace-creative-state.js";
import {
  applyRevisionCandidate, assessRevisionFindings, createRevisionSnapshot, findingSignature, revisionIsStale,
  selectionFromHighlighted, selectionFromTextarea,
} from "./revision.js";
import {
  adoptNarrativeResponse, subjectDeleteConfirmation, subjectDisplayLabel, availableSubjects, materializeSubject, subjectSelectorOptions, createSubject, defaultSubjectIdentity, IDENTITY_FOCUS_OPTIONS, IDENTITY_VIEW_OPTIONS, REFERENCE_LAYOUT_OPTIONS,
  isComparisonAsset, isSubjectIdentityAsset, normalizeSubjectIdentity, normalizeSubjectRegistry, normalizedComparisonSubjects,
  subjectIdentityHelperText, subjectIdentityNotesGuidance, subjectIdentityNotesPlaceholder, subjectLabel,
} from "./subject-identity.js";
import { groupReferenceAssets, subjectIdentityPresentation } from "./reference-asset-groups.js";
import { normalizeSeedSettings, resolveSeed } from "./seed.js";
import { canEjectModel } from "./eject.js";
import { resolveProjectSave, resolveProjectSaveRoute } from "./project-save.js";
import { createProjectGeneratedState } from "./project-generated-state.js";
import { ephemeralSequence, generationLabel, movedGenerationIds, sequenceFromProject, shouldMarkWorkspaceDirty, structuralOperationAllowed } from "./sequence-workflow.js";
import { createSequenceRenderAction } from "./sequence-render-next.js";
import { storyGenerationFeedback } from "./story-generation-feedback.js";
import { createStoryPlanner } from "./story-planning.js";
import { applyScopedPlan, fillMissingPlan, mergeRecommendations, PLAN_SCOPES, planningFromProject, RELATIONSHIP_OPTIONS, safeRecommendations, freshPlanning, recommendationAssetLabel, renderStoryShotMismatch as renderStoryShotMismatchAdvisory } from "./planning-workflow.js";
import { migrateLegacyProject } from "./project-migration.js";
import {
  continuityFromProject, freshUnsavedContinuity, freshUnsavedWorkspace, openFilesystemProjectSafely, openLegacyProjectSafely, persistFilesystemProject,
  deleteFilesystemProjectAndReset, deleteLegacyProjectAndReset, loadProjectSources, prepareImportedWorkspace,
  importedSequenceMetadata, clearImportedSequenceMetadata, resetUnsavedProjectState, projectIdFromLocation, projectLocation,
} from "./project-workflow.js";
import { canStartRepair, repairUiState } from "./repair-state.js";
import { migrateStoredSettings, parseStoredSettings, serializeEndpointProfile, validateSettingsDraft, modelSelectionEndpoint } from "./settings-persistence.js";
import { createRenderedTakes } from "./rendered-takes.js";
import { createTakeContinuation } from "./continue-selected-take.js";
import { mountContinuationDrafting } from "./continuation-drafting.js";
import { createWorkflowPreparation, currentExecutionOutput } from "./workflow-preparation.js";
import { createExecutionConfig } from "./execution-config.js";
import { groupInputMapEntries, inputMapEmptyMessage, inputMapLabelForAsset, inputMapRefreshStatus } from "./input-map.js";
import { effectiveDialogueLanguage, dialogueResolutionIsCurrent, dialogueResolutionNeedsProvider, staleDialogueResolution, editedDialogueResolution } from "./dialogue-language-state.js";
import { buildGenerationRequest } from "./generation-request.js";
import { requestFactLines } from "./generation-diagnostics.js";
import { beginProviderActivity, createProviderActivity, mountProviderActivity } from "./provider-activity.js";
import { createNavigationAuthority } from "./navigation-authority.js";

let storyPlanner = null;
const providerActivity = createProviderActivity();
const navigationAuthority = createNavigationAuthority();
let continuationDrafting = null;
let providerContextEpoch = 0;
function providerAttempt(id, label, status, cancel, options = {}) {
  return beginProviderActivity(providerActivity, { id, label, owner: "app", cancel, ...options }, status);
}
function providerContextCurrent(epoch) { return epoch === providerContextEpoch; }
function invalidateProviderContext() {
  providerContextEpoch++;
  if (providerActivity.active()) providerActivity.cancelActive();
}
let workflowPreparation = null;
let savedPrompts = null;
let renderedTakes = null;
let sequenceRenderAction = null;
let committedSettings = null;
let committedPersistKey = false;
let apiKeyEdited = false;
let apiKeyClearRequested = false;

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const modeDescriptions = {
  T2VA: "Text-driven video and audio generation using the official three-field base format.",
  I2VA: "One exact first-frame Picture anchors 0.00 seconds; the timeline develops forward.",
  FL2VA: "Exact first and last Pictures anchor a continuous path across the effective duration.",
  L2VA: "One exact last-frame Picture anchors the ending; the timeline converges toward it.",
  Ref2VA: "Full-reference mode with semantic Subjects, Pictures, Videos, Audio, and six official sections.",
};
const roleCatalog = {
  image: [
    ["subject_identity", "Subject / identity reference"], ["environment", "Environment reference"],
    ["character_comparison_scale", "Character Comparison / Scale"],
    ["style_appearance", "Style / appearance reference"], ["continuity_state", "Continuity-state reference"],
    ["first_frame_anchor", "First-frame anchor"], ["keyframe_anchor", "Keyframe anchor"],
    ["last_frame_anchor", "Last-frame anchor"], ["storyboard_composition", "Storyboard / composition"],
    ["general_visual", "General visual reference"],
  ],
  video: [
    ["continuation_source", "Continuation source"], ["tail_reference_continuation", "Tail continuation reference"],
    ["editing_source", "Editing source"], ["motion_action", "Motion / action reference"],
    ["camera_movement", "Camera movement reference"], ["cut_pacing_rhythm", "Cut / pacing / rhythm"],
    ["general_video", "General video reference"],
  ],
  audio: [
    ["full_reuse", "Full audio reuse / copy 1:1"], ["partial_reuse", "Partial audio reuse"],
    ["music_beat_rhythm", "Music / beat / rhythm"], ["voice_timbre", "Voice timbre reference"],
    ["dialogue_spoken_content", "Dialogue / spoken-content"], ["sound_audio_style", "Sound / audio-style"],
    ["general_audio", "General audio reference"],
  ],
};
const state = {
  mode: "T2VA",
  assets: [],
  subjects: [],
  projectSequence: null,
  importedSequenceSubjects: null,
  importedSequenceNextSubjectNumber: null,
  nextSubjectNumber: 1,
  outputs: [],
  outputIndex: 0,
  currentProjectId: null,
  currentProjectSavedName: null,
  activeProjectType: "none",
  currentLegacyProjectId: null,
  sequenceGenerations: [],
  activeGenerationId: null,
  sequenceContext: "",
  scenePlan: "",
  savedScenePlan: "",
  incomingState: "",
  intendedOutgoingState: "",
  visualHandoff: freshVisualHandoff(),
  relationshipToPrevious: null,
  cameraIntent: "",
  plannedShotCount: null,
  hasSavedPlanning: false,
  planningRecommendations: [],
  filesystemProjects: [],
  legacyProjects: [],
  dirty: true,
  pendingAssetKind: null,
  pendingAssetSubjectId: null,
  reattachId: null,
  endpoint: {
    id: "manual-endpoint",
    displayName: "Manual endpoint",
    baseUrl: "http://127.0.0.1:1234/v1",
    source: "manual",
  },
  discoveredEndpoints: new Map(),
  discoveredModelCatalog: new Map(),
  connectionState: "unverified",
  generation: null,
  generationStarting: false,
  revisionSelection: null,
  revision: null,
  ejecting: false,
  repairRunning: false,
  rawEdit: false,
  creativeInterpretation: DEFAULT_CREATIVE_INTERPRETATION,
  creativeControls: defaultCreativeControls(),
  referenceManifest: null,
  promptWarnings: [],
  referenceManifestStatus: "pending",
  storyPlannerTimeoutSeconds: 600,
  storyPlannerReasoning: true,
  storyPlannerVision: false,
  dialogueLanguage: "as_written",
  dialogueResolution: null,
  contextReplacing: false,
};

let referenceManifestRequest = 0;

function uid() {
  return crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function toast(message) {
  const element = document.createElement("div");
  element.className = "toast";
  element.textContent = message;
  $("#toast-region").append(element);
  setTimeout(() => element.remove(), 3000);
}

function setDirty(value = true) {
  state.dirty = value;
  workflowPreparation?.sync();
  renderedTakes?.sync();
  const indicator = $("#dirty-indicator");
  indicator.textContent = value ? "Unsaved" : "Saved";
  indicator.classList.toggle("saved", !value);
  if (typeof renderSequenceControls === "function") renderSequenceControls();
  storyPlanner?.refresh();
}

function captureContinuity() {
  return {
    sequenceContext: state.sequenceContext,
    incomingState: state.incomingState,
    intendedOutgoingState: state.intendedOutgoingState,
  };
}

function capturePlanning() {
  return {
    scenePlan: state.scenePlan,
    relationshipToPrevious: state.sequenceGenerations[0]?.generationId === state.activeGenerationId ? null : state.relationshipToPrevious,
    cameraIntent: state.cameraIntent,
    visualHandoff: { ...state.visualHandoff },
  };
}

function restorePlanning(planning = freshPlanning()) {
  const normalized = planningFromProject({ planning });
  state.scenePlan = normalized.scenePlan;
  state.savedScenePlan = normalized.scenePlan;
  storyPlanner?.restoreScenePlan(state.scenePlan);
  state.relationshipToPrevious = normalized.relationshipToPrevious;
  state.cameraIntent = normalized.cameraIntent;
  state.plannedShotCount = planning.shotCount ?? null;
  state.hasSavedPlanning = Boolean(normalized.scenePlan.trim()) && planning.shotCount != null;
  state.visualHandoff = normalized.visualHandoff;
  renderContinuityUi();
}

function renderStoryShotMismatch() {
  const advisory = $("#story-shot-mismatch");
  if (!advisory) return;
  renderStoryShotMismatchAdvisory(advisory, {
    hasSavedPlanning: state.hasSavedPlanning,
    plannedShotCount: state.plannedShotCount,
    actualShotCount: $("#shots").value,
  });
}

function restoreContinuity(continuity = freshUnsavedContinuity()) {
  const normalized = continuityFromProject({ continuity });
  state.sequenceContext = normalized.sequenceContext;
  state.incomingState = normalized.incomingState;
  state.intendedOutgoingState = normalized.intendedOutgoingState;
  renderContinuityUi();
}

function renderContinuityUi() {
  const context = $("#sequence-context");
  if (!context) return;
  context.value = state.sequenceContext;
  $("#incoming-state").value = state.incomingState;
  $("#intended-outgoing-state").value = state.intendedOutgoingState;
  const label = generationLabel({ activeGenerationId: state.activeGenerationId, generations: state.sequenceGenerations }, state.activeGenerationId);
  $("#incoming-state-generation").textContent = label;
  $("#outgoing-state-generation").textContent = label;
  const planningSelect = $("#relationship-to-previous");
  const activeIndex = state.sequenceGenerations.findIndex(item => item.generationId === state.activeGenerationId);
  if (planningSelect) {
    planningSelect.replaceChildren(
      ...(activeIndex === 0
        ? [new Option("Sequence start", "")]
        : [new Option("Unspecified", ""), ...RELATIONSHIP_OPTIONS.map(([value, text]) => new Option(text, value))]),
    );
    planningSelect.value = activeIndex === 0 ? "" : (state.relationshipToPrevious || "");
    planningSelect.disabled = activeIndex === 0 || state.activeProjectType === "none" && !state.dirty;
  }
  const camera = $("#camera-intent");
  if (camera) camera.value = state.cameraIntent;
  renderStoryShotMismatch();
  const revise = $("#revise-plan");
  if (revise) revise.disabled = state.activeProjectType !== "filesystem" || Boolean(state.generation) || Boolean(state.revision?.running);
  renderPlanningRecommendations();
  renderHandoffUi();
}

function renderHandoffUi() {
  const nativeOnly = state.mode === "I2VA" && state.visualHandoff.strategy === "native_i2va_continuation";
  if (nativeOnly) {
    $("#mode-description").textContent = "Native-only continuation of an accepted I2VA execution. No new first-frame Picture; a verified successor Profile is required.";
    $("#reference-helper").textContent = "No new Picture is expected for native-only I2VA. Existing references must be removed explicitly before authoring or execution.";
  } else if (state.mode === "I2VA") {
    $("#mode-description").textContent = modeDescriptions.I2VA;
    $("#reference-helper").textContent = "Add the exact first-frame Picture required by ordinary I2VA.";
  }
  renderVisualHandoff($("#visual-handoff"), {
    policy: state.visualHandoff, relationship: state.relationshipToPrevious,
    summary: state.referenceManifest?.visualHandoff, dirty: state.dirty,
    previewUrl: state.activeProjectType === "filesystem" ? `/api/projects/${encodeURIComponent(state.currentProjectId)}/generations/${encodeURIComponent(state.activeGenerationId)}/visual-handoff/preview` : null,
    onChange: value => { state.visualHandoff = value; setDirty(); renderHandoffUi(); },
  });
}

function renderPlanningRecommendations() {
  const target = $("#planning-recommendations");
  if (!target) return;
  target.replaceChildren();
  if (!state.planningRecommendations.length) {
    target.classList.add("hidden");
    return;
  }
  target.classList.remove("hidden");
  const heading = document.createElement("div"); heading.className = "eyebrow"; heading.textContent = "Reference recommendations"; target.append(heading);
  for (const item of state.planningRecommendations) {
    const source = item.source === "previous_generation" ? "Previous Generation" : "Current Generation";
    const asset = item.source === "current_generation" ? state.assets.find(asset => asset.id === item.assetId) : null;
    const line = document.createElement("p"); line.className = "helper"; line.textContent = `${source} — ${recommendationAssetLabel(asset, item.assetId)}: ${item.recommendation}`; target.append(line);
  }
}

function safeFilename(name) {
  return (name || "miniconstruct").replace(/[^a-z0-9._-]+/gi, "-").replace(/^-|-$/g, "");
}

function normalizeEndpointUrl(baseUrl) {
  try {
    const url = new URL(baseUrl.trim());
    url.pathname = url.pathname.replace(/\/+$/, "") || "/";
    return url.toString().replace(/\/$/, "");
  } catch {
    return baseUrl.trim().replace(/\/+$/, "");
  }
}

function manualEndpointId(baseUrl) {
  return `manual:${normalizeEndpointUrl(baseUrl)}`;
}

function endpointKey(endpoint) {
  return normalizeEndpointUrl(endpoint.baseUrl);
}

function endpointProfile() {
  const baseUrl = $("#base-url").value.trim();
  const isManual = state.endpoint.source === "manual";
  return serializeEndpointProfile(state.endpoint, {
    id: isManual ? manualEndpointId(baseUrl) : state.endpoint.id,
    displayName: $("#endpoint-display-name").value.trim() || "Manual endpoint",
    baseUrl,
    apiKey: apiKeyClearRequested ? null : apiKeyEdited && $("#api-key").value
      ? $("#api-key").value : (state.endpoint.apiKey || null),
  });
}

function readSettingsDraft() {
  const seed = normalizeSeedSettings({
    seedMode: $("#seed-mode").value,
    fixedSeed: $("#fixed-seed").value,
  });
  return {
    endpoint: endpointProfile(),
    modelId: $("#model-id").value.trim(),
    temperature: $("#temperature").value.trim() ? Number($("#temperature").value) : NaN,
    maxTokens: $("#max-tokens").value.trim() ? Number($("#max-tokens").value) : NaN,
    contextWindow: $("#context-window").value.trim() ? Number($("#context-window").value) : null,
    timeoutSeconds: 120,
    supportsVision: $("#vision-support").checked,
    reasoningMode: $("#reasoning-mode").value,
    ...seed,
  };
}

function settings() {
  return structuredClone(committedSettings || validateSettingsDraft(readSettingsDraft()));
}

function renderSeedSettings() {
  const seed = normalizeSeedSettings({ seedMode: $("#seed-mode").value, fixedSeed: $("#fixed-seed").value });
  $("#seed-mode").value = seed.seedMode;
  $("#fixed-seed-wrap").classList.toggle("hidden", seed.seedMode !== "fixed");
  $("#fixed-seed").required = seed.seedMode === "fixed";
}

function persistSettings() {
  if (!committedSettings) return;
  const value = migrateStoredSettings({ ...committedSettings, persistKey: committedPersistKey,
    storyPlannerTimeoutSeconds: state.storyPlannerTimeoutSeconds, storyPlannerReasoning: state.storyPlannerReasoning,
    storyPlannerVision: state.storyPlannerVision }, state.endpoint);
  if (!value.persistKey) delete value.endpoint.apiKey;
  localStorage.setItem("miniconstruct-settings", JSON.stringify(value));
}

function renderCommittedSettings() {
  const value = committedSettings;
  if (!value) return;
  state.endpoint = value.endpoint;
  $("#endpoint-display-name").value = value.endpoint.displayName;
  $("#base-url").value = value.endpoint.baseUrl;
  $("#model-id").value = value.modelId;
  $("#temperature").value = value.temperature;
  $("#max-tokens").value = value.maxTokens;
  $("#context-window").value = value.contextWindow ?? "";
  $("#reasoning-mode").value = value.reasoningMode;
  const seed = normalizeSeedSettings(value);
  $("#seed-mode").value = seed.seedMode;
  $("#fixed-seed").value = seed.fixedSeed ?? "";
  $("#vision-support").checked = value.supportsVision === true;
  $("#persist-key").checked = committedPersistKey;
  $("#api-key").value = value.endpoint.apiKey || "";
  apiKeyEdited = false;
  apiKeyClearRequested = false;
  renderSeedSettings();
  setActiveModelStatus();
}

function validatedSettingsDraft() {
  if (!$("#endpoint-display-name").value.trim()) throw new Error("Enter an Endpoint name.");
  if (!$("#base-url").value.trim()) throw new Error("Enter a Base URL.");
  return validateSettingsDraft(readSettingsDraft());
}

function commitSettingsFromUi() {
  const candidate = validatedSettingsDraft();
  committedSettings = candidate;
  committedPersistKey = $("#persist-key").checked;
  state.endpoint = candidate.endpoint;
  persistSettings();
  apiKeyEdited = false;
  apiKeyClearRequested = false;
  setActiveModelStatus();
  return candidate;
}

function restoreSettings() {
  let warning = "";
  try {
    const raw = localStorage.getItem("miniconstruct-settings");
    let stored;
    try { stored = JSON.parse(raw || "{}"); } catch { stored = {}; }
    if (stored && typeof stored === "object" && Object.hasOwn(stored, "maxTokens") && (!Number.isInteger(stored.maxTokens)
      || stored.maxTokens < 128 || stored.maxTokens > 65536))
      warning = "Saved Max output tokens was invalid and reset to 4096. Review Settings.";
    const value = parseStoredSettings(raw, state.endpoint);
    committedSettings = validateSettingsDraft({
      endpoint: value.endpoint, modelId: value.modelId, temperature: value.temperature,
      maxTokens: value.maxTokens, contextWindow: value.contextWindow, timeoutSeconds: 120,
      supportsVision: value.supportsVision, reasoningMode: value.reasoningMode,
      seedMode: value.seedMode, fixedSeed: value.fixedSeed,
    });
    committedPersistKey = value.persistKey;
    state.storyPlannerTimeoutSeconds = value.storyPlannerTimeoutSeconds;
    state.storyPlannerReasoning = value.storyPlannerReasoning;
    state.storyPlannerVision = value.storyPlannerVision;
    storyPlanner?.restorePreferences({ timeoutSeconds: state.storyPlannerTimeoutSeconds, reasoning: state.storyPlannerReasoning,
      vision: state.storyPlannerVision });
  } catch { /* ignore malformed local preference */ }
  renderCommittedSettings();
  if (warning) $("#connection-message").textContent = warning;
}

function setActiveModelStatus() {
  const status = $("#active-model-status");
  const modelId = $("#model-id").value.trim();
  const endpoint = endpointProfile();
  const label = modelId ? `${modelId} — ${endpoint.displayName}` : "No model selected";
  const suffix = state.connectionState === "connected" ? " • Connected" : state.connectionState === "disconnected" ? " • Disconnected" : "";
  const dot = document.createElement("span");
  dot.className = `status-dot ${state.connectionState === "connected" ? "ok" : state.connectionState === "disconnected" ? "bad" : ""}`;
  status.replaceChildren(dot, document.createTextNode(`${label}${suffix}`));
  updateEjectButton();
}

function updateEjectButton() {
  const button = $("#eject-model");
  const disabled = !canEjectModel({
    modelId: $("#model-id").value, generating: Boolean(state.generation), revising: Boolean(state.revision?.running),
    repairing: state.repairRunning, ejecting: state.ejecting,
  });
  button.disabled = disabled;
  button.textContent = state.ejecting ? "Ejecting…" : "⏏ Eject";
}

async function ejectSelectedModel() {
  if ($("#eject-model").disabled) return;
  state.ejecting = true;
  updateEjectButton();
  try {
    const result = await api.eject(settings());
    toast(result.message || "Model ejected.");
  } catch (error) {
    toast(error.message);
  } finally {
    state.ejecting = false;
    updateEjectButton();
  }
}

function markManualEndpoint() {
  const nextUrl = $("#base-url").value;
  const changedUrl = normalizeEndpointUrl(nextUrl) !== normalizeEndpointUrl(state.endpoint.baseUrl);
  state.endpoint = {
    ...state.endpoint,
    id: manualEndpointId(nextUrl),
    source: "manual",
    apiKey: changedUrl ? null : state.endpoint.apiKey,
  };
  if (changedUrl) { $("#api-key").value = ""; apiKeyEdited = false; apiKeyClearRequested = false; }
  setActiveModelStatus();
}

function catalogModels() {
  return [...state.discoveredModelCatalog.values()]
    .flat()
    .sort((a, b) => a.displayName.localeCompare(b.displayName));
}

function renderPooledModels() {
  const selector = $("#model-selector");
  const current = `${endpointProfile().id}::${$("#model-id").value.trim()}`;
  selector.replaceChildren(new Option("Manual / custom model ID", ""));
  for (const model of catalogModels()) {
    selector.append(new Option(model.displayName, `${model.endpointId}::${model.modelId}`));
  }
  selector.value = [...selector.options].some(option => option.value === current) ? current : "";
}

function selectPooledModel(value) {
  if (!value) return;
  const separator = value.indexOf("::");
  const endpointId = value.slice(0, separator);
  const modelId = value.slice(separator + 2);
  const endpoint = state.discoveredEndpoints.get(endpointId);
  if (!endpoint) return;
  state.endpoint = modelSelectionEndpoint(committedSettings?.endpoint || state.endpoint, endpoint);
  $("#endpoint-display-name").value = state.endpoint.displayName;
  $("#base-url").value = state.endpoint.baseUrl;
  $("#api-key").value = state.endpoint.apiKey || "";
  $("#model-id").value = modelId;
  if (committedSettings) committedSettings = validateSettingsDraft({ ...committedSettings, endpoint: state.endpoint, modelId });
  state.connectionState = "connected";
  setActiveModelStatus();
  persistSettings();
}

function matchingRememberedKey(endpoint) {
  const matching = [state.endpoint, ...state.discoveredEndpoints.values()]
    .find(candidate => endpointKey(candidate) === endpointKey(endpoint) && candidate.apiKey);
  return matching?.apiKey || null;
}

function updateDiscoveredCatalog(endpoint, models, { replace = true } = {}) {
  const rememberedKey = matchingRememberedKey(endpoint);
  const profile = { ...endpoint, apiKey: rememberedKey };
  state.discoveredEndpoints.set(profile.id, profile);
  if (replace) {
    state.discoveredModelCatalog.set(profile.id, models.map(model => ({ ...model, endpointId: profile.id })));
  }
  renderPooledModels();
}

export function captureWorkspace() {
  const shotsValue = $("#shots").value.trim();
  const ratio = $("#aspect-ratio").value === "custom" ? $("#custom-ratio").value.trim() : $("#aspect-ratio").value;
  return {
    schemaVersion: 1,
    projectId: state.currentProjectId,
    projectName: $("#project-name").value.trim() || "Untitled Project",
    mode: state.mode,
    durationSeconds: Number($("#duration").value),
    shots: shotsValue === "" ? null : Number(shotsValue),
    aspectRatio: ratio || "4:3",
    variations: Number($("#variations").value),
    creativeRequest: $("#creative-request").value,
    dialogue: $("#dialogue").value,
    dialogueLanguage: state.dialogueLanguage,
    dialogueResolution: state.dialogueResolution,
    referenceLabels: $("#reference-labels").value,
    assets: structuredClone(state.assets),
    subjects: structuredClone(state.subjects),
    nextSubjectNumber: state.nextSubjectNumber,
    ...captureWorkspaceCreativeState(state),
  };
}

function renderCreativeControlsUi() {
  renderCreativeControls(
    $("#creative-controls"),
    state.creativeControls,
    visibleAssets().some(asset => asset.kind === "image" && asset.role === "subject_identity"),
  );
  renderH3PromptDetailUi();
}

function renderCreativeInterpretationUi() {
  renderCreativeInterpretation($(".author-panel"), state.creativeInterpretation);
}

function renderH3PromptDetailUi() {
  renderH3PromptDetailControl($(".author-panel"), state.creativeControls);
}

export function restoreWorkspace(workspace, sequence = null, dialogueLanguage = null) {
  state.projectSequence = sequence;
  markRevisionStale("A different workspace was loaded.");
  restoreContinuity();
  restorePlanning();
  state.planningRecommendations = [];
  state.currentProjectId = workspace.projectId || null;
  state.currentProjectSavedName = state.currentProjectId ? (workspace.projectName || "Untitled Project") : null;
  state.assets = structuredClone(workspace.assets || []).map(asset => ({
    notes: "", options: {}, order: 0, attached: asset.kind === "image", ...asset,
    subjectIdentity: normalizeSubjectIdentity(asset.subjectIdentity),
    attached: asset.kind === "image" ? true : false,
  }));
  const registry = normalizeSubjectRegistry(workspace.subjects, state.assets, workspace.nextSubjectNumber, state.projectSequence);
  state.subjects = registry.subjects;
  state.nextSubjectNumber = registry.nextSubjectNumber;
  $("#project-name").value = workspace.projectName || "Untitled Project";
  $("#duration").value = workspace.durationSeconds ?? 6;
  $("#shots").value = workspace.shots ?? "";
  renderStoryShotMismatch();
  const commonRatios = ["auto", ...H3_ASPECT_RATIO_CHOICES];
  const ratio = workspace.aspectRatio || "4:3";
  $("#aspect-ratio").value = commonRatios.includes(ratio) ? ratio : "custom";
  $("#custom-ratio").value = commonRatios.includes(ratio) ? "" : ratio;
  $("#custom-ratio-wrap").classList.toggle("hidden", $("#aspect-ratio").value !== "custom");
  $("#variations").value = workspace.variations ?? 1;
  $("#creative-request").value = workspace.creativeRequest || "";
  $("#dialogue").value = workspace.dialogue || "";
  // A saved Project's canonical Sequence value wins; Workspace is only an
  // effective projection for standalone authoring and transport compatibility.
  state.dialogueLanguage = effectiveDialogueLanguage(dialogueLanguage, workspace.dialogueLanguage);
  state.dialogueResolution = workspace.dialogueResolution || null;
  renderDialogueLanguageUi();
  $("#reference-labels").value = workspace.referenceLabels || "";
  Object.assign(state, restoreWorkspaceCreativeState(workspace));
  state.referenceManifest = null;
  setMode(workspace.mode || "T2VA", false);
  renderAssets();
  renderCreativeInterpretationUi();
  renderCreativeControlsUi();
  updateCharCount();
  state.outputs = [];
  renderWarnings([]);
  renderOutput();
  setDirty(false);
}

export function buildGenerateRequest(authoringPath = "one_shot") {
  if (workflowPreparation && !workflowPreparation.routingReady) throw new Error("Wait for this Generation's reference routing to load, then try again.");
  return buildGenerationRequest({ workspace: captureWorkspace(), llm: settings(),
    authoringPath,
    filesystemProjectId: state.activeProjectType === "filesystem" ? state.currentProjectId : null,
    generationId: state.activeProjectType === "filesystem" ? state.activeGenerationId : null,
    sequenceDialogueLanguage: state.dialogueLanguage, executionRouting: workflowPreparation?.routing || { assets: {} } });
}

function setMode(mode, dirty = true) {
  state.mode = mode;
  $$("[data-mode]").forEach(button => {
    const active = button.dataset.mode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  $("#mode-description").textContent = modeDescriptions[mode];
  $("#references-panel").classList.toggle("hidden", mode === "T2VA");
  $("#reference-labels-wrap").classList.toggle("hidden", mode !== "Ref2VA");
  $("#reference-order-helper").classList.toggle("hidden", mode !== "Ref2VA");
  renderAssetActions();
  renderAssets();
  if (dirty) setDirty();
}

function renderAssetActions() {
  const container = $("#asset-actions");
  container.replaceChildren();
  const kinds = state.mode === "Ref2VA" ? ["image", "video", "audio"] : ["image"];
  for (const kind of kinds) {
    const button = document.createElement("button");
    button.className = "quiet";
    button.textContent = state.mode === "Ref2VA" && kind === "image"
      ? "+ New Subject Picture" : `+ ${kind[0].toUpperCase()}${kind.slice(1)}`;
    button.addEventListener("click", () => chooseAsset(kind));
    container.append(button);
  }
  $("#reference-helper").textContent = state.mode === "Ref2VA"
    ? "New Subject Picture creates a new identity. Use Add identity Picture within a Subject to attach another view to that identity. Pictures may be sent as vision inputs. Video and Audio stay metadata-only; attach the real media separately in H3 / ComfyUI."
    : "Add the exact frame image required by this mode. The browser processes it for the prompt-writing model.";
}

function visibleAssets() {
  if (state.mode === "Ref2VA") return state.assets;
  if (state.mode === "I2VA") return state.assets.filter(a => a.kind === "image" && a.role === "first_frame_anchor");
  if (state.mode === "FL2VA") return state.assets.filter(a => a.kind === "image" && ["first_frame_anchor", "last_frame_anchor"].includes(a.role));
  if (state.mode === "L2VA") return state.assets.filter(a => a.kind === "image" && a.role === "last_frame_anchor");
  return [];
}

function manifestRequestBody() {
  return {
    ...(state.activeProjectType === "filesystem" && !state.contextReplacing ? { filesystemProjectId: state.currentProjectId, generationId: state.activeGenerationId } : {}),
    executionRouting: workflowPreparation?.routing || { assets: {} },
    mode: state.mode,
    assets: state.assets.map(asset => ({
      id: asset.id,
      kind: asset.kind,
      filename: asset.filename,
      role: asset.role,
      order: asset.order,
      subjectIdentity: asset.subjectIdentity,
      comparisonSubjects: asset.comparisonSubjects,
    })),
    subjects: state.subjects,
  };
}

function assetManifestEntry(asset) {
  return state.referenceManifest?.entries?.find(entry => entry.id === asset.id);
}

function applyCanonicalAssetLabels(entries = state.referenceManifest?.entries) {
  for (const card of $$(".asset-card[data-asset-id]")) {
    const label = card.querySelector("[data-h3-label]");
    const h3Label = inputMapLabelForAsset(entries, card.dataset.assetId);
    label.textContent = h3Label;
    label.classList.toggle("hidden", !h3Label);
  }
}

function renderInputMap() {
  const container = $("#input-map-list");
  container.replaceChildren();
  const groups = groupInputMapEntries(state.referenceManifest?.entries);
  if (state.referenceManifestStatus !== "loaded" || !groups.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = inputMapEmptyMessage({
      assetCount: state.assets.length,
      status: state.referenceManifestStatus,
    });
    container.append(empty);
    return;
  }
  for (const group of groups) {
    const section = document.createElement("section");
    section.className = "input-map-group";
    const heading = document.createElement("h4");
    heading.textContent = group.label;
    section.append(heading);
    for (const entry of group.entries) {
      const row = document.createElement("div");
      row.className = "input-map-row";
      const label = document.createElement("strong");
      label.className = "input-map-label";
      label.textContent = `<${entry.h3Label}>`;
      const detail = document.createElement("div");
      detail.className = "input-map-detail";
      const filename = document.createElement("span");
      filename.className = "input-map-filename";
      filename.textContent = entry.filename;
      detail.append(filename);
      row.append(label, detail);
      section.append(row);
    }
    container.append(section);
  }
}

async function refreshReferenceManifest() {
  const requestId = ++referenceManifestRequest;
  try {
    const result = await api.referenceManifest(manifestRequestBody());
    if (requestId !== referenceManifestRequest) return;
    state.referenceManifest = result;
    state.referenceManifestStatus = "loaded";
    renderHandoffUi();
    renderWarnings(state.promptWarnings);
    applyCanonicalAssetLabels(result.entries);
    renderInputMap();
  } catch {
    if (requestId !== referenceManifestRequest) return;
    if (state.referenceManifest) {
      state.referenceManifestStatus = "loaded";
      return;
    }
    state.referenceManifestStatus = "failed";
    applyCanonicalAssetLabels([]);
    renderInputMap();
  }
}

function requestReferenceManifestRefresh() {
  state.referenceManifestStatus = inputMapRefreshStatus(Boolean(state.referenceManifest));
  if (state.referenceManifestStatus === "pending") renderInputMap();
  void refreshReferenceManifest();
}

function renderAssets({ refreshManifest = true } = {}) {
  state.clearAssetDrop?.();
  const container = $("#asset-list");
  container.replaceChildren();
  const assets = visibleAssets().sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  const renderGroup = (label, groupAssets, className = "") => {
    const group = document.createElement("section");
    group.className = `reference-asset-group ${className}`.trim();
    const heading = document.createElement("h3"); heading.textContent = label;
    const cards = document.createElement("div"); cards.className = "reference-group-cards";
    for (const asset of groupAssets) cards.append(assetCard(asset, assetManifestEntry(asset)?.h3Label || ""));
    group.append(heading, cards);
    return group;
  };
  const renderGroupedReferenceAssets = () => {
    const subjectContext = state.projectSequence
      ? availableSubjects(state.subjects, state.projectSequence)
      : state.subjects;
    const grouping = groupReferenceAssets({ assets, subjects: subjectContext });
    let rendered = false;
    for (const subjectGroup of grouping.subjectGroups) {
      const section = document.createElement("details");
      section.className = "reference-subject-group";
      section.dataset.subjectId = subjectGroup.subject.id;
      section.open = subjectGroup.groups.length > 0;
      const summary = document.createElement("summary"); summary.textContent = subjectGroup.label;
      section.append(summary);
      const attach = document.createElement("button"); attach.type = "button"; attach.className = "quiet";
      attach.textContent = "Add identity Picture";
      attach.title = `Attach a Picture to ${subjectGroup.label}, preserving its identity`;
      attach.addEventListener("click", () => chooseAsset("image", null, subjectGroup.subject.id));
      section.append(attach);
      if (state.activeProjectType === "filesystem") {
        const nameLabel = document.createElement("label"); nameLabel.className = "subject-narrative-name";
        nameLabel.textContent = "Narrative name";
        const name = document.createElement("input"); name.type = "text"; name.maxLength = 80;
        name.name = `mc-subject-narrative-${subjectGroup.subject.id}`;
        name.autocomplete = "off";
        name.setAttribute("data-1p-ignore", "");
        name.setAttribute("data-lpignore", "true");
        name.value = subjectGroup.subject.narrativeName || "";
        name.placeholder = subjectGroup.subject.narrativeName ? "Enter a replacement name" : "Optional name, e.g. Alice";
        const save = document.createElement("button"); save.type = "button"; save.className = "quiet";
        save.textContent = subjectGroup.subject.narrativeName ? "Rename" : "Set name";
        save.disabled = !name.value.trim();
        name.addEventListener("input", () => { save.disabled = !name.value.trim(); });
        save.addEventListener("click", () => renameSequenceSubject(subjectGroup.subject, name.value));
        nameLabel.append(name, save); section.append(nameLabel);
        const nameHelp = document.createElement("p"); nameHelp.className = "helper";
        nameHelp.textContent = subjectGroup.subject.narrativeName
          ? "Enter a replacement to rename. Saved names cannot be cleared."
          : "Naming is optional. You can keep using the technical Subject label.";
        section.append(nameHelp);
        const profile = subjectGroup.subject.characterProfile || {};
        const profileDetails = document.createElement("details"); profileDetails.className = "character-profile";
        const profileSummary = document.createElement("summary"); profileSummary.textContent = "Character Profile";
        const profileHelp = document.createElement("p"); profileHelp.className = "helper";
        profileHelp.textContent = "Stable character notes for Story Planner—not current plot state, location, possessions, or temporary conditions.";
        profileDetails.append(profileSummary, profileHelp);
        const fieldLabel = document.createElement("label"); fieldLabel.textContent = "Character Profile";
        const textarea = document.createElement("textarea"); textarea.rows = 5; textarea.maxLength = 4000;
        textarea.name = `mc-subject-profile-${subjectGroup.subject.id}`; textarea.autocomplete = "off";
        textarea.value = profile.notes || "";
        textarea.addEventListener("input", () => {
          const canonical = state.projectSequence?.subjects?.find(item => item.id === subjectGroup.subject.id);
          if (!canonical) return;
          canonical.characterProfile = { roleBackground: "", personality: "", appearance: "", speechMannerisms: "", notes: "", ...(canonical.characterProfile || {}) };
          canonical.characterProfile.notes = textarea.value;
          if (typeof setDirty === "function") setDirty();
        });
        fieldLabel.append(textarea); profileDetails.append(fieldLabel);
        section.append(profileDetails);
      }
      if (subjectGroup.groups.length) {
        for (const group of subjectGroup.groups) section.append(renderGroup(group.label, group.assets, "reference-identity-group"));
      } else {
        const empty = document.createElement("p"); empty.className = "muted reference-subject-empty";
        empty.textContent = subjectGroup.structuralUse
          ? "Participates through a Shared / Scene reference (Character Comparison / Scale)."
          : "No references in this Generation.";
        section.append(empty);
        if (state.activeProjectType === "filesystem" && !subjectGroup.structuralUse) {
          const removeSubject = document.createElement("button");
          removeSubject.className = "quiet danger";
          removeSubject.textContent = "Delete Subject from Sequence";
          removeSubject.title = `Delete ${subjectGroup.label} from the entire Sequence`;
          removeSubject.addEventListener("click", () => deleteSequenceSubject(subjectGroup.subject));
          section.append(removeSubject);
        }
      }
      container.append(section); rendered = true;
    }
    if (grouping.environment.assets.length) {
      container.append(renderGroup(grouping.environment.label, grouping.environment.assets, "reference-environment-group"));
      rendered = true;
    }
    if (grouping.sharedGroups.length) {
      const shared = document.createElement("details"); shared.className = "reference-shared-group"; shared.open = true;
      const summary = document.createElement("summary"); summary.textContent = "Shared / Scene References"; shared.append(summary);
      for (const group of grouping.sharedGroups) shared.append(renderGroup(group.label, group.assets, "reference-shared-subgroup"));
      container.append(shared); rendered = true;
    }
    return rendered;
  };
  const usesGroupedRendering = state.mode === "Ref2VA";
  const rendered = usesGroupedRendering ? renderGroupedReferenceAssets() : assets.length > 0;
  if (!rendered) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = state.mode === "FL2VA" ? "Add first- and last-frame images." : "No references added for this mode.";
    container.append(empty);
    renderCreativeControlsUi();
    if (refreshManifest) requestReferenceManifestRefresh();
    return;
  }
  if (!usesGroupedRendering) {
    for (const asset of assets) container.append(assetCard(asset, assetManifestEntry(asset)?.h3Label || ""));
  }
  renderCreativeControlsUi();
  applyCanonicalAssetLabels();
  if (refreshManifest) requestReferenceManifestRefresh();
}

function normalizeSubjectsInState() {
  const registry = normalizeSubjectRegistry(state.subjects, state.assets, state.nextSubjectNumber, state.projectSequence);
  state.subjects = registry.subjects;
  state.nextSubjectNumber = registry.nextSubjectNumber;
}

function dialogueLanguageLabel() {
  return $("#dialogue-language").selectedOptions[0]?.textContent || "Spoken Output";
}

function dialogueResolutionCurrent() {
  return dialogueResolutionIsCurrent({ dialogueLanguage: state.dialogueLanguage, dialogue: $("#dialogue").value, resolution: state.dialogueResolution });
}

function renderDialogueLanguageUi() {
  const translated = state.dialogueLanguage !== "as_written";
  $("#dialogue-language").value = state.dialogueLanguage;
  $("#resolved-dialogue-wrap").classList.toggle("hidden", !translated);
  $("#dialogue-source-label").firstChild.textContent = translated ? "Dialogue / Lyrics — Source" : "Dialogue / Lyrics — Exact";
  $("#resolved-dialogue-label").firstChild.textContent = `Spoken Output — ${dialogueLanguageLabel()}`;
  $("#resolved-dialogue").value = state.dialogueResolution?.resolvedText || "";
  const status = $("#dialogue-resolution-status");
  status.textContent = !translated ? "" : dialogueResolutionCurrent()
    ? (state.dialogueResolution.status === "user_edited" ? "User edited" : "Current")
    : "Stale / needs translation";
}

function markDialogueResolutionStale() {
  if (state.dialogueLanguage === "as_written" || !state.dialogueResolution) return renderDialogueLanguageUi();
  state.dialogueResolution = staleDialogueResolution(state.dialogueResolution);
  renderDialogueLanguageUi();
}

async function refreshDialogueResolution(authoringPath = state.activeProjectType === "filesystem" ? "story" : "one_shot", forGeneration = false, generationSignal) {
  if (state.dialogueLanguage === "as_written") return;
  const controller = new AbortController();
  let cancelled = false, activityToken = null;
  if (!forGeneration) {
    const label = `Spoken Output — ${state.dialogueLanguage}`;
    activityToken = providerAttempt("dialogue-translation", label, message => $("#dialogue-resolution-status").textContent = message,
      () => { cancelled = true; controller.abort(); $("#dialogue-resolution-status").textContent = "Translation cancelled."; providerActivity.finish(activityToken); },
      { verb: "Translating", requestLabel: `Translate / Refresh — ${state.dialogueLanguage}` });
    if (!activityToken) return;
  }
  const epoch = providerContextEpoch;
  const sourceText = $("#dialogue").value, targetLanguage = state.dialogueLanguage;
  $("#dialogue-resolution-status").textContent = "Translating…";
  try {
    const nativeSource = state.activeProjectType === "filesystem" && state.visualHandoff.strategy === "native_i2va_continuation"
      ? { filesystemProjectId: state.currentProjectId, generationId: state.activeGenerationId } : {};
    const result = await api.resolveDialogue({ authoringPath, workspace: captureWorkspace(), llm: settings(), ...nativeSource }, forGeneration ? generationSignal : controller.signal);
    if (cancelled || generationSignal?.aborted || !providerContextCurrent(epoch)
        || sourceText !== $("#dialogue").value || targetLanguage !== state.dialogueLanguage) return;
    state.dialogueResolution = result.resolution;
    renderDialogueLanguageUi(); setDirty();
  } catch (error) {
    if (cancelled || error.name === "AbortError") { $("#dialogue-resolution-status").textContent = "Translation cancelled."; return; }
    throw error;
  } finally { providerActivity.finish(activityToken); }
}

async function ensureDialogueResolution(authoringPath, signal) {
  if (state.dialogueLanguage === "as_written" || dialogueResolutionCurrent()) return;
  await refreshDialogueResolution(authoringPath, true, signal);
}

function characterProfileUpdates() {
  return (state.projectSequence?.subjects || []).map(subject => ({
    subjectId: subject.id,
    characterProfile: {
      roleBackground: "", personality: "", appearance: "", speechMannerisms: "", notes: "",
      ...(subject.characterProfile || {}),
    },
  }));
}

let referenceAuthority = null, referenceMutation = null;
async function syncReferenceAuthority() {
  if (referenceMutation) await referenceMutation;
  return referenceAuthority?.sync();
}

async function changeReferenceAssets(change, dirtyOnFailure = false) {
  if (referenceMutation) return;
  const context = [state.currentProjectId, state.activeGenerationId];
  const previous = structuredClone({ assets: state.assets, subjects: state.subjects, nextSubjectNumber: state.nextSubjectNumber });
  const dirty = state.dirty;
  // Keep the canonical cards visible until the server accepts the proposed edit.
  for (const control of $$("#asset-list button, #asset-list input, #asset-list select, #asset-list textarea, #asset-actions button")) control.disabled = true;
  const operation = (async () => {
    try {
      await change();
      if (context[0] !== state.currentProjectId || context[1] !== state.activeGenerationId)
        throw new Error("The active Generation changed before the Reference Assets could be saved.");
      normalizeSubjectsInState();
      await referenceAuthority?.sync();
      if (context[0] !== state.currentProjectId || context[1] !== state.activeGenerationId) return;
      if (state.activeProjectType !== "filesystem") setDirty();
    } catch (error) {
      if (context[0] === state.currentProjectId && context[1] === state.activeGenerationId) {
        Object.assign(state, previous);
        // Reference errors cannot discard concurrent Main Prompt edits.
        setDirty(dirtyOnFailure || dirty || state.dirty);
      }
      toast(`Could not save Reference Assets: ${error.message}`);
      throw error;
    } finally {
      if (context[0] === state.currentProjectId && context[1] === state.activeGenerationId) {
        renderAssetActions(); renderAssets();
      }
    }
  })();
  referenceMutation = operation;
  try { await operation; } finally { if (referenceMutation === operation) referenceMutation = null; }
}

function editReferenceAssets(change, dirtyOnFailure = false) { void changeReferenceAssets(change, dirtyOnFailure).catch(() => {}); }

function assignNewSubject(asset) {
  const created = createSubject({ subjects: state.subjects, nextSubjectNumber: state.nextSubjectNumber }, state.projectSequence);
  state.subjects.push(created.subject);
  state.nextSubjectNumber = created.nextSubjectNumber;
  asset.subjectIdentity = { ...normalizeSubjectIdentity(asset.subjectIdentity), subjectId: created.subject.id };
}

function assetCard(asset, label) {
  const card = document.createElement("article");
  card.className = "asset-card";
  card.dataset.assetId = asset.id;
  const preview = document.createElement("div");
  preview.className = "asset-preview";
  if (asset.kind === "image" && asset.image?.data_url) {
    const img = document.createElement("img");
    img.src = asset.image.data_url;
    img.alt = `Preview of ${asset.filename}`;
    preview.append(img);
  } else preview.textContent = asset.kind === "image" ? "Picture unavailable" : asset.kind === "video" ? "▶" : "♫";

  const content = document.createElement("div");
  const title = document.createElement("div");
  title.className = "asset-title";
  const strong = document.createElement("strong"); strong.textContent = asset.filename;
  const labelEl = document.createElement("span"); labelEl.className = "asset-label"; labelEl.dataset.h3Label = ""; labelEl.textContent = label;
  labelEl.classList.toggle("hidden", !label);
  title.append(strong, labelEl);
  const meta = document.createElement("p"); meta.className = "asset-meta";
  const duration = asset.durationSeconds == null ? "" : ` · ${asset.durationSeconds.toFixed(3)}s`;
  const dimensions = asset.kind === "image" && asset.image?.width && asset.image?.height ? ` · ${asset.image.width} × ${asset.image.height}` : "";
  meta.textContent = `${asset.mimeType}${dimensions}${duration}`;
  if (isSubjectIdentityAsset(asset)) {
    const semanticMeta = document.createElement("span"); semanticMeta.className = "asset-semantic-meta";
    semanticMeta.textContent = ` · ${subjectIdentityPresentation(asset.subjectIdentity).metadata}`;
    meta.append(semanticMeta);
  }
  if (asset.kind !== "image" || asset.payloadAvailability) {
    const payloadStatus = document.createElement("span");
    payloadStatus.className = asset.mediaPayload ? "" : "media-missing";
    payloadStatus.textContent = ` - ${mediaPayloadStatus(asset)}`;
    meta.append(payloadStatus);
  }

  const fields = document.createElement("div"); fields.className = "asset-fields";
  const roleLabel = document.createElement("label"); roleLabel.textContent = "Role";
  const role = document.createElement("select");
  for (const [value, text] of roleCatalog[asset.kind]) {
    const option = new Option(text, value, false, value === asset.role); role.append(option);
  }
  role.disabled = state.mode !== "Ref2VA";
  role.addEventListener("change", () => editReferenceAssets(() => {
    asset.role = role.value;
    normalizeSubjectsInState();
    if (isComparisonAsset(asset)) asset.comparisonSubjects = normalizedComparisonSubjects(asset, state.subjects);
  }));
  roleLabel.append(role);
  const notesLabel = document.createElement("label"); notesLabel.textContent = "Notes";
  const notes = document.createElement("textarea"); notes.rows = 2;
  notes.placeholder = isSubjectIdentityAsset(asset) ? subjectIdentityNotesPlaceholder(asset.subjectIdentity)
    : isComparisonAsset(asset) ? "Optional: describe the intended relative scale, e.g. Subject 2's head reaches Subject 1's upper chest."
      : "Asset-specific facts or constraints";
  notes.value = asset.notes || "";
  notes.addEventListener("input", () => {
    asset.notes = notes.value;
    if (state.activeProjectType !== "filesystem") setDirty();
  });
  notes.addEventListener("change", () => editReferenceAssets(() => {}, true));
  notesLabel.append(notes);
  if (isSubjectIdentityAsset(asset)) {
    fields.classList.add("subject-identity-fields");
    const identity = asset.subjectIdentity = normalizeSubjectIdentity(asset.subjectIdentity);
    const notesInfo = document.createElement("span");
    notesInfo.className = "notes-info";
    notesInfo.textContent = " ⓘ";
    notesInfo.title = subjectIdentityNotesGuidance(identity);
    notesInfo.setAttribute("aria-label", notesInfo.title);
    notesInfo.setAttribute("role", "img");
    notesLabel.insertBefore(notesInfo, notes);
    const subjectSetLabel = document.createElement("label"); subjectSetLabel.textContent = "Identity set";
    const subjectSet = document.createElement("select"); subjectSet.title = "Stable character grouping; it does not depend on Picture order.";
    for (const option of subjectSelectorOptions(state.subjects, state.projectSequence)) subjectSet.append(new Option(option.label, option.value, false, identity.subjectId === option.value));
    subjectSet.disabled = state.mode !== "Ref2VA";
    subjectSet.addEventListener("change", () => editReferenceAssets(() => {
      if (subjectSet.value === "__new_subject__") assignNewSubject(asset);
      else {
        materializeSubject(state.subjects, subjectSet.value, state.projectSequence);
        asset.subjectIdentity.subjectId = subjectSet.value;
      }
      normalizeSubjectsInState();
    }));
    subjectSetLabel.append(subjectSet);
    const focusLabel = document.createElement("label"); focusLabel.textContent = "Identity focus";
    const focus = document.createElement("select");
    focus.title = "What identity information this Picture provides.";
    for (const [value, text, guidance] of IDENTITY_FOCUS_OPTIONS) {
      const option = new Option(text, value, false, value === identity.focus); option.title = guidance; focus.append(option);
    }
    focus.disabled = state.mode !== "Ref2VA";
    focus.addEventListener("change", () => editReferenceAssets(() => { asset.subjectIdentity.focus = focus.value; }));
    focusLabel.append(focus);
    const viewLabel = document.createElement("label"); viewLabel.textContent = "View";
    const view = document.createElement("select");
    view.title = "Which appearance viewpoint this Picture documents, not a required generated camera view.";
    for (const [value, text, guidance] of IDENTITY_VIEW_OPTIONS) {
      const option = new Option(text, value, false, value === identity.view); option.title = guidance; view.append(option);
    }
    view.disabled = state.mode !== "Ref2VA";
    view.addEventListener("change", () => editReferenceAssets(() => { asset.subjectIdentity.view = view.value; }));
    viewLabel.append(view);
    const layoutLabel = document.createElement("label"); layoutLabel.textContent = "Reference layout";
    const layout = document.createElement("select"); layout.title = "How this reference is arranged, separate from the identity information it provides.";
    for (const [value, text, guidance] of REFERENCE_LAYOUT_OPTIONS) {
      const option = new Option(text, value, false, value === identity.layout); option.title = guidance; layout.append(option);
    }
    layout.disabled = state.mode !== "Ref2VA";
    layout.addEventListener("change", () => editReferenceAssets(() => { asset.subjectIdentity.layout = layout.value; }));
    layoutLabel.append(layout);
    if (identity.layout === "reference_sheet") {
      view.disabled = true;
      view.title = "A reference sheet has multiple complementary views; this stored singular View is inactive until Single view is selected.";
      viewLabel.classList.add("inactive-field");
    }
    const helper = document.createElement("p"); helper.className = "subject-identity-helper"; helper.textContent = subjectIdentityHelperText(identity);
    fields.append(roleLabel, subjectSetLabel, focusLabel, viewLabel, layoutLabel, notesLabel, helper);
  } else if (isComparisonAsset(asset)) {
    fields.classList.add("comparison-fields");
    const comparisonSubjects = state.projectSequence ? availableSubjects(state.subjects, state.projectSequence) : state.subjects;
    asset.comparisonSubjects = normalizedComparisonSubjects(asset, comparisonSubjects);
    const comparisonLabel = document.createElement("fieldset"); comparisonLabel.className = "comparison-subjects";
    const legend = document.createElement("legend"); legend.textContent = "Subjects (choose at least two)"; comparisonLabel.append(legend);
    for (const subject of comparisonSubjects) {
      const option = document.createElement("label"); option.className = "check";
      const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = asset.comparisonSubjects.includes(subject.id); checkbox.disabled = state.mode !== "Ref2VA";
      checkbox.addEventListener("change", () => editReferenceAssets(() => {
        const selected = new Set(asset.comparisonSubjects);
        if (checkbox.checked) {
          materializeSubject(state.subjects, subject.id, state.projectSequence);
          selected.add(subject.id);
        } else selected.delete(subject.id);
        asset.comparisonSubjects = [...selected];
        normalizeSubjectsInState();
      }));
      option.append(checkbox, ` ${subjectLabel(comparisonSubjects, subject.id)}`); comparisonLabel.append(option);
    }
    const helper = document.createElement("p"); helper.className = "subject-identity-helper";
    helper.textContent = asset.comparisonSubjects.length >= 2
      ? "Preserves relative height, body size, and broad proportions. It does not prescribe target-shot poses, side-by-side staging, or camera."
      : "Choose at least two Subjects. This comparison reference cannot be generated until it has two stable Subject selections.";
    fields.append(roleLabel, comparisonLabel, notesLabel, helper);
  } else fields.append(roleLabel, notesLabel);

  if (asset.kind === "audio" && asset.role === "music_beat_rhythm") {
    const sync = document.createElement("label"); sync.className = "check";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = asset.options?.syncChoreography === true;
    checkbox.addEventListener("change", () => editReferenceAssets(() => { asset.options.syncChoreography = checkbox.checked; }));
    sync.append(checkbox, " Synchronize choreography to beat / rhythm"); content.append(title, meta, fields, sync);
  } else content.append(title, meta, fields);

  if (asset.kind === "video" && asset.role === "tail_reference_continuation") {
    const help = document.createElement("p"); help.className = "helper";
    help.textContent = "Use a bounded recent tail as continuation evidence. Start the new action from its ending state; it is not a frame-identical opening.";
    content.append(help);
  }
  const actions = document.createElement("div"); actions.className = "asset-actions";
  const orderKind = { image: "Picture", video: "Video", audio: "Audio" }[asset.kind];
  for (const [text, direction] of [["↑", -1], ["↓", 1]]) {
    const button = document.createElement("button"); button.className = "quiet"; button.textContent = text;
    button.title = direction < 0 ? `Move earlier in canonical ${orderKind} order` : `Move later in canonical ${orderKind} order`;
    button.setAttribute("aria-label", button.title);
    button.addEventListener("click", () => moveAsset(asset.id, direction)); actions.append(button);
  }
  if (asset.kind === "image") {
    const replace = document.createElement("button"); replace.className = "quiet"; replace.textContent = "Replace Picture";
    replace.addEventListener("click", () => chooseAsset("image", asset.id)); actions.append(replace);
  } else {
    const reattach = document.createElement("button"); reattach.className = "quiet"; reattach.textContent = "Reattach";
    reattach.addEventListener("click", () => chooseAsset(asset.kind, asset.id)); actions.append(reattach);
  }
  const remove = document.createElement("button"); remove.className = "quiet danger"; remove.textContent = "Remove";
  remove.addEventListener("click", () => editReferenceAssets(() => { state.assets = state.assets.filter(item => item.id !== asset.id); normalizeOrders(asset.kind); }));
  actions.append(remove); content.append(actions); card.append(preview, content); return card;
}

function normalizeOrders(kind) {
  state.assets.filter(item => item.kind === kind).sort((a, b) => a.order - b.order || a.id.localeCompare(b.id))
    .forEach((asset, index) => { asset.order = index; });
}

function moveAsset(id, direction) {
  const asset = state.assets.find(item => item.id === id);
  const siblings = state.assets.filter(item => item.kind === asset.kind).sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  const current = siblings.findIndex(item => item.id === id);
  const target = current + direction;
  if (target < 0 || target >= siblings.length) return;
  editReferenceAssets(() => { [siblings[current].order, siblings[target].order] = [siblings[target].order, siblings[current].order]; });
}

function chooseAsset(kind, reattachId = null, subjectId = null) {
  state.pendingAssetKind = kind; state.reattachId = reattachId; state.pendingAssetSubjectId = subjectId;
  const input = $("#asset-file-input");
  input.accept = { image: "image/*", video: "video/*", audio: "audio/*" }[kind];
  input.multiple = !reattachId;
  input.value = ""; input.click();
}

function classifyAssetFile(file) {
  if (file.type.startsWith("image/")) return "image";
  if (file.type.startsWith("video/")) return "video";
  if (file.type.startsWith("audio/")) return "audio";
  if (file.type && file.type !== "application/octet-stream") return null;
  const extension = file.name.split(".").pop()?.toLowerCase();
  if (["jpg", "jpeg", "png", "webp", "gif", "avif", "bmp"].includes(extension)) return "image";
  if (["mp4", "mov", "webm", "mkv", "m4v", "avi"].includes(extension)) return "video";
  if (["wav", "mp3", "m4a", "aac", "ogg", "flac", "opus"].includes(extension)) return "audio";
  return null;
}

async function processImage(file) {
  let source;
  try {
    source = await createImageBitmap(file);
  } catch {
    const dataUrl = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error("Could not read image."));
      reader.readAsDataURL(file);
    });
    source = await new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error("Could not decode image."));
      image.src = dataUrl;
    });
  }
  const sourceWidth = source.naturalWidth || source.width;
  const sourceHeight = source.naturalHeight || source.height;
  const scale = Math.min(1, 1600 / Math.max(sourceWidth, sourceHeight));
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));
  const canvas = document.createElement("canvas"); canvas.width = width; canvas.height = height;
  canvas.getContext("2d").drawImage(source, 0, 0, width, height); source.close?.();
  const dataUrl = canvas.toDataURL("image/jpeg", .88);
  return { data_url: dataUrl, width, height };
}

function readDuration(file, kind) {
  return new Promise(resolve => {
    const media = document.createElement(kind);
    const url = URL.createObjectURL(file);
    media.preload = "metadata";
    media.onloadedmetadata = () => { const value = Number.isFinite(media.duration) ? media.duration : null; URL.revokeObjectURL(url); resolve(value); };
    media.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
    media.src = url;
  });
}

function defaultImageRole() {
  if (state.mode === "I2VA") return "first_frame_anchor";
  if (state.mode === "L2VA") return "last_frame_anchor";
  if (state.mode === "FL2VA") return state.assets.some(a => a.kind === "image" && a.role === "first_frame_anchor") ? "last_frame_anchor" : "first_frame_anchor";
  return "subject_identity";
}

async function createAssetFromFile(file, kind, reattachId = null, subjectId = null, dropSubjectId = null) {
  const context = [state.currentProjectId, state.activeGenerationId];
  const requireContext = () => {
    if (context[0] !== state.currentProjectId || context[1] !== state.activeGenerationId)
      throw new Error("The active Generation changed while reading the reference file.");
  };
  if (reattachId) {
    const asset = state.assets.find(item => item.id === reattachId);
    if (!asset) return null;
    const replacement = kind === "image" ? replacePicture(asset, file, await processImage(file))
      : await readMediaReference(file, kind, readDuration);
    requireContext(); Object.assign(asset, replacement, { payloadAvailability: null, payloadSha256: null });
    asset.attached = true;
    return `${asset.filename} ${kind === "image" ? "replaced" : "reattached"}`;
  }
  const asset = {
    id: uid(), kind, filename: file.name, mimeType: file.type || `${kind}/unknown`,
    durationSeconds: null, role: kind === "image"
      ? (state.mode === "Ref2VA" && dropSubjectId === "" ? "general_visual" : defaultImageRole())
      : roleCatalog[kind][0][0],
    notes: "", options: {}, subjectIdentity: defaultSubjectIdentity(state.projectSequence), order: state.assets.filter(item => item.kind === kind).length, attached: true, image: null,
  };
  if (kind === "image") {
    asset.image = await processImage(file);
    asset.mimeType = asset.image.data_url.slice(5).split(";")[0];
  }
  else {
    Object.assign(asset, await readMediaReference(file, kind, readDuration));
  }
  requireContext();
  if (kind === "image" && state.mode === "Ref2VA" && dropSubjectId && dropSubjectId !== "") {
    const subjects = state.projectSequence ? availableSubjects(state.subjects, state.projectSequence) : state.subjects;
    if (!subjects.some(subject => subject.id === dropSubjectId))
      throw new Error("The Subject drop target is no longer available in this Generation.");
  }
  if (kind === "image" && state.mode !== "Ref2VA") {
    state.assets.filter(item => item.kind === "image" && item.role === asset.role)
      .forEach(item => { item.role = "general_visual"; });
  }
  state.assets.push(asset);
  if (isSubjectIdentityAsset(asset)) {
    if (subjectId === null) assignNewSubject(asset);
    else {
      materializeSubject(state.subjects, subjectId, state.projectSequence);
      asset.subjectIdentity.subjectId = subjectId;
    }
    normalizeSubjectsInState();
  }
  return asset.filename;
}

async function ingestAssetFiles(files, forcedKind = null, reattachId = null, subjectId = null, dropSubjectId = null) {
  const isReattach = Boolean(reattachId);
  const accepted = [], rejected = [];
  for (const file of [...files]) {
    const classified = classifyAssetFile(file);
    if (!classified || (forcedKind && classified !== forcedKind)) {
      rejected.push(file.name); continue;
    }
    if (state.mode !== "Ref2VA" && classified !== "image") {
      rejected.push(`${file.name} (Video/Audio are available in Ref2VA)`); continue;
    }
    try {
      let result;
      const contextualSubjectId = classified === "image" && state.mode === "Ref2VA" && dropSubjectId ? dropSubjectId : subjectId;
      await changeReferenceAssets(async () => { result = await createAssetFromFile(file, forcedKind || classified, reattachId, contextualSubjectId, dropSubjectId); });
      if (result) accepted.push(result);
      if (reattachId) break;
    } catch {
      rejected.push(file.name);
    }
  }
  if (accepted.length) {
    renderAssets();
    toast(isReattach ? accepted[0] : accepted.length === 1 ? `${accepted[0]} added` : `${accepted.length} reference assets added`);
  }
  if (rejected.length) toast(`Unsupported or incompatible: ${rejected.join(", ")}`);
  state.pendingAssetKind = null; state.reattachId = null; state.pendingAssetSubjectId = null;
}

async function runConnection(action) {
  const message = $("#connection-message");
  message.textContent = action === "models" ? "Discovering models…" : "Testing connection…";
  try {
    const candidate = validatedSettingsDraft();
    const result = action === "models" ? await api.models(candidate) : await api.test(candidate);
    const models = result.models || [];
    const endpoint = candidate.endpoint;
    state.endpoint = { ...endpoint };
    updateDiscoveredCatalog(endpoint, models);
    message.textContent = result.message || `Discovered ${models.length} model(s). Manual model IDs are always accepted.`;
    state.connectionState = "connected";
    setActiveModelStatus();
  } catch (error) {
    message.textContent = error.message;
    state.connectionState = "disconnected";
    setActiveModelStatus();
  }
}

async function discoverLocalEndpoints() {
  const message = $("#connection-message");
  message.textContent = "Checking known local servers…";
  try {
    const candidate = validatedSettingsDraft();
    const response = await api.discoverEndpoints({ manualEndpoint: candidate.endpoint });
    for (const result of response.endpoints || []) {
      const canReplaceCatalog = result.discoveryState === "catalog_available" || result.connected;
      updateDiscoveredCatalog(result.endpoint, result.models || [], { replace: canReplaceCatalog });
    }
    const connected = (response.endpoints || []).filter(result => result.connected);
    const detected = (response.endpoints || []).filter(result => result.discoveryState !== "unavailable" && !result.connected);
    const detectedMessage = detected.map(result => result.message).filter(Boolean).join(" ");
    message.textContent = connected.length
      ? `Found ${connected.map(result => result.endpoint.displayName).join(", ")}.${detectedMessage ? ` ${detectedMessage}` : ""}`
      : detectedMessage || "No known local servers responded. Your manual endpoint remains available.";
    const selectedProbe = (response.endpoints || []).find(result =>
      result.endpoint.id === endpointProfile().id || endpointKey(result.endpoint) === endpointKey(endpointProfile()),
    );
    if (selectedProbe) state.connectionState = selectedProbe.connected ? "connected" : "unverified";
    setActiveModelStatus();
  } catch (error) {
    message.textContent = error.message;
    state.connectionState = "disconnected";
    setActiveModelStatus();
  }
}

function setGenerationStatus(text, stateName = "idle") {
  const status = $("#generation-status");
  status.textContent = text;
  status.dataset.state = stateName;
}

function setGenerationControls(active) {
  const button = $("#generate");
  const mainActive = active && state.generation?.authoringPath === "one_shot";
  const storyActive = active && state.generation?.authoringPath === "story";
  button.querySelector("span").textContent = mainActive ? "Stop" : "Generate from Main Prompt";
  $("#generate-story").querySelector("span").textContent = storyActive ? "Stop" : "Generate from Story";
  button.querySelector("kbd").classList.toggle("hidden", mainActive);
  button.classList.toggle("stop", mainActive);
  button.setAttribute("aria-pressed", String(mainActive));
  $("#generate-story").classList.toggle("stop", storyActive);
  $("#generate-story").setAttribute("aria-pressed", String(storyActive));
  renderOutput();
}

function stopGeneration() {
  const session = state.generation;
  if (!session || session.stopping) return;
  session.stopping = true;
  setGenerationStatus("Stopping…", "stopping");
  session.controller.abort();
}

function updateStreamedOutput(variation) {
  const session = state.generation;
  if (!session || session.renderQueued) return;
  session.renderQueued = true;
  requestAnimationFrame(() => {
    if (state.generation !== session) return;
    session.renderQueued = false;
    if (state.outputs !== session.outputs || state.outputIndex !== variation) return;
    const prompt = session.outputs[variation]?.prompt || "";
    if (state.rawEdit) {
      const output = $("#output");
      output.value = prompt;
      output.scrollTop = output.scrollHeight;
    } else {
      const highlighted = $("#output-highlighted");
      highlightPrompt(highlighted, prompt);
      highlighted.scrollTop = highlighted.scrollHeight;
    }
  });
}

function seconds(value) {
  return value == null ? null : `${(value / 1000).toFixed(value < 10000 ? 2 : 1)}s`;
}

function renderPerformance(session, metrics = null) {
  if (metrics) session.metrics.set(metrics.variation, metrics);
  const current = session.metrics.get(state.outputIndex) || metrics;
  const effectiveSeed = current && Object.hasOwn(current, "seed") ? current.seed : session.outputs[state.outputIndex]?.seed;
  const summary = $("#performance-summary");
  const details = $("#performance-details");
  summary.classList.remove("hidden");
  details.classList.remove("hidden");
  const parts = [];
  if (current?.firstEventMs != null) parts.push(`First event ${seconds(current.firstEventMs)}`);
  if (current?.firstReasoningMs != null) parts.push(`Reasoning ${seconds(current.firstReasoningMs)}`);
  if (current?.firstContentMs != null) parts.push(`Final content ${seconds(current.firstContentMs)}`);
  if (current?.finalContentMs != null) parts.push(`Final output ${seconds(current.finalContentMs)}`);
  summary.textContent = parts.join(" · ") || "Upstream request starting…";
  const diagnostic = session.diagnostics || {};
  const lines = [
    `Generate clicked: ${session.clickedAt}`,
    ...requestFactLines(session),
    `Request assembly: ${diagnostic.assemblyMs ?? "—"} ms`,
    `Estimated output room: ${diagnostic.estimatedCompletionHeadroom?.toLocaleString() ?? "unknown"} tokens${diagnostic.completionBudgetFits === false ? " · full requested budget may not fit" : ""}`,
    `Vision images: ${diagnostic.sentVisionImages?.length ?? 0}${diagnostic.imageDimensions?.length ? ` · Canonical: ${diagnostic.imageDimensions.map(item => `${item.width}×${item.height}`).join(", ")}` : ""}${diagnostic.sentVisionImages?.length ? ` · Sent to prompt writer: ${diagnostic.sentVisionImages.map(item => `${item.width}×${item.height}`).join(", ")} (${(diagnostic.sentVisionEncodedBytes / 1024).toFixed(1)} KiB encoded)` : ""}`,
    `Backend/model: ${diagnostic.backend || "—"} / ${diagnostic.model || "—"}`,
    `Seed used: ${effectiveSeed ?? "backend default"}`,
    `Cache-input fingerprint: ${diagnostic.fingerprint || "waiting"}`,
  ];
  if (current) {
    lines.push(
      `First upstream event: ${seconds(current.firstEventMs) || "—"}`,
      `First reasoning delta: ${seconds(current.firstReasoningMs) || "none observed"}`,
      `First final-content delta: ${seconds(current.firstContentMs) || "—"}`,
      `Reasoning lead time: ${seconds(current.reasoningMs) || "—"}`,
      `Final-content duration: ${seconds(current.finalContentMs) || "—"}`,
      `Total upstream duration: ${seconds(current.totalMs) || "—"}`,
      `Reasoning characters observed: ${current.reasoningChars ?? 0}`,
      `Usage: ${Object.keys(current.usage || {}).length ? JSON.stringify(current.usage) : "not provided"}`,
      `Compatibility fallback: ${current.compatibilityFallback ? "yes" : "no"}`,
    );
  }
  $("#performance-diagnostics").textContent = lines.join("\n");
}

async function generate(authoringPath = "one_shot") {
  if (authoringPath === "story") $("#story-generation-feedback").textContent = "";
  const id = authoringPath === "story" ? "h3-story" : "h3-main";
  const label = authoringPath === "story" ? "Generate from Story" : "Generate from Main Prompt";
  const intent = generationClickIntent(state.generation, state.generationStarting);
  if (intent === "stop" && providerActivity.active()?.id === id) { providerActivity.cancelActive(); setGenerationStatus(`${label} is already running. Stopping…`, "stopping"); return; }
  if (providerActivity.active()) { setGenerationStatus(providerActivity.conflict(id, label), "error"); return; }
  if (intent === "already-starting") { setGenerationStatus(`${label} is already running.`, "preparing"); return; }
  const controller = new AbortController();
  const activityToken = providerAttempt(id, label, message => setGenerationStatus(message, "error"), () => {
    if (state.generation?.controller === controller) stopGeneration();
    else controller.abort();
  });
  if (!activityToken) return;
  const contextEpoch = providerContextEpoch;
  state.generationStarting = true;
  let requestSnapshot, outputs, session;
  try {
    markRevisionStale("The source prompt was replaced by a new generation.");
    if (authoringPath === "story" && (state.activeProjectType !== "filesystem" || state.dirty || !state.scenePlan.trim())) {
      const message = storyGenerationFeedback({ visibleScenePlan: state.scenePlan, savedScenePlan: state.savedScenePlan });
      $("#story-generation-feedback").textContent = message;
      throw new Error(message);
    }
    await syncReferenceAuthority();
    persistSettings();
    await ensureDialogueResolution(authoringPath, controller.signal);
    if (controller.signal.aborted || !providerContextCurrent(contextEpoch)) return;
    requestSnapshot = snapshotGenerationRequest(buildGenerateRequest(authoringPath));
    outputs = Array.from({ length: requestSnapshot.workspace.variations }, () => ({
      prompt: "", validation: null, generationStatus: "pending",
    }));
    session = {
      controller, requestSnapshot, authoringPath, stopping: false, renderQueued: false,
      completed: new Set(), streamError: null, outputs,
      clickedAt: new Date().toISOString(), metrics: new Map(), diagnostics: null,
    };
    state.generation = session;
  } catch (error) {
    if (error.name !== "AbortError") toast(error.message);
    return;
  } finally {
    state.generationStarting = false;
    if (!session) providerActivity.finish(activityToken);
  }
  updateEjectButton();
  state.outputs = outputs;
  state.outputIndex = 0;
  renderWarnings([]);
  $("#performance-summary").classList.add("hidden");
  $("#performance-details").classList.add("hidden");
  setGenerationStatus("Preparing request…", "preparing");
  setGenerationControls(true);
  try {
    await api.streamGenerate(requestSnapshot, {
      signal: controller.signal,
      onEvent: ({ event, data }) => {
        if (state.generation !== session || !providerContextCurrent(contextEpoch) || controller.signal.aborted) return;
        if (event === "start") {
          renderWarnings(data.warnings || []);
          session.diagnostics = data.diagnostics || null;
          renderPerformance(session);
          setGenerationStatus("Waiting for first token…", "waiting");
        } else if (event === "variation_start") {
          session.outputs[data.variation].generationStatus = "waiting";
          session.outputs[data.variation].seed = data.seed ?? null;
          if (state.outputs === session.outputs) {
            state.outputIndex = data.variation;
            renderOutput();
          }
          setGenerationStatus(`Waiting for first token…${requestSnapshot.workspace.variations > 1 ? ` Variation ${data.variation + 1}` : ""}`, "waiting");
        } else if (event === "metrics") {
          renderPerformance(session, data);
        } else if (event === "reasoning") {
          setGenerationStatus(`Reasoning…${requestSnapshot.workspace.variations > 1 ? ` Variation ${data.variation + 1}` : ""}`, "waiting");
        } else if (event === "compatibility_fallback") {
          setGenerationStatus("Backend rejected optional reasoning controls; retrying with backend defaults…", "waiting");
        } else if (event === "seed_unsupported") {
          if (session.outputs[data.variation]) session.outputs[data.variation].seed = null;
          renderWarnings(["This endpoint does not support the seed parameter; continuing with backend defaults."]);
        } else if (event === "delta") {
          const output = session.outputs[data.variation];
          if (!output || typeof data.text !== "string") return;
          output.prompt += data.text;
          output.generationStatus = "generating";
          setGenerationStatus(`Generating…${requestSnapshot.workspace.variations > 1 ? ` Variation ${data.variation + 1}` : ""}`, "generating");
          updateStreamedOutput(data.variation);
        } else if (event === "complete") {
          session.outputs[data.variation] = {
            prompt: data.prompt, validation: data.validation, sourceFingerprint: data.sourceFingerprint,
            authoringPath: data.authoringPath || authoringPath,
            referenceMap: data.referenceMap, executionRouting: requestSnapshot.executionRouting,
            generationStatus: "complete", seed: data.seed ?? null,
          };
          renderPerformance(session, data.metrics);
          session.completed.add(data.variation);
          if (state.outputs === session.outputs) renderOutput();
        } else if (event === "error") {
          session.streamError = data;
          const output = session.outputs[data.variation];
          if (output) { output.validation = null; output.generationStatus = "error"; }
          if (state.outputs === session.outputs) renderOutput();
        }
      },
    });
    if (!providerContextCurrent(contextEpoch)) return;
    if (controller.signal.aborted) { setGenerationStatus("Stopped", "stopped"); return; }
    if (session.streamError) {
      const suffix = session.streamError.partial ? " — partial output preserved" : "";
      setGenerationStatus(`Error${suffix}`, "error");
      renderWarnings([session.streamError.message]);
    } else {
      setGenerationStatus("Complete", "complete");
    }
  } catch (error) {
    if (!providerContextCurrent(contextEpoch)) return;
    if (error.name === "AbortError") {
      for (const output of session.outputs) {
        if (output.generationStatus !== "complete") {
          output.validation = null;
          output.generationStatus = "stopped";
        }
      }
      if (state.outputs === session.outputs) renderOutput();
      const hasPartial = session.outputs.some(output => output.generationStatus === "stopped" && output.prompt);
      setGenerationStatus(hasPartial ? "Stopped by user — partial output" : "Stopped", "stopped");
    } else {
      const hasPartial = session.outputs.some(output => output.prompt);
      setGenerationStatus(hasPartial ? "Error — partial output preserved" : "Error", "error");
      toast(error.message);
      renderWarnings([error.message]);
    }
  } finally {
    providerActivity.finish(activityToken);
    if (state.generation === session) {
      state.generation = null;
      setGenerationControls(false);
      workflowPreparation?.sync();
      renderedTakes?.sync();
      updateEjectButton();
    }
  }
}

function renderWarnings(warnings = []) {
  state.promptWarnings = warnings;
  const visible = visiblePromptWarnings(warnings);
  const banner = $("#warning-banner");
  banner.classList.toggle("hidden", !visible.length); banner.textContent = visible.join(" ");
}

function renderOutput() {
  workflowPreparation?.sync();
  renderedTakes?.sync();
  const current = state.outputs[state.outputIndex];
  savedPrompts?.sync();
  const generating = Boolean(state.generation);
  $("#empty-output").classList.toggle("hidden", Boolean(current));
  $("#output-view-controls").classList.toggle("hidden", !current);
  $("#output-highlighted").classList.toggle("hidden", !current || state.rawEdit);
  $("#output").classList.toggle("hidden", !current || !state.rawEdit);
  $("#output").value = current?.prompt || "";
  if (current && !state.rawEdit) highlightPrompt($("#output-highlighted"), current.prompt);
  $("#highlighted-view").classList.toggle("active", !state.rawEdit);
  $("#edit-raw").classList.toggle("active", state.rawEdit);
  $("#highlighted-view").setAttribute("aria-selected", String(!state.rawEdit));
  $("#edit-raw").setAttribute("aria-selected", String(state.rawEdit));
  $("#output").readOnly = generating || state.repairRunning;
  for (const id of ["copy-output", "download-output"]) $("#" + id).disabled = !current;
  for (const id of ["validate-output", "regenerate"]) $("#" + id).disabled = !current || generating;
  const repairButton = $("#repair-output");
  const repairUi = repairUiState({ hasOutput: Boolean(current), generating, repairRunning: state.repairRunning });
  repairButton.disabled = repairUi.disabled;
  repairButton.textContent = repairUi.label;
  repairButton.setAttribute("aria-busy", String(repairUi.busy));
  updateReviseSelectionButton();
  const tabs = $("#variation-tabs"); tabs.replaceChildren(); tabs.classList.toggle("hidden", state.outputs.length < 2);
  state.outputs.forEach((output, index) => {
    const button = document.createElement("button"); button.textContent = output.origin === "history" ? "History item" : `Variation ${index + 1}`; button.classList.toggle("active", index === state.outputIndex);
    button.addEventListener("click", () => {
      if (index !== state.outputIndex) markRevisionStale("The active output variation changed.");
      state.outputIndex = index; state.revisionSelection = null; renderOutput();
    }); tabs.append(button);
  });
  renderValidation(current?.currentAudit || current?.validation,
    current?.currentAudit ? "Current Format Audit" : current?.validation ? "Validation recorded when generated" : null);
}

function setOutputView(raw) {
  const current = state.outputs[state.outputIndex];
  if (!current) return;
  const from = state.rawEdit ? $("#output") : $("#output-highlighted");
  const scrollTop = from.scrollTop;
  state.rawEdit = raw;
  state.revisionSelection = null;
  renderOutput();
  const to = raw ? $("#output") : $("#output-highlighted");
  to.scrollTop = scrollTop;
  if (raw) to.focus();
}

function updateReviseSelectionButton() {
  const current = state.outputs[state.outputIndex];
  const selected = state.revisionSelection;
  $("#revise-selection").disabled = !current || Boolean(state.generation) || Boolean(state.revision?.running)
    || !selected || selected.outputIndex !== state.outputIndex || selected.fullPrompt !== current.prompt;
}

function captureActiveSelection() {
  const current = state.outputs[state.outputIndex];
  if (!current || state.generation) return;
  const snapshot = state.rawEdit
    ? selectionFromTextarea($("#output"), current.prompt)
    : selectionFromHighlighted($("#output-highlighted"), window.getSelection(), current.prompt);
  state.revisionSelection = snapshot ? { ...snapshot, outputIndex: state.outputIndex } : null;
  updateReviseSelectionButton();
}

function revisionStale() {
  return Boolean(state.revision?.stale) || revisionIsStale(state.outputs, state.outputIndex, state.revision);
}

function markRevisionStale(message = "The source prompt changed.") {
  if (!state.revision) return;
  state.revision.stale = true;
  state.revision.staleMessage = message;
  if (!$("#revision-panel").classList.contains("hidden")) {
    $("#revision-status").textContent = `${message} Apply Revision is disabled.`;
    $("#apply-revision").disabled = true;
    $("#apply-revision-anyway").disabled = true;
    $("#apply-revision-anyway").classList.add("hidden");
  }
}

function openRevisionPanel() {
  const selected = state.revisionSelection;
  const current = state.outputs[state.outputIndex];
  if (!selected || !current || selected.fullPrompt !== current.prompt) return;
  state.revision = createRevisionSnapshot(
    state.outputs, state.outputIndex, selected, "", captureWorkspace(), settings(),
  );
  Object.assign(state.revision, { running: false, stale: false, preview: "", complete: false });
  $("#revision-selected").textContent = selected.selectedText;
  $("#revision-preview").textContent = "";
  $("#revision-preview-wrap").classList.add("hidden");
  $("#revision-validation").replaceChildren();
  $("#revision-status").textContent = "Ready to generate a replacement preview.";
  $("#revision-instruction").value = "";
  $("#apply-revision").disabled = true;
  $("#apply-revision-anyway").disabled = true;
  $("#apply-revision-anyway").classList.add("hidden");
  $("#retry-revision").classList.add("hidden");
  $("#generate-revision").classList.remove("hidden");
  $("#stop-revision").classList.add("hidden");
  $("#revision-panel").classList.remove("hidden");
  $("#revision-instruction").focus();
  updateReviseSelectionButton();
}

function renderRevisionValidation(validation, originalValidation) {
  const target = $("#revision-validation");
  const assessment = assessRevisionFindings(validation, originalValidation);
  target.replaceChildren();
  const summary = document.createElement("p");
  if (assessment.newStructural.length) {
    summary.textContent = `Candidate has ${assessment.newStructural.length} new structural error(s). Apply Anyway is required to accept it.`;
  } else if (assessment.workspaceMismatches.length) {
    summary.textContent = `Candidate has ${assessment.workspaceMismatches.length} workspace consistency mismatch(es); the revised output may intentionally diverge from workspace settings.`;
  } else if (assessment.preexistingStructural.length) {
    summary.textContent = `Candidate retains ${assessment.preexistingStructural.length} pre-existing structural finding(s), but introduces none.`;
  } else {
    summary.textContent = "Candidate has no structural errors.";
  }
  target.append(summary);
  for (const item of validation?.findings || []) {
    const row = document.createElement("p");
    const scope = item.category === "workspace_consistency" ? "Workspace mismatch" : item.severity;
    const prior = assessment.preexistingStructural.some(existing => findingSignature(existing) === findingSignature(item));
    row.textContent = `${scope}${prior ? " (pre-existing)" : ""} · ${item.code}: ${item.message}`;
    target.append(row);
  }
  return assessment;
}

function updateRevisionApplyActions(revision) {
  const ready = Boolean(revision?.complete && revision.candidatePrompt && revision.assessment && !revisionStale());
  const hasNewStructural = Boolean(revision?.assessment?.newStructural.length);
  $("#apply-revision").disabled = !ready || hasNewStructural;
  $("#apply-revision-anyway").disabled = !ready || !hasNewStructural;
  $("#apply-revision-anyway").classList.toggle("hidden", !ready || !hasNewStructural);
}

function setRevisionRunning(running) {
  if (state.revision) state.revision.running = running;
  $("#stop-revision").classList.toggle("hidden", !running);
  $("#revision-instruction").disabled = running;
  updateEjectButton();
  updateReviseSelectionButton();
}

async function generateRevision() {
  const revision = state.revision;
  const label = "Revising H3 Prompt", id = "h3-revision";
  if (providerActivity.active()) { $("#revision-status").textContent = providerActivity.conflict(id, label); return; }
  if (!revision) { $("#revision-status").textContent = "Choose an output excerpt before Generate Revision."; return; }
  if (revisionStale()) { $("#revision-status").textContent = "Reference or prompt authority changed. Reopen Revision against the current Workspace."; return; }
  if (revision.running) { $("#revision-status").textContent = "Revision is already in progress."; return; }
  const instruction = $("#revision-instruction").value;
  if (!instruction.trim()) { $("#revision-status").textContent = "Enter a revision request first."; return; }
  revision.instruction = instruction;
  revision.preview = ""; revision.complete = false; revision.candidatePrompt = null; revision.validation = null; revision.assessment = null;
  revision.controller = new AbortController();
  const activityToken = providerAttempt(id, label, message => $("#revision-status").textContent = message, () => stopRevision());
  if (!activityToken) return;
  const contextEpoch = providerContextEpoch;
  $("#revision-preview").textContent = "";
  $("#revision-preview-wrap").classList.remove("hidden");
  $("#apply-revision").disabled = true;
  $("#apply-revision-anyway").disabled = true;
  $("#apply-revision-anyway").classList.add("hidden");
  $("#retry-revision").classList.add("hidden");
  $("#revision-status").textContent = "Waiting for revision…";
  setRevisionRunning(true);
  const request = {
    workspace: revision.workspace,
    authoringPath: revision.authoringPath,
    filesystemProjectId: state.activeProjectType === "filesystem" ? state.currentProjectId : null,
    generationId: state.activeProjectType === "filesystem" ? state.activeGenerationId : null,
    executionRouting: state.outputs[revision.originIndex]?.executionRouting || { assets: {} },
    llm: { ...revision.llm, seed: resolveSeed(revision.llm) },
    outputIndex: revision.originIndex,
    selection: {
      fullPrompt: revision.selection.fullPrompt,
      beforeSelection: revision.selection.beforeSelection,
      selectedText: revision.selection.selectedText,
      afterSelection: revision.selection.afterSelection,
    },
    instruction,
  };
  try {
    await syncReferenceAuthority();
    if (revisionStale()) throw new Error("Reference Assets changed. Reopen Revision against the current Workspace.");
    await api.streamRevision(request, {
      signal: revision.controller.signal,
      onEvent: ({ event, data }) => {
        if (state.revision !== revision || !providerContextCurrent(contextEpoch) || revision.controller.signal.aborted) return;
        if (event === "start") renderWarnings(data.warnings || []);
        else if (event === "reasoning" && !revisionStale()) $("#revision-status").textContent = "Reasoning about the selected excerpt…";
        else if (event === "compatibility_fallback" && !revisionStale()) $("#revision-status").textContent = "Retrying with backend-default compatibility…";
        else if (event === "seed_unsupported" && !revisionStale()) $("#revision-status").textContent = "Seed is unsupported here; continuing with backend defaults…";
        else if (event === "delta") {
          revision.preview += data.text || ""; $("#revision-preview").textContent = revision.preview;
          if (!revisionStale()) $("#revision-status").textContent = "Streaming revision preview…";
        } else if (event === "complete") {
          revision.preview = data.replacement; revision.candidatePrompt = data.candidatePrompt;
          revision.validation = data.validation; revision.sourceFingerprint = data.sourceFingerprint;
          revision.referenceMap = data.referenceMap; revision.complete = true;
          $("#revision-preview").textContent = data.replacement;
          revision.assessment = renderRevisionValidation(data.validation, data.originalValidation);
          const stale = revisionStale();
          updateRevisionApplyActions(revision);
          $("#revision-status").textContent = stale
            ? `${revision.staleMessage || "The source prompt changed."} Apply Revision is disabled.`
            : revision.assessment.newStructural.length
              ? "Candidate has new structural errors. Retry, or use Apply Anyway after review."
              : revision.assessment.workspaceMismatches.length
                ? "Workspace mismatch noted. Apply Revision keeps the intentional output divergence."
                : revision.assessment.preexistingStructural.length
                  ? "Candidate retains pre-existing structural findings but introduces none. Review and apply when ready."
                  : "Revision preview complete. Review it before applying.";
        } else if (event === "error") {
          revision.error = data.message; $("#revision-status").textContent = data.message;
        }
      },
    });
  } catch (error) {
    if (providerContextCurrent(contextEpoch)) $("#revision-status").textContent = error.name === "AbortError"
      ? "Revision stopped. The original prompt is unchanged; partial preview cannot be applied."
      : error.message;
  } finally {
    providerActivity.finish(activityToken);
    if (state.revision === revision) {
      setRevisionRunning(false);
      $("#retry-revision").classList.remove("hidden");
      if (!revision.complete) {
        $("#apply-revision").disabled = true;
        $("#apply-revision-anyway").disabled = true;
        $("#apply-revision-anyway").classList.add("hidden");
      }
    }
  }
}

function stopRevision() {
  if (providerActivity.active()?.id === "h3-revision" && !providerActivity.active().cancelling) {
    providerActivity.cancelActive(); return;
  }
  if (state.revision?.running) state.revision.controller.abort();
}

function cancelRevision() {
  stopRevision(); state.revision = null; state.revisionSelection = null;
  $("#revision-panel").classList.add("hidden"); updateReviseSelectionButton();
}

function applyRevision(anyway = false) {
  anyway = anyway === true;
  const revision = state.revision;
  const permitted = anyway
    ? !$("#apply-revision-anyway").disabled
    : !$("#apply-revision").disabled;
  if (!revision || !permitted || !applyRevisionCandidate(state.outputs, state.outputIndex, revision)) {
    if (revision) markRevisionStale();
    return;
  }
  state.revision = null; state.revisionSelection = null; $("#revision-panel").classList.add("hidden");
  renderOutput(); toast(anyway ? "Revision applied despite structural findings" : "Revision applied to the selected excerpt");
}

function renderValidation(validation, label = null) {
  const summary = $("#validation-summary"), list = $("#validation-list");
  if (!validation) {
    summary.className = "validation-summary neutral"; summary.innerHTML = '<span class="status-dot"></span><span>Not validated</span>';
    list.innerHTML = '<p class="muted">Generate or paste a prompt to inspect official H3 grammar and invariants.</p>'; return;
  }
  const errors = validation.findings.filter(item => item.severity === "ERROR").length;
  const warnings = validation.findings.filter(item => item.severity === "WARNING").length;
  const status = errors ? "bad" : warnings ? "warn" : "ok";
  summary.className = `validation-summary ${status}`;
  summary.innerHTML = `<span class="status-dot ${status === "ok" ? "ok" : status === "bad" ? "bad" : ""}"></span><span>${errors ? `${errors} error(s)` : warnings ? `Valid with ${warnings} warning(s)` : "Structurally valid"}</span>`;
  list.replaceChildren();
  if (label) { const heading = document.createElement("p"); heading.textContent = label; list.append(heading); }
  list.append(...validation.findings.map(item => {
    const row = document.createElement("div"); row.className = `finding ${item.severity}`;
    const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = item.severity;
    const message = document.createElement("span"); message.textContent = item.message; row.append(badge, message); return row;
  }));
}

async function validateCurrent() {
  const index = state.outputIndex, current = state.outputs[index]; if (!current) return;
  current.prompt = $("#output").value;
  try {
    const audit = await api.validate({
      ...(state.activeProjectType === "filesystem" ? { filesystemProjectId: state.currentProjectId, generationId: state.activeGenerationId } : {}),
      prompt: current.prompt, mode: state.mode, durationSeconds: Number($("#duration").value),
      shots: $("#shots").value.trim() === "" ? null : Number($("#shots").value),
      assets: state.assets, dialogue: $("#dialogue").value,
      dialogueLanguage: state.dialogueLanguage, dialogueResolution: state.dialogueResolution,
      executionRouting: current.executionRouting || { assets: {} },
    });
    if (state.outputIndex !== index || state.outputs[index] !== current) return;
    state.outputs[index] = currentFormatAudit(current, audit);
    renderValidation(audit, "Current Format Audit");
    workflowPreparation?.sync();
  } catch (error) { toast(error.message); }
}

async function repairCurrent() {
  const id = "h3-repair", label = "Repairing H3 Format";
  if (providerActivity.active()) { toast(providerActivity.conflict(id, label)); return; }
  if (!canStartRepair(state.repairRunning)) return;
  const current = state.outputs[state.outputIndex]; if (!current) return;
  const controller = new AbortController();
  let cancelled = false, adopted = false, activityToken;
  activityToken = providerAttempt(id, label, toast, () => {
    cancelled = true; controller.abort(); state.repairRunning = false; renderOutput(); updateEjectButton();
    toast("Format repair cancelled."); providerActivity.finish(activityToken);
  });
  if (!activityToken) return;
  const contextEpoch = providerContextEpoch, outputIndex = state.outputIndex;
  state.repairRunning = true; renderOutput(); updateEjectButton();
  try {
    await syncReferenceAuthority();
    if (cancelled || !providerContextCurrent(contextEpoch)) return;
    const result = await api.repair({ ...buildGenerateRequest(current.authoringPath || "one_shot"), executionRouting: current.executionRouting || { assets: {} }, prompt: $("#output").value, findings: (current.currentAudit || current.validation)?.findings || [] }, controller.signal);
    if (cancelled || !providerContextCurrent(contextEpoch) || state.outputs[outputIndex] !== current) return;
    markRevisionStale("The source prompt was replaced by format repair.");
    result.executionRouting = current.executionRouting;
    result.savedPromptTarget = current.savedPromptTarget;
    state.outputs[outputIndex] = result; renderOutput(); renderWarnings(result.warnings || []); toast("One repair pass completed");
    adopted = true; state.repairRunning = false; renderOutput(); updateEjectButton(); providerActivity.finish(activityToken);
  } catch (error) { if (!cancelled && error.name !== "AbortError" && providerContextCurrent(contextEpoch)) toast(error.message); }
  finally { providerActivity.finish(activityToken); if (!cancelled && !adopted) { state.repairRunning = false; renderOutput(); updateEjectButton(); } }
}

async function showInstructions() {
  const current = state.outputs[state.outputIndex];
  const authoringPath = current?.authoringPath || "one_shot";
  $("#instructions-path").value = authoringPath;
  $("#instructions-dialog").showModal();
  await loadInstructions(authoringPath);
}

async function loadInstructions(authoringPath) {
  try {
    await syncReferenceAuthority();
    if (!await ensureInstructionsDialogue(authoringPath)) return;
    const result = await api.assemble(buildGenerateRequest(authoringPath));
    $("#instructions-output").textContent = result.instructions;
    renderWarnings([...(result.warnings || []), ...(result.capacity?.completionBudgetWarning ? [result.capacity.completionBudgetWarning] : [])]);
  } catch (error) { toast(error.message); }
}

async function ensureInstructionsDialogue(authoringPath) {
  while (true) {
    const snapshot = { dialogueLanguage: state.dialogueLanguage, dialogue: $("#dialogue").value, resolution: state.dialogueResolution };
    const needsProvider = await dialogueResolutionNeedsProvider(snapshot);
    const current = { dialogueLanguage: state.dialogueLanguage, dialogue: $("#dialogue").value, resolution: state.dialogueResolution };
    if (snapshot.dialogueLanguage !== current.dialogueLanguage || snapshot.dialogue !== current.dialogue || snapshot.resolution !== current.resolution) continue;
    if (!needsProvider) return true;
    await refreshDialogueResolution(authoringPath);
    const after = { dialogueLanguage: state.dialogueLanguage, dialogue: $("#dialogue").value, resolution: state.dialogueResolution };
    if (snapshot.dialogueLanguage !== after.dialogueLanguage || snapshot.dialogue !== after.dialogue) continue;
    const stillNeedsProvider = await dialogueResolutionNeedsProvider(after);
    if (after.dialogueLanguage !== state.dialogueLanguage || after.dialogue !== $("#dialogue").value || after.resolution !== state.dialogueResolution) continue;
    if (stillNeedsProvider) return false;
  }
}

function persistenceWorkspace() {
  const workspace = captureWorkspace();
  workspace.assets = workspace.assets.map(asset => asset.kind === "image" ? asset : { ...asset, attached: false });
  return workspace;
}

function setEphemeralSequence() {
  state.projectSequence = null;
  const sequence = ephemeralSequence();
  state.sequenceGenerations = sequence.generations;
  state.activeGenerationId = sequence.activeGenerationId;
  renderSequenceControls();
  renderContinuityUi();
}

function rememberProjectLocation(projectId) {
  window.history.replaceState(null, "", projectLocation(window.location.href, projectId));
}

function adoptProjectResponse(project, { dirty = false, generatedReset = false } = {}) {
  navigationAuthority.invalidate();
  if (generatedReset) {
    sequenceRenderAction?.reset();
    renderedTakes?.reset(); workflowPreparation?.reset(); savedPrompts?.reset();
  } else savedPrompts?.rememberCurrent();
  invalidateProviderContext();
  // Component syncs run from restoreWorkspace/setDirty. Mark the old context
  // unavailable first so those intermediate renders cannot read a mixed
  // Project/new-Generation pair while the authoritative response is adopted.
  state.contextReplacing = true;
  workflowPreparation?.sync(); renderedTakes?.sync();
  try {
    rememberProjectLocation(project.id);
    restoreWorkspace(project.workspace, project.sequence, project.dialogueLanguage);
    state.projectFingerprint = project.projectFingerprint;
    state.projectEditFingerprint = project.projectEditFingerprint;
    state.projectUpdatedAt = project.updatedAt;
    state.currentProjectId = project.id;
    state.currentProjectSavedName = project.name;
    state.currentLegacyProjectId = null;
    state.activeProjectType = "filesystem";
    const sequence = sequenceFromProject(project);
    state.sequenceGenerations = sequence.generations;
    state.activeGenerationId = sequence.activeGenerationId;
    restoreContinuity(project.continuity);
    restorePlanning(project.planning);
    storyPlanner?.restoreStoryIdea(project.storyIdea);
    storyPlanner?.restoreScenePlan(state.scenePlan);
    storyPlanner?.restoreStoryCreativeDirection(project.storyCreativeDirection);
    storyPlanner?.restoreStoryPlanningStyle(project.storyPlanningStyle);
    storyPlanner?.restoreStoryMemory(project.storyMemoryAfter);
    setDirty(dirty);
    renderProjectOptions(state.filesystemProjects, state.legacyProjects);
    renderSequenceControls();
    renderContinuityUi();
  } finally {
    state.contextReplacing = false;
    workflowPreparation?.sync(); renderedTakes?.sync();
    requestReferenceManifestRefresh();
    savedPrompts?.load({ restore: !generatedReset });
  }
}

function renderSequenceControls() {
  continuationDrafting?.sync();
  const select = $("#generation-select");
  if (!select) return;
  const saved = state.activeProjectType === "filesystem" && Boolean(state.currentProjectId) && state.sequenceGenerations.length > 0;
  const generations = saved ? state.sequenceGenerations : ephemeralSequence().generations;
  select.replaceChildren(...generations.map((generation, index) => new Option(`Generation ${index + 1}`, generation.generationId || "ephemeral")));
  select.value = saved ? (state.activeGenerationId || "") : "ephemeral";
  select.disabled = !saved;
  const activeIndex = saved ? generations.findIndex(item => item.generationId === state.activeGenerationId) : 0;
  const dirty = state.dirty;
  const canEdit = structuralOperationAllowed({ saved, dirty });
  const setDisabled = (id, disabled) => { const button = $(`#${id}`); if (button) button.disabled = disabled; };
  setDisabled("generation-add", !canEdit);
  setDisabled("generation-before", !canEdit);
  setDisabled("generation-after", !canEdit);
  setDisabled("generation-duplicate", !canEdit);
  setDisabled("generation-earlier", !canEdit || activeIndex <= 0);
  setDisabled("generation-later", !canEdit || activeIndex < 0 || activeIndex >= generations.length - 1);
  setDisabled("generation-delete", !canEdit || generations.length <= 1);
  $("#sequence-count").textContent = saved ? `${generations.length} ${generations.length === 1 ? "Generation" : "Generations"}` : "Draft";
  $("#sequence-helper").textContent = saved
    ? (dirty ? "Save or discard changes before editing the Sequence." : "Generation numbers follow the current order.")
    : "Save the Project to add more Generations.";
}

function currentPlanningDraft() {
  return {
    creativeRequest: $("#creative-request").value,
    dialogue: $("#dialogue").value,
    incomingState: state.incomingState,
    intendedOutgoingState: state.intendedOutgoingState,
    planning: capturePlanning(),
  };
}

function applyPlanningDraft(next) {
  $("#creative-request").value = next.creativeRequest;
  $("#dialogue").value = next.dialogue ?? "";
  state.incomingState = next.incomingState;
  state.intendedOutgoingState = next.intendedOutgoingState;
  state.relationshipToPrevious = next.planning.relationshipToPrevious;
  state.cameraIntent = next.planning.cameraIntent;
  renderContinuityUi();
  updateCharCount();
}

function planningRequestBody() {
  return {
    workspace: captureWorkspace(),
    continuity: captureContinuity(),
    planning: capturePlanning(),
    llm: { ...settings(), seed: resolveSeed(settings()) },
  };
}

async function inferFromPrevious() {
  const activeIndex = state.sequenceGenerations.findIndex(item => item.generationId === state.activeGenerationId);
  const status = $("#planning-status");
  const id = "infer-plan", label = "Inferring Plan from Previous";
  if (providerActivity.active()) { status.textContent = providerActivity.conflict(id, label); return; }
  if (state.activeProjectType !== "filesystem" || !state.currentProjectId || activeIndex <= 0) {
    status.textContent = "Select a saved Generation after Generation 1 before inferring a plan."; return;
  }
  const controller = new AbortController(); let cancelled = false, activityToken;
  activityToken = providerAttempt(id, label, message => status.textContent = message, () => {
    cancelled = true; controller.abort(); state.inferRunning = false;
    status.textContent = "Inference cancelled."; providerActivity.finish(activityToken);
  });
  if (!activityToken) return;
  const contextEpoch = providerContextEpoch, projectId = state.currentProjectId, generationId = state.activeGenerationId;
  state.inferRunning = true;
  status.textContent = "Preparing a proposal from the committed predecessor…";
  try {
    const proposal = await api.inferPlan(projectId, generationId, planningRequestBody(), controller.signal);
    if (cancelled || !providerContextCurrent(contextEpoch)) return;
    const next = fillMissingPlan(currentPlanningDraft(), proposal);
    applyPlanningDraft(next);
    state.planningRecommendations = safeRecommendations(proposal.referenceRecommendations);
    setDirty();
    status.textContent = "Proposal applied to missing fields. Review it and Save when ready.";
  } catch (error) {
    if (!cancelled && error.name !== "AbortError" && providerContextCurrent(contextEpoch)) {
      status.textContent = `Inference failed: ${error.message}`;
      toast(`Inference failed: ${error.message}`);
    }
  } finally {
    providerActivity.finish(activityToken);
    if (!cancelled) { state.inferRunning = false; renderContinuityUi(); }
  }
}

function renderPlanRevisionScopes() {
  const fieldset = $("#plan-revision-scopes");
  if (!fieldset || fieldset.querySelector("input")) return;
  for (const [value, label] of PLAN_SCOPES) {
    const wrapper = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox"; checkbox.value = value; checkbox.checked = true;
    wrapper.append(checkbox, document.createTextNode(` ${label}`)); fieldset.append(wrapper);
  }
}

function openPlanRevision() {
  if ($("#revise-plan").disabled) return;
  renderPlanRevisionScopes();
  $("#plan-revision-instruction").value = "";
  $("#plan-revision-status").textContent = "Choose fields, enter an instruction, and generate one scoped proposal.";
  $("#plan-revision-panel").classList.remove("hidden");
  $("#plan-revision-instruction").focus();
}

function closePlanRevision() {
  $("#plan-revision-panel").classList.add("hidden");
}

async function revisePlan() {
  const instruction = $("#plan-revision-instruction").value;
  const scopes = $$("#plan-revision-scopes input:checked").map(input => input.value);
  const status = $("#plan-revision-status");
  const id = "plan-revision", label = "Generating Plan Revision";
  if (providerActivity.active()) { status.textContent = providerActivity.conflict(id, label); return; }
  if (!instruction.trim()) { status.textContent = "Enter a revision request first."; return; }
  if (!scopes.length) { status.textContent = "Select at least one editable field."; return; }
  const controller = new AbortController(); let cancelled = false, activityToken;
  activityToken = providerAttempt(id, label, message => status.textContent = message, () => {
    cancelled = true; controller.abort(); state.planRevisionRunning = false;
    status.textContent = "Plan revision cancelled."; providerActivity.finish(activityToken);
  });
  if (!activityToken) return;
  const contextEpoch = providerContextEpoch, projectId = state.currentProjectId, generationId = state.activeGenerationId;
  status.textContent = "Generating a scoped planning proposal…";
  state.planRevisionRunning = true;
  try {
    const proposal = await api.revisePlan(projectId, generationId, {
      ...planningRequestBody(), instruction, scopes,
    }, controller.signal);
    if (cancelled || !providerContextCurrent(contextEpoch)) return;
    applyPlanningDraft(applyScopedPlan(currentPlanningDraft(), proposal, scopes));
    state.planningRecommendations = mergeRecommendations(state.planningRecommendations, proposal.referenceRecommendations);
    setDirty();
    status.textContent = "Selected fields updated in the draft. Review it and Save when ready.";
  } catch (error) {
    if (!cancelled && error.name !== "AbortError" && providerContextCurrent(contextEpoch)) {
      status.textContent = `Plan revision failed: ${error.message}`;
      toast(`Plan revision failed: ${error.message}`);
    }
  } finally {
    providerActivity.finish(activityToken);
    if (!cancelled) { state.planRevisionRunning = false; renderContinuityUi(); }
  }
}

async function switchGeneration(generationId) {
  if (state.activeProjectType !== "filesystem" || !state.currentProjectId) return false;
  if (generationId === state.activeGenerationId) return true;
  if (!storyPlanner.confirmContextChange({ dirty: state.dirty, message: "Switch Generation?", allowPendingSelection: true })) {
    renderSequenceControls();
    return false;
  }
  const projectId = state.currentProjectId, originatingGenerationId = state.activeGenerationId;
  const ticket = navigationAuthority.begin(projectId, originatingGenerationId);
  invalidateProviderContext();
  try {
    let project;
    try {
      project = await api.activateGeneration(projectId, generationId, state.projectFingerprint);
    } catch (error) {
      if (error.status !== 409 || !navigationAuthority.ownsContext(ticket, state.currentProjectId, originatingGenerationId)) throw error;
      const latest = await api.loadProject(projectId);
      if (!navigationAuthority.ownsContext(ticket, state.currentProjectId, originatingGenerationId)) return false;
      if (!latest.sequence?.generations?.some(item => item.generationId === generationId)) throw error;
      project = await api.activateGeneration(projectId, generationId, latest.projectFingerprint);
    }
    if (!navigationAuthority.ownsContext(ticket, state.currentProjectId, originatingGenerationId)) return false;
    if (!storyPlanner.hasPendingRequest) storyPlanner.clearDraft();
    adoptProjectResponse(project);
    return state.activeGenerationId === generationId;
  } catch (error) {
    if (!navigationAuthority.ownsContext(ticket, state.currentProjectId, originatingGenerationId)) return false;
    renderSequenceControls();
    toast(`Could not switch Generation: ${error.message}`);
    return false;
  }
}

function structuralSequenceReady() {
  if (!state.currentProjectId || state.activeProjectType !== "filesystem") {
    toast("Save the Project to edit its Sequence.");
    return false;
  }
  if (state.dirty) {
    toast("Save or discard the current Generation changes before editing the Sequence.");
    return false;
  }
  return true;
}

async function runSequenceOperation(operation, successMessage) {
  if (!structuralSequenceReady()) return;
  if (!storyPlanner.confirmContextChange({ message: "Change the canonical Sequence?" })) return;
  const projectId = state.currentProjectId, generationId = state.activeGenerationId;
  const ticket = navigationAuthority.begin(projectId, generationId);
  try {
    const project = await operation();
    if (!navigationAuthority.ownsContext(ticket, state.currentProjectId, generationId)) return;
    storyPlanner.clearDraft();
    adoptProjectResponse(project);
    toast(successMessage);
  } catch (error) { if (navigationAuthority.ownsContext(ticket, state.currentProjectId, generationId)) toast(`Sequence operation failed: ${error.message}`); }
}

function addGeneration() {
  runSequenceOperation(() => api.createGeneration(state.currentProjectId), "Generation added");
}

function insertGeneration(offset) {
  if (!structuralSequenceReady()) return;
  const activeIndex = state.sequenceGenerations.findIndex(item => item.generationId === state.activeGenerationId);
  runSequenceOperation(
    () => api.createGeneration(state.currentProjectId, activeIndex + offset),
    offset === 0 ? "Generation inserted before" : "Generation inserted after",
  );
}

function duplicateGeneration() {
  runSequenceOperation(
    () => api.duplicateGeneration(state.currentProjectId, state.activeGenerationId),
    "Generation duplicated",
  );
}

function moveGeneration(direction) {
  runSequenceOperation(
    () => api.reorderGenerations(state.currentProjectId, movedGenerationIds({
      activeGenerationId: state.activeGenerationId,
      generations: state.sequenceGenerations,
    }, direction)),
    "Generation moved",
  );
}

function deleteGeneration() {
  if (!structuralSequenceReady()) return;
  const ordinal = generationLabel({ activeGenerationId: state.activeGenerationId, generations: state.sequenceGenerations }, state.activeGenerationId);
  if (!confirm(`Delete ${ordinal}?`)) return;
  runSequenceOperation(
    () => api.deleteGeneration(state.currentProjectId, state.activeGenerationId),
    "Generation deleted",
  );
}

async function renameSequenceSubject(subject, name) {
  if (!structuralSequenceReady()) return;
  if (!name.trim()) { toast("Enter a name. Saved names cannot be cleared."); return; }
  if (!storyPlanner.confirmContextChange({ message: "Rename this Subject and invalidate the Story Planner snapshot?" })) return;
  const context = { projectId: state.currentProjectId, generationId: state.activeGenerationId };
  const ticket = navigationAuthority.begin(context.projectId, context.generationId);
  try {
    const project = await api.nameSequenceSubject(context.projectId, subject.id, name);
    if (!navigationAuthority.ownsContext(ticket, state.currentProjectId, context.generationId)) return;
    navigationAuthority.invalidate();
    if (!adoptNarrativeResponse(state, project, context)) return;
    storyPlanner.clearDraft();
    if (state.subjects.some(item => item.id === subject.id)) markRevisionStale("The Subject narrative identity changed.");
    renderAssets();
    await workflowPreparation?.refreshSelection();
    toast("Narrative name saved.");
  } catch (error) { toast(`Sequence operation failed: ${error.message}`); }
}

function deleteSequenceSubject(subject) {
  if (!structuralSequenceReady()) return;
  if (!confirm(subjectDeleteConfirmation(subject))) return;
  runSequenceOperation(
    () => api.deleteSequenceSubject(state.currentProjectId, subject.id),
    `${subjectDisplayLabel(subject)} deleted from Sequence`,
  );
}

function projectSelectionValue() {
  if (state.activeProjectType === "filesystem") return state.currentProjectId || "";
  if (state.activeProjectType === "legacy") return `legacy:${state.currentLegacyProjectId}`;
  return "";
}

function renderProjectOptions(filesystemProjects, legacyProjects) {
  const select = $("#project-select");
  const filesystem = document.createElement("optgroup"); filesystem.label = "Projects";
  filesystemProjects.forEach(project => filesystem.append(new Option(project.name, project.id)));
  const legacy = document.createElement("optgroup"); legacy.label = "Legacy browser Projects";
  legacyProjects.forEach(project => legacy.append(new Option(`${project.name} (Legacy)`, `legacy:${project.id}`)));
  select.replaceChildren(new Option("Unsaved workspace", ""), filesystem, legacy);
  select.value = projectSelectionValue();
  $("#project-migrate").disabled = state.activeProjectType !== "legacy";
  $("#project-delete").disabled = state.activeProjectType === "none";
  $("#project-delete").title = state.activeProjectType === "filesystem" ? "Delete filesystem Project permanently" : "Delete legacy browser Project";
}

async function refreshProjects() {
  const result = await loadProjectSources({
    listFilesystem: async () => api.listProjects(), listLegacy: listLegacyProjects,
    cachedFilesystem: state.filesystemProjects, cachedLegacy: state.legacyProjects,
  });
  if (result.filesystemOk) state.filesystemProjects = result.filesystemProjects;
  if (result.legacyOk) state.legacyProjects = result.legacyProjects;
  renderProjectOptions(result.filesystemProjects, result.legacyProjects);
  if (result.filesystemError) toast(`Could not load filesystem Projects: ${result.filesystemError.message}`);
  if (result.legacyError) toast(`Could not discover legacy Projects: ${result.legacyError.message}`);
  return result;
}

async function clearGeneratedState(clearSavedPrompts) {
  if (!structuralSequenceReady() || state.projectSaving) return false;
  if (!storyPlanner.confirmContextChange({ message: "Clear generated state and discard the Story Planner proposal?" })) return false;
  invalidateProviderContext();
  renderedTakes.reset(); workflowPreparation.reset();
  sequenceRenderAction?.reset();
  const savedInert = [...document.querySelectorAll(".left-rail, .project-bar, .right-stage")].map(element => [element, element.inert]);
  state.projectSaving = true;
  for (const [element] of savedInert) element.inert = true;
  try {
    const project = await api.clearGeneratedState(state.currentProjectId, state.projectFingerprint, clearSavedPrompts);
    storyPlanner.resetProjectContext();
    adoptProjectResponse(project, { generatedReset: true });
    try { await refreshProjects(); } catch (error) { toast(error.message); }
    toast("Generated state cleared. Busy media files may remain as unreferenced files; external ComfyUI outputs are unchanged.");
    return true;
  } catch (error) {
    toast(`Could not clear generated state: ${error.message}`);
    void renderedTakes.refresh(); void workflowPreparation.refresh();
    return false;
  } finally {
    state.projectSaving = false;
    for (const [element, inert] of savedInert) element.inert = inert;
  }
}

async function saveProject(asNew = false, renameInPlace = false, copyOptions = {}) {
  if (referenceMutation) { try { await referenceMutation; } catch { return { saved: false, created: false }; } }
  if (state.projectSaving) return { saved: false, created: false };
  if (state.activeProjectType === "legacy" && !asNew && !renameInPlace) {
    toast("Import this legacy Project before saving it as a filesystem Project.");
    return { saved: false, created: false };
  }
  const workspace = persistenceWorkspace();
  if (asNew && copyOptions.name) workspace.projectName = copyOptions.name;
  const continuity = captureContinuity();
  const planning = capturePlanning();
  const characterProfiles = characterProfileUpdates();
  const projects = state.filesystemProjects;
  const decision = resolveProjectSave({
    currentProjectId: state.activeProjectType === "filesystem" ? state.currentProjectId : null,
    currentProjectSavedName: state.currentProjectSavedName,
    currentProjectName: workspace.projectName,
    projects,
    asNew,
    renameInPlace,
  });
  if (decision.collision) {
    toast("A different saved project already uses that name. Choose another name.");
    return { saved: false, created: false };
  }
  const saveRoute = resolveProjectSaveRoute({
    activeProjectType: state.activeProjectType,
    currentProjectId: state.currentProjectId,
    create: decision.create,
    asNew,
  });
  const create = saveRoute !== "save";
  if (!storyPlanner.confirmContextChange({ message: "Save the Project and invalidate its Story Planner snapshot?" })) {
    return { saved: false, created: false };
  }
  invalidateProviderContext();
  workspace.projectId = saveRoute === "save" ? state.currentProjectId : null;
  // Keep the selected editor stable until its Save response has been adopted.
  const savedInert = [...document.querySelectorAll(".left-rail, .project-bar, .right-stage")].map(element => [element, element.inert]);
  state.projectSaving = true;
  for (const [element] of savedInert) element.inert = true;
  try {
    if (saveRoute === "fork") {
      const response = await api.saveProjectAs(state.currentProjectId, workspace.projectName, workspace, continuity, planning,
        storyPlanner.storyIdea, storyPlanner.memoryEdited ? storyPlanner.storyMemoryForSave : undefined,
        { storyMemoryGenerationId: state.activeGenerationId, expectedProjectFingerprint: state.projectFingerprint },
      storyPlanner.storyCreativeDirection, characterProfiles, state.dialogueLanguage, storyPlanner.storyPlanningStyle,
      { copyMode: copyOptions.copyMode || "full", keepSavedPrompts: copyOptions.keepSavedPrompts === true });
      storyPlanner.resetProjectContext();
      adoptProjectResponse(response, { generatedReset: copyOptions.copyMode === "fresh" });
      try { await refreshProjects(); } catch (error) { toast(error.message); }
      toast("Project saved as a new filesystem Project");
      return { saved: true, created: true };
    }
    const hasImportedSequence = Array.isArray(state.importedSequenceSubjects);
    const saved = await persistFilesystemProject({
      projectId: create ? null : state.currentProjectId,
      workspace,
      continuity,
      planning,
      storyIdea: storyPlanner.storyIdea,
      storyCreativeDirection: storyPlanner.storyCreativeDirection,
      storyPlanningStyle: storyPlanner.storyPlanningStyle,
      storyMemory: create || storyPlanner.memoryEdited ? storyPlanner.storyMemoryForSave : undefined,
      memoryAuthority: { storyMemoryGenerationId: state.activeGenerationId, expectedProjectFingerprint: state.projectFingerprint },
      sequenceSubjects: create && hasImportedSequence ? state.importedSequenceSubjects : undefined,
      sequenceNextSubjectNumber: create && hasImportedSequence ? state.importedSequenceNextSubjectNumber || undefined : undefined,
      characterProfiles,
      dialogueLanguage: state.dialogueLanguage,
      createProject: api.createProject,
      saveProject: api.saveProject,
    });
    if (create) storyPlanner.resetProjectContext();
    if (create && copyOptions.copyMode === "fresh") {
      clearImportedSequenceMetadata(state);
      adoptProjectResponse(saved.response, { generatedReset: true });
      try { await refreshProjects(); } catch (error) { toast(error.message); }
      toast("Fresh filesystem Project saved");
      return { saved: true, created: true };
    }
    state.projectSequence = saved.response.sequence;
    state.savedScenePlan = saved.response.planning?.scenePlan || "";
    await adoptSavedMedia(state.assets, saved.response.workspace);
    storyPlanner.restoreStoryIdea(saved.response.storyIdea);
    storyPlanner.restoreStoryCreativeDirection(saved.response.storyCreativeDirection);
    storyPlanner.restoreStoryPlanningStyle(saved.response.storyPlanningStyle);
    state.projectFingerprint = saved.response.projectFingerprint;
    state.projectEditFingerprint = saved.response.projectEditFingerprint;
    state.projectUpdatedAt = saved.response.updatedAt;
    storyPlanner.restoreStoryMemory(saved.response.storyMemoryAfter);
    rememberProjectLocation(saved.projectId);
    state.currentProjectId = saved.projectId;
    state.currentProjectSavedName = saved.name;
    state.activeProjectType = "filesystem";
    clearImportedSequenceMetadata(state);
    state.currentLegacyProjectId = null;
    $("#project-name").value = saved.name;
    if (saved.sequence) {
      const sequence = sequenceFromProject(saved.response);
      state.sequenceGenerations = sequence.generations;
      state.activeGenerationId = sequence.activeGenerationId;
    } else {
      setEphemeralSequence();
    }
    renderAssets();
    setDirty(false);
    savedPrompts?.ensureLoaded();
    try { await refreshProjects(); } catch (error) { toast(error.message); }
    toast(create ? "Filesystem Project saved" : "Filesystem Project updated");
    return { saved: true, created: create };
  } catch (error) {
    toast(`Project save failed: ${error.message}`);
    return { saved: false, created: false };
  } finally {
    state.projectSaving = false;
    for (const [element, inert] of savedInert) element.inert = inert;
  }
}

function applyFreshUnsavedWorkspace() {
  navigationAuthority.invalidate();
  invalidateProviderContext();
  clearImportedSequenceMetadata(state);
  rememberProjectLocation(null);
  storyPlanner?.resetProjectContext();
  restoreWorkspace(freshUnsavedWorkspace());
  resetUnsavedProjectState(state);
  setEphemeralSequence();
  setDirty(true);
  renderProjectOptions(state.filesystemProjects, state.legacyProjects);
}

async function newProject() {
  if (!storyPlanner.confirmContextChange({ dirty: state.dirty, message: "Create a new Project?" })) return;
  navigationAuthority.invalidate();
  applyFreshUnsavedWorkspace();
}

async function loadSelectedProject() {
  const selection = $("#project-select").value;
  if (!selection) return;
  if (!storyPlanner.confirmContextChange({ dirty: state.dirty, message: "Open this Project?" })) { $("#project-select").value = projectSelectionValue(); return; }
  const ticket = navigationAuthority.begin(state.currentProjectId, state.activeGenerationId);
  if (selection.startsWith("legacy:")) {
    const legacyId = selection.slice("legacy:".length);
    const result = await openLegacyProjectSafely({ id: legacyId, currentWorkspace: captureWorkspace(), loadProject: getLegacyProject });
    if (!navigationAuthority.current(ticket)) return;
    if (!result.ok) {
      $("#project-select").value = projectSelectionValue();
      toast(`Could not load legacy Project: ${result.error.message}`);
      return;
    }
    const project = result.project;
    invalidateProviderContext();
    storyPlanner.resetProjectContext();
    restoreWorkspace({ ...project.workspace, projectId: null, projectName: project.name || project.workspace.projectName });
    state.currentProjectId = null; state.currentProjectSavedName = null;
    state.currentLegacyProjectId = legacyId; state.activeProjectType = "legacy";
    rememberProjectLocation(null);
    setEphemeralSequence();
    setDirty(false); renderProjectOptions(state.filesystemProjects, state.legacyProjects);
    return;
  }
  const result = await openFilesystemProjectSafely({
    id: selection,
    currentWorkspace: captureWorkspace(),
    loadProject: api.loadProject,
  });
  if (!navigationAuthority.current(ticket)) return;
  if (!result.ok) { toast(`Could not load Project: ${result.error.message}`); $("#project-select").value = projectSelectionValue(); return; }
  const project = result.project;
  storyPlanner.resetProjectContext();
  adoptProjectResponse(project);
}

async function removeCurrentProject() {
  if (state.activeProjectType === "filesystem") {
    const projectId = state.currentProjectId;
    if (!projectId || !confirm(`Delete “${$("#project-name").value}” permanently?`)) return;
    if (!storyPlanner.confirmContextChange({ message: "Delete this Project?" })) return;
    const generationId = state.activeGenerationId;
    const ticket = navigationAuthority.begin(projectId, generationId);
    let reset = false;
    try {
      await deleteFilesystemProjectAndReset({
        id: projectId,
        deleteProject: api.deleteProject,
        filesystemProjects: state.filesystemProjects,
        reset: () => {
          if (navigationAuthority.ownsContext(ticket, state.currentProjectId, generationId)) { reset = true; applyFreshUnsavedWorkspace(); }
        },
      });
      if (reset) toast("Filesystem Project deleted");
    } catch (error) { if (navigationAuthority.ownsContext(ticket, state.currentProjectId, generationId)) toast(`Could not delete Project: ${error.message}`); }
    return;
  }
  if (state.activeProjectType !== "legacy" || !confirm(`Delete legacy Project “${$("#project-name").value}”?`)) return;
  const legacyId = state.currentLegacyProjectId;
  const ticket = navigationAuthority.begin(state.currentProjectId, state.activeGenerationId);
  try {
    await deleteLegacyProjectAndReset({
      id: legacyId,
      deleteProject: deleteLegacyProject,
      legacyProjects: state.legacyProjects,
      reset: () => { if (navigationAuthority.current(ticket) && state.currentLegacyProjectId === legacyId) applyFreshUnsavedWorkspace(); },
    });
    if (navigationAuthority.current(ticket) && state.currentLegacyProjectId === legacyId) toast("Legacy browser Project deleted");
  } catch (error) { if (navigationAuthority.current(ticket) && state.currentLegacyProjectId === legacyId) toast(`Could not delete legacy Project: ${error.message}`); }
}

async function renameCurrentProject() {
  if (!$("#project-name").value.trim()) { toast("Enter a project name first"); return; }
  if (state.activeProjectType === "legacy") { toast("Import this legacy Project before renaming it."); return; }
  const result = await saveProject(false, true);
  if (result.saved) toast("Project renamed");
}

async function migrateCurrentLegacyProject() {
  if (state.activeProjectType !== "legacy") return;
  const legacyId = state.currentLegacyProjectId;
  const ticket = navigationAuthority.begin(state.currentProjectId, state.activeGenerationId);
  try {
    const legacy = await getLegacyProject(legacyId);
    if (!navigationAuthority.current(ticket) || state.currentLegacyProjectId !== legacyId) return;
    if (!legacy) throw new Error("Legacy Project was not found in this browser.");
    const migration = await migrateLegacyProject(legacy, api.createProject);
    if (!navigationAuthority.current(ticket) || state.currentLegacyProjectId !== legacyId) return;
    navigationAuthority.invalidate();
    invalidateProviderContext();
    storyPlanner.resetProjectContext();
    state.projectFingerprint = migration.created.projectFingerprint;
    state.projectEditFingerprint = migration.created.projectEditFingerprint;
    state.projectUpdatedAt = migration.created.updatedAt;
    restoreWorkspace(migration.created.workspace, migration.created.sequence);
    rememberProjectLocation(migration.created.id);
    state.currentProjectId = migration.created.id;
    state.currentProjectSavedName = migration.created.name;
    state.currentLegacyProjectId = null; state.activeProjectType = "filesystem";
    const sequence = sequenceFromProject(migration.created);
    state.sequenceGenerations = sequence.generations;
    state.activeGenerationId = sequence.activeGenerationId;
    renderAssets();
    setDirty(false);
    savedPrompts?.ensureLoaded();
    try { await refreshProjects(); } catch (error) { toast(error.message); }
    toast("Legacy Project imported as a filesystem Project");
  } catch (error) { if (navigationAuthority.current(ticket) && state.currentLegacyProjectId === legacyId) toast(`Legacy Project import failed: ${error.message}`); }
}

function download(content, filename, type) {
  const blob = new Blob([content], { type }); const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportProject() {
  const workspace = metadataOnlyWorkspace(persistenceWorkspace());
  toast("Workspace JSON includes Audio/Video metadata only. Use Save As or copy the Project folder to preserve files.");
  const envelope = { format: "MiniConstruct Project", schemaVersion: 1, exportedAt: new Date().toISOString(),
    storyIdea: storyPlanner.storyIdea, storyCreativeDirection: storyPlanner.storyCreativeDirection,
    storyPlanningStyle: storyPlanner.storyPlanningStyle,
    sequenceSubjects: structuredClone(state.projectSequence?.subjects || []),
    sequenceNextSubjectNumber: state.projectSequence?.nextSubjectNumber ?? undefined, workspace };
  download(JSON.stringify(envelope, null, 2), `${safeFilename(workspace.projectName)}.miniconstruct-project.json`, "application/json");
}

async function importProject(file) {
  try {
    const payload = JSON.parse(await file.text());
    if (payload.workspace?.llm || payload.apiKey) throw new Error("Project contains unexpected connection secrets.");
    if (payload.workspace) payload.workspace = metadataOnlyWorkspace(payload.workspace);
    const validated = await api.validateProject(payload);
    if (!storyPlanner.confirmContextChange({ dirty: state.dirty, message: "Import this Project?" })) return;
    invalidateProviderContext();
    storyPlanner.resetProjectContext();
    storyPlanner.restoreStoryIdea(validated.storyIdea);
    storyPlanner.restoreStoryCreativeDirection(validated.storyCreativeDirection);
    storyPlanner.restoreStoryPlanningStyle(validated.storyPlanningStyle);
    const importedSequence = importedSequenceMetadata(payload, validated);
    state.importedSequenceSubjects = importedSequence.sequenceSubjects;
    state.importedSequenceNextSubjectNumber = importedSequence.sequenceNextSubjectNumber;
    const workspace = prepareImportedWorkspace(validated.workspace);
    workspace.projectName = workspace.projectName || file.name.replace(/\.miniconstruct.*$/i, "");
    restoreWorkspace(workspace); resetUnsavedProjectState(state); rememberProjectLocation(null); setEphemeralSequence(); setDirty(true);
    const saved = await saveProject(false);
    toast(saved.saved ? "Project imported" : "Project imported but not saved; choose a different name.");
  } catch (error) { toast(`Import failed: ${error.message}`); }
}

function updateCharCount() { $("#char-count").textContent = `${$("#creative-request").value.length.toLocaleString()} chars`; }

function hasFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes("Files");
}

function setupAssetDropTarget() {
  const panel = $("#references-panel");
  const hint = $("#asset-drop-hint");
  let activeGroup = null;
  const clearDrop = () => {
    panel.classList.remove("drop-active");
    activeGroup?.classList.remove("drop-target"); activeGroup = null;
    hint.textContent = state.mode === "Ref2VA" ? "Images become General Visual References; video/audio stay shared" : "Drop image references here";
  };
  state.clearAssetDrop = clearDrop;
  const subjectGroupAt = target => state.mode === "Ref2VA" ? target?.closest?.(".reference-subject-group[data-subject-id]") || null : null;
  const updateDropTarget = target => {
    const group = subjectGroupAt(target);
    if (group !== activeGroup) {
      activeGroup?.classList.remove("drop-target");
      activeGroup = group;
      activeGroup?.classList.add("drop-target");
    }
    const subjectNumber = group && state.subjects.find(subject => subject.id === group.dataset.subjectId)?.number;
    hint.textContent = subjectNumber
      ? `Drop images to attach to existing Subject ${subjectNumber}; video/audio stay shared`
      : state.mode === "Ref2VA" ? "Images become General Visual References; video/audio stay shared" : "Drop image references here";
  };
  panel.addEventListener("dragenter", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    panel.classList.add("drop-active");
    updateDropTarget(event.target);
  });
  panel.addEventListener("dragover", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
    updateDropTarget(event.target);
  });
  panel.addEventListener("dragleave", event => {
    if (!hasFiles(event)) return;
    if (event.relatedTarget && panel.contains(event.relatedTarget)) updateDropTarget(event.relatedTarget);
    else clearDrop();
  });
  panel.addEventListener("drop", event => {
    if (!hasFiles(event)) return;
    const targetSubjectGroup = subjectGroupAt(event.target);
    const dropSubjectId = targetSubjectGroup?.dataset.subjectId || "";
    event.preventDefault(); event.stopPropagation(); clearDrop();
    ingestAssetFiles(event.dataTransfer.files, null, null, null, dropSubjectId).catch(error => toast(error.message));
  });
  document.addEventListener("dragover", event => { if (hasFiles(event)) event.preventDefault(); });
  document.addEventListener("drop", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (!panel.contains(event.target)) clearDrop();
  });
  document.addEventListener("dragend", clearDrop);
  document.addEventListener("visibilitychange", () => { if (document.hidden) clearDrop(); });
}

function bindEvents() {
  $$("[data-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
  $("#asset-file-input").addEventListener("change", event => ingestAssetFiles(event.target.files, state.pendingAssetKind, state.reattachId, state.pendingAssetSubjectId).catch(error => toast(error.message)));
  $("#discover-models").addEventListener("click", () => runConnection("models"));
  $("#eject-model").addEventListener("click", ejectSelectedModel);
  $("#test-connection").addEventListener("click", () => runConnection("test"));
  $("#discover-local-endpoints").addEventListener("click", discoverLocalEndpoints);
  $("#open-settings").addEventListener("click", () => { renderCommittedSettings(); $("#settings-dialog").showModal(); });
  $$('[data-close-settings]').forEach(button => button.addEventListener("click", () => $("#settings-dialog").close()));
  $("#save-settings").addEventListener("click", () => {
    try { commitSettingsFromUi(); $("#connection-message").textContent = "Settings saved."; $("#settings-dialog").close(); }
    catch (error) { $("#connection-message").textContent = error.message; }
  });
  $("#settings-dialog").addEventListener("close", () => { renderCommittedSettings(); renderedTakes?.sync(); });
  $("#api-key").addEventListener("input", () => { apiKeyEdited = true; apiKeyClearRequested = false; });
  $("#clear-api-key").addEventListener("click", () => {
    $("#api-key").value = ""; apiKeyClearRequested = true; apiKeyEdited = false;
    $("#connection-message").textContent = "API key will be cleared when you save Settings.";
  });
  $("#seed-mode").addEventListener("change", renderSeedSettings);
  $("#model-selector").addEventListener("change", event => {
    try { selectPooledModel(event.target.value); }
    catch (error) { $("#connection-message").textContent = error.message; }
  });
  $("#model-id").addEventListener("input", () => { setActiveModelStatus(); renderPooledModels(); });
  $("#endpoint-display-name").addEventListener("input", markManualEndpoint);
  $("#base-url").addEventListener("input", markManualEndpoint);
  $("#creative-controls").addEventListener("click", event => {
    const button = event.target.closest("[data-creative-field]");
    if (!button) return;
    const { creativeField: field, creativeValue: value } = button.dataset;
    if (field === "music.mode") state.creativeControls.music.mode = value;
    else if (field === "subjectIdentityFidelity.level") state.creativeControls.subjectIdentityFidelity.level = value;
    else if (field.startsWith("camera.")) state.creativeControls.camera[field.slice("camera.".length)] = value;
    renderCreativeControlsUi(); setDirty();
  });
  $("#creative-interpretation-controls").addEventListener("click", event => {
    const button = event.target.closest("[data-creative-interpretation]");
    if (!button) return;
    state.creativeInterpretation = normalizeCreativeInterpretation(button.dataset.creativeInterpretation);
    renderCreativeInterpretationUi(); setDirty();
  });
  $("#h3-prompt-detail-controls").addEventListener("click", event => {
    const button = event.target.closest("[data-prompt-detail]");
    if (!button) return;
    state.creativeControls = selectH3PromptDetail(state.creativeControls, button.dataset.promptDetail);
    renderH3PromptDetailUi(); setDirty();
  });
  $("#creative-controls").addEventListener("input", event => {
    const field = event.target.dataset.toneField;
    if (!field) return;
    const levels = field === "performanceStyle" ? PERFORMANCE_STYLE_LEVELS
      : field === "performanceEnergy" ? PERFORMANCE_ENERGY_LEVELS : TONE_LEVELS;
    const value = semanticFromRange(levels, event.target.value);
    state.creativeControls.tonePerformance[field] = value;
    event.target.setAttribute("aria-valuetext", semanticLabel(value));
    event.target.parentElement.querySelector("output").textContent = semanticLabel(value);
    setDirty();
  });
  $("#music-description").addEventListener("input", event => {
    state.creativeControls.music.description = event.target.value; setDirty();
  });
  $("#visual-style-preset").addEventListener("change", event => {
    state.creativeControls.visualStyle.preset = event.target.value; renderCreativeControlsUi(); setDirty();
  });
  $("#visual-style-custom").addEventListener("input", event => {
    state.creativeControls.visualStyle.custom = event.target.value; setDirty();
  });
  $("#reset-camera-controls").addEventListener("click", () => {
    state.creativeControls = resetCameraControls(state.creativeControls); renderCreativeControlsUi(); setDirty();
  });
  $("#reset-tone-performance").addEventListener("click", () => {
    state.creativeControls = resetTonePerformance(state.creativeControls); renderCreativeControlsUi(); setDirty();
  });
  $("#reset-creative-controls").addEventListener("click", () => {
    state.creativeControls = resetCreativeControls(state.creativeControls); renderCreativeControlsUi(); setDirty();
  });
  setupAssetDropTarget();
  $("#generate").addEventListener("click", () => generate("one_shot"));
  $("#generate-story").addEventListener("click", () => generate("story"));
  $("#regenerate").addEventListener("click", () => generate(state.outputs[state.outputIndex]?.authoringPath || "one_shot"));
  $("#validate-output").addEventListener("click", validateCurrent); $("#repair-output").addEventListener("click", repairCurrent);
  $("#revise-selection").addEventListener("click", openRevisionPanel);
  $("#generate-revision").addEventListener("click", generateRevision);
  $("#retry-revision").addEventListener("click", generateRevision);
  $("#stop-revision").addEventListener("click", stopRevision);
  $("#cancel-revision").addEventListener("click", cancelRevision);
  $("#apply-revision").addEventListener("click", () => applyRevision());
  $("#apply-revision-anyway").addEventListener("click", () => applyRevision(true));
  $("#show-instructions").addEventListener("click", showInstructions);
  $("#instructions-path").addEventListener("change", event => loadInstructions(event.target.value));
  $("#highlighted-view").addEventListener("click", () => setOutputView(false));
  $("#edit-raw").addEventListener("click", () => setOutputView(true));
  $$("[data-close-dialog]").forEach(button => button.addEventListener("click", () => $("#instructions-dialog").close()));
  $("#copy-instructions").addEventListener("click", () => navigator.clipboard.writeText($("#instructions-output").textContent).then(() => toast("Instructions copied")));
  $("#copy-output").addEventListener("click", () => navigator.clipboard.writeText($("#output").value).then(() => toast("Clean prompt copied")));
  $("#download-output").addEventListener("click", () => download($("#output").value, `${safeFilename($("#project-name").value)}-${state.mode}.txt`, "text/plain"));
  $("#output").addEventListener("input", () => {
    const current = state.outputs[state.outputIndex];
    if (current) {
      if (state.revision?.running) stopRevision();
      state.revision = null;
      $("#revision-panel").classList.add("hidden");
      setRevisionRunning(false);
      state.outputs[state.outputIndex] = markOutputManuallyEdited(current, $("#output").value);
      savedPrompts?.sync();
      workflowPreparation?.sync();
      state.revisionSelection = null;
      renderValidation(null);
      updateReviseSelectionButton();
    }
  });
  $("#output").addEventListener("select", captureActiveSelection);
  $("#output").addEventListener("keyup", captureActiveSelection);
  $("#output").addEventListener("mouseup", captureActiveSelection);
  $("#output-highlighted").addEventListener("keyup", captureActiveSelection);
  $("#output-highlighted").addEventListener("mouseup", captureActiveSelection);
  document.addEventListener("selectionchange", () => { if (!state.rawEdit) captureActiveSelection(); });
  $("#aspect-ratio").addEventListener("change", () => { $("#custom-ratio-wrap").classList.toggle("hidden", $("#aspect-ratio").value !== "custom"); setDirty(); });
  $("#creative-request").addEventListener("input", updateCharCount);
  $("#project-new").addEventListener("click", newProject); $("#project-save").addEventListener("click", () => saveProject(false));
  createProjectGeneratedState({ root: document,
    getContext: () => ({ saved: state.activeProjectType === "filesystem" && Boolean(state.currentProjectId),
      dirty: state.dirty, name: $("#project-name").value }),
    saveAs: options => saveProject(true, false, options), clearGeneratedState, notify: toast });
  $("#project-delete").addEventListener("click", removeCurrentProject);
  $("#project-migrate").addEventListener("click", migrateCurrentLegacyProject);
  $("#project-rename").addEventListener("click", renameCurrentProject);
  $("#project-select").addEventListener("change", loadSelectedProject); $("#project-export").addEventListener("click", exportProject);
  $("#project-import").addEventListener("change", event => { if (event.target.files[0]) importProject(event.target.files[0]); event.target.value = ""; });
  $("#generation-select").addEventListener("change", event => switchGeneration(event.target.value));
  sequenceRenderAction = createSequenceRenderAction({ button: $("#sequence-render-next"), status: $("#sequence-render-next-status"), api,
    getProjectId: () => state.activeProjectType === "filesystem" ? state.currentProjectId : null,
    openAndPrepare: async next => {
      if (!(await switchGeneration(next.generationId))) return;
      const prepared = await workflowPreparation.prepareSavedSelection(next.profileId);
      if (!prepared || prepared.generationId !== next.generationId)
        throw new Error(`${next.generationLabel} changed before Prepare completed. Inspect the execution panel and Prepare again.`);
      $("#workflow-preparation-primary").scrollIntoView({ block: "start", behavior: "smooth" });
    } });
  $("#dialogue-language").addEventListener("change", event => { state.dialogueLanguage = event.target.value; markDialogueResolutionStale(); setDirty(); });
  $("#dialogue").addEventListener("input", () => { markDialogueResolutionStale(); });
  $("#resolved-dialogue").addEventListener("input", event => {
    if (state.dialogueLanguage === "as_written") return;
    state.dialogueResolution = editedDialogueResolution({ dialogueLanguage: state.dialogueLanguage, dialogue: $("#dialogue").value,
      resolution: state.dialogueResolution, resolvedText: event.target.value });
    renderDialogueLanguageUi(); setDirty();
  });
  $("#refresh-dialogue-translation").addEventListener("click", () => refreshDialogueResolution().catch(error => { renderDialogueLanguageUi(); toast(error.message); }));
  $("#generation-add").addEventListener("click", addGeneration);
  $("#generation-before").addEventListener("click", () => insertGeneration(0));
  $("#generation-after").addEventListener("click", () => insertGeneration(1));
  $("#generation-duplicate").addEventListener("click", duplicateGeneration);
  $("#generation-earlier").addEventListener("click", () => moveGeneration("earlier"));
  $("#generation-later").addEventListener("click", () => moveGeneration("later"));
  $("#generation-delete").addEventListener("click", deleteGeneration);
  $("#sequence-context").addEventListener("input", event => { state.sequenceContext = event.target.value; setDirty(); });
  $("#incoming-state").addEventListener("input", event => { state.incomingState = event.target.value; setDirty(); });
  $("#intended-outgoing-state").addEventListener("input", event => { state.intendedOutgoingState = event.target.value; setDirty(); });
  $("#relationship-to-previous").addEventListener("change", event => { state.relationshipToPrevious = event.target.value || null; setDirty(); renderHandoffUi(); });
  $("#camera-intent").addEventListener("input", event => { state.cameraIntent = event.target.value; setDirty(); });
  $("#shots").addEventListener("input", renderStoryShotMismatch);
  $("#infer-from-previous").addEventListener("click", inferFromPrevious);
  $("#revise-plan").addEventListener("click", openPlanRevision);
  $("#cancel-plan-revision").addEventListener("click", closePlanRevision);
  $("#cancel-plan-revision-secondary").addEventListener("click", closePlanRevision);
  $("#generate-plan-revision").addEventListener("click", revisePlan);
  document.addEventListener("input", event => { if (shouldMarkWorkspaceDirty(event.target)) setDirty(); });
  document.addEventListener("keydown", event => {
    if (event.target.closest?.("#story-planner") || event.target.closest?.("#continuation-drafting")) return;
    if (event.key === "Escape" && !$("#revision-panel").classList.contains("hidden")) { cancelRevision(); return; }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); if (!state.generation && !state.revision?.running) generate("one_shot"); }
  });
  window.addEventListener("beforeunload", event => { if (state.dirty || storyPlanner?.hasDraft) { event.preventDefault(); event.returnValue = ""; } });
}

function projectMutationContext() {
  return { navigationTicket: navigationAuthority.capture(state.currentProjectId, state.activeGenerationId) };
}

async function init() {
  mountProviderActivity($("#provider-activity"), providerActivity);
  const outputMutation = (response, context) => {
    // A fingerprint is Project-wide, but its receipt belongs to the navigation
    // that issued the request. A delayed old-Generation snapshot must not undo
    // the fingerprint adopted by a newer Project/Generation load (including ABA).
    if (state.contextReplacing || state.activeProjectType !== "filesystem"
        || !navigationAuthority.current(context.navigationTicket)) return;
    if (context.projectId === state.currentProjectId && response.projectFingerprint) {
      // updatedAt is strictly ordered at the canonical write boundary. A response
      // from an older commit must not roll back a newer same-navigation receipt.
      if (response.projectUpdatedAt && state.projectUpdatedAt
          && Date.parse(response.projectUpdatedAt) < Date.parse(state.projectUpdatedAt)) return;
      if (state.projectEditFingerprint && response.projectEditFingerprint
          && state.projectEditFingerprint !== response.projectEditFingerprint
          && !context.projectEditMutation) return;
      state.projectFingerprint = response.projectFingerprint;
      if (response.projectEditFingerprint) state.projectEditFingerprint = response.projectEditFingerprint;
      if (response.projectUpdatedAt) state.projectUpdatedAt = response.projectUpdatedAt;
    }
  };
  referenceAuthority = createReferenceAuthority({ api,
    getContext: () => ({ saved: state.activeProjectType === "filesystem", projectId: state.currentProjectId,
      generationId: state.activeGenerationId, projectFingerprint: state.projectFingerprint,
      projectEditFingerprint: state.projectEditFingerprint, ...projectMutationContext() }),
    getReferences: () => ({ assets: state.assets, subjects: state.subjects }),
    onMutation: async (response, context) => {
      outputMutation(response, { ...context, projectEditMutation: true });
      await adoptSavedMedia(state.assets, { assets: Object.entries(response.payloadStates || {}).map(([id, status]) =>
        ({ id, ...status, mediaPayload: response.mediaPayloads?.[id] })) });
      if (!navigationAuthority.current(context.navigationTicket) || context.projectId !== state.currentProjectId) return;
      if (Object.values(response.payloadStates || {}).some(status => status.payloadAvailability)) renderAssets({ refreshManifest: false });
      if (state.projectSequence) {
        for (const subject of response.subjects || [])
          if (!state.projectSequence.subjects.some(existing => existing.id === subject.id)) state.projectSequence.subjects.push(subject);
        state.projectSequence.nextSubjectNumber = response.nextSubjectNumber;
      }
      if (response.changed) {
        if (state.generation || state.inferRunning || state.planRevisionRunning || state.repairRunning || state.revision?.running) invalidateProviderContext();
        state.planningRecommendations = state.planningRecommendations.filter(item => item.source !== "current_generation");
        renderPlanningRecommendations();
        const summary = state.sequenceGenerations.find(item => item.generationId === state.activeGenerationId);
        if (summary) summary.referenceAssets = state.assets.map(({ id, kind, filename, role, subjectIdentity, comparisonSubjects }) =>
          ({ id, kind, filename, role, subjectIdentity: structuredClone(subjectIdentity), comparisonSubjects: structuredClone(comparisonSubjects) }));
        for (const output of state.outputs) output.validation = null;
        markRevisionStale("Reference Assets changed. Generate a new H3 prompt.");
        await workflowPreparation?.invalidateReferences();
        storyPlanner?.invalidateReferences();
        void savedPrompts?.load();
        state.referenceManifest = null;
        await refreshReferenceManifest();
        renderOutput();
      }
    },
  });
  renderedTakes = createRenderedTakes({ root: $("#rendered-takes"), liveRoot: $("#render-live-preview"), api, providerActivity, getSettings: settings,
    onContinue: createTakeContinuation({ api, navigationAuthority,
      getContext: () => ({ projectId: state.currentProjectId, generationId: state.activeGenerationId,
        generations: state.sequenceGenerations, projectFingerprint: state.projectFingerprint,
        dirty: state.dirty, replacing: state.contextReplacing }),
      ready: () => structuralSequenceReady(),
      adopt: project => { storyPlanner.clearDraft(); adoptProjectResponse(project); },
      notify: toast,
      confirm: message => confirm(message) && storyPlanner.confirmContextChange({ message }),
    }),
    getContext: () => ({ saved: state.activeProjectType === "filesystem" && !state.contextReplacing, projectId: state.currentProjectId,
      replacing: state.contextReplacing, generations: state.sequenceGenerations,
      generationId: state.activeGenerationId, projectFingerprint: state.projectFingerprint, ...projectMutationContext() }),
    canChangeObservedState: context => !storyPlanner?.pendingAffectsObservedGeneration(context.generationId),
    onMutation: outputMutation,
    onReconciled: () => { void workflowPreparation?.refreshContinuation(); } });
  renderedTakes.sync();
  continuationDrafting = mountContinuationDrafting({ root: $("#continuation-drafting"), api,
    activity: providerActivity, navigationAuthority, getSettings: () => ({ ...settings(), seed: resolveSeed(settings()) }),
    getContext: () => ({ projectId: state.currentProjectId, generationId: state.activeGenerationId,
      projectFingerprint: state.projectFingerprint, replacing: state.contextReplacing,
      projectEditFingerprint: state.projectEditFingerprint,
      eligible: state.activeProjectType === "filesystem" && state.sequenceGenerations.findIndex(g => g.generationId === state.activeGenerationId) > 0
        && ["continue_previous_shot", "new_shot_preserve_continuity", "match_previous_ending_frame"].includes(state.relationshipToPrevious) }),
    getCreativeRequest: () => $("#creative-request").value,
    applyCreativeRequest: text => { $("#creative-request").value = text; updateCharCount(); setDirty(); },
    confirm: message => confirm(message), notify: toast });
  continuationDrafting.sync();
  workflowPreparation = createWorkflowPreparation({ root: $("#workflow-preparation-primary"),
    primaryRoot: $("#workflow-preparation-primary"), detailsRoot: $("#workflow-preparation-details"), api,
    onProjectMutation: (response, context) => outputMutation(response, { ...context, projectEditMutation: true }),
    syncReferences: syncReferenceAuthority,
    onSelectionChanged: () => renderedTakes.refresh(),
    onSubmitted: (response, context) => { outputMutation(response, context); renderedTakes.watchSubmitted(response, context); },
    onRoutingChanged: () => { void refreshReferenceManifest(); },
    getContext: () => ({ saved: state.activeProjectType === "filesystem" && !state.contextReplacing, projectId: state.currentProjectId,
      generationId: state.activeGenerationId, projectFingerprint: state.projectFingerprint,
      projectEditFingerprint: state.projectEditFingerprint, ...projectMutationContext(),
      mode: state.mode, durationSeconds: Number($("#duration").value),
      aspectRatio: $("#aspect-ratio").value === "custom" ? $("#custom-ratio").value.trim() : $("#aspect-ratio").value, dirty: state.dirty }),
    getOutput: () => currentExecutionOutput(state.outputs[state.outputIndex], state.generation, state.outputs, state.repairRunning) });
  workflowPreparation.refresh();
  const executionConfig = createExecutionConfig({ root: $("#execution-config"), api,
    onConnectionsChanged: () => workflowPreparation.refreshConnections(),
    onProfilesChanged: () => workflowPreparation.refreshProfiles() });
  $("#open-execution").addEventListener("click", () => { $("#execution-dialog").showModal(); executionConfig.refresh(); });
  $$('[data-close-execution]').forEach(button => button.addEventListener("click", () => $("#execution-dialog").close()));
  $("#execution-dialog").addEventListener("close", () => executionConfig.discardDuplicateDraft());
  $("#execution-dialog").addEventListener("keydown", event => event.stopPropagation());
  window.addEventListener("beforeunload", event => { if (executionConfig.hasDraft) { event.preventDefault(); event.returnValue = ""; } });
  storyPlanner = createStoryPlanner({
    root: $("#story-planner"), api, activity: providerActivity, generateStoryButton: $("#generate-story"),
    getContext: () => ({ saved: state.activeProjectType === "filesystem", projectId: state.currentProjectId,
      dirty: state.dirty, generations: state.sequenceGenerations, activeGenerationId: state.activeGenerationId, assets: state.assets, subjects: state.projectSequence?.subjects || [] }),
    onIdeaChange: () => setDirty(true),
    onScenePlanChange: value => { state.scenePlan = value; setDirty(true); },
    onCreativeDirectionChange: () => setDirty(true),
    onPlanningStyleChange: () => setDirty(true),
    onMemoryChange: () => setDirty(true),
    onConfirmMemory: () => saveProject(false),
    onSelectGeneration: id => switchGeneration(id),
    getSettings: () => ({ ...settings(), seed: resolveSeed(settings()) }),
    getPreferences: () => ({ timeoutSeconds: state.storyPlannerTimeoutSeconds, reasoning: state.storyPlannerReasoning,
      vision: state.storyPlannerVision }),
    onPreferencesChange: value => {
      if (Number.isFinite(value.timeoutSeconds) && value.timeoutSeconds >= 1 && value.timeoutSeconds <= 3600) state.storyPlannerTimeoutSeconds = value.timeoutSeconds;
      state.storyPlannerReasoning = value.reasoning !== false;
      state.storyPlannerVision = value.vision === true;
      persistSettings();
    },
    adoptProject: project => { adoptProjectResponse(project); refreshProjects().catch(error => toast(error.message)); },
    onApplyBusy: value => { for (const element of document.querySelectorAll(".left-rail, .project-bar, .right-stage")) element.inert = value; },
  });
  savedPrompts = createSavedPrompts({ root: document, api,
    getContext: () => ({ saved: state.activeProjectType === "filesystem", projectId: state.currentProjectId,
      generationId: state.activeGenerationId, candidateBusy: Boolean(state.generation || state.repairRunning || state.revision?.running) }),
    getOutput: () => state.outputs[state.outputIndex],
    setOutput: output => { markRevisionStale("A saved H3 prompt was loaded."); state.outputs = [output]; state.outputIndex = 0; renderOutput(); },
    notify: message => toast(message),
  });
  restoreSettings(); renderPooledModels(); bindEvents(); setMode("T2VA", false); renderCreativeInterpretationUi(); renderCreativeControlsUi(); updateCharCount(); setEphemeralSequence();
  try { await refreshProjects(); } catch (error) { toast(error.message); }
  setDirty(false);
  const projectId = projectIdFromLocation(window.location.href);
  if (projectId) {
    const ticket = navigationAuthority.begin(state.currentProjectId, state.activeGenerationId);
    try {
      const project = await api.loadProject(projectId);
      if (navigationAuthority.current(ticket)) adoptProjectResponse(project);
    } catch (error) {
      if (navigationAuthority.current(ticket)) { rememberProjectLocation(null); toast(`Could not reopen Project: ${error.message}`); }
    }
  }
}

init().catch(error => toast(error.message));
