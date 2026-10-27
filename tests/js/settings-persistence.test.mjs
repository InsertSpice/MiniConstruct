import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  SETTINGS_SCHEMA_VERSION, migrateStoredSettings, normalizeEndpointProfile, parseStoredSettings, serializeEndpointProfile,
  modelSelectionEndpoint, validateSettingsDraft,
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
assert.equal(oldValid.contextWindow, null);
assert.equal(migrateStoredSettings({ contextWindow: 65536 }).contextWindow, 65536);
assert.equal(migrateStoredSettings({}).storyPlannerReasoning, true);
assert.equal(migrateStoredSettings({}).storyPlannerVision, false);
assert.equal(parseStoredSettings(JSON.stringify({ storyPlannerVision: true })).storyPlannerVision, true);
assert.equal(parseStoredSettings(JSON.stringify({ storyPlannerVision: false })).storyPlannerVision, false);
assert.equal(migrateStoredSettings({}).storyPlannerTimeoutSeconds, 600);
assert.equal(migrateStoredSettings({ storyPlannerReasoning: false, storyPlannerTimeoutSeconds: 1200 }).storyPlannerReasoning, false);
assert.equal(migrateStoredSettings({ storyPlannerReasoning: false, storyPlannerTimeoutSeconds: 1200 }).storyPlannerTimeoutSeconds, 1200);
assert.equal(migrateStoredSettings({ storyPlannerTimeoutSeconds: 3601 }).storyPlannerTimeoutSeconds, 600);

assert.deepEqual(migrateStoredSettings(null).endpoint, normalizeEndpointProfile(null));
assert.doesNotThrow(() => migrateStoredSettings("not settings"));
assert.deepEqual(parseStoredSettings("{bad json"), migrateStoredSettings(null));
assert.equal(parseStoredSettings('{"endpoint":{"baseUrl":42}}').endpoint.baseUrl, "http://127.0.0.1:1234/v1");

const payload = serializeEndpointProfile({ ...stale.endpoint, modelManagement: {}, unknownGarbage: true }, { apiKey: null });
assert.deepEqual(payload, { id: "lm", displayName: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1", source: "lm_studio", apiKey: null });

assert.equal(migrateStoredSettings({ maxTokens: 22 }).maxTokens, 4096);
assert.throws(() => validateSettingsDraft({ ...migrated, timeoutSeconds: 120, maxTokens: 22 }), /Max output tokens/);
assert.deepEqual(validateSettingsDraft({ ...migrated, timeoutSeconds: 120 }), { ...migrated, timeoutSeconds: 120 });
assert.deepEqual(modelSelectionEndpoint(stale.endpoint, {
  id: "discovered", displayName: "Autofilled", baseUrl: "http://127.0.0.1:1234/v1/", source: "lm_studio", apiKey: null,
}), { id: "discovered", displayName: "LM Studio", baseUrl: "http://127.0.0.1:1234/v1/", source: "lm_studio", apiKey: "key" });
assert.equal(modelSelectionEndpoint(stale.endpoint, {
  id: "other", displayName: "Other", baseUrl: "http://127.0.0.1:9999/v1", source: "manual", apiKey: "other-key",
}).apiKey, "other-key");
const complete = { ...migrated, supportsVision: true, contextWindow: 32768, persistKey: true,
  storyPlannerReasoning: false, storyPlannerTimeoutSeconds: 300, storyPlannerVision: true };
assert.deepEqual(parseStoredSettings(JSON.stringify(complete)), complete);
assert.equal(parseStoredSettings(JSON.stringify({ ...complete, maxTokens: 22 })).endpoint.apiKey, "key");

const html = readFileSync(new URL("../../web/index.html", import.meta.url), "utf8");
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
assert.equal(new Set(ids).size, ids.length, "Static DOM IDs must be unique");
const settingNames = [...html.matchAll(/\bname="(mc-llm-[^"]+)"/g)].map(match => match[1]);
assert.equal(new Set(settingNames).size, settingNames.length, "Settings names must be unique");
for (const id of ["endpoint-display-name", "base-url", "model-id", "api-key", "max-tokens", "context-window",
  "temperature", "reasoning-mode", "seed-mode", "fixed-seed", "persist-key", "vision-support"]) {
  const tag = html.match(new RegExp(`<[^>]+id="${id}"[^>]*>`))?.[0];
  assert.ok(tag, `Missing settings input ${id}`);
  assert.match(tag, /\bname="mc-[^"]+"/);
  assert.match(tag, /\bautocomplete="(?:off|new-password)"/);
}
assert.match(html, /id="api-key"[^>]*type="password"[^>]*autocomplete="new-password"/);
const app = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
assert.match(app, /function settings\(\)\s*\{\s*return structuredClone\(committedSettings/);
assert.match(app, /function persistSettings\(\)\s*\{\s*if \(!committedSettings\) return;/);
assert.match(app, /name\.name = `mc-subject-narrative-\$\{subjectGroup\.subject\.id\}`/);
assert.match(app, /name\.autocomplete = "off"/);
