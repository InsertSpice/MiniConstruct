import { api } from "./api.js";
import {
  addHistory, clearHistory as clearHistoryDb, deleteProject, getProject,
  listHistory, listProjects, putProject,
} from "./db.js";
import { snapshotGenerationRequest } from "./streaming.js";
import { highlightPrompt } from "./highlighter.js";

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
    ["style_appearance", "Style / appearance reference"], ["continuity_state", "Continuity-state reference"],
    ["first_frame_anchor", "First-frame anchor"], ["keyframe_anchor", "Keyframe anchor"],
    ["last_frame_anchor", "Last-frame anchor"], ["storyboard_composition", "Storyboard / composition"],
    ["general_visual", "General visual reference"],
  ],
  video: [
    ["continuation_source", "Continuation source"], ["seamless_overlap_continuation", "Seamless overlap continuation"],
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
  outputs: [],
  outputIndex: 0,
  currentProjectId: null,
  dirty: true,
  pendingAssetKind: null,
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
  dragDepth: 0,
  generation: null,
  rawEdit: false,
};

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
  const indicator = $("#dirty-indicator");
  indicator.textContent = value ? "Unsaved" : "Saved";
  indicator.classList.toggle("saved", !value);
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
  return {
    ...state.endpoint,
    id: isManual ? manualEndpointId(baseUrl) : state.endpoint.id,
    displayName: $("#endpoint-display-name").value.trim() || "Manual endpoint",
    baseUrl,
    apiKey: $("#api-key").value || null,
  };
}

function settings() {
  return {
    endpoint: endpointProfile(),
    modelId: $("#model-id").value.trim(),
    temperature: Number($("#temperature").value),
    maxTokens: Number($("#max-tokens").value),
    timeoutSeconds: 120,
    supportsVision: $("#vision-support").checked,
    reasoningMode: $("#reasoning-mode").value,
  };
}

function persistSettings() {
  const value = settings();
  if (!$("#persist-key").checked) delete value.endpoint.apiKey;
  value.persistKey = $("#persist-key").checked;
  localStorage.setItem("miniconstruct-settings", JSON.stringify(value));
}

function restoreSettings() {
  try {
    const value = JSON.parse(localStorage.getItem("miniconstruct-settings") || "{}");
    const endpoint = value.endpoint || (value.baseUrl ? {
      id: "manual-endpoint", displayName: "Manual endpoint", baseUrl: value.baseUrl,
      source: "manual", apiKey: value.apiKey,
    } : null);
    if (endpoint) {
      state.endpoint = { ...state.endpoint, ...endpoint };
      $("#endpoint-display-name").value = state.endpoint.displayName || "Manual endpoint";
      $("#base-url").value = state.endpoint.baseUrl || "http://127.0.0.1:1234/v1";
    }
    if (value.modelId || value.model) $("#model-id").value = value.modelId || value.model;
    if (value.temperature !== undefined) $("#temperature").value = value.temperature;
    if (value.maxTokens) $("#max-tokens").value = value.maxTokens;
    if (["off", "default", "on"].includes(value.reasoningMode)) $("#reasoning-mode").value = value.reasoningMode;
    $("#vision-support").checked = value.supportsVision === true;
    $("#persist-key").checked = value.persistKey === true;
    if (value.persistKey && (endpoint?.apiKey || value.apiKey)) $("#api-key").value = endpoint?.apiKey || value.apiKey;
  } catch { /* ignore malformed local preference */ }
  setActiveModelStatus();
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
}

function markManualEndpoint() {
  state.endpoint = {
    ...state.endpoint,
    id: manualEndpointId($("#base-url").value),
    source: "manual",
  };
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
  state.endpoint = { ...endpoint, apiKey: endpoint.apiKey || null };
  $("#endpoint-display-name").value = state.endpoint.displayName;
  $("#base-url").value = state.endpoint.baseUrl;
  $("#api-key").value = state.endpoint.apiKey || "";
  $("#model-id").value = modelId;
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
    aspectRatio: ratio || "auto",
    variations: Number($("#variations").value),
    creativeRequest: $("#creative-request").value,
    dialogue: $("#dialogue").value,
    referenceLabels: $("#reference-labels").value,
    assets: structuredClone(state.assets),
  };
}

export function restoreWorkspace(workspace) {
  state.currentProjectId = workspace.projectId || null;
  state.assets = structuredClone(workspace.assets || []).map(asset => ({
    notes: "", options: {}, order: 0, attached: asset.kind === "image", ...asset,
    attached: asset.kind === "image" ? true : false,
  }));
  $("#project-name").value = workspace.projectName || "Untitled Project";
  $("#duration").value = workspace.durationSeconds ?? 6;
  $("#shots").value = workspace.shots ?? "";
  const commonRatios = ["auto", "16:9", "9:16", "1:1", "4:3", "3:4"];
  const ratio = workspace.aspectRatio || "auto";
  $("#aspect-ratio").value = commonRatios.includes(ratio) ? ratio : "custom";
  $("#custom-ratio").value = commonRatios.includes(ratio) ? "" : ratio;
  $("#custom-ratio-wrap").classList.toggle("hidden", $("#aspect-ratio").value !== "custom");
  $("#variations").value = workspace.variations ?? 1;
  $("#creative-request").value = workspace.creativeRequest || "";
  $("#dialogue").value = workspace.dialogue || "";
  $("#reference-labels").value = workspace.referenceLabels || "";
  setMode(workspace.mode || "T2VA", false);
  renderAssets();
  updateCharCount();
  state.outputs = [];
  renderOutput();
  setDirty(false);
}

export function buildGenerateRequest() {
  return { workspace: captureWorkspace(), llm: settings() };
}

function activeNumbering() {
  const map = new Map();
  let active = state.assets;
  if (state.mode === "T2VA") active = [];
  else if (state.mode === "I2VA") active = state.assets.filter(a => a.kind === "image" && a.role === "first_frame_anchor");
  else if (state.mode === "FL2VA") active = state.assets.filter(a => a.kind === "image" && ["first_frame_anchor", "last_frame_anchor"].includes(a.role));
  else if (state.mode === "L2VA") active = state.assets.filter(a => a.kind === "image" && a.role === "last_frame_anchor");
  for (const kind of ["image", "video", "audio"]) {
    const prefix = { image: "Picture", video: "Video", audio: "Audio" }[kind];
    let items = active.filter(item => item.kind === kind);
    if (state.mode === "FL2VA" && kind === "image") {
      items = items.sort((a, b) => ({ first_frame_anchor: 0, last_frame_anchor: 1 }[a.role] - { first_frame_anchor: 0, last_frame_anchor: 1 }[b.role]) || a.order - b.order);
    } else items = items.sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
    items
      .forEach((asset, index) => map.set(asset.id, `${prefix} ${index + 1}`));
  }
  return map;
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
    button.textContent = `+ ${kind[0].toUpperCase()}${kind.slice(1)}`;
    button.addEventListener("click", () => chooseAsset(kind));
    container.append(button);
  }
  $("#reference-helper").textContent = state.mode === "Ref2VA"
    ? "Pictures may be sent as vision inputs. Video and Audio stay metadata-only; attach the real media separately in H3 / ComfyUI."
    : "Add the exact frame image required by this mode. The browser processes it for the prompt-writing model.";
}

function visibleAssets() {
  if (state.mode === "Ref2VA") return state.assets;
  if (state.mode === "I2VA") return state.assets.filter(a => a.kind === "image" && a.role === "first_frame_anchor");
  if (state.mode === "FL2VA") return state.assets.filter(a => a.kind === "image" && ["first_frame_anchor", "last_frame_anchor"].includes(a.role));
  if (state.mode === "L2VA") return state.assets.filter(a => a.kind === "image" && a.role === "last_frame_anchor");
  return [];
}

function renderAssets() {
  const container = $("#asset-list");
  container.replaceChildren();
  const numbering = activeNumbering();
  const assets = visibleAssets().sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  if (!assets.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = state.mode === "FL2VA" ? "Add first- and last-frame images." : "No references added for this mode.";
    container.append(empty);
    return;
  }
  for (const asset of assets) container.append(assetCard(asset, numbering.get(asset.id)));
}

function assetCard(asset, label) {
  const card = document.createElement("article");
  card.className = "asset-card";
  const preview = document.createElement("div");
  preview.className = "asset-preview";
  if (asset.kind === "image") {
    const img = document.createElement("img");
    img.src = asset.image.data_url;
    img.alt = `Preview of ${asset.filename}`;
    preview.append(img);
  } else preview.textContent = asset.kind === "video" ? "▶" : "♫";

  const content = document.createElement("div");
  const title = document.createElement("div");
  title.className = "asset-title";
  const strong = document.createElement("strong"); strong.textContent = asset.filename;
  const labelEl = document.createElement("span"); labelEl.className = "asset-label"; labelEl.textContent = label;
  title.append(strong, labelEl);
  const meta = document.createElement("p"); meta.className = "asset-meta";
  const duration = asset.durationSeconds == null ? "" : ` · ${asset.durationSeconds.toFixed(3)}s`;
  meta.textContent = `${asset.mimeType}${duration}`;
  if (asset.kind !== "image" && !asset.attached) {
    const missing = document.createElement("span"); missing.className = "media-missing"; missing.textContent = " · media not attached"; meta.append(missing);
  }

  const fields = document.createElement("div"); fields.className = "asset-fields";
  const roleLabel = document.createElement("label"); roleLabel.textContent = "Role";
  const role = document.createElement("select");
  for (const [value, text] of roleCatalog[asset.kind]) {
    const option = new Option(text, value, false, value === asset.role); role.append(option);
  }
  role.disabled = state.mode !== "Ref2VA";
  role.addEventListener("change", () => { asset.role = role.value; renderAssets(); setDirty(); });
  roleLabel.append(role);
  const notesLabel = document.createElement("label"); notesLabel.textContent = "Notes";
  const notes = document.createElement("textarea"); notes.rows = 2; notes.placeholder = "Asset-specific facts or constraints"; notes.value = asset.notes || "";
  notes.addEventListener("input", () => { asset.notes = notes.value; setDirty(); });
  notesLabel.append(notes); fields.append(roleLabel, notesLabel);

  if (asset.kind === "audio" && asset.role === "music_beat_rhythm") {
    const sync = document.createElement("label"); sync.className = "check";
    const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = asset.options?.syncChoreography === true;
    checkbox.addEventListener("change", () => { asset.options.syncChoreography = checkbox.checked; setDirty(); });
    sync.append(checkbox, " Synchronize choreography to beat / rhythm"); content.append(title, meta, fields, sync);
  } else content.append(title, meta, fields);

  if (asset.kind === "video" && asset.role === "seamless_overlap_continuation") {
    const help = document.createElement("p"); help.className = "helper";
    help.textContent = "Use the complete clip as the target opening overlap; approximately 1–2 seconds is recommended, not required.";
    content.append(help);
  }
  const actions = document.createElement("div"); actions.className = "asset-actions";
  for (const [text, direction] of [["↑", -1], ["↓", 1]]) {
    const button = document.createElement("button"); button.className = "quiet"; button.textContent = text; button.title = direction < 0 ? "Move up" : "Move down";
    button.addEventListener("click", () => moveAsset(asset.id, direction)); actions.append(button);
  }
  if (asset.kind !== "image") {
    const reattach = document.createElement("button"); reattach.className = "quiet"; reattach.textContent = "Reattach";
    reattach.addEventListener("click", () => chooseAsset(asset.kind, asset.id)); actions.append(reattach);
  }
  const remove = document.createElement("button"); remove.className = "quiet danger"; remove.textContent = "Remove";
  remove.addEventListener("click", () => { state.assets = state.assets.filter(item => item.id !== asset.id); normalizeOrders(asset.kind); renderAssets(); setDirty(); });
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
  [siblings[current].order, siblings[target].order] = [siblings[target].order, siblings[current].order];
  renderAssets(); setDirty();
}

function chooseAsset(kind, reattachId = null) {
  state.pendingAssetKind = kind; state.reattachId = reattachId;
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

async function createAssetFromFile(file, kind, reattachId = null) {
  if (reattachId) {
    const asset = state.assets.find(item => item.id === reattachId);
    if (!asset) return null;
    asset.filename = file.name; asset.mimeType = file.type || `${kind}/unknown`; asset.durationSeconds = await readDuration(file, kind); asset.attached = true;
    return `${asset.filename} reattached`;
  }
  const asset = {
    id: uid(), kind, filename: file.name, mimeType: file.type || `${kind}/unknown`,
    durationSeconds: null, role: kind === "image" ? defaultImageRole() : roleCatalog[kind][0][0],
    notes: "", options: {}, order: state.assets.filter(item => item.kind === kind).length, attached: true, image: null,
  };
  if (kind === "image") asset.image = await processImage(file);
  else asset.durationSeconds = await readDuration(file, kind);
  if (kind === "image" && state.mode !== "Ref2VA") {
    state.assets.filter(item => item.kind === "image" && item.role === asset.role)
      .forEach(item => { item.role = "general_visual"; });
  }
  state.assets.push(asset);
  return asset.filename;
}

async function ingestAssetFiles(files, forcedKind = null, reattachId = null) {
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
      const result = await createAssetFromFile(file, forcedKind || classified, reattachId);
      if (result) accepted.push(result);
      if (reattachId) break;
    } catch {
      rejected.push(file.name);
    }
  }
  if (accepted.length) {
    renderAssets(); setDirty();
    toast(isReattach ? accepted[0] : accepted.length === 1 ? `${accepted[0]} added` : `${accepted.length} reference assets added`);
  }
  if (rejected.length) toast(`Unsupported or incompatible: ${rejected.join(", ")}`);
  state.pendingAssetKind = null; state.reattachId = null;
}

async function runConnection(action) {
  persistSettings();
  const message = $("#connection-message");
  message.textContent = action === "models" ? "Discovering models…" : "Testing connection…";
  try {
    const result = action === "models" ? await api.models(settings()) : await api.test(settings());
    const models = result.models || [];
    const endpoint = endpointProfile();
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
  persistSettings();
  const message = $("#connection-message");
  message.textContent = "Checking known local servers…";
  try {
    const response = await api.discoverEndpoints({ manualEndpoint: endpointProfile() });
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
  button.querySelector("span").textContent = active ? "Stop" : "Generate prompt";
  button.querySelector("kbd").classList.toggle("hidden", active);
  button.classList.toggle("stop", active);
  button.setAttribute("aria-pressed", String(active));
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
    `Request assembly: ${diagnostic.assemblyMs ?? "—"} ms`,
    `Assembled text: ${(diagnostic.assembledTextChars ?? 0).toLocaleString()} characters`,
    `Images: ${diagnostic.imageCount ?? 0}${diagnostic.imageDimensions?.length ? ` (${diagnostic.imageDimensions.map(item => `${item.width}×${item.height}`).join(", ")})` : ""}`,
    `Reasoning mode: ${diagnostic.reasoningMode || session.requestSnapshot.llm.reasoningMode}`,
    `Backend/model: ${diagnostic.backend || "—"} / ${diagnostic.model || "—"}`,
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

async function saveCompletedGenerationHistory(session) {
  if (!session.completed.size || session.historySaved) return;
  session.historySaved = true;
  const snapshot = session.requestSnapshot.workspace;
  for (const index of session.completed) {
    const variation = session.outputs[index];
    await addHistory({
      createdAt: new Date().toISOString(), mode: snapshot.mode,
      projectId: snapshot.projectId, projectName: snapshot.projectName,
      prompt: variation.prompt, validation: variation.validation,
    });
  }
  await renderHistory();
}

async function generate() {
  if (state.generation) { stopGeneration(); return; }
  persistSettings();
  const requestSnapshot = snapshotGenerationRequest(buildGenerateRequest());
  const controller = new AbortController();
  const outputs = Array.from({ length: requestSnapshot.workspace.variations }, () => ({
    prompt: "", validation: null, generationStatus: "pending",
  }));
  const session = {
    controller, requestSnapshot, stopping: false, renderQueued: false,
    completed: new Set(), streamError: null, historySaved: false, outputs,
    clickedAt: new Date().toISOString(), metrics: new Map(), diagnostics: null,
  };
  state.generation = session;
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
        if (state.generation !== session) return;
        if (event === "start") {
          renderWarnings(data.warnings || []);
          session.diagnostics = data.diagnostics || null;
          renderPerformance(session);
          setGenerationStatus("Waiting for first token…", "waiting");
        } else if (event === "variation_start") {
          session.outputs[data.variation].generationStatus = "waiting";
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
        } else if (event === "delta") {
          const output = session.outputs[data.variation];
          if (!output || typeof data.text !== "string") return;
          output.prompt += data.text;
          output.generationStatus = "generating";
          setGenerationStatus(`Generating…${requestSnapshot.workspace.variations > 1 ? ` Variation ${data.variation + 1}` : ""}`, "generating");
          updateStreamedOutput(data.variation);
        } else if (event === "complete") {
          renderPerformance(session, data.metrics);
          session.outputs[data.variation] = {
            prompt: data.prompt, validation: data.validation, generationStatus: "complete",
          };
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
    if (session.streamError) {
      const suffix = session.streamError.partial ? " — partial output preserved" : "";
      setGenerationStatus(`Error${suffix}`, "error");
      renderWarnings([session.streamError.message]);
    } else {
      setGenerationStatus("Complete", "complete");
    }
  } catch (error) {
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
    try { await saveCompletedGenerationHistory(session); }
    catch (historyError) { toast(`Prompt completed, but History could not be updated: ${historyError.message}`); }
    if (state.generation === session) {
      state.generation = null;
      setGenerationControls(false);
    }
  }
}

function renderWarnings(warnings = []) {
  const banner = $("#warning-banner");
  banner.classList.toggle("hidden", !warnings.length); banner.textContent = warnings.join(" ");
}

function renderOutput() {
  const current = state.outputs[state.outputIndex];
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
  $("#output").readOnly = generating;
  for (const id of ["copy-output", "download-output"]) $("#" + id).disabled = !current;
  for (const id of ["validate-output", "repair-output", "regenerate"]) $("#" + id).disabled = !current || generating;
  const tabs = $("#variation-tabs"); tabs.replaceChildren(); tabs.classList.toggle("hidden", state.outputs.length < 2);
  state.outputs.forEach((_, index) => {
    const button = document.createElement("button"); button.textContent = `Variation ${index + 1}`; button.classList.toggle("active", index === state.outputIndex);
    button.addEventListener("click", () => { state.outputIndex = index; renderOutput(); }); tabs.append(button);
  });
  renderValidation(current?.validation);
}

function setOutputView(raw) {
  const current = state.outputs[state.outputIndex];
  if (!current) return;
  const from = state.rawEdit ? $("#output") : $("#output-highlighted");
  const scrollTop = from.scrollTop;
  state.rawEdit = raw;
  renderOutput();
  const to = raw ? $("#output") : $("#output-highlighted");
  to.scrollTop = scrollTop;
  if (raw) to.focus();
}

function renderValidation(validation) {
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
  list.replaceChildren(...validation.findings.map(item => {
    const row = document.createElement("div"); row.className = `finding ${item.severity}`;
    const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = item.severity;
    const message = document.createElement("span"); message.textContent = item.message; row.append(badge, message); return row;
  }));
}

async function validateCurrent() {
  const current = state.outputs[state.outputIndex]; if (!current) return;
  current.prompt = $("#output").value;
  try {
    current.validation = await api.validate({
      prompt: current.prompt, mode: state.mode, durationSeconds: Number($("#duration").value),
      shots: $("#shots").value.trim() === "" ? null : Number($("#shots").value),
      assets: state.assets, dialogue: $("#dialogue").value,
    });
    renderValidation(current.validation);
  } catch (error) { toast(error.message); }
}

async function repairCurrent() {
  const current = state.outputs[state.outputIndex]; if (!current) return;
  try {
    const result = await api.repair({ ...buildGenerateRequest(), prompt: $("#output").value, findings: current.validation?.findings || [] });
    state.outputs[state.outputIndex] = result; renderOutput(); toast("One repair pass completed");
    await addHistory({ createdAt: new Date().toISOString(), mode: state.mode, projectId: state.currentProjectId, projectName: $("#project-name").value, prompt: result.prompt, validation: result.validation, repaired: true });
    await renderHistory();
  } catch (error) { toast(error.message); }
}

async function showInstructions() {
  try {
    const result = await api.assemble(buildGenerateRequest());
    $("#instructions-output").textContent = result.instructions;
    $("#instructions-dialog").showModal(); renderWarnings(result.warnings);
  } catch (error) { toast(error.message); }
}

function persistenceWorkspace() {
  const workspace = captureWorkspace();
  workspace.assets = workspace.assets.map(asset => asset.kind === "image" ? asset : { ...asset, attached: false });
  return workspace;
}

async function refreshProjects() {
  const projects = await listProjects();
  const select = $("#project-select");
  select.replaceChildren(new Option("Unsaved workspace", ""), ...projects.map(project => new Option(project.name, project.id)));
  select.value = state.currentProjectId || "";
}

async function saveProject(asNew = false) {
  const id = asNew || !state.currentProjectId ? uid() : state.currentProjectId;
  state.currentProjectId = id;
  const workspace = persistenceWorkspace(); workspace.projectId = id;
  const record = { id, name: workspace.projectName, schemaVersion: 1, updatedAt: new Date().toISOString(), workspace };
  await putProject(record); setDirty(false); await refreshProjects(); toast("Project saved locally");
}

async function newProject() {
  if (state.dirty && !confirm("Replace the unsaved workspace?")) return;
  restoreWorkspace({ schemaVersion: 1, projectName: "Untitled Project", mode: "T2VA", durationSeconds: 6, shots: null, aspectRatio: "auto", variations: 1, creativeRequest: "", dialogue: "", referenceLabels: "", assets: [] });
  state.currentProjectId = null; setDirty(true); await refreshProjects();
}

async function loadSelectedProject() {
  const id = $("#project-select").value;
  if (!id) return;
  if (state.dirty && !confirm("Replace the unsaved workspace?")) { $("#project-select").value = state.currentProjectId || ""; return; }
  const project = await getProject(id); if (project) restoreWorkspace(project.workspace);
}

async function removeCurrentProject() {
  if (!state.currentProjectId || !confirm(`Delete “${$("#project-name").value}”?`)) return;
  await deleteProject(state.currentProjectId); await newProject(); toast("Project deleted");
}

async function renameCurrentProject() {
  if (!$("#project-name").value.trim()) { toast("Enter a project name first"); return; }
  await saveProject(false);
  toast("Project renamed");
}

function download(content, filename, type) {
  const blob = new Blob([content], { type }); const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function exportProject() {
  const workspace = persistenceWorkspace();
  const envelope = { format: "MiniConstruct Project", schemaVersion: 1, exportedAt: new Date().toISOString(), workspace };
  download(JSON.stringify(envelope, null, 2), `${safeFilename(workspace.projectName)}.miniconstruct-project.json`, "application/json");
}

async function importProject(file) {
  try {
    const payload = JSON.parse(await file.text());
    if (payload.workspace?.llm || payload.apiKey) throw new Error("Project contains unexpected connection secrets.");
    const validated = await api.validateProject(payload);
    const workspace = validated.workspace;
    workspace.projectId = uid(); workspace.projectName = workspace.projectName || file.name.replace(/\.miniconstruct.*$/i, "");
    restoreWorkspace(workspace); state.currentProjectId = workspace.projectId; setDirty(true); await saveProject(false); toast("Project imported");
  } catch (error) { toast(`Import failed: ${error.message}`); }
}

async function renderHistory() {
  const entries = await listHistory(), container = $("#history-list"); container.replaceChildren();
  if (!entries.length) { const p = document.createElement("p"); p.className = "muted"; p.textContent = "No generated prompts yet."; container.append(p); return; }
  for (const entry of entries) {
    const item = document.createElement("article"); item.className = "history-item"; item.tabIndex = 0;
    const title = document.createElement("strong"); title.textContent = `${entry.mode} · ${entry.projectName || "Unsaved"}`;
    const time = document.createElement("span"); time.textContent = new Date(entry.createdAt).toLocaleString();
    const preview = document.createElement("p"); preview.textContent = entry.prompt;
    item.append(title, time, preview);
    const restore = () => { state.outputs = [{ prompt: entry.prompt, validation: entry.validation }]; state.outputIndex = 0; renderOutput(); };
    item.addEventListener("click", restore); item.addEventListener("keydown", event => { if (event.key === "Enter") restore(); }); container.append(item);
  }
}

function updateCharCount() { $("#char-count").textContent = `${$("#creative-request").value.length.toLocaleString()} chars`; }

function hasFiles(event) {
  return [...(event.dataTransfer?.types || [])].includes("Files");
}

function setupAssetDropTarget() {
  const panel = $("#references-panel");
  const clearDrop = () => { state.dragDepth = 0; panel.classList.remove("drop-active"); };
  panel.addEventListener("dragenter", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    state.dragDepth += 1;
    panel.classList.add("drop-active");
  });
  panel.addEventListener("dragover", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });
  panel.addEventListener("dragleave", event => {
    if (!hasFiles(event)) return;
    state.dragDepth -= 1;
    if (state.dragDepth <= 0) clearDrop();
  });
  panel.addEventListener("drop", event => {
    if (!hasFiles(event)) return;
    event.preventDefault(); event.stopPropagation(); clearDrop();
    ingestAssetFiles(event.dataTransfer.files).catch(error => toast(error.message));
  });
  document.addEventListener("dragover", event => { if (hasFiles(event)) event.preventDefault(); });
  document.addEventListener("drop", event => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    if (!panel.contains(event.target)) clearDrop();
  });
}

function bindEvents() {
  $$("[data-mode]").forEach(button => button.addEventListener("click", () => setMode(button.dataset.mode)));
  $("#asset-file-input").addEventListener("change", event => ingestAssetFiles(event.target.files, state.pendingAssetKind, state.reattachId).catch(error => toast(error.message)));
  $("#discover-models").addEventListener("click", () => runConnection("models"));
  $("#test-connection").addEventListener("click", () => runConnection("test"));
  $("#discover-local-endpoints").addEventListener("click", discoverLocalEndpoints);
  $("#open-settings").addEventListener("click", () => $("#settings-dialog").showModal());
  $$('[data-close-settings]').forEach(button => button.addEventListener("click", () => $("#settings-dialog").close()));
  $("#settings-dialog").addEventListener("close", persistSettings);
  $("#model-selector").addEventListener("change", event => selectPooledModel(event.target.value));
  $("#model-id").addEventListener("input", () => { setActiveModelStatus(); renderPooledModels(); });
  $("#endpoint-display-name").addEventListener("input", markManualEndpoint);
  $("#base-url").addEventListener("input", markManualEndpoint);
  setupAssetDropTarget();
  $("#generate").addEventListener("click", generate); $("#regenerate").addEventListener("click", generate);
  $("#validate-output").addEventListener("click", validateCurrent); $("#repair-output").addEventListener("click", repairCurrent);
  $("#show-instructions").addEventListener("click", showInstructions);
  $("#highlighted-view").addEventListener("click", () => setOutputView(false));
  $("#edit-raw").addEventListener("click", () => setOutputView(true));
  $$("[data-close-dialog]").forEach(button => button.addEventListener("click", () => $("#instructions-dialog").close()));
  $("#copy-instructions").addEventListener("click", () => navigator.clipboard.writeText($("#instructions-output").textContent).then(() => toast("Instructions copied")));
  $("#copy-output").addEventListener("click", () => navigator.clipboard.writeText($("#output").value).then(() => toast("Clean prompt copied")));
  $("#download-output").addEventListener("click", () => download($("#output").value, `${safeFilename($("#project-name").value)}-${state.mode}.txt`, "text/plain"));
  $("#output").addEventListener("input", () => { const current = state.outputs[state.outputIndex]; if (current) { current.prompt = $("#output").value; current.validation = null; renderValidation(null); } });
  $("#aspect-ratio").addEventListener("change", () => { $("#custom-ratio-wrap").classList.toggle("hidden", $("#aspect-ratio").value !== "custom"); setDirty(); });
  $("#creative-request").addEventListener("input", updateCharCount);
  $("#project-new").addEventListener("click", newProject); $("#project-save").addEventListener("click", () => saveProject(false));
  $("#project-save-as").addEventListener("click", () => saveProject(true)); $("#project-delete").addEventListener("click", removeCurrentProject);
  $("#project-rename").addEventListener("click", renameCurrentProject);
  $("#project-select").addEventListener("change", loadSelectedProject); $("#project-export").addEventListener("click", exportProject);
  $("#project-import").addEventListener("change", event => { if (event.target.files[0]) importProject(event.target.files[0]); event.target.value = ""; });
  $("#clear-history").addEventListener("click", async () => { if (confirm("Clear local generated-prompt history?")) { await clearHistoryDb(); renderHistory(); } });
  document.addEventListener("input", event => {
    if (event.target.closest(".left-rail")) setDirty();
  });
  document.addEventListener("keydown", event => { if ((event.ctrlKey || event.metaKey) && event.key === "Enter") { event.preventDefault(); if (!state.generation) generate(); } });
  window.addEventListener("beforeunload", event => { if (state.dirty) { event.preventDefault(); event.returnValue = ""; } });
}

async function init() {
  restoreSettings(); renderPooledModels(); bindEvents(); setMode("T2VA", false); updateCharCount(); await refreshProjects(); await renderHistory(); setDirty(false);
}

init().catch(error => toast(error.message));
