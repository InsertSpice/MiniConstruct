import assert from "node:assert/strict";
import {
  SETTINGS_SCHEMA_VERSION, migrateStoredSettings, normalizeEndpointProfile, parseStoredSettings, serializeEndpointProfile,
} from "../../web/js/settings-persistence.js";

const stale = {
  endpoint: {
    id: "lm", displayName: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1", source: "lm_studio", apiKey: "key",
    modelManagement: { ejectMode: "auto" }, someFutureGarbage: true,
  },
  modelId: "chosen-model", temperature: 0.7, maxTokens: 8192, reasoningMode: "on", seedMode: "fixed", fixedSeed: 42,
};
const migrated = migrateStoredSettings(stale);
assert.equal(migrated.schemaVersion, SETTINGS_SCHEMA_VERSION);
assert.deepEqual(migrated.endpoint, { id: "lm", displayName: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1", source: "lm_studio", apiKey: "key" });
assert.equal(migrated.modelId, "chosen-model");
assert.equal(migrated.temperature, 0.7);
assert.equal(migrated.fixedSeed, 42);
assert.equal("modelManagement" in migrated.endpoint, false);
assert.equal("someFutureGarbage" in migrated.endpoint, false);

const oldValid = migrateStoredSettings({ endpoint: stale.endpoint, model: "legacy-model", temperature: 0.4, maxTokens: 4096 });
assert.equal(oldValid.schemaVersion, SETTINGS_SCHEMA_VERSION);
assert.equal(oldValid.modelId, "legacy-model");
assert.equal(oldValid.maxTokens, 4096);

assert.deepEqual(migrateStoredSettings(null).endpoint, normalizeEndpointProfile(null));
assert.doesNotThrow(() => migrateStoredSettings("not settings"));
assert.deepEqual(parseStoredSettings("{bad json"), migrateStoredSettings(null));
assert.equal(parseStoredSettings('{"endpoint":{"baseUrl":42}}').endpoint.baseUrl, "http://127.0.0.1:1234/v1");

const payload = serializeEndpointProfile({ ...stale.endpoint, modelManagement: {}, unknownGarbage: true }, { apiKey: null });
assert.deepEqual(payload, { id: "lm", displayName: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1", source: "lm_studio", apiKey: null });
