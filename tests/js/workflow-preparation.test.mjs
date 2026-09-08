import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { Element } from "./support/dom.mjs";
import { createWorkflowPreparation, canSelectExecutionOutput } from "../../web/js/workflow-preparation.js";
import { api as realApi } from "../../web/js/api.js";
import { applyRevisionCandidate } from "../../web/js/revision.js";

function setup() {
  const doc = { createElement: tag => new Element(tag, doc) };
  const root = new Element("div", doc);
  const context = { saved: true, projectId: "project-a", generationId: "generation-a", mode: "Ref2VA", dirty: false };
  const output = { prompt: "completed prompt", sourceFingerprint: "abc", validation: { valid: true }, generationStatus: "complete" };
  const statuses = new Map(); const calls = [];
  let outputValue = output;
  const profiles = [{ id: "profile-a", name: "Capacity", compatibleModes: ["Ref2VA"] },
    { id: "profile-b", name: "Text only", compatibleModes: ["T2VA"] }];
  const selection = () => structuredClone(statuses.get(context.generationId) || { selected: null, stale: false, currentFingerprint: "abc" });
  const api = {
    executionConnections: async () => [{ id: "connection-a", name: "Local ComfyUI", baseUrl: "http://localhost:8188" }],
    sendExecution: async body => {
      calls.push(["send", body]);
      return { submitted: true, promptId: "expected-prompt-id", connectionName: "Local ComfyUI", queueNumber: 9 };
    },
    executionSelection: async (p, g) => { calls.push(["status", p, g]); return structuredClone(statuses.get(g) || { selected: null, stale: false }); },
    selectExecutionPrompt: async (p, g, body) => {
      calls.push(["select", p, g, body]);
      const response = { selected: { id: "selected-1", ...body }, stale: false, currentFingerprint: "abc" };
      statuses.set(g, response); return structuredClone(response);
    },
    clearExecutionPrompt: async (p, g) => { calls.push(["clear", p, g]); statuses.delete(g); return selection(); },
    executionProfiles: async () => structuredClone(profiles),
    prepareWorkflow: async body => {
      calls.push(["prepare", body]);
      return { profileName: "Capacity", mode: "Ref2VA", graph: {}, assetRequirements: [],
        resolutions: [{ label: "Picture 1", value: "cyclist.png", nodeId: "154", inputName: "image", presence: "optional", status: "active" },
          { label: "Picture 2", value: "not present", nodeId: "157", inputName: "image", presence: "optional", status: "inactive" }],
        warnings: ["Unbound LoadImage 201 retains <img src=x onerror=alert(1)> as a workflow constant."], sentToComfyUI: false };
    },
  };
  const ui = createWorkflowPreparation({ root, api, getContext: () => context, getOutput: () => outputValue });
  const find = id => { const node = root.querySelector(`#preparation-${id}`); assert.ok(node, id); return node; };
  return { root, context, output, profiles, statuses, calls, ui, api, find,
    outputValue: value => { outputValue = value; },
    click: async id => { assert.equal(find(id).disabled, false, `${id} enabled`); await find(id).emit("click"); },
    async ready() { await ui.refresh(); await this.click("use"); await this.click("prepare"); } };
}

test("F4 Connection selection is separate and Send requires current Prepare", async () => {
  const s = setup(); await s.ui.refresh();
  assert.equal(s.find("connection").value, "connection-a");
  assert.match(s.find("connection").textContent, /Local ComfyUI/);
  assert.equal(s.find("send").disabled, true);
  await s.click("use"); assert.equal(s.find("send").disabled, true);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
  assert.match(s.find("receipt").textContent, /Prepared offline/);
  assert.ok(!s.profiles.some(p => "connectionId" in p));
});

test("F4 Send uses only IDs and displays a transient queue receipt, then requires Prepare", async () => {
  const s = setup(); await s.ready(); await s.click("send");
  assert.deepEqual(s.calls.find(c => c[0] === "send")[1], { projectId: "project-a", generationId: "generation-a",
    profileId: "profile-a", connectionId: "connection-a" });
  assert.match(s.find("receipt").textContent, /Queued in ComfyUI.*\nConnection: Local ComfyUI.*\nPrompt ID: expected-prompt-id.*\nQueue number: 9/);
  assert.equal(s.find("send").disabled, true);
  assert.equal(s.find("result").querySelector("details").open, undefined);
  assert.equal(s.root.querySelector("progress"), null);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
});

test("F4 double click sends once while busy and a late receipt cannot follow a Generation switch", async () => {
  const s = setup(); await s.ready(); let finish, count = 0;
  s.api.sendExecution = () => { count++; return new Promise(resolve => { finish = resolve; }); };
  const pending = s.find("send").emit("click");
  assert.equal(s.find("send").disabled, true);
  await s.find("send").emit("click"); assert.equal(count, 1);
  s.context.generationId = "other"; s.ui.sync();
  finish({ submitted: true, promptId: "old", connectionName: "old" }); await pending;
  assert.doesNotMatch(s.find("receipt").textContent, /Queued|old/);
});

for (const field of ["dirty", "projectId", "generationId", "mode"]) {
  test(`F4 ${field} change clears send readiness and receipt`, async () => {
    const s = setup(); await s.ready(); await s.click("send");
    s.context[field] = field === "dirty" ? true : "changed"; s.ui.sync();
    assert.equal(s.find("send").disabled, true);
    assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
  });
}

test("F4 Connection change retains offline graph but clears receipt and requires Prepare", async () => {
  const s = setup(); await s.ready(); const graph = s.ui.prepared;
  await s.click("send"); await s.find("connection").emit("change");
  assert.equal(s.ui.prepared, graph); assert.equal(s.find("send").disabled, true);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
});

test("F4 Connection deletion or mutation refreshes and disables Send", async () => {
  const s = setup(); await s.ready();
  s.api.executionConnections = async () => [{ id: "connection-a", name: "Renamed", baseUrl: "http://new" }];
  await s.ui.refreshConnections(); assert.match(s.find("connection").textContent, /Renamed/);
  assert.equal(s.find("send").disabled, true);
  await s.click("prepare"); await s.click("send");
  s.api.executionConnections = async () => []; await s.ui.refreshConnections();
  assert.equal(s.find("connection").value, ""); assert.equal(s.find("send").disabled, true);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
});

test("F4 multiple Connections require an explicit choice", async () => {
  const s = setup();
  s.api.executionConnections = async () => [{ id: "a", name: "One" }, { id: "b", name: "Two" }];
  await s.ready(); assert.equal(s.find("connection").value, ""); assert.equal(s.find("send").disabled, true);
  s.find("connection").value = "b"; await s.find("connection").emit("change");
  assert.equal(s.find("send").disabled, true);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
});

for (const category of ["Connections", "Profiles"]) {
  test(`F4 late ${category} refresh cannot retain readiness from an intervening Prepare`, async () => {
    const s = setup(); await s.ready(); let finish;
    s.api[`execution${category}`] = () => new Promise(resolve => { finish = resolve; });
    const pending = s.ui[`refresh${category}`]();
    await s.click("prepare"); assert.equal(s.find("send").disabled, false);
    finish(category === "Connections" ? [{ id: "connection-a", name: "Changed", baseUrl: "http://other" }] : s.profiles);
    await pending; assert.equal(s.find("send").disabled, true);
  });
}

for (const mutation of ["profile", "selection", "routing"]) {
  test(`F4 ${mutation} mutation clears Send receipt`, async () => {
    const s = setup(); await s.ready(); await s.click("send");
    if (mutation === "profile") await s.ui.refreshProfiles();
    if (mutation === "selection") await s.click("clear");
    if (mutation === "routing") {
      s.api.executionRouting = async () => ({ routing: { assets: {} }, assets: [], referenceMap: [] });
      await s.ui.refresh();
    }
    assert.equal(s.find("send").disabled, true); assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
  });
}

for (const message of ["ComfyUI rejected prompt validation.",
  "Submission status is unknown. Expected prompt ID: expected-id. Check ComfyUI before sending again."]) {
  test(`F4 failure is clear and never automatically retried: ${message}`, async () => {
    const s = setup(); await s.ready(); let count = 0;
    s.api.sendExecution = async () => { count++; throw new Error(message); };
    await s.click("send"); assert.equal(count, 1);
    assert.equal(s.find("message").textContent, message); assert.equal(s.find("send").disabled, true);
    assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
    assert.doesNotMatch(s.find("receipt").textContent, /NOT SENT/);
  });
}

test("F4 browser connection loss warns about unknown status", async () => {
  const s = setup(); await s.ready();
  s.api.sendExecution = async () => { throw new TypeError("Failed to fetch"); };
  await s.click("send"); assert.match(s.find("message").textContent, /unknown.*Check ComfyUI/);
});

test("F4 API wrapper posts IDs only to backend Send", async () => {
  const prior = globalThis.fetch; const calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return { ok: true, json: async () => ({ submitted: true }) }; };
  try {
    const ids = { projectId: "p", generationId: "g", profileId: "f", connectionId: "c" };
    await realApi.sendExecution(ids);
    assert.equal(calls[0][0], "/api/execution/send");
    assert.deepEqual(JSON.parse(calls[0][1].body), ids); assert.equal(calls.length, 1);
  } finally { globalThis.fetch = prior; }
});

test("routing defaults, persistence refresh, semantic UI and immediate invalidation", async () => {
  const s = setup();
  let response = { routing: { assets: {} }, referenceMap: [], assets: [
    { id: "clip", kind: "video", label: "Video 1", filename: "clip.mp4" },
    { id: "voice", kind: "audio", label: "Audio 1", filename: "voice.wav" }] };
  s.api.executionRouting = async () => structuredClone(response);
  s.api.saveExecutionRouting = async (p, g, routing) => {
    s.calls.push(["routing", p, g, routing]);
    response.routing = routing; return structuredClone(response);
  };
  await s.ready();
  let checks = s.find("routing").querySelectorAll("input");
  assert.deepEqual(checks.map(item => item.checked), [true, false, true]);
  const routingLabels = s.find("routing").querySelectorAll("label");
  assert.equal(routingLabels.length, 3);
  assert.ok(routingLabels.every(label => label.children[0].tagName === "input"));
  assert.match(routingLabels[0].textContent, /Use video frames/);
  assert.doesNotMatch(s.find("routing").textContent, /nodeId|GetVideoComponents|ref_video/);
  checks[1].checked = true; const pending = checks[1].emit("change");
  assert.equal(s.ui.prepared, null); await pending;
  assert.equal(s.calls.find(call => call[0] === "routing")[3].assets.clip.useEmbeddedAudio, true);
  assert.match(s.find("selection").textContent, /Selected and current/);
  await s.ui.refresh(); checks = s.find("routing").querySelectorAll("input");
  assert.equal(checks[1].checked, true);
  s.context.generationId = "generation-b"; response.routing = { assets: {} };
  s.ui.sync(); await s.ui.refresh();
  assert.equal(s.find("routing").querySelectorAll("input")[1].checked, false);
});

test("late routing response cannot change the next Generation's choices", async () => {
  const s = setup(); let finish;
  s.api.executionRouting = async () => ({ routing: { assets: {} }, referenceMap: [], assets: [] });
  await s.ui.refresh();
  s.api.executionRouting = () => new Promise(resolve => { finish = resolve; });
  const old = s.ui.refresh(); await Promise.resolve();
  s.context.generationId = "new-generation";
  s.api.executionRouting = async () => ({ routing: { assets: {} }, referenceMap: [], assets: [] });
  s.ui.sync(); await s.ui.refresh();
  finish({ routing: { assets: { old: { useEmbeddedAudio: true } } }, assets: [], referenceMap: [] });
  await old; assert.deepEqual(s.ui.routing, { assets: {} });
});

test("Use for execution sends exact output and provenance and Clear removes it", async () => {
  const s = setup(); await s.ui.refresh();
  assert.match(s.find("selection").textContent, /No generated prompt/);
  await s.click("use"); assert.match(s.find("selection").textContent, /Selected and current/);
  assert.deepEqual(s.calls.find(c => c[0] === "select")[3], { prompt: "completed prompt", sourceFingerprint: "abc" });
  await s.click("clear"); assert.match(s.find("selection").textContent, /No generated prompt/);
  assert.equal(s.find("prepare").disabled, true);
});

test("reload restores selected prompt with no outputs and no History reads", async () => {
  const s = setup(); s.outputValue(null);
  s.statuses.set("generation-a", { selected: { prompt: "durable prompt", sourceFingerprint: "abc" }, stale: false });
  await s.ui.refresh();
  assert.match(s.root.textContent, /durable prompt/); assert.equal(s.find("use").disabled, true);
  await s.click("prepare"); assert.ok(s.ui.prepared);
});

test("stale selection is visible and prevents Prepare", async () => {
  const s = setup(); s.statuses.set("generation-a", { selected: { prompt: "old" }, stale: true });
  await s.ui.refresh(); assert.match(s.find("selection").textContent, /stale for the current saved Generation/);
  assert.equal(s.find("prepare").disabled, true); assert.equal(s.find("clear").disabled, false);
});

test("compatible profiles filter by mode", async () => {
  const s = setup(); await s.ui.refresh();
  assert.deepEqual(s.find("profile").children.map(n => n.value), ["profile-a"]);
  s.context.mode = "T2VA"; s.ui.sync();
  assert.deepEqual(s.find("profile").children.map(n => n.value), ["profile-b"]);
});

test("Prepare summary has actual filenames, targets, inactive slots and literal warnings", async () => {
  const s = setup(); await s.ready();
  assert.match(s.find("result").textContent, /Picture 1 · cyclist.png · optional slot ACTIVE/);
  assert.match(s.find("result").textContent, /Picture 2 · not present.*INACTIVE/);
  assert.match(s.find("result").textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(s.root.querySelector("img"), null);
  assert.match(s.find("result").textContent, /Prepared workflow is ready/);
  assert.match(s.root.textContent, /NOT SENT TO COMFYUI/);
  assert.ok(!s.root.querySelectorAll("button").some(b => /^(Send|Run|Queue)$/.test(b.textContent)));
  assert.deepEqual(s.calls.find(c => c[0] === "prepare")[1], { projectId: "project-a", generationId: "generation-a", profileId: "profile-a" });
});

test("Prepare server errors replace success with actionable text", async () => {
  const s = setup(); await s.ready();
  s.api.prepareWorkflow = async () => { throw new Error("Picture 3 is required but missing"); };
  await s.click("prepare"); assert.equal(s.ui.prepared, null);
  assert.match(s.find("message").textContent, /Picture 3 is required/);
  assert.doesNotMatch(s.root.textContent, /Prepared workflow is ready/);
});

for (const [field, value] of [["dirty", true], ["generationId", "generation-b"], ["projectId", "project-b"], ["mode", "T2VA"], ["saved", false]]) {
  test(`${field} change immediately invalidates the prepared result`, async () => {
    const s = setup(); await s.ready(); s.context[field] = value; s.ui.sync();
    assert.equal(s.ui.prepared, null); assert.doesNotMatch(s.root.textContent, /Prepared workflow is ready/);
    if (field === "dirty") { assert.match(s.find("message").textContent, /Save before preparing/); assert.equal(s.find("prepare").disabled, true); }
  });
}

test("selection replace and clear invalidate success", async () => {
  const s = setup(); await s.ready(); await s.click("use"); assert.equal(s.ui.prepared, null);
  await s.click("prepare"); await s.click("clear"); assert.equal(s.ui.prepared, null);
});

test("profile choice invalidates success", async () => {
  const s = setup(); await s.ready(); await s.find("profile").emit("change"); assert.equal(s.ui.prepared, null);
});

for (const action of ["edit", "replace", "delete"]) {
  test(`saved profile ${action} refresh invalidates and updates summaries`, async () => {
    const s = setup(); await s.ready();
    if (action === "delete") s.profiles.splice(0, 1);
    else { s.profiles[0].name = "Changed profile"; s.profiles[0].preparationIssues = ["Optional reference needs consumers"]; }
    await s.ui.refreshProfiles();
    assert.equal(s.ui.prepared, null);
    if (action === "delete") assert.equal(s.find("prepare").disabled, true);
    else { assert.match(s.find("profile").textContent, /Changed profile/); assert.match(s.find("profile-issues").textContent, /needs consumers/); }
  });
}

test("selection refresh becoming stale removes an earlier ready result", async () => {
  const s = setup(); await s.ready(); s.statuses.get("generation-a").stale = true;
  await s.ui.refreshSelection(); assert.equal(s.ui.prepared, null);
  assert.match(s.find("selection").textContent, /stale/);
});

test("late Prepare response cannot resurrect readiness after dirty state", async () => {
  const s = setup(); await s.ui.refresh(); await s.click("use");
  const result = await s.api.prepareWorkflow({}); let resolve;
  s.api.prepareWorkflow = () => new Promise(done => { resolve = done; });
  const pending = s.click("prepare"); s.context.dirty = true; s.ui.sync(); resolve(result); await pending;
  assert.equal(s.ui.prepared, null); assert.doesNotMatch(s.root.textContent, /Prepared workflow is ready/);
});

test("late status response does not leak another Generation selection", async () => {
  const s = setup(); await s.ui.refresh(); let resolve;
  s.api.executionSelection = (_p, g) => g === "generation-a" ? new Promise(done => { resolve = done; }) : Promise.resolve({ selected: null });
  const pending = s.ui.refreshSelection(); await Promise.resolve(); s.context.generationId = "generation-b"; s.ui.sync();
  resolve({ selected: { prompt: "wrong generation" }, stale: false }); await pending;
  assert.doesNotMatch(s.root.textContent, /wrong generation/);
});

test("status and profile failures clear availability", async () => {
  const s = setup(); await s.ready();
  s.api.executionSelection = async () => { throw new Error("Missing saved payload"); };
  await s.ui.refreshSelection(); assert.equal(s.find("prepare").disabled, true);
  assert.match(s.find("message").textContent, /Missing saved payload/);
  s.api.executionProfiles = async () => { throw new Error("Profiles unavailable"); };
  await s.ui.refreshProfiles(); assert.equal(s.find("profile").value, "");
});

for (const patch of [{ sourceFingerprint: null }, { validation: { valid: false } }, { generationStatus: "stopped" }, { generationStatus: "generating" }, { prompt: "" }]) {
  test(`incomplete or unproven output is not selectable: ${JSON.stringify(patch)}`, () => {
    const s = setup(); assert.equal(canSelectExecutionOutput({ ...s.output, ...patch }), false);
  });
}

test("Revision carries candidate provenance instead of the original output's snapshot", () => {
  const original = { prompt: "before", sourceFingerprint: "old" }; const outputs = [original];
  assert.ok(applyRevisionCandidate(outputs, 0, { originIndex: 0, originOutput: original, sourcePrompt: "before",
    candidatePrompt: "after", sourceFingerprint: "new", validation: { valid: true } }));
  assert.equal(original.sourceFingerprint, "new");
});

test("selection and Prepare API wrappers expose only canonical IDs and explicit selection fields", async () => {
  const previous = globalThis.fetch; const calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({}) }; };
  try {
    await realApi.executionSelection("p/x", "g/x");
    await realApi.selectExecutionPrompt("p", "g", { prompt: "text", sourceFingerprint: "f" });
    await realApi.clearExecutionPrompt("p", "g");
    await realApi.prepareWorkflow({ projectId: "p", generationId: "g", profileId: "w" });
    assert.equal(calls[0][0], "/api/execution/projects/p%2Fx/generations/g%2Fx/selection");
    assert.deepEqual(calls.map(c => c[1].method), ["GET", "PUT", "DELETE", "POST"]);
    assert.deepEqual(JSON.parse(calls[3][1].body), { projectId: "p", generationId: "g", profileId: "w" });
  } finally { globalThis.fetch = previous; }
});

test("app connects profile changes, authoring dirtiness and response provenance", async () => {
  const text = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(text, /onProfilesChanged: \(\) => workflowPreparation.refreshProfiles\(\)/);
  assert.match(text, /state.dirty = value;\s+workflowPreparation\?\.sync\(\)/);
  assert.match(text, /sourceFingerprint: data.sourceFingerprint/);
  assert.match(text, /revision.sourceFingerprint = data.sourceFingerprint/);
  const initialization = text.slice(text.indexOf("async function init()"));
  assert.doesNotMatch(initialization, /await renderHistory\(\)/);
  assert.match(text, /#load-history/);
});

test("ComfyUI preparation is rendered after the complete core prompt workflow", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const order = ["id=\"output-highlighted\"", "id=\"validate-output\"", "id=\"repair-output\"",
    "id=\"revision-panel\"", "class=\"lower-grid\"", "id=\"workflow-preparation\""]
    .map(marker => html.indexOf(marker));
  assert.ok(order.every(index => index >= 0));
  assert.ok(order.every((index, position) => position === 0 || index > order[position - 1]));
});


test("same-tick Generation switch suppresses superseded selection GET before sending", async () => {
  const s = setup();
  s.ui.sync();
  s.context.generationId = "generation-b";
  s.ui.sync();
  await s.ui.refreshSelection();
  assert.deepEqual(s.calls.filter(c => c[0] === "status"), [["status", "project-a", "generation-b"]]);
});
