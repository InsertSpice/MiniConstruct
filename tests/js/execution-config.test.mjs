import assert from "node:assert/strict";
import test from "node:test";
import { createExecutionConfig, discoverDeactivationTargets, inspectWorkflow, isGraphLink, scalarHint } from "../../web/js/execution-config.js";
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
    executionProfiles: async () => structuredClone(profiles),
    executionProfile: async id => structuredClone(profiles.find(item => item.id === id)),
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

test("mounted configuration loads separate areas, labels, canonical modes and no authoring dirtiness", async () => {
  const s = setup(); await s.config.refresh();
  assert.match(s.root.textContent, /ComfyUI Connections — where/);
  assert.match(s.root.textContent, /Workflow Profiles — how/);
  assert.match(s.root.textContent, /No saved connections/);
  assert.equal(s.find("load-status").textContent, "Saved configuration loaded");
  assert.equal(s.find("profile-status").attributes["role"], "status");
  for (const input of s.root.querySelectorAll("input,select,textarea")) assert.equal(input.parentElement.tagName, "label");
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

test("connection save failure keeps draft and load errors remain actionable", async () => {
  const s = setup(); await s.config.refresh();
  await s.set("connection-name", "Draft"); s.api.saveExecutionConnection = async () => { throw new Error("Invalid URL"); };
  await s.click("connection-save"); assert.equal(s.find("connection-status").textContent, "Invalid URL");
  assert.equal(s.find("connection-name").value, "Draft");
  s.api.executionConnections = async () => { throw new Error("Read failed"); };
  await s.config.refresh(); assert.equal(s.find("load-status").textContent, "Read failed");
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
