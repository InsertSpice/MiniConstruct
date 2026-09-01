export const SETTINGS_SCHEMA_VERSION = 2;

const ENDPOINT_SOURCES = new Set(["manual", "lm_studio", "ollama", "unsloth_studio"]);
const REASONING_MODES = new Set(["off", "default", "on"]);
const SEED_MODES = new Set(["backend_default", "random", "fixed"]);

const stringOr = (value, fallback) => typeof value === "string" && value.trim() ? value : fallback;
const numberOr = (value, fallback) => typeof value === "number" && Number.isFinite(value) ? value : fallback;

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

export function migrateStoredSettings(value, endpointFallback = {}) {
  const stored = value && typeof value === "object" ? value : {};
  const legacyEndpoint = stored.endpoint || (typeof stored.baseUrl === "string" ? {
    id: "manual-endpoint", displayName: "Manual endpoint", baseUrl: stored.baseUrl, source: "manual", apiKey: stored.apiKey,
  } : null);
  return {
    schemaVersion: SETTINGS_SCHEMA_VERSION,
    endpoint: normalizeEndpointProfile(legacyEndpoint, endpointFallback),
    modelId: stringOr(stored.modelId, typeof stored.model === "string" ? stored.model : ""),
    temperature: numberOr(stored.temperature, undefined),
    maxTokens: numberOr(stored.maxTokens, undefined),
    supportsVision: stored.supportsVision === true,
    reasoningMode: REASONING_MODES.has(stored.reasoningMode) ? stored.reasoningMode : "off",
    seedMode: SEED_MODES.has(stored.seedMode) ? stored.seedMode : "backend_default",
    fixedSeed: numberOr(stored.fixedSeed, null),
    persistKey: stored.persistKey === true,
  };
}

export function parseStoredSettings(raw, endpointFallback = {}) {
  try {
    return migrateStoredSettings(JSON.parse(raw || "{}"), endpointFallback);
  } catch {
    return migrateStoredSettings(null, endpointFallback);
  }
}
