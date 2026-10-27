import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { migrateStoredSettings, parseStoredSettings, serializeEndpointProfile, validateSettingsDraft, modelSelectionEndpoint } from "../../web/js/settings-persistence.js";
import { normalizeSeedSettings } from "../../web/js/seed.js";

const source = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
const names = ["endpointProfile", "readSettingsDraft", "settings", "persistSettings", "renderCommittedSettings",
  "validatedSettingsDraft", "commitSettingsFromUi", "restoreSettings", "selectPooledModel"];
const functions = names.map(name => {
  const match = source.match(new RegExp(`function ${name}\\([^\\n]*\\) \\{[\\s\\S]*?\\n\\}`));
  assert.ok(match, `Missing ${name}`);
  return match[0];
}).join("\n");

function harness() {
  const values = {
    "endpoint-display-name": "Unsloth", "base-url": "http://127.0.0.1:1234/v1", "model-id": "original-model",
    "api-key": "saved-key", temperature: "0.4", "max-tokens": "8192", "context-window": "32768",
    "reasoning-mode": "on", "seed-mode": "fixed", "fixed-seed": "42",
  };
  const controls = Object.fromEntries(Object.entries(values).map(([id, value]) => [`#${id}`, { value }]));
  controls["#persist-key"] = { checked: true };
  controls["#vision-support"] = { checked: true };
  controls["#connection-message"] = { textContent: "" };
  const saved = new Map();
  const state = { endpoint: { id: "unsloth", displayName: "Unsloth", baseUrl: values["base-url"],
    source: "unsloth_studio", apiKey: "saved-key" }, storyPlannerTimeoutSeconds: 600,
    storyPlannerReasoning: true, discoveredEndpoints: new Map(), connectionState: "unverified" };
  const context = vm.createContext({ state, structuredClone, normalizeSeedSettings, migrateStoredSettings,
    parseStoredSettings, serializeEndpointProfile, validateSettingsDraft, modelSelectionEndpoint,
    $: selector => controls[selector], setActiveModelStatus() {},
    localStorage: { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) } });
  vm.runInContext("let committedSettings = null; let committedPersistKey = false; let apiKeyEdited = false; let apiKeyClearRequested = false; let storyPlanner = null;", context);
  vm.runInContext("function renderSeedSettings() {}", context);
  vm.runInContext(functions, context);
  const run = code => vm.runInContext(code, context);
  return { run, controls, saved, state };
}

test("unrelated UI edits and transient autofill cannot persist or send changed LLM settings", () => {
  const h = harness();
  h.run("commitSettingsFromUi()");
  const initial = JSON.parse(h.saved.get("miniconstruct-settings"));
  h.controls["#endpoint-display-name"].value = "Makoto";
  h.controls["#model-id"].value = "Jane";
  h.controls["#api-key"].value = "";
  h.controls["#max-tokens"].value = "22";
  h.run("apiKeyEdited = true; persistSettings()");
  assert.deepEqual(JSON.parse(h.saved.get("miniconstruct-settings")), initial);
  const current = h.run("settings()");
  assert.equal(current.endpoint.displayName, "Unsloth");
  assert.equal(current.modelId, "original-model");
  assert.equal(current.endpoint.apiKey, "saved-key");
  assert.equal(current.maxTokens, 8192);
  assert.throws(() => h.run("commitSettingsFromUi()"), /Max output tokens/);
  assert.deepEqual(JSON.parse(h.saved.get("miniconstruct-settings")), initial);
});

test("settings restore and same-endpoint model choice keep the saved key and name", () => {
  const h = harness();
  h.run("commitSettingsFromUi()");
  h.controls["#api-key"].value = "";
  h.run("apiKeyEdited = true; commitSettingsFromUi()");
  assert.equal(JSON.parse(h.saved.get("miniconstruct-settings")).endpoint.apiKey, "saved-key");
  h.run("restoreSettings()");
  assert.equal(h.controls["#api-key"].value, "saved-key");
  h.state.discoveredEndpoints.set("found", { id: "found", displayName: "Autofilled", baseUrl: "http://127.0.0.1:1234/v1/",
    source: "unsloth_studio", apiKey: null });
  h.run('selectPooledModel("found::next-model")');
  const current = h.run("settings()");
  assert.equal(current.modelId, "next-model");
  assert.equal(current.endpoint.displayName, "Unsloth");
  assert.equal(current.endpoint.apiKey, "saved-key");
  assert.equal(current.maxTokens, 8192);
});
