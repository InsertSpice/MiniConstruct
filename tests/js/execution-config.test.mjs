import assert from "node:assert/strict";
import test from "node:test";
import { createExecutionConfig, discoverControlLeaves, discoverDeactivationTargets, inspectWorkflow, isGraphLink, scalarHint } from "../../web/js/execution-config.js";
import { api as realApi } from "../../web/js/api.js";
import { shouldMarkWorkspaceDirty } from "../../web/js/sequence-workflow.js";
import { readFile } from "node:fs/promises";

import { Element } from "./support/dom.mjs";

const graph = {
  "node:x": { class_type: "<custom-script>", _meta: { title: "<img src=x onerror=alert(1)>" },
    inputs: { prompt: "original", duration: 8, image: "p.png", audio: "a.wav", video: "v.mp4", linked: ["42", 0], data: { anything: 1 } } },
  "42": { class_type: "Loader", inputs: { enabled: true } },
};
const sourceBinding = (kind = "generated_prompt", inputName = "prompt", more = {}) => ({ source: { kind, ...more }, nodeId: "node:x", inputName, ...(kind === "reference_asset" ? { presence: "optional", routes: [] } : {}) });

function setup(onProfilesChanged = () => {}) {
  const doc = { createElement: tag => new Element(tag, doc) };
  const root = new Element("div", doc); root.id = "execution-config";
  const calls = [], confirmations = []; let approve = true, serial = 0;
  const connections = [], profiles = [];
  const api = {
    executionCapabilities: async () => ({ compatibleModes: ["T2VA", "I2VA", "FL2VA", "L2VA", "Ref2VA"], assetKinds: ["image", "video", "audio"], maxWorkflowBytes: 2 * 1024 * 1024 }),
    executionConnections: async () => structuredClone(connections),
    saveExecutionConnection: async (id, body) => {
      calls.push(["connection-save", id, body]); const saved = { id: id || `c${++serial}`, ...body, baseUrl: body.baseUrl.replace(/\/+$/, "") };
      const index = connections.findIndex(item => item.id === id); if (index < 0) connections.push(saved); else connections[index] = saved;
      return saved;
    },
    testExecutionConnection: async id => { calls.push(["test", id]); return { status: "connected", message: "Connected. No workflow was executed" }; },
    deleteExecutionConnection: async id => { calls.push(["connection-delete", id]); connections.splice(connections.findIndex(item => item.id === id), 1); },
    executionProfiles: async () => profiles.map(({ workflow, workflowJson, bindings, ...summary }) => structuredClone({ ...summary, preparationIssues: [] })),
    executionProfile: async id => { calls.push(["profile-get", id]); return structuredClone(profiles.find(item => item.id === id)); },
    duplicateExecutionProfileDraft: async id => {
      calls.push(["duplicate-draft", id]);
      const { id: ignored, workflow, fingerprint, ...draft } = structuredClone(profiles.find(item => item.id === id));
      let number = 1;
      const name = draft.name;
      do { draft.name = `${name} (Copy${number === 1 ? "" : ` ${number}`})`; number++; }
      while (profiles.some(item => item.name === draft.name));
      return { ...draft, workflowJson: JSON.stringify(workflow) };
    },
    saveExecutionProfile: async (id, body) => {
      calls.push(["profile-save", id, structuredClone(body)]);
      const saved = { ...body, workflow: body.workflowJson ? JSON.parse(body.workflowJson) : profiles.find(item => item.id === id).workflow, id: id || `p${++serial}`, fingerprint: "abc123" };
      const index = profiles.findIndex(item => item.id === id); if (index < 0) profiles.push(saved); else profiles[index] = saved;
      return saved;
    },
    deleteExecutionProfile: async id => { calls.push(["profile-delete", id]); profiles.splice(profiles.findIndex(item => item.id === id), 1); },
    validateExecutionProfile: async body => { calls.push(["validate", structuredClone(body)]); return { valid: true, errors: [] }; },
  };
  const config = createExecutionConfig({ root, api, onProfilesChanged, confirmDelete: message => { confirmations.push(message); return approve; } });
  const find = id => { const item = root.querySelector(`#execution-${id}`); assert.ok(item, id); return item; };
  return { root, config, api, calls, profiles, connections, confirmations, find, approve: value => { approve = value; },
    click: async id => find(id).emit("click"),
    set: async (id, value) => { find(id).value = value; await find(id).emit("change"); },
    import: async (value = graph, size = 100) => {
      find("workflow-file").files = [{ size, text: async () => typeof value === "string" ? value : JSON.stringify(value) }];
      await find("workflow-file").emit("change");
    } };
}

async function draft(s) {
  await s.config.refresh(); await s.set("profile-name", "Reusable"); await s.set("description", "Description");
  s.find("mode-Ref2VA").checked = true; await s.import();
}

test("mounted Duplicate retains reusable declarations, cancels, saves independently and reloads", async () => {
  let notifications = 0;
  const s = setup(() => { notifications++; });
  const source = { id: "original", name: "Extender", description: "Reusable", compatibleModes: ["Ref2VA", "I2VA"],
    workflow: structuredClone(graph), fingerprint: "abc", bindings: [sourceBinding(),
      sourceBinding("renderer_seed", "duration"), sourceBinding("aspect_ratio", "ratio", {
        valueMap: { "16:9": "wide" }, enumChoices: ["wide"] }),
      { ...sourceBinding("reference_asset", "image", { assetKind: "image", ordinal: 1 }), routes: [
        { nodeId: "42", inputName: "link", expectedSourceNodeId: "node:x", expectedOutputIndex: 0, channel: "image" }] }],
    exposedControls: [{ id: "enabled", label: "Enabled", target: { nodeId: "42", inputName: "enabled", path: [] }, type: "boolean", defaultValue: true }],
    workflowControlPresets: [{ name: "Off", values: { enabled: false } }],
    nativeContinuation: { kind: "native_h3_av", version: 1, inputs: { enable: { nodeId: "42", inputName: "enabled" } } } };
  source.workflow["node:x"].inputs.ratio = "wide";
  s.profiles.push(structuredClone(source));
  await s.config.refresh();
  assert.equal(s.find("profile-duplicate-original").textContent, "Duplicate");
  const notified = notifications;
  await s.click("profile-duplicate-original");
  assert.equal(notifications, notified);
  assert.equal(s.find("profile-name").value, "Extender (Copy)");
  assert.match(s.find("profile-status").textContent, /Duplicating Extender/);
  assert.equal(s.find("mode-I2VA").checked, true);
  assert.deepEqual(s.profiles, [source]);
  await s.click("preset-edit-0"); s.find("preset-value-enabled").checked = true; await s.click("preset-save");
  assert.deepEqual(s.profiles, [source]);
  await s.click("profile-cancel");
  assert.equal(s.config.hasDraft, false); assert.deepEqual(s.profiles, [source]);
  await s.click("profile-duplicate-original"); s.config.discardDuplicateDraft();
  assert.equal(s.config.hasDraft, false); assert.deepEqual(s.profiles, [source]);
  await s.click("profile-duplicate-original"); await s.click("profile-save");
  assert.match(s.find("profile-status").textContent, /New Workflow Profile created/);
  const copy = s.profiles[1];
  assert.equal(s.profiles.length, 2); assert.notEqual(copy.id, source.id);
  assert.deepEqual([...copy.compatibleModes].sort(), [...source.compatibleModes].sort());
  for (const key of ["workflow", "bindings", "exposedControls", "workflowControlPresets", "nativeContinuation"])
    assert.deepEqual(copy[key], source[key], key);
  assert.equal(s.calls.filter(item => item[0] === "profile-save").length, 1);
  assert.equal(s.calls.find(item => item[0] === "profile-save")[1], null);
  await s.config.refresh(); await s.click(`profile-edit-${copy.id}`);
  await s.click("preset-edit-0"); s.find("preset-value-enabled").checked = true; await s.click("preset-save");
  await s.click("profile-save"); assert.deepEqual(s.profiles[0], source);
  await s.click("profile-duplicate-original"); assert.equal(s.find("profile-name").value, "Extender (Copy 2)");
  await s.click("profile-cancel"); await s.click(`profile-delete-${copy.id}`);
  assert.deepEqual(s.profiles, [source]);
});

test("duplicate replacement preserves matching bindings and exposes missing targets without remapping", async () => {
  const s = setup(); await draft(s);
  s.profiles.push({ id: "source", name: "Source", compatibleModes: ["Ref2VA"], workflow: structuredClone(graph),
    bindings: [sourceBinding()], exposedControls: [], workflowControlPresets: [], nativeContinuation: null });
  await s.config.refresh(); await s.click("profile-duplicate-source");
  const original = structuredClone(s.profiles);
  const replacement = structuredClone(graph); replacement.extra = { class_type: "Hyperflow", inputs: { enabled: true } };
  await s.import(replacement); await s.click("profile-validate");
  assert.deepEqual(s.calls.at(-1)[1].bindings, original[0].bindings);
  delete replacement["node:x"]; await s.import(replacement);
  assert.match(s.find("bindings").textContent, /missing or non-scalar target; edit or remove/);
  await s.click("profile-validate"); assert.deepEqual(s.calls.at(-1)[1].bindings, original[0].bindings);
  assert.deepEqual(s.profiles, original);
});

test("mounted duplicate uses real API GET draft then explicit POST with exact workflow text", async () => {
  const s = setup();
  const id = "source";
  const raw = '{"1":{"class_type":"Custom","inputs":{"seed":9007199254740993}}}';
  const body = { name: "Source (Copy)", description: "", compatibleModes: ["Ref2VA"], workflowJson: raw,
    bindings: [], exposedControls: [], workflowControlPresets: [], nativeContinuation: null };
  const originalFetch = globalThis.fetch, requests = [];
  let saved = null;
  globalThis.fetch = async (url, options) => {
    requests.push([url, options.method, options.body]);
    let result;
    if (url.endsWith("/duplicate-draft")) result = body;
    else if (url === "/api/execution/profiles" && options.method === "POST") {
      const submitted = JSON.parse(options.body); assert.equal(submitted.workflowJson, raw);
      saved = { ...submitted, id: "copy", workflow: JSON.parse(raw), fingerprint: "abc" }; result = saved;
    } else if (url === "/api/execution/profiles/copy") result = saved;
    else if (url === "/api/execution/profiles") result = [{ id, name: "Source", compatibleModes: ["Ref2VA"] }, ...(saved ? [saved] : [])];
    else throw new Error(`Unexpected request ${url}`);
    return { ok: true, json: async () => structuredClone(result) };
  };
  for (const key of ["executionProfiles", "executionProfile", "duplicateExecutionProfileDraft", "saveExecutionProfile"]) s.api[key] = realApi[key];
  try {
    await s.config.refresh(); await s.click("profile-duplicate-source");
    assert.equal(saved, null);
    await s.click("profile-save"); assert.equal(saved.id, "copy");
    assert.equal(requests.filter(item => item[1] !== "GET").length, 1);
    assert.match(s.find("profile-status").textContent, /created/);
  } finally { globalThis.fetch = originalFetch; }
});

test("generic control editor discovers direct and structured leaves and saves edits", async () => {
  const s = setup(); await draft(s);
  const leaves = discoverControlLeaves(inspectWorkflow({
    "42": { class_type: "AnyCustomNode", inputs: { lora_1: { on: true, strength: 1.0, lora: "turbo.safetensors" }, lora_2: { on: false }, steps: 20, linked: ["7", 0] } },
  }));
  assert.ok(leaves.some(item => item.inputName === "lora_1" && item.path.join(".") === "on"));
  assert.ok(leaves.some(item => item.inputName === "steps" && item.path.length === 0));
  assert.ok(!leaves.some(item => item.inputName === "linked"));
  await s.import({ "42": { class_type: "AnyCustomNode", inputs: { lora_1: { on: true, strength: 1.0, lora: "turbo.safetensors" }, steps: 20 } } });
  const option = Array.from(s.find("control-target").children).find(item => item.textContent.includes("lora_1 › on"));
  assert.ok(option);
  await s.set("control-target", option.value);
  assert.equal(s.find("control-type").value, "boolean");
  assert.equal(s.find("control-default").type, "checkbox");
  s.find("control-id").value = "turbo_enabled"; s.find("control-label").value = "Turbo LoRA";
  s.find("control-default").checked = true;
  await s.click("control-save");
  await s.click("profile-save");
  assert.equal(s.profiles[0].exposedControls[0].target.path[0], "on");
  assert.equal(s.profiles[0].exposedControls[0].defaultValue, true);
  await s.click("control-edit-0");
  assert.equal(s.find("control-default").checked, true);
  s.find("control-default").checked = false;
  s.find("control-label").value = "Turbo active";
  await s.click("control-save");
  await s.click("profile-save");
  assert.equal(s.profiles[0].exposedControls[0].defaultValue, false);
  assert.equal(s.root.querySelector("#execution-control-edit-0").disabled, false);
  await s.click("control-remove-0");
  assert.equal(s.root.querySelector("#execution-control-edit-0"), null);
});

test("preset editor initializes from control defaults and keeps named drafts independent", async () => {
  const s = setup(); await draft(s);
  await s.set("control-target", Array.from(s.find("control-target").children).find(option => option.textContent.includes("enabled")).value);
  s.find("control-id").value = "enabled"; s.find("control-label").value = "Enabled";
  s.find("control-default").checked = true; await s.click("control-save");
  assert.equal(s.find("preset-value-enabled").type, "checkbox");
  assert.equal(s.find("preset-value-enabled").checked, true);
  s.find("preset-name").value = "Turbo"; await s.click("preset-save");
  await s.click("profile-save");
  assert.deepEqual(s.profiles[0].workflowControlPresets, [{ name: "Turbo", values: { enabled: true } }]);
  await s.click("preset-edit-0"); s.find("preset-value-enabled").checked = false;
  s.find("preset-name").value = "Quality"; await s.click("preset-save");
  assert.match(s.find("preset-status").textContent, /Preset updated in Profile draft — click Save profile to persist/);
  await s.click("profile-save");
  assert.deepEqual(s.profiles[0].workflowControlPresets, [{ name: "Quality", values: { enabled: false } }]);
  await s.click("preset-remove-0"); await s.click("profile-save");
  assert.deepEqual(s.profiles[0].workflowControlPresets, []);
});

test("native capability JSON survives profile editing and invalid JSON never saves", async () => {
  const s = setup(); await draft(s);
  const capability = { kind: "native_h3_av", version: 1, compatibilityKey: "generic-v1", inputs: {} };
  s.find("native-continuation").value = JSON.stringify(capability);
  await s.click("profile-save");
  assert.deepEqual(s.profiles[0].nativeContinuation, capability);
  await s.click(`profile-edit-${s.profiles[0].id}`);
  assert.deepEqual(JSON.parse(s.find("native-continuation").value), capability);
  s.find("native-continuation").value = "{";
  const count = s.calls.filter(x => x[0] === "profile-save").length;
  await s.click("profile-save");
  assert.equal(s.calls.filter(x => x[0] === "profile-save").length, count);
  assert.equal(s.find("native-continuation").value, "{");
  assert.match(s.find("profile-status").textContent, /invalid JSON/);
});

test("existing profile survives a fresh page via real API PUT, summary list and full detail", async () => {
  const id = "11111111-2222-4333-8444-555555555555";
  let disk = JSON.stringify({ id, name: "Existing", description: "", compatibleModes: ["Ref2VA"],
    bindings: [], workflow: graph, fingerprint: "abc", nativeContinuation: {
      kind: "native_h3_av", version: 1, compatibilityKey: "generic-v1", inputs: {} } });
  const requests = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    requests.push([url, options.method, options.body && JSON.parse(options.body)]);
    let profile = JSON.parse(disk), result;
    if (url === `/api/execution/profiles/${id}` && options.method === "PUT") {
      profile = { ...profile, ...JSON.parse(options.body) };
      disk = JSON.stringify(profile); result = profile;
    } else if (url === `/api/execution/profiles/${id}` && options.method === "GET") result = profile;
    else if (url === "/api/execution/profiles" && options.method === "GET") {
      const { workflow, bindings, ...summary } = profile;
      result = [{ ...summary, preparationIssues: [] }];
    } else throw new Error(`Unexpected request: ${options.method} ${url}`);
    return { ok: true, json: async () => structuredClone(result) };
  };
  const page = async () => {
    const s = setup();
    for (const key of ["executionProfiles", "executionProfile", "saveExecutionProfile"]) s.api[key] = realApi[key];
    await s.config.refresh(); await s.click(`profile-edit-${id}`);
    assert.equal(s.find("profile-name").value, "Existing");
    return s;
  };
  try {
    let a = await page();
    assert.equal(JSON.parse(a.find("native-continuation").value).sourceLinks, undefined);
    const capability = JSON.parse(a.find("native-continuation").value);
    capability.sourceLinks = [{ nodeId: "164", inputName: "prev_latent", expectedSourceNodeId: "167", expectedOutputIndex: 0 }];
    a.find("native-continuation").value = JSON.stringify(capability);
    await a.click("profile-save");
    assert.match(a.find("profile-status").textContent, /Workflow Profile saved/);
    assert.deepEqual(JSON.parse(disk).nativeContinuation, capability);
    assert.deepEqual(requests.filter(r => r[1] === "PUT").map(r => [r[0], r[2].nativeContinuation]),
      [[`/api/execution/profiles/${id}`, capability]]);
    a.root.replaceChildren(); a = null;
    const b = await page();
    assert.deepEqual(JSON.parse(b.find("native-continuation").value), capability);
    assert.equal(requests.filter(r => r[0].endsWith(id) && r[1] === "GET").length, 3);
  } finally { globalThis.fetch = originalFetch; }
});

test("canonical GET disagreement fails Save without discarding draft", async () => {
  const s = setup(); await draft(s);
  const text = JSON.stringify({ inputs: {}, sourceLinks: [{ nodeId: "164" }] });
  s.find("native-continuation").value = text;
  s.api.executionProfile = async id => ({ id, nativeContinuation: null });
  await s.click("profile-save");
  assert.match(s.find("profile-status").textContent, /reloaded Profile declarations disagree/);
  assert.equal(s.find("native-continuation").value, text);
});

test("Save adopts canonical typed defaults rather than repainting submitted text", async () => {
  const s = setup(); await draft(s);
  s.find("native-continuation").value = '{"inputs":{}}';
  const save = s.api.saveExecutionProfile;
  s.api.saveExecutionProfile = (id, body) => save(id, { ...body,
    nativeContinuation: { ...body.nativeContinuation, sourceLinks: [], adapter: "opaque_keys" } });
  await s.click("profile-save");
  assert.match(s.find("profile-status").textContent, /New Workflow Profile created/);
  assert.deepEqual(JSON.parse(s.find("native-continuation").value), { inputs: {}, sourceLinks: [], adapter: "opaque_keys" });
});

test("Validate draft and then Save both use the current native capability textarea", async () => {
  const s = setup(); await draft(s);
  const capability = { kind: "native_h3_av", version: 1, compatibilityKey: "generic-v1", inputs: {},
    sourceLinks: [{ nodeId: "164", inputName: "prev_latent", expectedSourceNodeId: "167", expectedOutputIndex: 0 }] };
  s.find("native-continuation").value = JSON.stringify(capability);
  await s.click("profile-validate"); await s.click("profile-save");
  const calls = s.calls.filter(x => ["validate", "profile-save"].includes(x[0]));
  assert.deepEqual(calls[0][1].nativeContinuation.sourceLinks, capability.sourceLinks);
  assert.deepEqual(calls[1][2].nativeContinuation.sourceLinks, capability.sourceLinks);
});

test("native capability backend validation failure keeps the edited textarea", async () => {
  const s = setup(); await draft(s);
  const text = JSON.stringify({ kind: "native_h3_av", version: 1, compatibilityKey: "generic-v1", inputs: {},
    sourceLinks: [{ nodeId: "164", inputName: "prev_latent", expectedSourceNodeId: "167", expectedOutputIndex: 0 }] });
  s.find("native-continuation").value = text;
  s.api.saveExecutionProfile = async () => { throw new Error("Native source link target must match its expected source node and output index"); };
  await s.click("profile-save");
  assert.equal(s.find("native-continuation").value, text);
  assert.match(s.find("profile-status").textContent, /Native source link target/);
});

test("a mismatching save response fails visibly and retains the draft", async () => {
  const s = setup(); await draft(s);
  const text = JSON.stringify({ kind: "native_h3_av", version: 1, compatibilityKey: "generic-v1", inputs: {},
    sourceLinks: [{ nodeId: "164", inputName: "prev_latent", expectedSourceNodeId: "167", expectedOutputIndex: 0 }] });
  s.find("native-continuation").value = text;
  const originalSave = s.api.saveExecutionProfile;
  s.api.saveExecutionProfile = async (id, body) => {
    await originalSave(id, body);
    return { ...s.profiles[0], nativeContinuation: { kind: "native_h3_av", version: 1,
      compatibilityKey: "generic-v1", inputs: {} } };
  };
  await s.click("profile-save");
  assert.equal(s.find("native-continuation").value, text);
  assert.match(s.find("profile-status").textContent, /could not be verified/);
});

test("mounted configuration loads separate areas, labels, canonical modes and no authoring dirtiness", async () => {
  const s = setup(); await s.config.refresh();
  assert.match(s.root.textContent, /ComfyUI Connections — where/);
  assert.match(s.root.textContent, /Workflow Profiles — how/);
  assert.match(s.root.textContent, /No saved connections/);
  assert.equal(s.find("load-status").textContent, "Saved configuration loaded");
  assert.equal(s.find("profile-status").attributes["role"], "status");
  for (const input of s.root.querySelectorAll("input,select,textarea")) {
    if (input.hidden) continue;
    const wrapped = input.parentElement.tagName === "label";
    const explicitlyLabelled = s.root.querySelectorAll("label").some(label => label.attributes.for === input.id);
    const switchLabel = (input.parentElement.className || "").includes("mini-switch");
    assert.ok(wrapped || explicitlyLabelled || switchLabel, `${input.id} has an accessible label`);
  }
  assert.equal(s.find("mode-Ref2VA").value, "Ref2VA");
  assert.equal(shouldMarkWorkspaceDirty(s.find("profile-name")), false);
});

test("connection create/edit/test statuses and confirmed deletion use backend API", async () => {
  const s = setup(); await s.config.refresh();
  await s.set("connection-name", "Local"); await s.set("base-url", "http://fake.test:9999/"); await s.click("connection-save");
  assert.equal(s.find("base-url").value, "http://fake.test:9999");
  const id = s.connections[0].id;
  await s.click(`connection-edit-${id}`); await s.set("connection-name", "Remote"); await s.click("connection-save");
  assert.equal(s.connections.length, 1); assert.equal(s.connections[0].name, "Remote");
  await s.click(`connection-test-${id}`); assert.match(s.find(`connection-result-${id}`).textContent, /connected/);
  s.api.testExecutionConnection = async () => ({ status: "timeout", message: "Timed out" });
  await s.click(`connection-test-${id}`); assert.match(s.find(`connection-result-${id}`).textContent, /timeout/);
  s.api.testExecutionConnection = async () => { throw new Error("Backend unavailable"); };
  await s.click(`connection-test-${id}`); assert.equal(s.find(`connection-result-${id}`).textContent, "Backend unavailable");
  s.approve(false); await s.click(`connection-delete-${id}`); assert.equal(s.connections.length, 1);
  s.approve(true); await s.click(`connection-delete-${id}`); assert.equal(s.connections.length, 0);
  assert.match(s.confirmations[0], /Delete connection/);
  assert.equal(s.find("connection-name").value, "");
});

test("Save Connection sends the documented local ComfyUI payload", async () => {
  const s = setup(); await s.config.refresh();
  await s.set("connection-name", "Local ComfyUI");
  await s.set("base-url", "http://127.0.0.1:8188");
  await s.click("connection-save");
  assert.deepEqual(s.calls.find(call => call[0] === "connection-save"), ["connection-save", null,
    { name: "Local ComfyUI", baseUrl: "http://127.0.0.1:8188", localInputDirectory: null }]);
});

test("local input root stays Connection-scoped and shows backend fallback guidance", async () => {
  const s = setup(); await s.config.refresh();
  s.api.localExecutionInputStatus = async id => ({ status: "warning", message: "Input root unavailable; using verified HTTP staging." });
  await s.set("connection-name", "Mapped"); await s.set("base-url", "http://never.test");
  await s.set("local-input-directory", "C:\\ComfyUI\\input"); await s.click("connection-save");
  assert.equal(s.connections[0].localInputDirectory, "C:\\ComfyUI\\input");
  assert.match(s.find("connection-status").textContent, /verified HTTP/);
  const id = s.connections[0].id;
  await s.click("connection-new"); assert.equal(s.find("local-input-directory").value, "");
  await s.click(`connection-edit-${id}`); assert.equal(s.find("local-input-directory").value, "C:\\ComfyUI\\input");
  await s.click(`connection-delete-${id}`); assert.equal(s.find("local-input-directory").value, "");
});

test("connection save failure keeps draft and load errors remain actionable", async () => {
  const s = setup(); await s.config.refresh();
  await s.set("connection-name", "Draft"); s.api.saveExecutionConnection = async () => { throw new Error("Invalid URL"); };
  await s.click("connection-save"); assert.equal(s.find("connection-status").textContent, "Invalid URL");
  assert.equal(s.find("connection-name").value, "Draft");
  s.api.executionConnections = async () => { throw new Error("Read failed"); };
  await s.config.refresh(); assert.equal(s.find("load-status").textContent, "Read failed");
});

test("connection validation response is rendered as a safe field-level message", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => ({ ok: false, status: 422, json: async () => ({ detail: [{
    loc: ["body", "baseUrl"], msg: "Value error, base URL must include a host and port",
  }] }) });
  try {
    await assert.rejects(() => realApi.saveExecutionConnection(null, { name: "Local", baseUrl: "http://" }),
      /baseUrl: Value error, base URL must include a host and port/);
  } finally { globalThis.fetch = originalFetch; }
});

test("API import shows literal node labels and scalar vs graph-link input choices", async () => {
  const s = setup(); await draft(s);
  assert.match(s.find("nodes").textContent, /node:x · <custom-script> · <img src=x onerror=alert\(1\)>/);
  assert.match(s.find("nodes").textContent, /linked: graph link/);
  assert.equal(s.root.querySelector("img"), null);
  await s.set("target-node", "node:x");
  const choices = s.find("target-input").children;
  assert.equal(choices.find(item => item.value === "linked").disabled, true);
  assert.equal(choices.find(item => item.value === "data").disabled, true);
  assert.equal(choices.find(item => item.value === "prompt").disabled, false);
  assert.equal(isGraphLink(["opaque", 0]), true); assert.equal(isGraphLink(["opaque", -1]), false);
  assert.throws(() => inspectWorkflow({ nodes: [], links: [] }), /editor workflow/);
});

test("workflow invalid JSON/editor/size failures preserve prior draft", async () => {
  const s = setup(); await draft(s);
  for (const [value, size, message] of [["{", 1, /JSON/], [{ nodes: [] }, 50, /editor workflow/], [graph, 2097153, /2 MiB/], [[], 5, /API-format/]]) {
    await s.import(value, size); assert.match(s.find("import-status").textContent, message);
    assert.match(s.find("nodes").textContent, /node:x/);
  }
  await s.click("profile-save"); assert.deepEqual(s.profiles[0].workflow, graph);
});

test("binding CRUD supports completed prompt, duration and all reference kinds", async () => {
  const s = setup(); await draft(s); await s.set("target-node", "node:x"); await s.set("target-input", "prompt");
  await s.click("binding-save"); await s.click("binding-save"); assert.match(s.find("binding-status").textContent, /already has a binding/);
  await s.set("source", "duration"); await s.set("target-input", "duration"); await s.click("binding-save");
  await s.set("source", "reference_asset");
  for (const kind of ["image", "audio", "video"]) {
    await s.set("asset-kind", kind); await s.set("target-input", kind); await s.click("binding-save");
  }
  await s.click("binding-edit-2"); await s.set("ordinal", "2"); await s.click("binding-save");
  assert.match(s.find("bindings").textContent, /Picture 2/);
  await s.click("profile-save");
  assert.deepEqual(s.profiles[0].bindings, [sourceBinding(), sourceBinding("duration", "duration"),
    sourceBinding("reference_asset", "image", { assetKind: "image", ordinal: 2 }),
    sourceBinding("reference_asset", "audio", { assetKind: "audio", ordinal: 1 }),
    sourceBinding("reference_asset", "video", { assetKind: "video", ordinal: 1 })]);
  assert.deepEqual(s.profiles[0].compatibleModes, ["Ref2VA"]);
  assert.match(s.find("fingerprint").textContent, /abc123/); assert.equal(s.config.hasDraft, false);
  await s.click("binding-remove-1"); assert.equal(s.config.hasDraft, true);
  await s.click("profile-save"); assert.equal(s.profiles[0].bindings.length, 4);
  assert.equal(Object.hasOwn(s.calls.filter(call => call[0] === "profile-save").at(-1)[2], "workflowJson"), false);
});

test("invalid ordinals and missing scalar targets do not create draft bindings", async () => {
  const s = setup(); await draft(s); await s.set("source", "reference_asset");
  await s.set("ordinal", "0"); await s.click("binding-save"); assert.match(s.find("binding-status").textContent, /positive integer/);
  await s.set("ordinal", "1"); await s.set("target-input", ""); await s.click("binding-save");
  assert.match(s.find("binding-status").textContent, /existing scalar/);
  await s.click("profile-save"); assert.deepEqual(s.profiles[0].bindings, []);
});

test("replacement surfaces stale bindings; failed save retains draft and saved source", async () => {
  const s = setup(); await draft(s); await s.set("target-node", "node:x"); await s.set("target-input", "prompt");
  await s.click("binding-save"); await s.click("profile-save");
  const replacement = structuredClone(graph); delete replacement["node:x"].inputs.prompt;
  await s.import(replacement); assert.match(s.find("bindings").textContent, /missing or non-scalar target/);
  const save = s.api.saveExecutionProfile;
  s.api.saveExecutionProfile = async () => { throw new Error("Binding 1: target input does not exist"); };
  await s.click("profile-save"); assert.match(s.find("profile-status").textContent, /target input does not exist/);
  assert.deepEqual(s.profiles[0].workflow, graph); assert.equal(s.config.hasDraft, true);
  await s.click("binding-remove-0"); s.api.saveExecutionProfile = save;
  await s.click("profile-save"); assert.deepEqual(s.profiles[0].workflow, replacement);
  assert.equal(s.profiles.length, 1);
});

test("profile edit/reopen, validation messages and confirmed deletion", async () => {
  const s = setup(); await draft(s); await s.click("profile-validate"); assert.match(s.find("profile-status").textContent, /valid offline/);
  s.api.validateExecutionProfile = async () => ({ valid: false, errors: ["missing node", "duplicate target"] });
  await s.click("profile-validate"); assert.equal(s.find("profile-status").textContent, "missing node\nduplicate target");
  await s.click("profile-save"); const id = s.profiles[0].id;
  await s.click("profile-new"); assert.equal(s.find("profile-name").value, "");
  await s.click(`profile-edit-${id}`); assert.equal(s.find("profile-name").value, "Reusable");
  assert.equal(s.find("mode-Ref2VA").checked, true);
  s.approve(false); await s.click(`profile-delete-${id}`); assert.equal(s.profiles.length, 1);
  s.approve(true); await s.click(`profile-delete-${id}`); assert.equal(s.profiles.length, 0);
  assert.match(s.confirmations.at(-1), /Delete Workflow Profile/);
});

test("double save is suppressed while an operation is pending", async () => {
  const s = setup(); await s.config.refresh(); let release, calls = 0;
  s.api.saveExecutionConnection = async () => { calls++; await new Promise(resolve => { release = resolve; }); return { id: "x", name: "x", baseUrl: "http://host" }; };
  const pending = s.click("connection-save"); await s.click("connection-save"); assert.equal(calls, 1);
  assert.equal(s.find("connection-save").disabled, true); release(); await pending;
  assert.equal(s.find("connection-save").disabled, false);
});

test("execution API wrappers stay under application routes with encoded IDs", async () => {
  const original = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({}) }; };
  try {
    await realApi.executionCapabilities(); await realApi.executionConnections();
    await realApi.saveExecutionConnection(null, { name: "test" }); await realApi.saveExecutionConnection("a/b", {});
    await realApi.testExecutionConnection("a/b"); await realApi.deleteExecutionConnection("a/b");
    await realApi.executionProfiles(); await realApi.executionProfile("a/b");
    await realApi.saveExecutionProfile(null, {}); await realApi.saveExecutionProfile("a/b", {});
    await realApi.validateExecutionProfile({}); await realApi.deleteExecutionProfile("a/b");
    assert.ok(calls.every(([url]) => url.startsWith("/api/execution/")));
    assert.deepEqual(calls.map(([url, opts]) => [url, opts.method]), [
      ["/api/execution/capabilities", "GET"], ["/api/execution/connections", "GET"], ["/api/execution/connections", "POST"],
      ["/api/execution/connections/a%2Fb", "PUT"], ["/api/execution/connections/a%2Fb/test", "POST"], ["/api/execution/connections/a%2Fb", "DELETE"],
      ["/api/execution/profiles", "GET"], ["/api/execution/profiles/a%2Fb", "GET"], ["/api/execution/profiles", "POST"],
      ["/api/execution/profiles/a%2Fb", "PUT"], ["/api/execution/profiles/validate", "POST"], ["/api/execution/profiles/a%2Fb", "DELETE"],
    ]);
  } finally { globalThis.fetch = original; }
});


test("reference fields hide across source switching; controlled consumers require explicit choice", async () => {
  const s = setup(); await s.config.refresh();
  for (const source of ["generated_prompt", "duration", "reference_asset", "generated_prompt"]) {
    await s.set("source", source);
    for (const field of ["asset-kind", "ordinal", "presence"]) assert.equal(s.find(field).parentElement.hidden, source !== "reference_asset");
  }
  await s.import({
    "154": { class_type: "LoadImage", inputs: { image: "cyclist.png" } },
    "h3": { class_type: "H3", inputs: { reference: ["154", 0], context: ["context", 0] } },
    "size": { class_type: "Sizing", inputs: { image: ["154", 1], context: ["context", 0] } },
    "context": { class_type: "PromptSource", inputs: {} },
  });
  await s.set("source", "reference_asset"); await s.set("target-node", "154");
  assert.match(s.find("consumer-status").textContent, /2 destinations.*cannot safely be optional/);
  const checks = s.find("consumers").querySelectorAll("input");
  assert.equal(checks.length, 2); assert.ok(checks.every(c => !c.checked));
  checks.forEach(c => { c.checked = true; });
  await s.click("binding-save"); await s.click("profile-save");
  assert.equal(s.profiles[0].bindings[0].presence, "optional");
  assert.deepEqual(s.profiles[0].bindings[0].routes, [
    { channel: "image", nodeId: "h3", inputName: "reference", expectedSourceNodeId: "154", expectedOutputIndex: 0 },
    { channel: "image", nodeId: "size", inputName: "image", expectedSourceNodeId: "154", expectedOutputIndex: 1 },
  ]);
  await s.click("binding-edit-0"); assert.ok(s.find("consumers").querySelectorAll("input").every(c => c.checked));
  await s.set("presence", "required"); assert.equal(s.find("consumers").parentElement.hidden, false);
  assert.match(s.find("required-helper").textContent, /always requires this reference/);
});

test("browser stylesheet preserves hidden reference-only fields despite fieldset display rules", async () => {
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  assert.match(css, /#execution-config fieldset\[hidden\][^{]*\{\s*display:\s*none\s*!important/);
  assert.match(css, /#execution-config \.reference-only\[hidden\][^{]*\{\s*display:\s*none\s*!important/);
  const s = setup(); await s.config.refresh(); await s.set("source", "duration");
  assert.equal(s.find("consumer-group").hidden, true);
  assert.equal(s.find("asset-kind").parentElement.hidden, true);
  assert.equal(s.find("ordinal").parentElement.hidden, true);
  assert.equal(s.find("presence").parentElement.hidden, true);
});

test("editor heading, highlighted row and cancel reset make edit state explicit", async () => {
  const s = setup(); await draft(s); await s.set("target-node", "node:x"); await s.set("target-input", "prompt"); await s.click("binding-save");
  await s.set("source", "duration"); await s.set("target-input", "duration"); await s.click("binding-save");
  await s.click("binding-edit-1");
  assert.equal(s.find("binding-editor-heading").textContent, "Editing binding: Duration → node:x / duration");
  assert.match(s.find("bindings").children[1].className, /editing/);
  assert.equal(s.find("binding-save").textContent, "Update binding");
  await s.click("binding-cancel");
  assert.equal(s.find("binding-editor-heading").textContent, "Add semantic binding");
  assert.equal(s.find("binding-save").textContent, "Add binding");
  assert.ok(!s.find("bindings").children.some(row => /editing/.test(row.className)));
  assert.equal(s.find("source").value, "generated_prompt");
  assert.equal(s.find("ordinal").value, "1");
  assert.equal(s.profiles.length, 0);
  assert.equal(s.config.hasDraft, true);
});

test("editing generated prompt or duration hides all reference-only controls", async () => {
  const s = setup(); await draft(s); await s.set("target-node", "node:x");
  for (const [source, input] of [["generated_prompt", "prompt"], ["duration", "duration"]]) {
    await s.set("source", source); await s.set("target-input", input); await s.click("binding-save");
  }
  for (const index of [0, 1]) {
    await s.click(`binding-edit-${index}`);
    assert.equal(s.find("asset-kind").parentElement.hidden, true);
    assert.equal(s.find("ordinal").parentElement.hidden, true);
    assert.equal(s.find("presence").parentElement.hidden, true);
    assert.equal(s.find("consumer-group").hidden, true);
    await s.click("binding-cancel");
  }
});

test("consumer labels lead with destination and preserve technical identity literally", async () => {
  const s = setup(); await s.config.refresh(); await s.import({
    "142": { class_type: "LoadImage", inputs: { image: "cyclist.png" } },
    "136": { class_type: "MiniMaxH3ReferenceToVideo", _meta: { title: "MiniMax H3 Reference to Video" }, inputs: { "ref_images.ref_image_0": ["142", 0], context: ["context", 0] } },
    "143": { class_type: "ImageScaleToTotalPixels", _meta: { title: "Scale Image to Total Pixels" }, inputs: { image: ["142", 0], context: ["context", 0], expression: "a".repeat(800) } },
    "context": { class_type: "PromptSource", inputs: {} },
  });
  await s.set("source", "reference_asset"); await s.set("target-node", "142");
  const labels = s.find("consumers").querySelectorAll("label").map(item => item.textContent);
  assert.match(labels[0], /MiniMax H3 Reference to Video → ref_images\.ref_image_0/);
  assert.match(labels[0], /node 136 · from LoadImage output 0/);
  assert.match(labels[1], /Scale Image to Total Pixels → image/);
  assert.ok(labels.every(label => !/a{100}/.test(label)));
  assert.ok(!s.root.querySelector("img"));
});

test("identical loaders have bounded distinguishing filename hints, never base64 hints", async () => {
  const nodes = inspectWorkflow({
    "154": { class_type: "LoadImage", inputs: { image: "cyclist.png" } },
    "157": { class_type: "LoadImage", inputs: { image: "dog.png" } },
    "159": { class_type: "LoadImage", inputs: { image: "a".repeat(10000) } },
  });
  assert.match(nodes[0].label, /154.*cyclist.png/); assert.match(nodes[1].label, /157.*dog.png/);
  assert.ok(nodes[2].label.length < 100); assert.ok(scalarHint("x".repeat(10000)).length <= 64);
  assert.equal(scalarHint("data:image/png;base64,AA=="), "[payload omitted]");
});

test("bounded downstream discovery exposes paired video terminals and rejects pathological traversal", () => {
  const videoGraph = {
    "170": { class_type: "LoadVideo", inputs: { file: "clip.mp4" } },
    "171": { class_type: "GetVideoComponents", inputs: { video: ["170", 0] } },
    "136": { class_type: "MiniMaxH3ReferenceToVideo", inputs: {
      "ref_videos.ref_video_0": ["171", 0], "ref_video_audios.ref_video_audio_0": ["171", 1],
      prompt: ["prompt-source", 0] } },
    "prompt-source": { class_type: "PromptSource", inputs: {} },
    "200": { class_type: "SaveVideo", inputs: { video: ["136", 0] } },
  };
  const targets = discoverDeactivationTargets(videoGraph, "170");
  assert.equal(targets.length, 2);
  assert.equal(targets.some(item => item.nodeId === "171"), false);
  assert.equal(targets.some(item => item.nodeId === "200"), false);
  assert.deepEqual(targets.filter(item => item.nodeId === "136").map(item => item.inputName), [
    "ref_videos.ref_video_0", "ref_video_audios.ref_video_audio_0"]);
  assert.match(targets.find(item => item.nodeId === "136").route.join(" → "), /LoadVideo.*GetVideoComponents.*MiniMax/);
  assert.throws(() => discoverDeactivationTargets(videoGraph, "170", { maxNodes: 1 }), /safety bound/);
});

test("sink without an independent merge is never an automatic boundary", () => {
  const graph = {
    "170": { class_type: "LoadVideo", inputs: { file: "clip.mp4" } },
    "171": { class_type: "GetVideoComponents", inputs: { video: ["170", 0] } },
    "136": { class_type: "SyntheticH3", inputs: { ref_video: ["171", 0] } },
    "200": { class_type: "SaveVideo", inputs: { video: ["136", 0] } },
  };
  const targets = discoverDeactivationTargets(graph, "170");
  assert.deepEqual(targets, []);
});

test("Video route classification is saved once with exact edge identities", async () => {
  const s = setup(); await s.config.refresh();
  await s.import({
    "loader": { class_type: "OpaqueLoader", inputs: { file: "clip.mp4" } },
    "adapter": { class_type: "OpaqueAdapter", inputs: { input: ["loader", 0] } },
    "context": { class_type: "Context", inputs: {} },
    "h3": { class_type: "CustomMerge", inputs: { frames: ["adapter", 0], sound: ["adapter", 1], prompt: ["context", 0] } },
  });
  await s.set("source", "reference_asset"); await s.set("asset-kind", "video"); await s.set("target-node", "loader");
  const checks = s.find("consumers").querySelectorAll("input");
  const channels = s.find("consumers").querySelectorAll("select");
  assert.deepEqual(channels.map(item => item.value), ["", ""]);
  for (const [index, value] of ["video_frames", "embedded_audio"].entries()) {
    checks[index].checked = true; channels[index].value = value; await channels[index].emit("change");
  }
  await s.click("binding-save"); await s.click("profile-save");
  assert.deepEqual(s.profiles[0].bindings[0].routes.map(route => [route.channel, route.expectedSourceNodeId, route.expectedOutputIndex]),
    [["video_frames", "adapter", 0], ["embedded_audio", "adapter", 1]]);
  await s.click("binding-edit-0");
  assert.deepEqual(s.find("consumers").querySelectorAll("select").map(item => item.value), ["video_frames", "embedded_audio"]);
});

test("existing bindings and Add/edit binding are separate, and Save applies a valid active edit", async () => {
  const s = setup(); await s.config.refresh();
  await s.import({
    "loader": { class_type: "OpaqueLoader", inputs: { file: "clip.mp4" } },
    "adapter": { class_type: "OpaqueAdapter", inputs: { input: ["loader", 0] } },
    "context": { class_type: "Context", inputs: {} },
    "h3": { class_type: "CustomMerge", inputs: { frames: ["adapter", 0], sound: ["adapter", 1], prompt: ["context", 0] } },
  });
  assert.notEqual(s.find("bindings").parentElement, s.find("binding-editor"));
  assert.notEqual(s.find("bindings").parentElement, s.find("binding-editor").parentElement);
  assert.equal(s.find("binding-editor").parentElement.tagName, "fieldset");
  assert.match(s.root.textContent, /Existing semantic bindings/);
  assert.match(s.root.textContent, /Add \/ edit semantic binding/);
  await s.set("source", "reference_asset"); await s.set("asset-kind", "video"); await s.set("target-node", "loader");
  let checks = s.find("consumers").querySelectorAll("input");
  let channels = s.find("consumers").querySelectorAll("select");
  checks[0].checked = true; channels[0].value = "video_frames"; await channels[0].emit("change");
  await s.click("binding-save"); await s.click("profile-save");
  const id = s.profiles[0].id;
  await s.click("binding-edit-0");
  checks = s.find("consumers").querySelectorAll("input"); channels = s.find("consumers").querySelectorAll("select");
  checks[1].checked = true; channels[1].value = "embedded_audio"; await channels[1].emit("change");
  await s.click("profile-save");
  assert.deepEqual(s.profiles[0].bindings[0].routes.map(route => route.channel), ["video_frames", "embedded_audio"]);
  await s.click(`profile-edit-${id}`); await s.click("binding-edit-0");
  assert.deepEqual(s.find("consumers").querySelectorAll("select").map(item => item.value), ["video_frames", "embedded_audio"]);
  assert.match(s.find("bindings").children[0].className, /editing/);
});

test("invalid active binding edit does not save stale or invalid replacement", async () => {
  const s = setup(); await s.config.refresh(); await s.import(); await s.set("target-node", "node:x"); await s.set("target-input", "prompt");
  await s.click("binding-save"); await s.click("profile-save");
  const before = structuredClone(s.profiles[0].bindings); const saves = s.calls.filter(call => call[0] === "profile-save").length;
  await s.click("binding-edit-0"); s.find("target-input").value = ""; await s.click("profile-save");
  assert.equal(s.calls.filter(call => call[0] === "profile-save").length, saves);
  assert.deepEqual(s.profiles[0].bindings, before);
  assert.match(s.find("binding-editor-heading").textContent, /Editing binding:/);
  assert.match(s.find("profile-status").textContent, /existing scalar target/);
});

test("saved profile create, update, delete and list refresh notify the preparation surface", async () => {
  let changes = 0; const s = setup(() => { changes++; }); await draft(s);
  const first = changes; await s.click("profile-save"); assert.equal(changes, first + 1);
  const id = s.profiles[0].id; await s.set("profile-name", "Updated"); await s.click("profile-save");
  assert.equal(changes, first + 2); await s.click(`profile-delete-${id}`); assert.equal(changes, first + 3);
  await s.config.refresh(); assert.equal(changes, first + 4);
});

test("Workflow Control editor changes fields immediately for each declared type", async () => {
  const s = setup(); await draft(s);
  await s.import({ node: { class_type: "Generic", inputs: { enabled: true, steps: 20, scale: 1.5, sampler: "euler" } } });
  const target = name => Array.from(s.find("control-target").children).find(option => option.textContent.includes(`/ ${name}`)).value;
  const wrapper = id => s.find(id).parentElement;
  await s.set("control-target", target("enabled"));
  assert.equal(s.find("control-default").type, "checkbox");
  assert.equal(wrapper("control-minimum").hidden, true);
  assert.equal(wrapper("control-maximum").hidden, true);
  assert.equal(wrapper("control-step").hidden, true);
  assert.equal(wrapper("control-choices").hidden, true);
  await s.set("control-target", target("steps"));
  assert.equal(s.find("control-default").type, "number");
  assert.equal(wrapper("control-minimum").hidden, false);
  assert.equal(wrapper("control-maximum").hidden, false);
  assert.equal(wrapper("control-step").hidden, false);
  assert.equal(wrapper("control-choices").hidden, true);
  await s.set("control-type", "float");
  assert.equal(s.find("control-default").type, "number");
  assert.equal(wrapper("control-minimum").hidden, false);
  await s.set("control-type", "boolean");
  assert.equal(s.find("control-default").type, "checkbox");
  assert.equal(wrapper("control-minimum").hidden, true);
  await s.set("control-type", "integer");
  await s.set("control-target", target("scale"));
  assert.equal(s.find("control-type").value, "float");
  assert.equal(wrapper("control-minimum").hidden, false);
  await s.set("control-target", target("sampler"));
  assert.equal(s.find("control-type").value, "enum");
  assert.equal(s.find("control-default").tagName, "select");
  assert.equal(s.find("control-default").value, "euler");
  assert.equal(wrapper("control-minimum").hidden, true);
  assert.equal(wrapper("control-maximum").hidden, true);
  assert.equal(wrapper("control-step").hidden, true);
  assert.equal(wrapper("control-choices").hidden, false);
  s.find("control-id").value = "sampler"; s.find("control-label").value = "Sampler";
  s.find("control-choices").value = "euler\nres_multistep"; await s.find("control-choices").emit("input");
  s.find("control-default").value = "res_multistep"; await s.click("control-save");
  assert.match(s.find("control-status").textContent, /Profile draft.*Save profile to persist/);
  assert.ok(s.find("control-id").parentElement.textContent.includes("Stable internal key"));
  assert.ok(s.find("control-default").parentElement.textContent.includes("neither Project defaults nor this Generation"));
});

test("aspect_ratio is an ordinary semantic source in the Profile binding editor", async () => {
  const s = setup(); await draft(s);
  await s.import({ "node:x": { class_type: "Generic", inputs: { prompt: "old", ratio: "auto" } } });
  assert.ok(Array.from(s.find("source").children).some(option => option.value === "aspect_ratio" && option.textContent === "Aspect Ratio (H3 Setup)"));
  await s.set("source", "aspect_ratio"); await s.set("target-node", "node:x"); await s.set("target-input", "ratio");
  await s.click("binding-save"); await s.click("profile-save");
  assert.deepEqual(s.profiles[0].bindings[0], sourceBinding("aspect_ratio", "ratio", { valueMap: {} }));
});

test("Aspect Ratio Profile editor configures exact mappings for canonical H3 values", async () => {
  const s = setup(); await draft(s);
  await s.import({ "node:x": { class_type: "Generic", inputs: { prompt: "old", ratio: "auto" } } });
  await s.set("source", "aspect_ratio");
  assert.equal(s.find("aspect-mapping").hidden, false);
  const table = s.root.querySelector(".aspect-ratio-mapping-table");
  assert.deepEqual(table.children.filter(child => child.tagName === "label").map(child => child.textContent), ["16:9", "9:16", "1:1", "4:3", "3:4", "3:2", "2:3", "21:9"]);
  s.find("aspect-map-0").value = "16:9 (Widescreen)";
  s.find("aspect-map-3").value = "4:3 (Standard)";
  await s.set("target-node", "node:x"); await s.set("target-input", "ratio");
  await s.click("binding-save"); await s.click("profile-save");
  assert.deepEqual(s.profiles[0].bindings[0].source, { kind: "aspect_ratio", valueMap: {
    "16:9": "16:9 (Widescreen)", "4:3": "4:3 (Standard)" } });
  await s.config.refresh();
  await s.root.querySelector(`#execution-profile-edit-${s.profiles[0].id}`).emit("click");
  await s.click("binding-edit-0");
  await s.set("source", "aspect_ratio");
  assert.equal(s.find("aspect-map-0").value, "16:9 (Widescreen)");
  assert.equal(s.find("aspect-map-3").value, "4:3 (Standard)");
});

test("control rows retain descriptive hierarchy and preset fields stay semantically grouped", async () => {
  const s = setup(); await draft(s);
  await s.import({ node: { class_type: "Generic", inputs: { enabled: true, steps: 20 } } });
  const target = name => Array.from(s.find("control-target").children).find(option => option.textContent.includes(`/ ${name}`)).value;
  await s.set("control-target", target("enabled")); s.find("control-id").value = "enabled"; s.find("control-label").value = "Spectrum Enabled";
  s.find("control-default").checked = true; await s.click("control-save");
  await s.set("control-target", target("steps")); s.find("control-id").value = "steps"; s.find("control-label").value = "Steps";
  s.find("control-default").value = "20"; await s.click("control-save");
  assert.equal(s.root.querySelector("#execution-control-list").children[0].querySelector("strong").textContent, "Spectrum Enabled");
  assert.match(s.root.querySelector("#execution-control-list").children[0].querySelector(".control-row-metadata").textContent, /ID: enabled.*Target:/);
  const boolean = s.find("preset-value-enabled");
  assert.match(boolean.parentElement.parentElement.className, /preset-boolean-row/);
  assert.ok(boolean.parentElement.children.includes(boolean));
  assert.equal(boolean.attributes["role"], "switch");
  assert.equal(boolean.attributes["aria-label"], "Spectrum Enabled");
  assert.ok(boolean.parentElement.parentElement.children.some(child => child.tagName === "label" && child.textContent === "Spectrum Enabled"));
  assert.ok(s.root.querySelectorAll("label").some(label => label.attributes.for === boolean.id && label.textContent === "Spectrum Enabled"));
  const numeric = s.find("preset-value-steps");
  assert.match(numeric.parentElement.className, /preset-field/);
  assert.equal(numeric.parentElement.children.length, 2);
  s.find("preset-name").value = "Quality";
  await s.click("preset-save");
  assert.match(s.find("preset-status").textContent, /Profile draft.*Save profile to persist/);
});

test("saved Ref2VA and I2VA profiles coexist and Edit only opens guided configuration", async () => {
  let notifications = 0;
  const s = setup(() => { notifications++; });
  s.profiles.push({ id: "ref", name: "Reference workflow", compatibleModes: ["Ref2VA"], workflow: graph, bindings: [sourceBinding()] },
    { id: "image", name: "Image workflow", compatibleModes: ["I2VA", "Ref2VA"], workflow: graph, bindings: [sourceBinding()] });
  const before = structuredClone(s.profiles);
  await s.config.refresh();
  assert.match(s.root.textContent, /Reference workflow.*Compatible H3 modes: Ref2VA.*Saved reusable/);
  assert.match(s.root.textContent, /Image workflow.*Compatible H3 modes: I2VA, Ref2VA/);
  assert.match(s.root.textContent, /Generated H3 Prompt → ComfyUI Execution Settings/);
  const count = notifications;
  await s.click("profile-edit-image");
  assert.equal(notifications, count);
  assert.equal(s.find("i2va-help").hidden, false);
  assert.match(s.find("i2va-help").textContent, /first-frame anchor.*ordinal 1.*Custom-node wiring/);
  assert.match(s.find("profile-steps").textContent, /Generated H3 prompt.*required.*Validate draft/);
  assert.deepEqual(s.profiles, before);
  assert.deepEqual(s.calls.map(call => call[0]), ["profile-get"]);
  await s.click("profile-new");
  assert.equal(s.find("i2va-help").hidden, true);
  assert.deepEqual(s.profiles, before);
});

const eightRatioMap = { "16:9": "16:9 (Widescreen)", "9:16": "9:16 (Portrait Widescreen)", "1:1": "1:1 (Square)",
  "4:3": "4:3 (Standard)", "3:4": "3:4 (Portrait Standard)", "3:2": "3:2 (Photo)", "2:3": "2:3 (Portrait Photo)", "21:9": "21:9 (Ultrawide)" };
async function ratioEditor({ conflict = false, existing = true } = {}) {
  const s = setup(); s.connections.push({ id: "c", name: "Mock", baseUrl: "http://never.test" });
  const six = Object.fromEntries(Object.entries(eightRatioMap).slice(0, 6));
  if (conflict) six["3:2"] = "manual old photo";
  s.profiles.push({ id: "p", name: "Ratios", compatibleModes: ["T2VA"],
    workflow: { "115": { class_type: "ResolutionSelector", inputs: { aspect_ratio: "4:3 (Standard)", prompt: "old" } } },
    bindings: existing ? [{ source: { kind: "aspect_ratio", valueMap: six }, nodeId: "115", inputName: "aspect_ratio" }] : [], exposedControls: [] });
  s.api.executionAspectRatioCapabilities = async (...args) => {
    s.calls.push(["ratio-discovery", ...args]);
    return { profileId: "p", target: { nodeId: "115", inputName: "aspect_ratio" }, classType: "ResolutionSelector",
      valueMap: eightRatioMap, choices: Object.values(eightRatioMap) };
  };
  await s.config.refresh(); await s.click("profile-edit-p");
  if (existing) await s.click("binding-edit-0");
  else { await s.set("source", "aspect_ratio"); await s.set("target-node", "115"); await s.set("target-input", "aspect_ratio"); }
  await s.set("enum-connection", "c");
  return s;
}

test("mounted Populate previews, preserves six entries, applies eight and reloads via normal Profile save", async () => {
  const s = await ratioEditor(); const before = structuredClone(s.profiles);
  await s.click("aspect-populate");
  assert.deepEqual(s.profiles, before);
  assert.equal(s.find("aspect-map-6").value, "");
  assert.match(s.root.textContent, /2:3: add 2:3 \(Portrait Photo\)/);
  await s.click("aspect-apply"); assert.deepEqual(s.profiles, before);
  await s.click("profile-save");
  assert.deepEqual(s.profiles[0].bindings[0].source, { kind: "aspect_ratio", valueMap: eightRatioMap, enumChoices: Object.values(eightRatioMap) });
  assert.deepEqual(s.profiles[0].exposedControls, []);
  await s.config.refresh(); await s.click("profile-edit-p"); await s.click("binding-edit-0");
  assert.equal(s.find("aspect-map-7").value, "21:9 (Ultrawide)");
  await s.click("aspect-populate"); assert.match(s.find("binding-status").textContent, /up to date/);
});

test("new semantic binding can populate without creating a Workflow Control", async () => {
  const s = await ratioEditor({ existing: false });
  await s.click("aspect-populate"); await s.click("aspect-apply"); await s.click("profile-save");
  assert.equal(s.profiles[0].bindings.length, 1);
  assert.deepEqual(s.profiles[0].bindings[0].source.valueMap, eightRatioMap);
  assert.deepEqual(s.profiles[0].exposedControls, []);
});

test("conflicting manual mappings need an explicit decision before applying", async () => {
  const s = await ratioEditor({ conflict: true }); await s.click("aspect-populate"); await s.click("aspect-apply");
  assert.match(s.find("binding-status").textContent, /Decide each conflicting/);
  assert.equal(s.find("aspect-map-5").value, "manual old photo");
  const check = s.find("aspect-mapping").querySelectorAll("input").find(item => item.type === "checkbox");
  check.checked = true; await s.click("aspect-apply"); await s.click("profile-save");
  assert.equal(s.profiles[0].bindings[0].source.valueMap["3:2"], "3:2 (Photo)");
});

test("offline discovery and changed targets retain the editable mappings without saving", async () => {
  const s = await ratioEditor(); const before = structuredClone(s.profiles);
  s.api.executionAspectRatioCapabilities = async () => { throw new Error("ComfyUI unreachable; check connection"); };
  await s.click("aspect-populate"); assert.match(s.find("binding-status").textContent, /unreachable/);
  assert.equal(s.find("aspect-map-5").value, "3:2 (Photo)");
  assert.deepEqual(s.profiles, before);
  s.api.executionAspectRatioCapabilities = async () => ({ profileId: "p", target: { nodeId: "wrong", inputName: "aspect_ratio" } });
  await s.click("aspect-populate"); assert.match(s.find("binding-status").textContent, /target or draft changed/);
  assert.deepEqual(s.profiles, before);
});


test("declared but contradictory numeric mapping requires an explicit semantic conflict decision", async () => {
  const s = await ratioEditor();
  const before = structuredClone(s.profiles);
  s.find("aspect-map-5").value = "4:3 (Standard)";
  await s.click("aspect-populate");
  assert.match(s.root.textContent, /current “4:3 \(Standard\)” declares ratio 4:3, which conflicts with H3 Setup 3:2/);
  assert.match(s.root.textContent, /Replace with “3:2 \(Photo\)”/);
  assert.doesNotMatch(s.find("binding-status").textContent, /up to date/);
  await s.click("aspect-apply");
  assert.match(s.find("binding-status").textContent, /Decide each conflicting/);
  assert.equal(s.find("aspect-map-5").value, "4:3 (Standard)");
  assert.deepEqual(s.profiles, before);
  const check = s.find("aspect-mapping").querySelectorAll("input").find(item => item.type === "checkbox");
  assert.equal(Boolean(check.checked), false);
  check.checked = true; await s.click("aspect-apply");
  assert.deepEqual(s.profiles, before); // Apply remains draft-only
  await s.click("profile-save");
  assert.equal(s.profiles[0].bindings[0].source.valueMap["3:2"], "3:2 (Photo)");
});

test("declared renderer-specific labels remain manual mappings with an equivalence review warning", async () => {
  const s = await ratioEditor();
  const discover = s.api.executionAspectRatioCapabilities;
  s.api.executionAspectRatioCapabilities = async (...args) => {
    const result = await discover(...args); result.choices.push("Photo preset"); return result;
  };
  s.find("aspect-map-5").value = "Photo preset";
  await s.click("aspect-populate");
  assert.match(s.root.textContent, /numeric ratio equivalence cannot be verified/);
  assert.match(s.find("binding-status").textContent, /Review renderer-specific labels/);
  assert.equal(s.find("aspect-mapping").querySelectorAll("input").filter(item => item.type === "checkbox").length, 0);
  await s.click("aspect-apply"); await s.click("profile-save");
  assert.equal(s.profiles[0].bindings[0].source.valueMap["3:2"], "Photo preset");
});

test("cancelling a numeric conflict review preserves the saved six mappings", async () => {
  const s = await ratioEditor(); const before = structuredClone(s.profiles);
  s.find("aspect-map-5").value = "4:3 (Standard)";
  await s.click("aspect-populate"); await s.click("binding-cancel");
  assert.deepEqual(s.profiles, before);
  await s.click("binding-edit-0");
  assert.equal(s.find("aspect-map-5").value, "3:2 (Photo)");
  assert.equal(s.find("aspect-apply").hidden, true);
});
