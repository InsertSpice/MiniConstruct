export const SETTINGS_SCHEMA_VERSION = 3;

const ENDPOINT_SOURCES = new Set(["manual", "lm_studio", "ollama", "unsloth_studio"]);
const REASONING_MODES = new Set(["off", "default", "on"]);
const SEED_MODES = new Set(["backend_default", "random", "fixed"]);
const MAX_SEED = 2147483647;

const stringOr = (value, fallback) => typeof value === "string" && value.trim() ? value : fallback;
const numberOr = (value, fallback) => typeof value === "number" && Number.isFinite(value) ? value : fallback;
const plannerTimeoutOr = value => {
  const timeout = numberOr(value, null);
  return timeout !== null && timeout >= 1 && timeout <= 3600 ? timeout : 600;
};

export function normalizeEndpointProfile(value, fallback = {}) {
  const endpoint = value && typeof value === "object" ? value : {};
  const source = ENDPOINT_SOURCES.has(endpoint.source) ? endpoint.source : (ENDPOINT_SOURCES.has(fallback.source) ? fallback.source : "manual");
  return {
    id: stringOr(endpoint.id, stringOr(fallback.id, "manual-endpoint")),
    displayName: stringOr(endpoint.displayName, stringOr(fallback.displayName, "Manual endpoint")),
    baseUrl: stringOr(endpoint.baseUrl, stringOr(fallback.baseUrl, "http://127.0.0.1:1234/v1")),
    source,
    apiKey: typeof endpoint.apiKey === "string" ? endpoint.apiKey : endpoint.apiKey === null ? null : (fallback.apiKey ?? null),
  };
}

export function serializeEndpointProfile(endpoint, overrides = {}) {
  return normalizeEndpointProfile({
    id: Object.hasOwn(overrides, "id") ? overrides.id : endpoint?.id,
    displayName: Object.hasOwn(overrides, "displayName") ? overrides.displayName : endpoint?.displayName,
    baseUrl: Object.hasOwn(overrides, "baseUrl") ? overrides.baseUrl : endpoint?.baseUrl,
    source: Object.hasOwn(overrides, "source") ? overrides.source : endpoint?.source,
    apiKey: Object.hasOwn(overrides, "apiKey") ? overrides.apiKey : endpoint?.apiKey,
  });
}

export function modelSelectionEndpoint(current, selected) {
  const sameUrl = current?.baseUrl?.trim().replace(/\/+$/, "").toLowerCase()
    === selected?.baseUrl?.trim().replace(/\/+$/, "").toLowerCase();
  return sameUrl
    ? normalizeEndpointProfile({ ...selected, displayName: current.displayName, apiKey: current.apiKey })
    : normalizeEndpointProfile(selected);
}

export function validateSettingsDraft(value) {
  const integer = (field, number, minimum, maximum) => {
    if (!Number.isInteger(number) || number < minimum || number > maximum)
      throw new Error(`${field} must be a whole number from ${minimum.toLocaleString()} to ${maximum.toLocaleString()}.`);
  };
  const endpoint = normalizeEndpointProfile(value.endpoint);
  if (!endpoint.displayName.trim() || endpoint.displayName.length > 160) throw new Error("Enter an Endpoint name of at most 160 characters.");
  if (!endpoint.baseUrl.trim()) throw new Error("Enter a Base URL.");
  if (typeof value.modelId !== "string" || value.modelId.length > 500) throw new Error("Model ID must be at most 500 characters.");
  if (!Number.isFinite(value.temperature) || value.temperature < 0 || value.temperature > 2)
    throw new Error("Temperature must be from 0 to 2.");
  integer("Max output tokens", value.maxTokens, 128, 65536);
  if (value.contextWindow !== null) integer("Backend context capacity", value.contextWindow, 1024, 10000000);
  integer("Timeout", value.timeoutSeconds, 1, 3600);
  if (!REASONING_MODES.has(value.reasoningMode)) throw new Error("Choose a valid Reasoning mode.");
  if (!SEED_MODES.has(value.seedMode)) throw new Error("Choose a valid Seed mode.");
  if (value.fixedSeed !== null) integer("Fixed seed", value.fixedSeed, 0, MAX_SEED);
  if (value.seedMode === "fixed" && value.fixedSeed === null) throw new Error("Enter a Fixed seed.");
  return { ...value, endpoint };
}

export function migrateStoredSettings(value, endpointFallback = {}) {
  const stored = value && typeof value === "object" ? value : {};
  const contextWindow = numberOr(stored.contextWindow, null);
  const legacyEndpoint = stored.endpoint || (typeof stored.baseUrl === "string" ? {
    id: "manual-endpoint", displayName: "Manual endpoint", baseUrl: stored.baseUrl, source: "manual", apiKey: stored.apiKey,
  } : null);
  return {
    schemaVersion: SETTINGS_SCHEMA_VERSION,
    endpoint: normalizeEndpointProfile(legacyEndpoint, endpointFallback),
    modelId: stringOr(stored.modelId, typeof stored.model === "string" ? stored.model : "").slice(0, 500),
    temperature: numberOr(stored.temperature, 0.4) >= 0 && numberOr(stored.temperature, 0.4) <= 2
      ? numberOr(stored.temperature, 0.4) : 0.4,
    maxTokens: Number.isInteger(stored.maxTokens) && stored.maxTokens >= 128 && stored.maxTokens <= 65536
      ? stored.maxTokens : 4096,
    contextWindow: Number.isInteger(contextWindow) && contextWindow >= 1024 && contextWindow <= 10000000 ? contextWindow : null,
    supportsVision: stored.supportsVision === true,
    reasoningMode: REASONING_MODES.has(stored.reasoningMode) ? stored.reasoningMode : "off",
    seedMode: SEED_MODES.has(stored.seedMode) && (stored.seedMode !== "fixed"
      || (Number.isInteger(stored.fixedSeed) && stored.fixedSeed >= 0 && stored.fixedSeed <= MAX_SEED))
      ? stored.seedMode : "backend_default",
    fixedSeed: Number.isInteger(stored.fixedSeed) && stored.fixedSeed >= 0 && stored.fixedSeed <= MAX_SEED
      ? stored.fixedSeed : null,
    persistKey: stored.persistKey === true,
    storyPlannerTimeoutSeconds: plannerTimeoutOr(stored.storyPlannerTimeoutSeconds),
    storyPlannerReasoning: stored.storyPlannerReasoning !== false,
    storyPlannerVision: stored.storyPlannerVision === true,
  };
}

export function parseStoredSettings(raw, endpointFallback = {}) {
  try {
    return migrateStoredSettings(JSON.parse(raw || "{}"), endpointFallback);
  } catch {
    return migrateStoredSettings(null, endpointFallback);
  }
}
