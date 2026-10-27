import assert from "node:assert/strict";
import test from "node:test";
import { readFile } from "node:fs/promises";
import { mountHtml, domOrder } from "./support/html.mjs";
import { Element } from "./support/dom.mjs";
import { createWorkflowPreparation, canSelectExecutionOutput, currentExecutionOutput, executionCandidateState, historyAdoptionState } from "../../web/js/workflow-preparation.js";
import { api as realApi } from "../../web/js/api.js";
import { applyRevisionCandidate } from "../../web/js/revision.js";
import { historyRecordFromOutput, historyOutput, showHistoryOutput } from "../../web/js/history-preview.js";
import { markOutputManuallyEdited } from "../../web/js/manual-edit.js";
import { createSavedPrompts } from "../../web/js/saved-prompts.js";
import { createExecutionConfig } from "../../web/js/execution-config.js";

function setup(callbacks = {}) {
  const doc = { createElement: tag => new Element(tag, doc) };
  const root = new Element("div", doc);
  const primaryRoot = new Element("section", doc); primaryRoot.id = "workflow-preparation-primary";
  const detailsRoot = new Element("section", doc); detailsRoot.id = "workflow-preparation-details";
  root.append(primaryRoot, detailsRoot);
  const workspace = new Element("main", doc); workspace.className = "workspace";
  const leftPane = new Element("aside", doc); leftPane.className = "left-rail";
  const rightPane = new Element("section", doc); rightPane.className = "right-stage";
  workspace.append(leftPane, rightPane); rightPane.append(root);
  const context = { saved: true, projectId: "project-a", generationId: "generation-a", projectFingerprint: "f".repeat(64), mode: "Ref2VA", durationSeconds: 8, aspectRatio: "16:9", dirty: false };
  const output = { prompt: "completed prompt", sourceFingerprint: "abc", validation: { valid: true }, generationStatus: "complete" };
  const statuses = new Map(); const calls = [];
  let currentFingerprint = "abc";
  let outputValue = output;
  const profiles = [{ id: "profile-a", name: "Capacity", compatibleModes: ["Ref2VA"] },
    { id: "profile-b", name: "Text only", compatibleModes: ["T2VA"] }];
  const selection = () => structuredClone(statuses.get(context.generationId) || { selected: null, stale: false, currentFingerprint });
  const api = {
    executionConnections: async () => [{ id: "connection-a", name: "Local ComfyUI", baseUrl: "http://localhost:8188" }],
    sendExecution: async body => {
      calls.push(["send", body]);
      return { submitted: true, promptId: "expected-prompt-id", connectionName: "Local ComfyUI", queueNumber: 9 };
    },
    executionSelection: async (p, g) => { calls.push(["status", p, g]); return structuredClone(statuses.get(g) || { selected: null, stale: false, currentFingerprint }); },
    updateExecutionSetup: async (p, g, body) => { calls.push(["duration", p, g, body]); return { projectFingerprint: "e".repeat(64), generationId: g, durationSeconds: body.durationSeconds }; },
    selectExecutionPrompt: async (p, g, body) => {
      calls.push(["select", p, g, body]);
      const response = { selected: { id: "selected-1", ...body }, stale: false, currentFingerprint };
      statuses.set(g, response); return structuredClone(response);
    },
    adoptHistoryExecutionPrompt: async (p, g, body) => {
      calls.push(["adopt", p, g, body]);
      const response = { selected: { id: "selected-adopted", ...body, sourceFingerprint: currentFingerprint,
        sourceKind: "adopted_history" }, stale: false, currentFingerprint };
      statuses.set(g, response); return structuredClone(response);
    },
    adoptManualEditExecutionPrompt: async (p, g, body) => {
      calls.push(["manual-adopt", p, g, body]);
      if (callbacks.rejectManualEdit) throw new Error("H3 validation failed: unresolved Picture 2.");
      const response = { selected: { id: "selected-manual", ...body, sourceFingerprint: currentFingerprint,
        sourceKind: "manual_edit" }, stale: false, currentFingerprint };
      statuses.set(g, response); return structuredClone(response);
    },
    clearExecutionPrompt: async (p, g) => { calls.push(["clear", p, g]); statuses.delete(g); return selection(); },
    executionProfiles: async () => structuredClone(profiles),
    executionProfile: async id => structuredClone(profiles.find(item => item.id === id)),
    prepareWorkflow: async body => {
      calls.push(["prepare", body]);
      return { profileName: "Capacity", mode: "Ref2VA", graph: {}, assetRequirements: [],
        resolutions: [{ label: "Picture 1", value: "cyclist.png", nodeId: "154", inputName: "image", presence: "optional", status: "active" },
          { label: "Picture 2", value: "not present", nodeId: "157", inputName: "image", presence: "optional", status: "inactive" }],
        referenceMap: [{ assetId: "cyclist", channel: "image", label: "Picture 1" }],
        warnings: ["Unbound LoadImage 201 retains <img src=x onerror=alert(1)> as a workflow constant."], sentToComfyUI: false };
    },
  };
  const ui = createWorkflowPreparation({ root: primaryRoot, primaryRoot, detailsRoot, api, getContext: () => context, getOutput: () => outputValue, ...callbacks });
  const find = id => { const node = root.querySelector(`#preparation-${id}`); assert.ok(node, id); return node; };
  return { root, primaryRoot, detailsRoot, workspace, leftPane, rightPane, context, output, profiles, statuses, calls, ui, api, doc, find,
    setCurrentFingerprint: value => { currentFingerprint = value; },
    outputValue: value => { outputValue = value; }, currentOutput: () => outputValue,
    click: async id => { assert.equal(find(id).disabled, false, `${id} enabled`); await find(id).emit("click"); },
    async ready() { await ui.refresh(); await this.click("use"); await this.click("prepare"); } };
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

test("local staging fallback warning preserves actual queue acceptance and provenance warning", async () => {
  const s = setup(); await s.ready();
  let sends = 0;
  s.api.sendExecution = async () => { sends++; return { submitted: true, promptId: "accepted-local-fallback", connectionName: "Mapped",
    stagingWarning: "Local input root unavailable; using verified HTTP staging.",
    provenanceWarning: "Accepted job provenance could not be saved." }; };
  await s.click("send");
  assert.match(s.find("receipt").textContent, /Queued in ComfyUI.*\nConnection: Mapped.*\nPrompt ID: accepted-local-fallback/);
  assert.match(s.find("message").textContent, /verified HTTP staging/);
  assert.match(s.find("message").textContent, /provenance could not be saved/);
  assert.equal(sends, 1);
});

test("generated-state reset clears prepared receipt and rejects late Prepare for the same Generation", async () => {
  const s = setup(); await s.ready();
  assert.ok(s.ui.prepared);
  const late = deferred(); s.api.prepareWorkflow = () => late.promise;
  const pending = s.click("prepare");
  await new Promise(resolve => setImmediate(resolve));
  s.ui.reset(); s.statuses.clear(); s.outputValue(null); await s.ui.refresh();
  assert.equal(s.ui.prepared, null);
  late.resolve({ graph: {}, profileName: "OLD PREPARATION", mode: "Ref2VA", assetRequirements: [] });
  await pending;
  assert.equal(s.ui.prepared, null); await s.click("send");
  assert.equal(s.calls.filter(call => call[0] === "send").length, 0);
  assert.doesNotMatch(s.root.textContent, /OLD PREPARATION/);
});

test("generated-state reset ignores late Send receipt and never starts its watcher", async () => {
  const submitted = [], s = setup({ onSubmitted: value => submitted.push(value) });
  await s.ready(); const late = deferred(); s.api.sendExecution = () => late.promise;
  const pending = s.click("send"); await new Promise(resolve => setImmediate(resolve));
  s.ui.reset(); s.statuses.clear(); s.outputValue(null); await s.ui.refresh();
  late.resolve({ submitted: true, promptId: "OLD PROMPT", renderSubmissionId: "OLD SEND" });
  await pending;
  assert.deepEqual(submitted, []); assert.equal(s.ui.prepared, null);
  assert.doesNotMatch(s.root.textContent, /OLD PROMPT/);
});

test("Reference Asset authority synchronizes before Use and Prepare only; mutation requires explicit reselection", async () => {
  let s;
  s = setup({ syncReferences: async () => { s.calls.push(["references"]); } });
  await s.ready();
  assert.ok(s.calls.findIndex(call => call[0] === "references") < s.calls.findIndex(call => call[0] === "select"));
  const preparedCount = s.calls.filter(call => call[0] === "prepare").length;
  s.statuses.delete(s.context.generationId);
  await s.ui.invalidateReferences();
  assert.equal(s.ui.prepared, null);
  await s.click("prepare");
  assert.equal(s.calls.filter(call => call[0] === "prepare").length, preparedCount);
  assert.match(s.find("message").textContent, /Explicitly Use/);
  await s.click("use"); await s.click("prepare");
  const verified = s.calls.filter(call => call[0] === "references").length;
  await s.click("send");
  assert.equal(s.calls.filter(call => call[0] === "references").length, verified);
  assert.equal(s.calls.filter(call => call[0] === "send").length, 1);
});

test("saved Reference changes invalidate preparation before Send without another synchronization", async () => {
  const s = setup();
  await s.ready();
  s.statuses.delete(s.context.generationId); await s.ui.invalidateReferences();
  await s.click("send");
  assert.equal(s.calls.filter(call => call[0] === "send").length, 0);
  assert.equal(s.ui.prepared, null);
  assert.match(s.find("message").textContent, /Prepare the current selection/);
});

for (const field of ["creativeRequest", "storyIdea", "notes"]) {
  test(`prepared Send preserves unsaved ${field} without any authoring write`, async () => {
    const s = setup(); await s.ready();
    s.ui.prepared.executionFingerprint = "a".repeat(64);
    const prepared = s.ui.prepared, selection = structuredClone(s.statuses);
    const before = s.calls.length;
    s.context[field] = "Local unsaved draft"; s.context.dirty = true;
    await Promise.all(s.ui.sync());
    assert.equal(s.ui.prepared, prepared);
    await s.click("send");
    assert.deepEqual(s.calls.slice(before).map(call => call[0]), ["send"]);
    assert.deepEqual(s.calls.at(-1)[1], { projectId: "project-a", generationId: "generation-a",
      profileId: "profile-a", connectionId: "connection-a", expectedExecutionFingerprint: "a".repeat(64) });
    assert.equal(s.context[field], "Local unsaved draft"); assert.equal(s.context.dirty, true);
    assert.deepEqual(s.statuses, selection);
  });
}

test("immediate repeated Prepare/Send tolerates renderer revisions and delayed Project receipts", async () => {
  let s, verified = 0, rejectSync = false;
  s = setup({ syncReferences: async () => {
    assert.equal(rejectSync, false, "Send must not enter the Reference PUT path"); verified++;
  } });
  await s.ready();
  for (let i = 0; i < 3; i++) {
    if (i) await s.click("prepare");
    const prepared = s.ui.prepared;
    prepared.executionFingerprint = "b".repeat(64);
    const late = deferred();
    const receipt = late.promise.then(() => {
      s.context.projectFingerprint = `renderer-or-late-receipt-${i}`;
      s.context.projectEditFingerprint = `edit-receipt-${i}`;
      return Promise.all(s.ui.sync());
    });
    late.resolve(); await receipt;
    assert.equal(s.ui.prepared, prepared);
    const count = verified; rejectSync = true;
    await s.click("send"); rejectSync = false;
    assert.equal(verified, count);
    assert.equal(s.calls.filter(call => call[0] === "send").length, i + 1);
    assert.equal(s.calls.at(-1)[1].expectedExecutionFingerprint, prepared.executionFingerprint);
  }
  assert.equal(s.calls.filter(call => call[0] === "select").length, 1);
});

test("draft edits during deferred Send preserve the exact acceptance receipt and prevent duplicates", async () => {
  const accepted = [], s = setup({ onSubmitted: response => accepted.push(response) }); await s.ready();
  const late = deferred(), entered = deferred(); let sends = 0;
  s.api.sendExecution = () => { sends++; entered.resolve(); return late.promise; };
  const pending = s.click("send"); await entered.promise;
  s.context.dirty = true; s.context.creativeRequest = "Draft during Send";
  await Promise.all(s.ui.sync());
  await s.find("send").emit("click");
  late.resolve({ submitted: true, promptId: "exact-prompt", renderSubmissionId: "exact-submission" }); await pending;
  assert.equal(sends, 1); assert.equal(accepted.length, 1);
  assert.match(s.find("receipt").textContent, /exact-prompt/);
  await s.click("send"); assert.equal(sends, 1);
});

test("mounted Use / Prepare / Send callbacks preserve loaded saved prompts and exact visible candidate", async () => {
  let s, savedPrompts;
  const mutation = (response, context) => {
    if (context.projectId === s.context.projectId && response.projectFingerprint)
      s.context.projectFingerprint = response.projectFingerprint;
    savedPrompts?.sync();
  };
  s = setup({ onProjectMutation: mutation,
    onSelectionChanged: () => savedPrompts.sync(),
    onSubmitted: (response, context) => { mutation(response, context); savedPrompts.sync(); } });
  const root = new Element("section", s.doc);
  for (const id of ["save-h3-prompt", "saved-h3-prompts", "load-saved-h3-prompt", "delete-saved-h3-prompt", "saved-h3-prompt-status", "saved-h3-prompt-details"]) {
    const node = new Element(id === "saved-h3-prompts" ? "select" : "button", s.doc); node.id = id; root.append(node);
  }
  const writes = []; let loads = 0;
  const api = { savedH3Prompt: async () => { loads++; return { prompts: [], activeSavedPromptId: null }; },
    saveH3Prompt: async (p, g, body) => {
      writes.push([p, g, body]);
      return { prompts: [{ id: "saved-1", saveNumber: 1, savedAt: "2026-10-03", ...body }], activeSavedPromptId: "saved-1" };
    } };
  savedPrompts = createSavedPrompts({ root, api, getContext: () => s.context,
    getOutput: s.currentOutput, setOutput: () => assert.fail("execution must preserve the candidate") });
  const candidate = s.currentOutput();
  await savedPrompts.load();
  await savedPrompts.saveCurrent();
  const editTarget = structuredClone(candidate.savedPromptTarget);
  writes.length = 0;
  assert.equal(root.querySelector("#save-h3-prompt").disabled, false);
  const send = s.api.sendExecution;
  s.api.sendExecution = async body => ({ ...await send(body), projectFingerprint: "sent-fingerprint" });
  await s.ready(); await s.click("send");
  assert.equal(s.context.projectFingerprint, "sent-fingerprint");
  assert.equal(s.currentOutput(), candidate);
  assert.deepEqual(candidate.savedPromptTarget, editTarget);
  assert.doesNotMatch(root.querySelector("#saved-h3-prompt-status").textContent, /Loading/);
  assert.equal(root.querySelector("#save-h3-prompt").disabled, false);
  assert.equal(writes.length, 0, "execution never auto-saves");
  await root.querySelector("#save-h3-prompt").emit("click");
  assert.equal(writes[0][2].prompt, candidate.prompt);
  assert.equal(writes[0][2].savedPromptId, "saved-1");
  const executionBefore = structuredClone(s.statuses);
  candidate.prompt = "manual changes"; candidate.origin = "manual_edit"; candidate.sourceFingerprint = null;
  await savedPrompts.saveCurrent();
  assert.deepEqual(s.statuses, executionBefore, "saved library update does not select execution text");
  s.ui.sync(); await s.click("use");
  assert.equal(s.statuses.get(s.context.generationId).selected.prompt, "manual changes");
  assert.deepEqual(candidate.savedPromptTarget, editTarget, "Update execution selection preserves overwrite target");
  assert.equal(loads, 1);
  assert.equal(s.calls.filter(call => call[0] === "send").length, 1);
});

test("F4 Connection selection is separate and Send requires current Prepare", async () => {
  const s = setup(); await s.ui.refresh();
  assert.equal(s.find("connection").value, "connection-a");
  assert.match(s.find("connection").textContent, /Local ComfyUI/);
  assert.equal(s.find("send").disabled, false);
  await s.click("use"); assert.equal(s.find("send").disabled, false);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
  assert.match(s.find("receipt").textContent, /Prepared offline/);
  assert.ok(!s.profiles.some(p => "connectionId" in p));
});

test("one-shot saved selection fills ordinary Prepare without selecting or sending", async () => {
  const s = setup();
  s.statuses.set(s.context.generationId, { selected: { prompt: "saved exact prompt", sourceKind: "manual_edit" }, stale: false });
  await s.ui.prepareSavedSelection("profile-a");
  assert.equal(s.calls.filter(call => call[0] === "prepare").length, 1);
  assert.equal(s.calls.filter(call => ["select", "adopt", "send"].includes(call[0])).length, 0);
  assert.match(s.find("status").textContent, /Prepared \u2014 (?:Ready to Send|Review warnings)/);
});

test("mounted Workflow Controls edit project and Generation scopes while Prepare and Send stay clickable", async () => {
  const s = setup();
  s.profiles[0].exposedControls = [
    { id: "enabled", label: "Spectrum", type: "boolean", defaultValue: true },
    { id: "steps", label: "Steps", type: "integer", defaultValue: 20 },
    { id: "strength", label: "Strength", type: "float", defaultValue: 1.0 },
    { id: "sampler", label: "Sampler", type: "enum", defaultValue: "euler" },
  ];
  s.profiles[0].workflowControlPresets = [
    { name: "Turbo 8-step", values: { enabled: false, steps: 8, strength: 1.0, sampler: "res_multistep" } },
    { name: "Quality + Spectrum", values: { enabled: true, steps: 20, strength: 0.5, sampler: "euler" } },
  ];
  const project = {}, generation = {};
  const state = () => ({ profileId: "profile-a", warnings: [], controls: s.profiles[0].exposedControls.map(def => ({
    ...def, choices: def.id === "sampler" ? ["euler", "res_multistep"] : [],
    value: generation[def.id] ?? project[def.id] ?? def.defaultValue,
    source: Object.hasOwn(generation, def.id) ? "generation_override" : Object.hasOwn(project, def.id) ? "project_default" : "profile_default",
    hasProjectOverride: Object.hasOwn(project, def.id), hasGenerationOverride: Object.hasOwn(generation, def.id),
    projectOverride: project[def.id], generationOverride: generation[def.id],
  })) });
  s.api.workflowControls = async () => state();
  s.api.saveWorkflowControls = async (_p, _g, _profile, scope, values) => {
    const target = scope === "project" ? project : generation;
    for (const key of Object.keys(target)) delete target[key]; Object.assign(target, values);
    return state();
  };
  await s.ui.refresh();
  assert.equal(s.find("workflow-controls").hidden, false);
  assert.equal(s.find("control-enabled").type, "checkbox");
  assert.equal(s.find("control-steps").type, "number");
  assert.equal(s.find("control-strength").type, "number");
  assert.equal(s.find("control-sampler").tagName, "select");
  assert.match(s.find("preset-match").textContent, /Custom/);
  s.find("preset").value = "Turbo 8-step";
  assert.deepEqual(project, {});
  await s.click("preset-apply");
  assert.deepEqual(project, s.profiles[0].workflowControlPresets[0].values);
  assert.match(s.find("preset-match").textContent, /Turbo 8-step/);
  assert.match(s.find("message").textContent, /Applied/);
  s.profiles[0].workflowControlPresets[0].values.steps = 10;
  assert.equal(project.steps, 8);
  s.find("control-steps").value = "8";
  await s.find("control-steps").emit("input");
  await s.find("control-steps").parentElement.parentElement.querySelector("button").emit("click");
  assert.equal(project.steps, 8);
  assert.equal(s.find("prepare").disabled, false);
  assert.equal(s.find("send").disabled, false);
  s.find("control-scope").value = "generation"; await s.find("control-scope").emit("change");
  assert.match(s.find("workflow-controls").textContent, /Profile default/);
  s.find("preset").value = "Quality + Spectrum";
  assert.deepEqual(generation, {});
  await s.click("preset-apply");
  assert.deepEqual(generation, s.profiles[0].workflowControlPresets[1].values);
  assert.match(s.find("preset-match").textContent, /Quality \+ Spectrum/);
  s.profiles[0].workflowControlPresets.pop();
  assert.equal(generation.steps, 20);
  s.find("control-steps").value = "12";
  await s.find("control-steps").emit("input");
  await s.find("control-steps").parentElement.parentElement.querySelector("button").emit("click");
  assert.equal(generation.steps, 12);
  const row = s.find("control-steps").parentElement.parentElement;
  await row.querySelectorAll("button")[1].emit("click");
  assert.equal(generation.steps, undefined);
  await s.click("control-clear");
  assert.deepEqual(generation, {});
  assert.equal(s.find("control-steps").value, "8");
  const prepareOriginal = s.api.prepareWorkflow;
  s.api.prepareWorkflow = async body => ({ ...(await prepareOriginal(body)),
    workflowControls: [{ id: "steps", label: "Steps", value: 8, source: "project_default" }] });
  await s.click("use"); await s.click("prepare");
  assert.doesNotMatch(s.find("result").children.filter(child => child.tagName === "p").map(child => child.textContent).join("\n"), /Steps: 8/);
  assert.equal(s.find("prepare").disabled, false);
  assert.equal(s.find("send").disabled, false);
});

test("Workflow Controls disclosure stays mounted, collapsed by default, and preserves active values across Profiles", async () => {
  const s = setup();
  s.profiles[0].exposedControls = [{ id: "enabled", label: "Enabled", type: "boolean", defaultValue: true },
    { id: "strength", label: "Strength", type: "float", defaultValue: 0.8 }];
  s.profiles[1].exposedControls = [{ id: "steps", label: "Steps", type: "integer", defaultValue: 24 }];
  const projectValues = new Map();
  const controlsState = profile => {
    const values = projectValues.get(profile.id) || {};
    return { profileId: profile.id, warnings: [], controls: profile.exposedControls.map(def => ({
    ...def, value: values[def.id] ?? def.defaultValue, source: Object.hasOwn(values, def.id) ? "project_default" : "profile_default",
    hasProjectOverride: Object.hasOwn(values, def.id), hasGenerationOverride: false, projectOverride: values[def.id],
  })) };
  };
  s.api.workflowControls = async (_p, _g, id) => controlsState(s.profiles.find(item => item.id === id));
  s.api.saveWorkflowControls = async (_p, _g, id, _scope, values) => {
    projectValues.set(id, { ...values });
    return controlsState(s.profiles.find(item => item.id === id));
  };
  await s.ui.refresh();
  const section = s.root.querySelector("#preparation-workflow-controls");
  const disclosure = section, summary = section.querySelector("summary");
  assert.ok(disclosure, "native disclosure exists");
  assert.equal(disclosure.tagName, "details", "the disclosure itself is the control container");
  assert.equal(section.querySelectorAll("details").length, 0, "there is no nested disclosure");
  assert.equal(disclosure.attributes.open, undefined, "fresh mounted disclosure has no open attribute");
  assert.match(summary.textContent, /^Workflow Controls \(2\)$/);
  disclosure.setAttribute("open", "");
  const strength = s.find("control-strength");
  assert.equal(strength.value, "0.8");
  strength.value = "0.8";
  await strength.emit("input");
  await s.ui.prepareSavedSelection("profile-a");
  const prepared = s.ui.prepared;
  const callsBeforeCollapse = s.calls.length;
  delete disclosure.attributes.open;
  assert.equal(s.calls.length, callsBeforeCollapse, "presentation toggle makes no API calls");
  assert.equal(s.ui.prepared, prepared, "presentation toggle retains preparation");
  assert.equal(s.root.querySelector("#preparation-workflow-controls"), disclosure, "same disclosure remains mounted");
  disclosure.setAttribute("open", "");
  assert.equal(s.find("control-strength"), strength, "same input remains mounted after re-expand");
  assert.equal(strength.value, "0.8", "draft remains in the input");
  assert.equal(s.calls.filter(call => call[0] === "saveWorkflowControls").length, 0, "collapse does not save control values");
  assert.match(summary.textContent, /Workflow Controls \(2\)/);
  const profile = s.find("profile"); profile.value = "profile-b"; await profile.emit("change");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(profile.value, "profile-b");
  assert.equal(section.querySelectorAll("details").length, 0, "profile replacement does not introduce nested disclosure");
  assert.match(summary.textContent, /^Workflow Controls \(1\)$/);
  assert.equal(disclosure.attributes.open, "", "profile refresh preserves open state");
  assert.ok(section.querySelector("#preparation-control-steps"));
  assert.equal(section.querySelector("#preparation-control-enabled"), null, "old Profile inputs are replaced");
});

test("prepared execution summary stays concise with many effective Workflow Controls", async () => {
  const s = setup();
  const controls = Array.from({ length: 46 }, (_, index) => ({ id: `control-${index}`, label: `Control ${index}`, type: "integer", defaultValue: index }));
  s.profiles[0].exposedControls = controls;
  s.api.workflowControls = async () => ({ profileId: "profile-a", warnings: [], controls: controls.map(item => ({ ...item,
    value: item.defaultValue, source: "project_default", hasProjectOverride: true, hasGenerationOverride: false })) });
  s.api.prepareWorkflow = async () => ({ profileName: "Capacity", mode: "Ref2VA", graph: {}, assetRequirements: [],
    nativeContinuation: { consume: false }, rendererSeedSettings: { mode: "fixed", fixedSeed: 3 },
    workflowControls: controls.map(item => ({ id: item.id, label: item.label, value: item.defaultValue, source: "project_default" })),
    referenceMap: [], warnings: [], information: [] });
  await s.ui.prepareSavedSelection("profile-a");
  const result = s.root.querySelector("#preparation-result");
  const summaryText = result.children.filter(child => child.tagName === "p").map(child => child.textContent).join("\n");
  assert.match(summaryText, /Ref2VA/);
  assert.doesNotMatch(summaryText, /Capacity/);
  assert.match(summaryText, /H3 prompt: not selected · Duration: 8 seconds/);
  assert.match(summaryText, /Native continuation: Not consuming/);
  assert.match(summaryText, /Renderer seed: fixed 3/);
  for (const control of controls) assert.doesNotMatch(summaryText, new RegExp(`${control.label}:`));
  assert.equal((summaryText.match(/Workflow controls:/gi) || []).length, 0);
  assert.match(result.querySelector("pre").textContent, /\"workflowControls\"/);
  const disclosure = s.root.querySelector("#preparation-workflow-controls");
  assert.equal(disclosure.querySelectorAll(".workflow-control-row").length, controls.length);
  assert.match(disclosure.querySelector("summary").textContent, /Workflow Controls \(46\)/);
});

test("Workflow Control drafts survive delayed continuation refresh, keep focused nodes, and save explicitly", async () => {
  const s = setup();
  s.profiles[0].exposedControls = [
    { id: "spectrum", label: "Spectrum Enabled", type: "boolean", defaultValue: false, target: { nodeId: "size", inputName: "enabled" } },
    { id: "megapixels", label: "Megapixels", type: "float", defaultValue: 0.7, target: { nodeId: "size", inputName: "megapixels" } },
  ];
  const projectValues = { spectrum: false }, generationValues = { "generation-b": { spectrum: false } };
  const stateFor = generationId => ({ profileId: "profile-a", warnings: [], controls: s.profiles[0].exposedControls.map(def => {
    const projectValue = projectValues[def.id], generation = generationValues[generationId] || {};
    const hasProjectOverride = Object.hasOwn(projectValues, def.id), hasGenerationOverride = Object.hasOwn(generation, def.id);
    return { ...def, value: hasGenerationOverride ? generation[def.id] : hasProjectOverride ? projectValue : def.defaultValue,
      source: hasGenerationOverride ? "generation_override" : hasProjectOverride ? "project_default" : "profile_default",
      hasProjectOverride, hasGenerationOverride, projectOverride: projectValue, generationOverride: generation[def.id] };
  }) });
  s.api.workflowControls = async (_projectId, generationId) => stateFor(generationId);
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a" }, executions: [], resolution: {} });
  s.api.saveWorkflowControls = async (_projectId, generationId, _profile, scope, values) => {
    if (scope === "project") Object.assign(projectValues, values);
    else generationValues[generationId] = { ...(generationValues[generationId] || {}), ...values };
    return stateFor(generationId);
  };
  await s.ui.refresh();
  s.find("control-scope").value = "generation";
  await s.find("control-scope").emit("change");

  const switchInput = s.find("control-spectrum");
  const switchRow = switchInput.closest(".workflow-control-row");
  const cleanColumns = [...switchRow.children];
  const expectedCells = ["workflow-control-label-cell", "workflow-control-value-cell", "helper workflow-control-source",
    "helper workflow-control-unsaved is-clean", "workflow-control-save", "workflow-control-inherit"];
  assert.deepEqual(switchRow.children.map(child => child.className), expectedCells);
  assert.equal(switchRow.querySelector(".workflow-control-unsaved").className.includes("is-clean"), true);
  const numericInput = s.find("control-megapixels");
  const numericRow = numericInput.closest(".workflow-control-row");
  assert.deepEqual(numericRow.children.map(child => child.className), expectedCells);
  assert.equal(numericRow.children[1].children[0], numericInput);
  const rows = s.find("control-rows");
  assert.equal(switchInput.type, "checkbox");
  assert.equal(switchInput.attributes.role, "switch");
  assert.equal(switchInput.attributes["aria-label"], "Spectrum Enabled");
  assert.equal(switchInput.parentElement.className, "mini-switch workflow-control-switch");
  assert.deepEqual(switchInput.parentElement.children.map(child => child.className),
    ["mini-switch-input", "mini-switch-track", "mini-switch-state"]);
  assert.ok(s.root.querySelectorAll("label").some(label => label.attributes.for === switchInput.id && label.textContent === "Spectrum Enabled"));
  assert.ok(s.root.querySelectorAll("label").some(label => label.attributes.for === numericInput.id && label.textContent === "Megapixels"));
  assert.equal(switchInput.checked, false);
  switchInput.checked = true;
  await switchInput.emit("change");
  assert.equal(s.find("control-spectrum"), switchInput);
  assert.equal(switchInput.closest(".workflow-control-row"), switchRow);
  assert.deepEqual([...switchRow.children], cleanColumns);
  assert.equal(switchInput.checked, true);
  assert.equal(switchInput.parentElement.querySelector(".mini-switch-state").textContent, "On");
  assert.equal(switchInput.closest(".workflow-control-row").querySelector(".workflow-control-unsaved").className.includes("is-clean"), false);

  s.doc.activeElement = numericInput;
  numericInput.value = "12.";
  await numericInput.emit("input");
  assert.equal(numericInput.closest(".workflow-control-row").querySelector(".workflow-control-unsaved").className.includes("is-clean"), false);

  const delayed = deferred(), started = deferred();
  s.api.workflowControls = async (_projectId, generationId) => {
    if (generationId === "generation-a") { started.resolve(); return delayed.promise; }
    return stateFor(generationId);
  };
  const continuationRefresh = s.ui.refreshContinuation();
  await continuationRefresh;
  await started.promise;
  assert.equal(s.find("control-spectrum"), switchInput);
  assert.equal(s.find("control-megapixels"), numericInput);
  assert.equal(s.find("control-rows"), rows);
  assert.equal(s.doc.activeElement, numericInput);
  assert.equal(numericInput.value, "12.");
  delayed.resolve(stateFor("generation-a"));
  await new Promise(resolve => setImmediate(resolve));
  s.api.workflowControls = async (_projectId, generationId) => stateFor(generationId);
  assert.equal(s.find("control-spectrum"), switchInput);
  assert.equal(switchInput.checked, true);
  assert.equal(s.find("control-megapixels"), numericInput);
  assert.equal(numericInput.value, "12.");
  assert.equal(s.doc.activeElement, numericInput);
  assert.match(switchInput.closest(".workflow-control-row").textContent, /Unsaved change/);
  assert.match(numericInput.closest(".workflow-control-row").textContent, /Unsaved change/);

  const spectrumRow = switchInput.closest(".workflow-control-row");
  const save = spectrumRow.querySelectorAll("button").find(button => button.textContent === "Save for this Generation");
  await save.emit("click");
  assert.deepEqual(generationValues["generation-a"], { spectrum: true });
  assert.equal(switchInput.closest(".workflow-control-row").querySelector(".workflow-control-source").textContent, "This Generation");
  assert.equal(switchInput.closest(".workflow-control-row").querySelector(".workflow-control-unsaved").className.includes("is-clean"), true);
  await s.ui.refreshContinuation();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(switchInput.checked, true);
  assert.equal(switchInput.closest(".workflow-control-row").querySelector(".workflow-control-source").textContent, "This Generation");

  s.context.generationId = "generation-b";
  const contextRefresh = s.ui.sync();
  await Promise.all(contextRefresh);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.find("control-spectrum").checked, false);
  assert.equal(s.find("control-spectrum").closest(".workflow-control-row").querySelector(".workflow-control-source").textContent, "This Generation");
});

test("late Workflow Control reads from an old Generation cannot replace current controls", async () => {
  const s = setup();
  s.profiles[0].exposedControls = [{ id: "steps", label: "Steps", type: "integer", defaultValue: 20,
    target: { nodeId: "sampling", inputName: "steps" } }];
  const oldRead = deferred(), oldStarted = deferred();
  const readGenerations = [];
  s.api.workflowControls = async (_projectId, generationId) => {
    readGenerations.push(generationId);
    if (generationId === "generation-b") { oldStarted.resolve(); return oldRead.promise; }
    const value = generationId === "generation-c" ? 33 : 20;
    return { profileId: "profile-a", warnings: [], controls: [{ ...s.profiles[0].exposedControls[0], value,
      source: generationId === "generation-c" ? "generation_override" : "profile_default",
      hasProjectOverride: false, hasGenerationOverride: generationId === "generation-c", projectOverride: undefined,
      generationOverride: generationId === "generation-c" ? value : undefined }] };
  };
  await s.ui.refresh();
  s.find("control-scope").value = "generation";
  await s.find("control-scope").emit("change");
  s.context.generationId = "generation-b";
  const generationB = s.ui.sync();
  await oldStarted.promise;
  s.context.generationId = "generation-c";
  const generationC = s.ui.sync();
  await Promise.all(generationC);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.find("control-steps").value, "33", `reads=${JSON.stringify(readGenerations)} context=${s.context.generationId} source=${s.find("control-steps").closest(".workflow-control-row").querySelector(".workflow-control-source").textContent}`);
  oldRead.resolve({ profileId: "profile-a", warnings: [], controls: [{ ...s.profiles[0].exposedControls[0], value: 22,
    source: "generation_override", hasProjectOverride: false, hasGenerationOverride: true, projectOverride: undefined, generationOverride: 22 }] });
  await Promise.all(generationB);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.find("control-steps").value, "33");
  assert.equal(s.find("control-steps").closest(".workflow-control-row").querySelector(".workflow-control-source").textContent, "This Generation");
});

test("H3 Aspect Ratio summary appears only for semantic binding and generic sibling controls stay editable", async () => {
  const s = setup();
  const profile = s.profiles[0];
  profile.exposedControls = [{ id: "megapixels", label: "Megapixels", type: "float", defaultValue: 0.7,
    target: { nodeId: "size-node", inputName: "megapixels" } }];
  profile.bindings = [{ source: { kind: "aspect_ratio" }, nodeId: "size-node", inputName: "aspect_ratio" }];
  s.api.executionProfiles = async () => s.profiles.map(({ bindings, ...summary }) => structuredClone(summary));
  s.api.executionProfile = async id => structuredClone(s.profiles.find(item => item.id === id));
  s.context.aspectRatio = "16:9";
  s.api.workflowControls = async () => ({ profileId: "profile-a", warnings: [], controls: [{
    ...profile.exposedControls[0], value: 0.7, source: "profile_default", hasProjectOverride: false,
    hasGenerationOverride: false, projectOverride: undefined, generationOverride: undefined,
  }] });
  await s.ui.refresh();
  assert.equal(s.find("resolution-summary").hidden, false);
  assert.equal(s.find("resolution-aspect").textContent, "16:9");
  assert.match(s.find("resolution-summary").textContent, /ResolutionAspect Ratio16:9H3 Setup/);
  assert.equal(s.find("control-megapixels").disabled, false);
  assert.equal(s.find("control-megapixels").parentElement.parentElement.className.includes("workflow-control-resolution-related"), true);
  s.find("control-megapixels").value = "1.0";
  await s.find("control-megapixels").emit("input");
  assert.equal(s.find("control-megapixels").closest(".workflow-control-row").querySelector(".workflow-control-unsaved").className.includes("is-clean"), false);

  const withoutBinding = setup();
  withoutBinding.profiles[0].exposedControls = [...profile.exposedControls];
  withoutBinding.api.workflowControls = async () => ({ profileId: "profile-a", warnings: [], controls: [{
    ...profile.exposedControls[0], value: 0.7, source: "profile_default", hasProjectOverride: false,
    hasGenerationOverride: false, projectOverride: undefined, generationOverride: undefined,
  }] });
  withoutBinding.context.aspectRatio = "16:9";
  await withoutBinding.ui.refresh();
  assert.equal(withoutBinding.find("resolution-summary").hidden, true);

  const aspectOnly = setup();
  aspectOnly.profiles[0].bindings = [{ source: { kind: "aspect_ratio" }, nodeId: "size-node", inputName: "aspect_ratio" }];
  aspectOnly.api.executionProfiles = async () => aspectOnly.profiles.map(({ bindings, ...summary }) => structuredClone(summary));
  aspectOnly.api.executionProfile = async id => structuredClone(aspectOnly.profiles.find(item => item.id === id));
  aspectOnly.context.aspectRatio = "4:3";
  await aspectOnly.ui.refresh();
  assert.equal(aspectOnly.find("workflow-controls").hidden, false);
  assert.equal(aspectOnly.find("resolution-summary").hidden, false);
  assert.equal(aspectOnly.find("resolution-aspect").textContent, "4:3");
  assert.equal(aspectOnly.find("control-scope").parentElement.hidden, true);
});

test("split-pane desktop scroll ownership and narrow stacked-flow CSS contract", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  assert.match(html, /<main class="workspace">\s*<aside class="left-rail">/);
  assert.match(html, /<section class="right-stage">/);
  assert.match(css, /@media \(min-width: 1101px\)[\s\S]*?html, body \{ height: 100%; overflow: hidden; \}[\s\S]*?\.workspace \{ height: 100%; min-height: 0; overflow: hidden; \}[\s\S]*?\.left-rail \{ overflow-x: hidden; overflow-y: auto; \}[\s\S]*?\.right-stage \{ position: static; overflow-x: hidden; overflow-y: auto; \}/);
  assert.match(css, /@media \(max-width: 1100px\)[\s\S]*?\.workspace \{ grid-template-columns: 1fr; height: auto; min-height: auto; overflow: visible; \}[\s\S]*?\.left-rail, \.right-stage \{ height: auto; min-height: auto; overflow: visible; \}/);
  assert.doesNotMatch(css, /direction:\s*rtl/);
});

test("background execution refresh preserves both pane scroll positions", async () => {
  const s = setup();
  await s.ui.refresh();
  s.leftPane.scrollTop = 418;
  s.rightPane.scrollTop = 327;
  const result = s.find("result");
  const replaceChildren = result.replaceChildren.bind(result);
  result.replaceChildren = (...children) => {
    const replaced = replaceChildren(...children);
    // Model browser scroll anchoring when a refresh changes content above the viewport.
    s.rightPane.scrollTop = 0;
    return replaced;
  };

  await s.ui.refreshContinuation();
  assert.equal(s.rightPane.scrollTop, 327);
  assert.equal(s.leftPane.scrollTop, 418);
});

test("pending Send stays clickable, reports progress, and queues only once", async () => {
  const s = setup(); await s.ready();
  for (const id of ["use", "adopt", "prepare", "send"]) {
    assert.match(s.find(id).className, /\bprimary\b/);
    assert.equal(s.find(id).disabled, false);
  }
  let finish, sends = 0;
  s.api.sendExecution = async () => { sends++; return new Promise(resolve => { finish = resolve; }); };
  const pending = s.find("send").emit("click");
  assert.equal(s.find("send").disabled, false);
  await s.find("send").emit("click");
  assert.equal(sends, 1);
  assert.match(s.find("message").textContent, /already in progress/);
  finish({ submitted: true, promptId: "one", connectionName: "Local ComfyUI" });
  await pending;
  assert.match(s.find("receipt").textContent, /Queued in ComfyUI/);
});

test("queue receipt renders before a deferred continuation refresh finishes", async () => {
  const s = setup(); await s.ready();
  const acceptance = deferred(), continuation = deferred();
  let sends = 0, refreshing = false;
  s.api.sendExecution = () => { sends++; return acceptance.promise; };
  s.api.continuationStatus = () => { refreshing = true; return continuation.promise; };
  const pending = s.click("send");
  assert.match(s.find("receipt").textContent, /Submitting to ComfyUI/);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued in ComfyUI/);
  acceptance.resolve({ submitted: true, promptId: "accepted-exactly-once", connectionName: "Local" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(refreshing, true);
  assert.match(s.find("receipt").textContent, /Queued in ComfyUI.*\nConnection: Local.*\nPrompt ID: accepted-exactly-once/);
  await s.click("send"); assert.equal(sends, 1);
  s.context.generationId = "generation-b"; s.ui.sync();
  continuation.resolve({ settings: {}, executions: [] });
  await pending;
  assert.doesNotMatch(s.find("receipt").textContent, /accepted-exactly-once|Queued/);
});

test("renderer outage after a pending Send never displays queue acceptance", async () => {
  const s = setup(); await s.ready();
  const outage = deferred(); let sends = 0;
  s.api.sendExecution = () => { sends++; return outage.promise; };
  const pending = s.click("send");
  assert.match(s.find("receipt").textContent, /Submitting/);
  outage.reject(new Error("Could not upload to ComfyUI; no prompt was submitted."));
  await pending;
  assert.doesNotMatch(s.find("receipt").textContent, /Queued in ComfyUI/);
  assert.match(s.find("message").textContent, /Could not upload/);
  assert.equal(sends, 1);
});

test("Send without current Prepare is clickable and explains why it cannot queue", async () => {
  const s = setup(); await s.ui.refresh();
  assert.equal(s.find("send").disabled, false);
  await s.find("send").emit("click");
  assert.match(s.find("message").textContent, /Prepare the current selection/);
  assert.equal(s.calls.filter(call => call[0] === "send").length, 0);
});

test("prepared native source and submitted renderer seed are visible together", async () => {
  const s = setup();
  const prepare = s.api.prepareWorkflow;
  s.api.prepareWorkflow = async body => ({ ...await prepare(body), rendererSeedControlled: true,
    nativeContinuation: { consume: true, segmentIndex: 2, sourceId: "12345678-aaaa-bbbb-cccc-dddddddddddd", contextFrames: 22,
      loadSegmentIndex: 1, saveSegmentIndex: 2 } });
  s.api.sendExecution = async () => ({ submitted: true, promptId: "prompt-1", connectionName: "Local ComfyUI", rendererSeed: 987 });
  await s.ready();
  assert.match(s.find("result").textContent, /Consuming predecessor.*Renderer seed: fresh random value on Send/);
  assert.match(s.find("result").querySelector("details").textContent, /Source Generation: G1.*Accepted execution: 12345678.*Context: 22 frames.*Load slot: 1.*Save slot: 2/);
  await s.click("send");
  assert.match(s.find("receipt").textContent, /Renderer seed: 987/);
});

test("renderer seed preference saves for this Generation and survives a fresh preparation view", async () => {
  const saved = { settings: { profileId: "profile-a" }, rendererSeedSettings: null, executions: [], resolution: { reason: "Ready" } };
  const configure = s => {
    s.profiles[0].rendererSeedBinding = true;
    s.api.continuationStatus = async () => structuredClone(saved);
    s.api.saveRendererSeedSettings = async (p, g, body) => {
      s.calls.push(["renderer-seed", p, g, body]); saved.rendererSeedSettings = body.settings;
      return structuredClone(saved);
    };
  };
  const s = setup(); configure(s); await s.ready();
  assert.equal(s.find("seed-mode").value, "random");
  assert.equal(s.find("seed-value").parentElement.hidden, true);
  s.find("seed-mode").value = "fixed"; await s.find("seed-mode").emit("change");
  assert.equal(s.find("seed-value").parentElement.hidden, false);
  s.find("seed-value").value = "12345"; await s.find("seed-value").emit("input");
  assert.equal(s.ui.prepared, null);
  await s.click("seed-save");
  assert.deepEqual(s.calls.find(row => row[0] === "renderer-seed")[3],
    { profileId: "profile-a", settings: { mode: "fixed", fixedSeed: 12345 } });
  const reopened = setup(); configure(reopened); await reopened.ui.refresh();
  assert.equal(reopened.find("seed-mode").value, "fixed");
  assert.equal(reopened.find("seed-value").value, 12345);
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  assert.match(html, /Prompt-writing seed/);
  assert.match(html, /prompt-writing LLM only, not ComfyUI rendering/);
});

test("unbound renderer profile offers workflow default and cannot save controlled mode", async () => {
  const s = setup();
  s.api.continuationStatus = async () => ({ settings: {}, rendererSeedSettings: null, executions: [], resolution: {} });
  s.api.saveRendererSeedSettings = async () => { throw new Error("Unexpected controlled save"); };
  await s.ui.refresh();
  assert.equal(s.find("seed-mode").value, "workflow_default");
  assert.equal(s.find("seed-mode").children.find(x => x.value === "random").disabled, true);
  assert.match(s.root.textContent, /Add a renderer_seed binding/);
});

test("native attempt removal and bulk cleanup refresh the selector without erasing renderer history", async () => {
  const s = setup();
  s.profiles[0].nativeContinuation = { contextFrames: 22, contextGrid: { minimum: 5, step: 17, alignment: "reject" },
    audioContinuity: false, audioEnabled: false, inputs: {} };
  const status = { settings: { profileId: "profile-a" }, acceptedId: "accepted", executions: [
    { id: "accepted", status: "available", renderSubmissionId: "send-a" },
    { id: "pending", status: "pending", renderSubmissionId: "send-b" },
    { id: "unavailable", status: "unavailable", renderSubmissionId: "send-c" },
    { id: "unavailable-2", status: "unavailable", renderSubmissionId: "send-c" },
  ], renderSubmissions: [{ id: "send-a" }, { id: "send-b" }, { id: "send-c" }], resolution: { reason: "Ready" } };
  s.api.continuationStatus = async () => structuredClone(status);
  s.api.removeContinuation = async (p, g, id) => {
    s.calls.push(["remove-native", p, g, id]); status.executions = status.executions.filter(row => row.id !== id);
    return structuredClone(status);
  };
  s.api.clearUnavailableContinuations = async () => {
    status.executions = status.executions.filter(row => row.status !== "unavailable");
    return { ...structuredClone(status), removedCount: 2 };
  };
  await s.ui.refresh();
  assert.equal(s.find("native").querySelectorAll(".native-attempt").length, 1);
  const accepted = s.find("native").querySelectorAll(".native-attempt")[0];
  assert.equal(accepted.querySelectorAll("button").some(x => x.textContent === "Remove native attempt"), false);
  const selector = s.root.querySelector("#preparation-native-attempt"); selector.value = "pending"; await selector.emit("change");
  const pending = s.find("native").querySelectorAll(".native-attempt")[0];
  assert.match(pending.textContent, /does not cancel the ComfyUI job/);
  await pending.querySelectorAll("button").find(x => x.textContent === "Remove native attempt").emit("click");
  assert.equal(s.find("native").querySelectorAll(".native-attempt").length, 1);
  assert.equal(status.renderSubmissions.length, 3);
  const clear = s.find("native").querySelectorAll("button").find(x => x.textContent === "Clear unavailable attempts");
  await clear.emit("click");
  assert.equal(s.find("native").querySelectorAll(".native-attempt").length, 1);
  assert.equal(s.find("native").querySelectorAll("button").some(x => x.textContent === "Clear unavailable attempts"), false);
  assert.match(s.find("message").textContent, /Unavailable native attempts removed/);
  assert.equal(status.renderSubmissions.length, 3);
});

test("native settings, explicit confirmation and execution fingerprint use server APIs", async () => {
  const s = setup();
  s.profiles[0].nativeContinuation = { contextFrames: 25, contextGrid: { minimum: 1, step: 4, alignment: "ceil" },
    audioContinuity: false, audioEnabled: false, inputs: {} };
  let status = { settings: { profileId: "profile-a", audioEnabled: true }, acceptedId: null,
    executions: [{ id: "attempt-a", renderSubmissionId: "submission-a", createdAt: "2026-09-22T00:08:00Z", status: "available",
      renderSubmission: { id: "submission-a", createdAt: "2026-09-22T00:08:00Z", comfyPromptId: "prompt-a" } }],
    renderSubmissions: [{ id: "submission-a", createdAt: "2026-09-22T00:08:00Z", comfyPromptId: "prompt-a" }],
    resolution: { reason: "First Generation: save only" } };
  s.api.continuationStatus = async () => structuredClone(status);
  s.api.saveContinuationSettings = async (p, g, body) => {
    s.calls.push(["native-settings", p, g, body]); status.settings = body; return structuredClone(status);
  };
  s.api.decideContinuation = async (p, g, id, body) => {
    s.calls.push(["native-confirm", p, g, id, body]); status.acceptedId = id;
    status.executions[0].status = body.status; return structuredClone(status);
  };
  const prepare = s.api.prepareWorkflow;
  s.api.prepareWorkflow = async body => ({ ...await prepare(body), executionFingerprint: "f".repeat(64) });
  await s.ready();
  assert.match(s.find("native").textContent, /ComfyUI send 1/);
  assert.match(s.find("native").textContent, /Latest/);
  const save = s.find("native").querySelectorAll("button").find(x => x.textContent === "Save continuation settings");
  await save.emit("click");
  assert.equal(s.calls.find(x => x[0] === "native-settings")[3].audioEnabled, false);
  assert.equal(s.find("send").disabled, false);
  const confirm = s.find("native").querySelectorAll("button").find(x => x.textContent === "Accept native state");
  await confirm.emit("click");
  assert.deepEqual(s.calls.find(x => x[0] === "native-confirm").slice(3), ["attempt-a", { status: "available", accept: true }]);
  assert.match(s.find("native").textContent, /Accepted/);
  assert.match(s.find("native").textContent, /Native state: Available/);
  await s.click("prepare"); await s.click("send");
  assert.equal(s.calls.find(x => x[0] === "send")[1].expectedExecutionFingerprint, "f".repeat(64));
});

test("continuation status refresh notifies the handoff manifest to re-resolve warnings", async () => {
  let refreshes = 0;
  const s = setup({ onRoutingChanged: () => { refreshes += 1; } });
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a" }, executions: [],
    resolution: { status: "native_available", native: { consume: true, requiresTailReference: false } } });
  await s.ui.refreshContinuation();
  assert.equal(refreshes, 1);
});

test("browsing a Workflow Profile never changes saved continuity until explicitly applied", async () => {
  const s = setup();
  s.profiles.push({ id: "profile-c", name: "Native alternative", compatibleModes: ["Ref2VA"], nativeContinuation: {
    contextFrames: 22, contextGrid: { minimum: 5, step: 17, alignment: "reject" }, audioContinuity: true, audioEnabled: true, inputs: {} } });
  let status = { settings: { profileId: "profile-a" }, acceptedId: null, executions: [], resolution: { reason: "Ready" } };
  s.api.continuationStatus = async () => structuredClone(status);
  s.api.saveContinuationSettings = async (p, g, body) => {
    s.calls.push(["native-settings", p, g, body]); status.settings = body; return structuredClone(status);
  };
  await s.ui.refresh();
  s.find("profile").value = "profile-c";
  await s.find("profile").emit("change");
  assert.equal(s.calls.filter(x => x[0] === "native-settings").length, 0);
  assert.equal(s.find("apply-profile").hidden, false);
  assert.match(s.find("native").textContent, /Browsing a different Profile/);
  await s.click("apply-profile");
  assert.deepEqual(s.calls.find(x => x[0] === "native-settings")[3], { profileId: "profile-c" });
});

test("F4 Send uses only IDs and displays a transient queue receipt, then requires Prepare", async () => {
  const s = setup(); await s.ready(); await s.click("send");
  assert.deepEqual(s.calls.find(c => c[0] === "send")[1], { projectId: "project-a", generationId: "generation-a",
    profileId: "profile-a", connectionId: "connection-a" });
  assert.match(s.find("receipt").textContent, /Queued in ComfyUI.*\nConnection: Local ComfyUI.*\nPrompt ID: expected-prompt-id.*\nQueue number: 9/);
  assert.equal(s.find("send").disabled, false);
  assert.notEqual(s.find("result").querySelector("details").open, true);
  assert.equal(s.root.querySelector("progress"), null);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
});

test("accepted Send notifies durable outputs even if authoring becomes dirty during transport", async () => {
  const recorded = [];
  const s = setup({ onSubmitted: (response, context) => recorded.push([response, context]) });
  await s.ready(); let finish;
  s.api.sendExecution = () => new Promise(resolve => finish = resolve);
  const pending = s.click("send");
  s.context.dirty = true; s.ui.sync();
  finish({ submitted: true, promptId: "accepted", renderSubmissionId: "durable" }); await pending;
  assert.equal(recorded[0][0].renderSubmissionId, "durable");
  assert.equal(recorded[0][1].generationId, "generation-a");
});

test("accepted Send with provenance failure shows warning and never retries", async () => {
  const s = setup(); await s.ready(); let calls = 0;
  s.api.sendExecution = async () => { calls++; return { submitted: true, promptId: "accepted", connectionName: "Local",
    provenanceWarning: "ComfyUI accepted this job, but its Project provenance could not be saved." }; };
  await s.click("send");
  assert.match(s.find("message").textContent, /accepted this job/);
  assert.match(s.find("receipt").textContent, /Prompt ID: accepted/);
  assert.equal(s.find("send").disabled, false); assert.equal(calls, 1);
});

test("F4 double click sends once while busy and a late receipt cannot follow a Generation switch", async () => {
  const s = setup(); await s.ready(); let finish, count = 0;
  s.api.sendExecution = () => { count++; return new Promise(resolve => { finish = resolve; }); };
  const pending = s.find("send").emit("click");
  assert.equal(s.find("send").disabled, false);
  await s.find("send").emit("click"); assert.equal(count, 1);
  s.context.generationId = "other"; s.ui.sync();
  finish({ submitted: true, promptId: "old", connectionName: "old" }); await pending;
  assert.doesNotMatch(s.find("receipt").textContent, /Queued|old/);
});

test("F4 accepted late Send notifies Project mutation receipt while preserving current Generation UI", async () => {
  const receipts = [];
  const s = setup({ onSubmitted: (response, context) => receipts.push([response, context]) }); await s.ready();
  let finish; s.api.sendExecution = () => new Promise(resolve => { finish = resolve; });
  const pending = s.find("send").emit("click");
  s.context.generationId = "generation-b"; s.ui.sync();
  finish({ submitted: true, promptId: "accepted-g1", projectFingerprint: "project-after-g1" }); await pending;
  assert.equal(receipts.length, 1);
  assert.equal(receipts[0][0].projectFingerprint, "project-after-g1");
  assert.equal(receipts[0][1].generationId, "generation-a");
  assert.doesNotMatch(s.find("receipt").textContent, /accepted-g1/);
});

for (const field of ["projectId", "generationId", "mode"]) {
  test(`F4 ${field} change clears send readiness and receipt`, async () => {
    const s = setup(); await s.ready(); await s.click("send");
    s.context[field] = field === "dirty" ? true : "changed"; s.ui.sync();
    assert.equal(s.find("send").disabled, false);
    assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
  });
}

test("F4 Connection change retains offline graph but clears receipt and requires Prepare", async () => {
  const s = setup(); await s.ready(); const graph = s.ui.prepared;
  await s.click("send"); await s.find("connection").emit("change");
  assert.equal(s.ui.prepared, graph); assert.equal(s.find("send").disabled, false);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
});

test("F4 Connection deletion or mutation refreshes and disables Send", async () => {
  const s = setup(); await s.ready();
  s.api.executionConnections = async () => [{ id: "connection-a", name: "Renamed", baseUrl: "http://new" }];
  await s.ui.refreshConnections(); assert.match(s.find("connection").textContent, /Renamed/);
  assert.equal(s.find("send").disabled, false);
  await s.click("prepare"); await s.click("send");
  s.api.executionConnections = async () => []; await s.ui.refreshConnections();
  assert.equal(s.find("connection").value, ""); assert.equal(s.find("send").disabled, false);
  assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
});

test("F4 multiple Connections require an explicit choice", async () => {
  const s = setup();
  s.api.executionConnections = async () => [{ id: "a", name: "One" }, { id: "b", name: "Two" }];
  await s.ready(); assert.equal(s.find("connection").value, ""); assert.equal(s.find("send").disabled, false);
  s.find("connection").value = "b"; await s.find("connection").emit("change");
  assert.equal(s.find("send").disabled, false);
  await s.click("prepare"); assert.equal(s.find("send").disabled, false);
});

for (const category of ["Connections", "Profiles"]) {
  test(`F4 late ${category} refresh cannot retain readiness from an intervening Prepare`, async () => {
    const s = setup(); await s.ready(); let finish;
    s.api[`execution${category}`] = () => new Promise(resolve => { finish = resolve; });
    const pending = s.ui[`refresh${category}`]();
    await s.click("prepare"); assert.equal(s.find("send").disabled, false);
    finish(category === "Connections" ? [{ id: "connection-a", name: "Changed", baseUrl: "http://other" }] : s.profiles);
    await pending; assert.equal(s.find("send").disabled, false);
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
    assert.equal(s.find("send").disabled, false); assert.doesNotMatch(s.find("receipt").textContent, /Queued/);
  });
}

for (const message of ["ComfyUI rejected prompt validation.",
  "Submission status is unknown. Expected prompt ID: expected-id. Check ComfyUI before sending again."]) {
  test(`F4 failure is clear and never automatically retried: ${message}`, async () => {
    const s = setup(); await s.ready(); let count = 0;
    s.api.sendExecution = async () => { count++; throw new Error(message); };
    await s.click("send"); assert.equal(count, 1);
    assert.equal(s.find("message").textContent, message); assert.equal(s.find("send").disabled, false);
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
  assert.match(s.find("selection").textContent, /Selected execution prompt is current/);
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

test("context replacement does not read a superseded Generation", async () => {
  const s = setup();
  s.api.executionRouting = async (p, g) => { s.calls.push(["routing", p, g]); return { routing: { assets: {} }, referenceMap: [], assets: [] }; };
  await s.ui.refresh(); s.calls.length = 0;
  s.context.saved = false; s.context.generationId = "old-generation"; s.ui.sync(); await Promise.resolve();
  assert.deepEqual(s.calls, []);
  s.context.saved = true; s.context.generationId = "generation-b"; s.ui.sync(); await s.ui.refresh();
  assert.ok(s.calls.filter(call => ["status", "routing"].includes(call[0])).every(call => call[2] === "generation-b"));
});

test("app adoption gates component context until Project and Generation are installed", async () => {
  const text = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(text, /state\.contextReplacing = true;/);
  assert.match(text, /saved: state\.activeProjectType === "filesystem" && !state\.contextReplacing/);
  assert.match(text, /state\.contextReplacing = false;/);
});

test("Use for execution sends exact output and provenance and Clear removes it", async () => {
  const s = setup(); await s.ui.refresh();
  assert.match(s.find("selection").textContent, /No generated prompt/);
  await s.click("use"); assert.match(s.find("selection").textContent, /Selected execution prompt is current/);
  assert.doesNotMatch(s.find("prepare-hint").textContent, /Use for execution/);
  assert.deepEqual(s.calls.find(c => c[0] === "select")[3], { prompt: "completed prompt", sourceFingerprint: "abc" });
  await s.click("clear"); assert.match(s.find("selection").textContent, /No generated prompt/);
  assert.equal(s.find("prepare").disabled, false);
  assert.equal(s.find("use").disabled, false);
  await s.click("use"); assert.equal(s.calls.filter(c => c[0] === "select").length, 2);
});

test("Prepare selects the visible invalid candidate exactly and Send follows the prepared selection", async () => {
  const s = setup();
  const prompt = "<d>[Japanese] 君はとても美しいね。</d>\n<Audio 1>\n  ";
  s.outputValue({ prompt, sourceFingerprint: "abc", validation: { valid: false,
    findings: [{ severity: "ERROR", code: "dangling_asset" }] }, generationStatus: "complete" });
  s.api.prepareWorkflow = async body => {
    s.calls.push(["prepare", body]);
    const selected = s.statuses.get("generation-a").selected;
    return { profileName: "Capacity", mode: "Ref2VA", graph: { h3: { inputs: { prompt: selected.prompt } } },
      assetRequirements: [], resolutions: [], referenceMap: [], warnings: [], sentToComfyUI: false };
  };
  await s.ui.refresh();
  await s.click("prepare");
  assert.equal(s.calls.filter(c => c[0] === "select").length, 1);
  assert.equal(s.calls.filter(c => c[0] === "prepare").length, 1);
  assert.equal(s.ui.prepared.graph.h3.inputs.prompt, prompt);
  assert.equal(s.statuses.get("generation-a").selected.prompt, prompt);
  await s.click("send");
  assert.equal(s.calls.filter(c => c[0] === "send").length, 1);
});

test("Prepare refreshes stale selection and then binds manual candidate bytes", async () => {
  const s = setup(); await s.ui.refresh(); await s.click("use");
  s.statuses.get("generation-a").stale = true;
  await s.ui.refreshSelection();
  assert.match(s.find("selection").textContent, /different saved authoring/);
  await s.click("prepare");
  assert.equal(s.calls.filter(c => c[0] === "select").length, 2);
  const manual = "Exact edited H3\n  with trailing spaces  ";
  s.outputValue(markOutputManuallyEdited(s.currentOutput(), manual)); s.ui.sync();
  await s.click("prepare");
  assert.equal(s.calls.filter(c => c[0] === "manual-adopt").length, 1);
  assert.equal(s.statuses.get("generation-a").selected.prompt, manual);
  assert.equal(s.calls.filter(c => c[0] === "prepare").length, 2);
});

test("Prepare adopts a stale visible History candidate with History provenance", async () => {
  const s = setup(); s.setCurrentFingerprint("b".repeat(64));
  const prompt = "historical prompt\n  exact bytes ";
  s.outputValue(historyOutput({ prompt, sourceFingerprint: "a".repeat(64),
    generationStatus: "complete", mode: "Ref2VA" }));
  await s.ui.refresh(); await s.click("prepare");
  assert.equal(s.calls.filter(c => c[0] === "adopt").length, 1);
  assert.equal(s.statuses.get("generation-a").selected.prompt, prompt);
  assert.equal(s.statuses.get("generation-a").selected.sourceKind, "adopted_history");
});

test("an edited candidate is distinct until explicit update, then adopts the exact text returned by the server", async () => {
  const s = setup(); await s.ui.refresh(); await s.click("use"); await s.click("prepare");
  assert.equal(s.find("send").disabled, false);
  s.outputValue(markOutputManuallyEdited(s.currentOutput(), "Exact candidate B\n  ")); s.ui.sync();
  assert.match(s.find("selection").textContent, /differs from Selected execution prompt/);
  assert.equal(s.find("use").textContent, "Update execution selection");
  assert.equal(s.find("selected").textContent, "Selected execution promptcompleted prompt");
  await s.click("use");
  assert.equal(s.ui.prepared, null);
  assert.equal(s.find("send").disabled, false);
  assert.deepEqual(s.calls.find(c => c[0] === "manual-adopt")[3], { prompt: "Exact candidate B\n  " });
  assert.match(s.find("selection").textContent, /Manual edit bound/);
  assert.equal(s.find("selected").textContent, "Selected execution promptExact candidate B\n  ");
  assert.equal(s.find("use").textContent, "Use for execution");
});

test("failed candidate update preserves the old selected prompt and the editable candidate", async () => {
  const s = setup({ rejectManualEdit: true }); await s.ui.refresh(); await s.click("use");
  s.outputValue(markOutputManuallyEdited(s.currentOutput(), "Invalid candidate B")); s.ui.sync();
  await s.click("use");
  assert.match(s.find("message").textContent, /H3 validation failed/);
  assert.equal(s.currentOutput().prompt, "Invalid candidate B");
  assert.equal(s.find("selected").textContent, "Selected execution promptcompleted prompt");
  assert.equal(s.find("use").textContent, "Update execution selection");
});

test("Clear selection removes authority without replacing the current edited candidate", async () => {
  const s = setup(); await s.ui.refresh(); await s.click("use");
  s.outputValue(markOutputManuallyEdited(s.currentOutput(), "Candidate B")); s.ui.sync();
  await s.click("clear");
  assert.equal(s.currentOutput().prompt, "Candidate B");
  assert.equal(s.find("selected").textContent, "Selected execution prompt");
  assert.equal(s.find("use").textContent, "Use for execution");
  assert.equal(s.find("use").disabled, false);
});

test("Revision, Repair, and chained manual edits explicitly update the execution candidate", async () => {
  const s = setup(); await s.ui.refresh(); await s.click("use");
  const output = s.currentOutput();
  const revision = { originIndex: 0, originOutput: output, sourcePrompt: output.prompt,
    candidatePrompt: "Revision B", validation: { valid: true },
    sourceFingerprint: "abc", authoringPath: "one_shot" };
  assert.equal(applyRevisionCandidate([output], 0, revision), true);
  s.ui.sync();
  assert.equal(s.find("selected").textContent, "Selected execution promptcompleted prompt");
  await s.click("use");
  assert.equal(s.calls.filter(call => call[0] === "select").at(-1)[3].prompt, "Revision B");

  s.outputValue(markOutputManuallyEdited(s.currentOutput(), "Manual C after Revision")); s.ui.sync();
  await s.click("use");
  assert.equal(s.calls.filter(call => call[0] === "manual-adopt").at(-1)[3].prompt, "Manual C after Revision");

  s.outputValue({ ...s.currentOutput(), prompt: "Repair D", sourceFingerprint: "abc",
    validation: { valid: true }, origin: undefined }); s.ui.sync();
  await s.click("use");
  assert.equal(s.calls.filter(call => call[0] === "select").at(-1)[3].prompt, "Repair D");

  s.outputValue(markOutputManuallyEdited(s.currentOutput(), "Manual E after Repair")); s.ui.sync();
  await s.click("use");
  assert.equal(s.calls.filter(call => call[0] === "manual-adopt").at(-1)[3].prompt, "Manual E after Repair");
  assert.equal(s.find("selected").textContent, "Selected execution promptManual E after Repair");
});

test("provenance-preserving History can be selected, cleared, and selected again", async () => {
  const s = setup(), fingerprint = "a".repeat(64);
  s.setCurrentFingerprint(fingerprint);
  const record = historyRecordFromOutput({ prompt: "exact historical H3", validation: { valid: true },
    sourceFingerprint: fingerprint, generationStatus: "complete", referenceMap: [] }, { mode: "Ref2VA" });
  s.outputValue(historyOutput(record));
  await s.ui.refresh(); assert.equal(s.find("use").disabled, false);
  assert.equal(s.find("adopt").hidden, true);
  await s.click("use");
  assert.deepEqual(s.calls.find(c => c[0] === "select")[3],
    { prompt: "exact historical H3", sourceFingerprint: fingerprint, referenceMap: [] });
  assert.equal(s.find("prepare").disabled, false);
  await s.click("prepare"); assert.ok(s.ui.prepared);
  await s.click("clear");
  assert.equal(s.find("prepare").disabled, false);
  assert.equal(s.find("use").disabled, false);
  await s.click("use"); assert.equal(s.calls.filter(c => c[0] === "select").length, 2);
});

test("legacy and stale History require explicit adoption while matching History uses normal selection", async () => {
  const s = setup(), fingerprint = "a".repeat(64);
  s.setCurrentFingerprint("b".repeat(64));
  const record = historyRecordFromOutput({ prompt: "visible historical H3", validation: { valid: true },
    sourceFingerprint: fingerprint, generationStatus: "complete" }, {});
  s.outputValue(historyOutput(record)); await s.ui.refresh();
  assert.equal(s.find("use").disabled, false);
  assert.equal(s.find("adopt").disabled, false);
  assert.match(s.find("candidate-hint").textContent, /older Generation snapshot/);
  s.outputValue(historyOutput({ prompt: "legacy H3", validation: { valid: true }, mode: "Ref2VA" })); s.ui.sync();
  assert.equal(s.find("use").disabled, false);
  assert.equal(s.find("adopt").disabled, false);
  assert.match(s.find("candidate-hint").textContent, /predates saved generation provenance/);
  s.setCurrentFingerprint(fingerprint); await s.ui.refreshSelection();
  const edited = markOutputManuallyEdited(historyOutput(record), `${record.prompt} changed`);
  s.outputValue(edited); s.ui.sync();
  assert.equal(s.find("use").disabled, false);
  assert.equal(s.find("adopt").hidden, true);
  assert.match(s.find("candidate-hint").textContent, /selected exactly as shown/);
  await s.click("use");
  assert.deepEqual(s.calls.find(c => c[0] === "manual-adopt")[3], { prompt: `${record.prompt} changed` });
  assert.equal(s.find("selection").textContent.includes("Manual edit bound"), true);
  await s.click("clear");
  assert.equal(s.find("use").disabled, false);
  await s.click("use");
  assert.equal(s.calls.filter(c => c[0] === "manual-adopt").length, 2);
  s.outputValue(historyOutput(record)); s.ui.sync();
  assert.equal(s.find("use").disabled, false);
  assert.equal(s.find("adopt").hidden, true);
});

test("legacy History adoption creates the ordinary selection, prepares, clears and can be adopted again", async () => {
  const s = setup();
  const history = historyOutput({ prompt: "legacy exact H3", validation: { valid: true }, mode: "Ref2VA" });
  s.outputValue(history); await s.ui.refresh();
  assert.equal(s.find("use").disabled, false);
  assert.equal(s.find("adopt").disabled, false);
  assert.match(s.find("candidate-hint").textContent, /binds its exact text/);
  await s.click("adopt");
  assert.deepEqual(s.calls.find(c => c[0] === "adopt")[3], { prompt: "legacy exact H3" });
  assert.equal(s.find("prepare").disabled, false);
  await s.click("prepare"); assert.ok(s.ui.prepared);
  await s.click("clear");
  assert.equal(s.find("adopt").disabled, false);
  await s.click("adopt");
  assert.equal(s.calls.filter(c => c[0] === "adopt").length, 2);
});

test("History adoption stays actionable for dirty and mismatched-mode items", () => {
  const output = historyOutput({ prompt: "legacy H3", mode: "T2VA" });
  const context = { saved: true, projectId: "p", generationId: "g", dirty: false, mode: "Ref2VA" };
  assert.equal(historyAdoptionState(context, output).eligible, true);
  assert.match(historyAdoptionState(context, output).reason, /adoption remains available/);
  assert.equal(historyAdoptionState({ ...context, dirty: true }, { ...output, historyMode: undefined }).eligible, true);
  assert.equal(historyAdoptionState({ ...context, saved: false }, output).eligible, false);
  assert.equal(historyAdoptionState(context, markOutputManuallyEdited(output, "edited")).visible, false);
});

test("dirty authoring keeps adoption, profile and Prepare actionable with backend errors", async () => {
  const s = setup();
  s.api.continuationStatus = async () => ({ settings: {}, executions: [], resolution: { reason: "Choose a profile." } });
  s.api.adoptHistoryExecutionPrompt = async () => { throw new Error("Saved Generation differs; reconcile edits first."); };
  s.api.adoptManualEditExecutionPrompt = async () => { throw new Error("Saved Generation differs; reconcile edits first."); };
  s.api.saveContinuationSettings = async () => { throw new Error("Profile is incompatible with this Generation."); };
  s.api.prepareWorkflow = async () => { throw new Error("Selected prompt is stale for the saved Generation."); };
  await s.ui.refresh(); await s.click("use");
  s.context.dirty = true; await s.ui.refresh();
  assert.equal(s.find("send").disabled, false);
  s.outputValue({ ...s.output, origin: "manual_edit", sourceFingerprint: null, validation: null }); s.ui.sync();
  await s.click("use");
  assert.match(s.find("message").textContent, /reconcile edits/);
  assert.equal(s.currentOutput().prompt, s.output.prompt);
  s.outputValue(historyOutput({ prompt: "historical prompt", mode: "Ref2VA" })); s.ui.sync();
  await s.click("adopt");
  assert.match(s.find("message").textContent, /reconcile edits/);
  await s.click("apply-profile");
  assert.match(s.find("message").textContent, /incompatible/);
  await s.click("prepare");
  assert.match(s.find("message").textContent, /reconcile edits/);
  assert.equal(s.find("send").disabled, false);
});

test("manual edit drops prior validation and History/reference provenance without losing exact text", () => {
  const source = historyOutput({ prompt: "old", mode: "Ref2VA", generationStatus: "complete",
    sourceFingerprint: "a".repeat(64), referenceMap: [{ assetId: "old" }], validation: { valid: true } });
  const edited = markOutputManuallyEdited(source, "Exact edited bytes\n  ");
  assert.equal(edited.prompt, "Exact edited bytes\n  ");
  assert.equal(edited.origin, "manual_edit");
  assert.equal(edited.sourceFingerprint, null);
  assert.equal(edited.validation, null);
  assert.equal("referenceMap" in edited, false);
  assert.equal("historyItem" in edited, false);
  assert.equal("historyMode" in edited, false);
  assert.equal(source.origin, "history");
});

test("failed manual-edit validation leaves the exact visible candidate available", async () => {
  const s = setup({ rejectManualEdit: true });
  const edited = { ...s.output, prompt: "exact invalid edit", origin: "manual_edit", sourceFingerprint: null, validation: null };
  s.outputValue(edited); await s.ui.refresh();
  assert.equal(s.find("use").disabled, false);
  await s.click("use");
  assert.match(s.find("message").textContent, /unresolved Picture 2/);
  assert.equal(s.currentOutput().prompt, "exact invalid edit");
  assert.equal(s.find("use").disabled, false);
});

test("switching fresh variations and History keeps each fingerprint attached to its own prompt", async () => {
  const s = setup(), current = "a".repeat(64);
  s.setCurrentFingerprint(current);
  const first = { prompt: "fresh A", validation: { valid: true }, sourceFingerprint: current, generationStatus: "complete" };
  const second = { prompt: "fresh B", validation: { valid: true }, sourceFingerprint: "b".repeat(64), generationStatus: "complete" };
  const entry = historyRecordFromOutput(first, {});
  const shown = showHistoryOutput([first, second], { ...entry, prompt: "History A" });
  await s.ui.refresh();
  for (const [index, eligible] of [[shown.index, true], [1, true], [0, true], [shown.index, true]]) {
    s.outputValue(shown.outputs[index]); s.ui.sync();
    assert.equal(s.find("use").disabled, false);
  }
  assert.equal(shown.outputs[0].sourceFingerprint, current);
  assert.equal(shown.outputs[1].sourceFingerprint, "b".repeat(64));
});

test("reload restores selected prompt with no outputs and no History reads", async () => {
  const s = setup(); s.outputValue(null);
  s.statuses.set("generation-a", { selected: { prompt: "durable prompt", sourceFingerprint: "abc" }, stale: false });
  await s.ui.refresh();
  assert.match(s.root.textContent, /durable prompt/); assert.equal(s.find("use").disabled, false);
  await s.click("prepare"); assert.ok(s.ui.prepared);
});

test("stale selection is visible and Prepare remains actionable", async () => {
  const s = setup(); s.statuses.set("generation-a", { selected: { prompt: "old" }, stale: true });
  await s.ui.refresh(); assert.match(s.find("selection").textContent, /different saved authoring/);
  assert.equal(s.find("prepare").disabled, false); assert.equal(s.find("clear").disabled, false);
});

test("compatible profiles filter by mode", async () => {
  const s = setup(); await s.ui.refresh();
  assert.deepEqual(s.find("profile").children.map(n => n.value), ["profile-a"]);
  s.context.mode = "T2VA"; s.ui.sync();
  assert.deepEqual(s.find("profile").children.map(n => n.value), ["profile-b"]);
});

test("Prepare summary has actual filenames, targets, inactive slots and literal warnings", async () => {
  const s = setup(); await s.ready();
  const result = s.find("result"), inspector = result.querySelector("details");
  assert.ok(inspector);
  assert.notEqual(inspector.open, true);
  assert.match(s.find("reference-summary").textContent, /Active References: 1 Pictures · 0 Audio · 0 Video/);
  assert.equal(result.children.some(child => child.tagName === "p" && /Picture 1 · cyclist\.png/.test(child.textContent)), false);
  assert.match(result.textContent, /Native continuation: Not consuming/);
  assert.match(result.textContent, /Renderer seed: workflow default/);
  assert.match(result.textContent, /H3 prompt: current · Duration: 8 seconds/);
  inspector.open = true;
  assert.equal(inspector.open, true);
  assert.match(inspector.textContent, /"label": "Picture 1"[\s\S]*?"status": "active"/);
  assert.match(inspector.textContent, /"label": "Picture 2"[\s\S]*?"status": "inactive"/);
  assert.match(s.find("result").textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(s.root.querySelector("img"), null);
  assert.match(s.find("status").textContent, /Prepared \u2014 (?:Ready to Send|Review warnings)/);
  assert.match(s.root.textContent, /Prepared offline · not sent to ComfyUI/);
  assert.ok(!s.root.querySelectorAll("button").some(b => /^(Send|Run|Queue)$/.test(b.textContent)));
  assert.deepEqual(s.calls.find(c => c[0] === "prepare")[1], { projectId: "project-a", generationId: "generation-a", profileId: "profile-a" });
});

test("prepared inspector keeps native continuation receipt details available", async () => {
  const s = setup();
  s.api.prepareWorkflow = async () => ({ profileName: "Capacity", mode: "Ref2VA", graph: {}, assetRequirements: [],
    resolutions: [], referenceMap: [], warnings: [],
    nativeContinuation: { consume: true, segmentIndex: 3, sourceId: "accepted-12345678", contextFrames: 22,
      loadSegmentIndex: 2, saveSegmentIndex: 3 },
    rendererSeedSettings: { mode: "fixed", fixedSeed: 123 }, sentToComfyUI: false });
  await s.ready();
  assert.match(s.find("result").querySelector("details").textContent, /Source Generation: G2 · Accepted execution: accepted ·/);
  assert.match(s.find("result").querySelector("details").textContent, /Context: 22 frames · Load slot: 2 · Save slot: 3/);
});

test("Prepare server errors replace success with actionable text", async () => {
  const s = setup(); await s.ready();
  s.api.prepareWorkflow = async () => { throw new Error("Picture 3 is required but missing"); };
  await s.click("prepare"); assert.equal(s.ui.prepared, null);
  assert.match(s.find("message").textContent, /Picture 3 is required/);
  assert.doesNotMatch(s.root.textContent, /Prepared \u2014 (?:Ready to Send|Review warnings)/);
});

test("G2 completed streamed H3 is selectable before History finishes, with accepted native state and no Selected Take", async () => {
  const s = setup(); s.context.generationId = "generation-b";
  s.profiles[0].nativeContinuation = { contextFrames: 22, contextGrid: { minimum: 1, step: 1, alignment: "ceil" }, audioContinuity: false, inputs: {} };
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a", contextFrames: 22 },
    acceptedId: "predecessor", executions: [], selectedTake: null,
    resolution: { reason: "Native state belongs to the accepted predecessor execution." } });
  const outputs = [{ prompt: "partial", validation: null, generationStatus: "generating" }];
  const session = { outputs };
  s.outputValue(currentExecutionOutput(outputs[0], session, outputs, false));
  await s.ui.refresh(); assert.equal(s.find("use").disabled, false);
  assert.match(s.find("candidate-hint").textContent, /can be adopted/);
  outputs[0] = { prompt: "exact streamed H3 prompt", validation: { valid: true },
    sourceFingerprint: "abc", generationStatus: "complete" };
  s.outputValue(currentExecutionOutput(outputs[0], session, outputs, false)); s.ui.sync();
  assert.equal(s.find("use").disabled, false);
  assert.match(s.find("candidate-hint").textContent, /ready for execution/);
  assert.match(s.find("native").textContent, /accepted predecessor execution/);
  await s.click("use");
  assert.deepEqual(s.calls.find(c => c[0] === "select"), ["select", "project-a", "generation-b",
    { prompt: "exact streamed H3 prompt", sourceFingerprint: "abc" }]);
  assert.match(s.find("selection").textContent, /Selected execution prompt is current/);
  assert.equal(s.find("prepare").disabled, false);
  await s.click("prepare"); assert.ok(s.ui.prepared);
});

test("non-streaming completed result remains eligible", async () => {
  const s = setup(); s.outputValue({ prompt: "repaired H3", validation: { valid: true }, sourceFingerprint: "abc" });
  await s.ui.refresh(); assert.equal(s.find("use").disabled, false);
  await s.click("use"); assert.equal(s.calls.find(c => c[0] === "select")[3].prompt, "repaired H3");
});

test("active stream exposes only its own completed variation and requires the saved fingerprint", () => {
  const output = { prompt: "valid H3", validation: { valid: true }, sourceFingerprint: "abc", generationStatus: "complete" };
  const outputs = [output], session = { outputs };
  const partial = { ...output, generationStatus: "generating" };
  assert.equal(currentExecutionOutput(partial, session, outputs, false), partial);
  assert.equal(currentExecutionOutput(output, session, [output], false), null);
  assert.equal(currentExecutionOutput(output, session, outputs, false), output);
  assert.equal(currentExecutionOutput(output, session, outputs, true), null);
  const context = { saved: true, projectId: "project-a", generationId: "generation-b", dirty: false };
  assert.equal(executionCandidateState(context, output, { currentFingerprint: "abc" }).eligible, true);
  assert.match(executionCandidateState(context, output, { currentFingerprint: "def" }).reason, /earlier saved authoring/);
  assert.match(executionCandidateState(context, output, null).reason, /can be adopted/);
});

test("candidate reasons distinguish stale, History, manual edit, dirty authoring and missing output", async () => {
  const s = setup(); await s.ui.refresh();
  const check = (output, reason, disabled = false) => { s.outputValue(output); s.ui.sync();
    assert.equal(s.find("use").disabled, false); assert.match(s.find("candidate-hint").textContent, reason); };
  check(null, /no H3 prompt to select/, true);
  check({ ...s.output, sourceFingerprint: null, origin: "history" }, /predates saved generation provenance/);
  const manual = { ...s.output, sourceFingerprint: null, origin: "manual_edit", validation: null };
  s.outputValue(manual); s.ui.sync();
  assert.equal(s.find("use").disabled, false);
  assert.match(s.find("candidate-hint").textContent, /selected exactly as shown/);
  check({ ...s.output, sourceFingerprint: "wrong" }, /earlier saved authoring/);
  s.context.dirty = true; check(s.output, /unsaved edits/);
  s.context.dirty = false; s.outputValue(s.output); s.ui.sync(); await s.ui.refreshSelection();
  assert.equal(s.find("use").disabled, false);
});

test("Generation switch and status refresh cannot transfer or erase a completed candidate", async () => {
  const s = setup(); await s.ui.refresh();
  await s.ui.refreshContinuation(); await s.ui.refreshSelection();
  assert.equal(s.find("use").disabled, false);
  s.context.generationId = "generation-b"; s.outputValue(null); s.ui.sync();
  assert.equal(s.find("use").disabled, false);
  await s.ui.refreshSelection(); assert.equal(s.find("use").disabled, false);
  s.context.generationId = "generation-a"; s.outputValue(s.output); s.ui.sync();
  await s.ui.refreshSelection(); assert.equal(s.find("use").disabled, false);
});

test("12 native attempts use one labelled selector and one contextual details area with exact submission links", async () => {
  const s = setup();
  s.profiles[0].nativeContinuation = { contextFrames: 25, contextGrid: { minimum: 1, step: 4, alignment: "ceil" }, audioContinuity: false, audioEnabled: false, inputs: {} };
  const submissions = Array.from({ length: 12 }, (_, index) => ({ id: `send-${index}`, createdAt: `2026-10-03T00:${String(index).padStart(2, "0")}:00Z`, comfyPromptId: `prompt-${index}`,
    jobStatus: index === 10 ? "cancelled" : "completed", outputFilenames: index === 9 ? ["a.mp4", "b.mp4"] : [`output-${index}.mp4`] }));
  let status = { settings: { profileId: "profile-a" }, acceptedId: "attempt-0", executions: submissions.map((submission, index) => ({
    id: `attempt-${index}`, renderSubmissionId: submission.id, createdAt: submission.createdAt, status: index === 10 ? "unavailable" : "available", renderSubmission: submission,
  })), renderSubmissions: submissions, resolution: { reason: "Ready" } };
  // The latest native attempt deliberately belongs to Send 1: only IDs may join them.
  status.executions[11].renderSubmissionId = submissions[0].id;
  status.executions[11].renderSubmission = submissions[11]; // Ignore mismatched embedded provenance; join Send 1 by ID.
  s.api.continuationStatus = async () => structuredClone(status);
  s.api.decideContinuation = async (p, g, id, body) => {
    s.calls.push(["native-confirm", p, g, id, body]);
    status.executions.find(record => record.id === id).status = body.status;
    status.acceptedId = body.accept ? id : null;
    return structuredClone(status);
  };
  s.api.removeContinuation = async (p, g, id) => {
    s.calls.push(["native-remove", p, g, id]); status.executions = status.executions.filter(record => record.id !== id); return structuredClone(status);
  };
  await s.ui.refresh();
  const selector = () => s.root.querySelector("#preparation-native-attempt");
  const card = () => s.find("native").querySelector(".native-attempt");
  const buttons = () => card().querySelectorAll("button");
  const choose = async id => { selector().value = id; await selector().emit("change"); };
  assert.equal(s.root.querySelectorAll("#preparation-native-attempt").length, 1);
  assert.equal(s.find("native").querySelectorAll(".native-attempt").length, 1);
  assert.equal(selector().children.length, 12);
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  const selectorRule = css.match(/\.native-selector select \{([^}]+)\}/)[1];
  assert.match(selectorRule, /width: 100%;.*min-width: 0/);
  assert.doesNotMatch(selectorRule, /(?:min-)?height:/);
  assert.match(css, /\.native-attempt-actions[^}]*flex-wrap: wrap/);
  assert.equal(selector().value, "attempt-0");
  assert.ok(s.find("native").querySelectorAll("label").some(label => label.attributes.for === "preparation-native-attempt"));
  assert.match(selector().children.find(option => option.value === "attempt-0").textContent, /✓ ACCEPTED.*ComfyUI send 1.*2026.*output-0.mp4/);
  assert.match(selector().children.find(option => option.value === "attempt-9").textContent, /2 outputs/);
  assert.deepEqual(buttons().map(button => button.textContent), ["Revoke acceptance"]);
  assert.match(card().textContent, /Accepted/);
  assert.notEqual(card().querySelector("details").open, true);
  await choose("attempt-11");
  assert.match(card().textContent, /ComfyUI send 1.*output-0.mp4/);
  assert.match(card().querySelector("details").textContent, /attempt-11.*send-0/);
  assert.equal(status.acceptedId, "attempt-0"); assert.equal(s.calls.some(call => call[0] === "native-confirm"), false);
  assert.equal(s.calls.some(call => call[0] === "select"), false);
  await s.ui.refreshContinuation(); assert.equal(selector().value, "attempt-11");
  s.context.dirty = true; await s.ui.refresh();
  assert.equal(selector().value, "attempt-11");
  assert.equal(buttons()[0].disabled, true);
  s.context.dirty = false; await s.ui.refresh();
  assert.equal(selector().value, "attempt-11");
  assert.deepEqual(buttons().map(button => button.textContent), ["Accept native state", "Remove native attempt"]);
  await buttons()[0].emit("click");
  assert.deepEqual(s.calls.find(call => call[0] === "native-confirm").slice(3), ["attempt-11", { status: "available", accept: true }]);
  assert.equal(selector().value, "attempt-11");
  assert.deepEqual(buttons().map(button => button.textContent), ["Revoke acceptance"]);
  await buttons()[0].emit("click");
  assert.deepEqual(s.calls.filter(call => call[0] === "native-confirm").at(-1).slice(3), ["attempt-11", { status: "available", accept: false }]);
  assert.equal(status.executions.find(record => record.id === "attempt-11").status, "available");
  assert.match(buttons()[1].title, /Renderer job, submission, Takes, media and external native artifacts remain/);
  const before = structuredClone(status.renderSubmissions);
  await buttons()[1].emit("click");
  assert.deepEqual(s.calls.find(call => call[0] === "native-remove").slice(3), ["attempt-11"]);
  assert.deepEqual(status.renderSubmissions, before);
  await choose("attempt-10");
  assert.match(card().textContent, /Render: Cancelled.*Native state: Unavailable/);
  assert.deepEqual(buttons().map(button => button.textContent), ["Remove native attempt"]);
  assert.doesNotMatch(s.find("native").textContent, /Mark unavailable/);
  // Switching Generations resets UI inspection; no acceptance or Take changes occur.
  status = { executions: [{ id: "G2-only", renderSubmissionId: "unknown", status: "pending", createdAt: "2026-10-03T02:00:00Z" }], acceptedId: null };
  s.context.generationId = "generation-b"; s.ui.sync(); await s.ui.refreshContinuation();
  assert.equal(selector().children.length, 1); assert.equal(selector().value, "G2-only");
  assert.deepEqual(buttons().map(button => button.textContent), ["Remove native attempt"]);
});

test("native selector initially inspects newest without accepting, and falls back to accepted after removal", async () => {
  const s = setup();
  let status = { acceptedId: null, executions: [
    { id: "newest", renderSubmissionId: "new-send", status: "available", createdAt: "2026-10-03T01:00:00Z" },
    { id: "old", renderSubmissionId: "old-send", status: "available", createdAt: "2026-10-03T00:00:00Z" },
  ] };
  s.api.continuationStatus = async () => structuredClone(status);
  await s.ui.refresh();
  assert.equal(s.root.querySelector("#preparation-native-attempt").value, "newest");
  assert.equal(status.acceptedId, null);
  assert.equal(s.calls.some(call => call[0] === "native-confirm" || call[0] === "select"), false);
  status.acceptedId = "old"; status.executions.shift();
  await s.ui.refreshContinuation();
  assert.equal(s.root.querySelector("#preparation-native-attempt").value, "old");
});

test("confirmed non-default native attempts retain acceptance and can be revoked independently", async () => {
  const s = setup();
  const status = { acceptedId: "B", executions: [
    { id: "A", renderSubmissionId: "send-A", status: "available", confirmed: true },
    { id: "B", renderSubmissionId: "send-B", status: "available", confirmed: true },
    { id: "C", renderSubmissionId: "send-C", status: "available", confirmed: false },
  ], resolution: { reason: "Matching native state is Available but has not been Accepted. Use Accept native state on the previous Generation, or choose another handoff strategy." } };
  s.api.continuationStatus = async () => structuredClone(status);
  s.api.decideContinuation = async (p, g, id, body) => {
    s.calls.push(["native-confirm", id, body]);
    status.executions.find(record => record.id === id).confirmed = body.accept;
    if (body.accept) status.acceptedId = id;
    else if (status.acceptedId === id) status.acceptedId = null;
    return structuredClone(status);
  };
  await s.ui.refresh();
  const selector = () => s.root.querySelector("#preparation-native-attempt");
  const card = () => s.find("native").querySelector(".native-attempt");
  const choose = async id => { selector().value = id; await selector().emit("change"); };
  assert.match(selector().children.find(option => option.value === "B").textContent, /ACCEPTED \(Default\)/);
  await choose("A");
  assert.match(card().textContent, /Accepted for a matching Selected Take/);
  assert.doesNotMatch(card().textContent, /Available is not Accepted/);
  assert.deepEqual(card().querySelectorAll("button").map(button => button.textContent), ["Revoke acceptance"]);
  assert.equal(status.acceptedId, "B");
  assert.equal(s.calls.some(call => call[0] === "native-confirm"), false);
  await card().querySelectorAll("button")[0].emit("click");
  assert.deepEqual(s.calls.find(call => call[0] === "native-confirm"), ["native-confirm", "A", { status: "available", accept: false }]);
  assert.equal(status.acceptedId, "B");
  assert.match(card().textContent, /Available is not Accepted/);
  assert.deepEqual(card().querySelectorAll("button").map(button => button.textContent), ["Accept native state", "Remove native attempt"]);
  await choose("C");
  assert.match(selector().children.find(option => option.value === "C").textContent, /^Available/);
  assert.match(card().textContent, /Available is not Accepted/);
  assert.match(s.find("native").textContent, /selecting a Take never accepts native state/);
  status.acceptedId = null;
  await s.ui.refreshContinuation();
  await choose("B");
  assert.match(card().textContent, /No default native attempt is selected/);
  assert.deepEqual(card().querySelectorAll("button").map(button => button.textContent), ["Revoke acceptance"]);
});

test("native attempt filenames are deduplicated and bounded without hiding renderer state", async () => {
  const s = setup();
  s.profiles[0].nativeContinuation = { contextFrames: 25, contextGrid: { minimum: 1, step: 4, alignment: "ceil" }, audioContinuity: false, audioEnabled: false, inputs: {} };
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a" }, acceptedId: null,
    executions: [{ id: "attempt", renderSubmissionId: "send", createdAt: "2026-09-22T00:01:00Z", status: "available",
      renderSubmission: { id: "send", createdAt: "2026-09-22T00:01:00Z", comfyPromptId: "prompt",
        jobStatus: "completed", outputFilenames: ["a.mp4", "a.mp4", "b.mp4", "c.mp4", "d.mp4"] } }],
    renderSubmissions: [{ id: "send", createdAt: "2026-09-22T00:01:00Z", comfyPromptId: "prompt" }],
    resolution: { reason: "Ready" } });
  await s.ui.refresh();
  const card = s.find("native").querySelector(".native-attempt");
  const visible = card.children.filter(x => x.tagName !== "details").map(x => x.textContent).join(" ");
  assert.match(visible, /Outputs: a.mp4 · b.mp4 · c.mp4 · \+1 more/);
  assert.doesNotMatch(visible, /d.mp4/);
  assert.match(visible, /Available is not Accepted/);
  assert.match(visible, /ComfyUI send 1/);
});

test("missing renderer provenance is explicit while the execution remains actionable", async () => {
  const s = setup();
  s.profiles[0].nativeContinuation = { contextFrames: 25, contextGrid: { minimum: 1, step: 4, alignment: "ceil" }, audioContinuity: false, audioEnabled: false, inputs: {} };
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a" }, acceptedId: null,
    executions: [{ id: "attempt-missing", renderSubmissionId: "missing", createdAt: "2026-09-22T00:02:00Z", status: "pending" }],
    resolution: { reason: "Ready" } });
  await s.ui.refresh();
  assert.match(s.find("native").textContent, /Submission record missing/);
  assert.equal(s.find("native").querySelectorAll("button").some(x => x.textContent === "Accept native state"), false);
});

test("historical submissions do not make the current preparation status claim never sent", async () => {
  const s = setup();
  await s.ui.refresh();
  assert.match(s.find("receipt").textContent, /No active prepared send/);
  assert.doesNotMatch(s.find("receipt").textContent, /NOT SENT TO COMFYUI/);
  assert.match(s.find("prepare-hint").textContent, /Prepare checks saved Project authority/);
});

for (const [field, value] of [["generationId", "generation-b"], ["projectId", "project-b"], ["mode", "T2VA"], ["saved", false]]) {
  test(`${field} change immediately invalidates the prepared result`, async () => {
    const s = setup(); await s.ready(); s.context[field] = value; s.ui.sync();
    assert.equal(s.ui.prepared, null); assert.doesNotMatch(s.root.textContent, /Prepared \u2014 (?:Ready to Send|Review warnings)/);
    if (field === "dirty") assert.match(s.find("message").textContent, /Unsaved edits remain local/);
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
    if (action === "delete") assert.equal(s.find("prepare").disabled, false);
    else { assert.match(s.find("profile").textContent, /Changed profile/); assert.match(s.find("profile-issues").textContent, /needs consumers/); }
  });
}

test("selection refresh becoming stale removes an earlier ready result", async () => {
  const s = setup(); await s.ready(); s.statuses.get("generation-a").stale = true;
  await s.ui.refreshSelection(); assert.equal(s.ui.prepared, null);
  assert.match(s.find("selection").textContent, /different saved authoring/);
});

test("draft dirtiness during Prepare preserves the prepared saved execution", async () => {
  const s = setup(); await s.ui.refresh(); await s.click("use");
  const result = await s.api.prepareWorkflow({}); let resolve;
  s.api.prepareWorkflow = () => new Promise(done => { resolve = done; });
  const pending = s.click("prepare");
  while (!resolve) await Promise.resolve();
  s.context.dirty = true; s.ui.sync(); resolve(result); await pending;
  assert.equal(s.ui.prepared, result); assert.match(s.root.textContent, /Prepared \u2014 (?:Ready to Send|Review warnings)/);
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
  await s.ui.refreshSelection(); assert.equal(s.find("prepare").disabled, false);
  assert.match(s.find("message").textContent, /Missing saved payload/);
  s.api.executionProfiles = async () => { throw new Error("Profiles unavailable"); };
  await s.ui.refreshProfiles(); assert.equal(s.find("profile").value, "");
});

for (const patch of [{ sourceFingerprint: null }, { validation: { valid: false } }, { generationStatus: "stopped" }, { generationStatus: "generating" }, { prompt: "" }]) {
  test(`nonempty output remains selectable: ${JSON.stringify(patch)}`, () => {
    const s = setup(); assert.equal(canSelectExecutionOutput({ ...s.output, ...patch }), Boolean(patch.prompt !== ""));
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
    await realApi.adoptHistoryExecutionPrompt("p", "g", { prompt: "legacy text" });
    await realApi.adoptManualEditExecutionPrompt("p", "g", { prompt: "manual text" });
    await realApi.clearExecutionPrompt("p", "g");
    await realApi.updateExecutionSetup("p", "g", { durationSeconds: 9, aspectRatio: "16:9", expectedProjectFingerprint: "f".repeat(64) });
    await realApi.prepareWorkflow({ projectId: "p", generationId: "g", profileId: "w" });
    assert.equal(calls[0][0], "/api/execution/projects/p%2Fx/generations/g%2Fx/selection");
    assert.equal(calls[1][0], "/api/execution/projects/p/generations/g/selection");
    assert.equal(calls[2][0], "/api/execution/projects/p/generations/g/selection/adopt-history");
    assert.deepEqual(JSON.parse(calls[2][1].body), { prompt: "legacy text" });
    assert.equal(calls[3][0], "/api/execution/projects/p/generations/g/selection/adopt-manual-edit");
    assert.deepEqual(JSON.parse(calls[3][1].body), { prompt: "manual text" });
    assert.equal(calls[5][0], "/api/execution/projects/p/generations/g/execution-setup");
    assert.deepEqual(JSON.parse(calls[5][1].body), { durationSeconds: 9, aspectRatio: "16:9", expectedProjectFingerprint: "f".repeat(64) });
    assert.deepEqual(calls.map(c => c[1].method), ["GET", "PUT", "POST", "POST", "DELETE", "PUT", "POST"]);
    assert.deepEqual(JSON.parse(calls[6][1].body), { projectId: "p", generationId: "g", profileId: "w" });
  } finally { globalThis.fetch = previous; }
});

test("app connects profile changes, authoring dirtiness and response provenance", async () => {
  const text = await readFile(new URL("../../web/js/app.js", import.meta.url), "utf8");
  assert.match(text, /onProfilesChanged: \(\) => workflowPreparation.refreshProfiles\(\)/);
  assert.match(text, /state.dirty = value;\s+workflowPreparation\?\.sync\(\)/);
  assert.match(text, /sourceFingerprint: data.sourceFingerprint/);
  assert.match(text, /context\.projectId === state\.currentProjectId && response\.projectFingerprint/);
  assert.doesNotMatch(text, /context\.projectId === state\.currentProjectId && context\.generationId === state\.activeGenerationId && response\.projectFingerprint/);
  assert.match(text, /revision.sourceFingerprint = data.sourceFingerprint/);
  assert.match(text, /state\.outputs\[state\.outputIndex\] = markOutputManuallyEdited\(current, \$\("#output"\)\.value\)/);
  assert.match(text, /state\.revision = null;\s+\$\("#revision-panel"\)\.classList\.add\("hidden"\)/);
  const initialization = text.slice(text.indexOf("async function init()"));
  assert.doesNotMatch(initialization, /await renderHistory\(\)/);
  assert.match(text, /createSavedPrompts/);
  assert.doesNotMatch(text, /#load-history|addHistory/);
  const subjectDeletion = text.slice(text.indexOf("function deleteSequenceSubject"));
  assert.match(subjectDeletion, /if \(!structuralSequenceReady\(\)\) return/);
  assert.match(text, /Save or discard the current Generation changes before editing the Sequence\./);
});

test("mounted page hierarchy keeps authoring actions and validation above execution details", async () => {
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const order = ["id=\"output-highlighted\"", "id=\"validate-output\"", "id=\"repair-output\"",
    "id=\"revision-panel\"", "class=\"lower-grid\"", "id=\"workflow-preparation-primary\"",
    "id=\"workflow-preparation-details\"", "id=\"rendered-takes\""]
    .map(marker => html.indexOf(marker));
  assert.ok(order.every(index => index >= 0));
  assert.ok(order.every((index, position) => position === 0 || index > order[position - 1]));
  for (const id of ["workflow-preparation-primary", "workflow-preparation-details", "validation-list", "saved-h3-prompts", "rendered-takes"])
    assert.equal((html.match(new RegExp(`id=\"${id}\"`, "g")) || []).length, 1, id);
  assert.equal((html.match(/id="rendered-takes"/g) || []).length, 1);
  assert.match(html, /id="workflow-preparation-primary" class="workflow-preparation execution-primary-mount"/);
});

test("Native history growth stays below one Prepare/Send and the unique Validation/Saved H3 Prompts panels", async () => {
  const s = setup();
  await s.ui.refresh();
  const launcher = s.root, prepare = s.find("prepare"), send = s.find("send");
  const validation = new Element("section", s.doc); validation.id = "validation-list"; validation.className = "panel validation-panel";
  const history = new Element("section", s.doc); history.id = "saved-h3-prompts"; history.className = "panel saved-prompts-panel";
  const lowerGrid = new Element("section", s.doc); lowerGrid.className = "lower-grid"; lowerGrid.append(validation, history);
  const nativeHistory = s.root.querySelector("#preparation-native-section");
  lowerGrid.parentElement = s.root; s.root.children.splice(s.root.children.indexOf(s.primaryRoot), 0, lowerGrid);
  assert.equal(launcher.querySelectorAll("#preparation-prepare").length, 1);
  assert.equal(launcher.querySelectorAll("#preparation-send").length, 1);
  assert.ok(launcher.children.indexOf(lowerGrid) < launcher.children.indexOf(s.primaryRoot));
  assert.ok(launcher.children.indexOf(lowerGrid) < launcher.children.indexOf(s.detailsRoot));
  assert.ok(s.detailsRoot.querySelector("#preparation-settings").children.indexOf(nativeHistory)
    > s.detailsRoot.querySelector("#preparation-settings").children.indexOf(s.detailsRoot.querySelector("#preparation-workflow-controls")));
  for (const id of ["submission-1", "submission-2", "submission-3"]) {
    const card = new Element("article", s.doc); card.id = id; nativeHistory.append(card);
  }
  assert.equal(launcher.querySelectorAll("#preparation-prepare").length, 1);
  assert.equal(launcher.querySelectorAll("#preparation-send").length, 1);
  assert.equal(launcher.querySelectorAll(".validation-panel").length, 1);
  assert.equal(launcher.querySelectorAll(".saved-prompts-panel").length, 1);
  assert.equal(launcher.querySelectorAll("#validation-list").length, 1);
  assert.equal(launcher.querySelectorAll("#saved-h3-prompts").length, 1);
  assert.equal(s.find("prepare"), prepare);
  assert.equal(s.find("send"), send);
  assert.ok(launcher.children.indexOf(lowerGrid) < launcher.children.indexOf(s.primaryRoot));
  assert.ok(launcher.children.indexOf(lowerGrid) < launcher.children.indexOf(s.detailsRoot));
});

test("canonical Prepare and Send sit between execution selection and settings", async () => {
  const s = setup();
  await s.ui.refresh();
  const prepare = s.find("prepare"), send = s.find("send");
  const primaryActions = s.primaryRoot.querySelector("#preparation-primary-actions");
  const settings = s.detailsRoot.querySelector("#preparation-settings");
  assert.equal(s.find("use").parentElement, prepare.parentElement);
  assert.equal(s.find("adopt").parentElement, prepare.parentElement);
  assert.equal(prepare.parentElement, s.root.querySelector(".execution-primary-action-row"));
  assert.equal(send.parentElement, prepare.parentElement);
  assert.equal(primaryActions.parentElement, s.primaryRoot.querySelector(".execution-primary-layout"));
  assert.equal(settings.parentElement, s.detailsRoot);
  assert.equal(s.detailsRoot.querySelector("#preparation-connection").closest("#preparation-settings"), settings);
  assert.equal(s.detailsRoot.querySelector("#preparation-profile").closest("#preparation-settings"), settings);
  assert.ok(settings.children.indexOf(s.root.querySelector("#preparation-workflow-controls")) > 0);
  assert.ok(settings.children.indexOf(s.root.querySelector("#preparation-native-section"))
    > settings.children.indexOf(s.root.querySelector("#preparation-workflow-controls")));
  assert.equal(s.root.querySelectorAll("#preparation-prepare").length, 1);
  assert.equal(s.root.querySelectorAll("#preparation-send").length, 1);
  assert.equal(settings.querySelectorAll("#preparation-send").length, 0);
  const controlSection = s.root.querySelector("#preparation-workflow-controls");
  controlSection.append(new Element("div", s.doc), new Element("div", s.doc), new Element("div", s.doc));
  assert.equal(s.find("prepare"), prepare);
  assert.equal(s.find("send"), send);
  assert.equal(s.root.querySelectorAll("#preparation-prepare").length, 1);
  assert.equal(s.root.querySelectorAll("#preparation-send").length, 1);
});

test("current duration is synchronized for Prepare and invalidates after duration changes", async () => {
  const s = setup();
  await s.ui.refresh();
  await s.click("use");
  await s.click("prepare");
  const durationWrite = s.calls.find(row => row[0] === "duration");
  assert.deepEqual(durationWrite.slice(1), ["project-a", "generation-a", {
    durationSeconds: 8, aspectRatio: "16:9", expectedProjectFingerprint: "f".repeat(64),
  }]);
  const originalPrepare = s.calls.find(row => row[0] === "prepare");
  assert.deepEqual(originalPrepare[1], { projectId: "project-a", generationId: "generation-a", profileId: "profile-a" });
  s.context.durationSeconds = 9;
  s.ui.sync();
  assert.equal(s.ui.prepared, null);
  s.calls.length = 0;
  await s.click("send");
  assert.equal(s.calls.filter(row => row[0] === "send").length, 0);
});

test("presentation-only context changes do not invalidate prepared execution", async () => {
  const s = setup();
  await s.ui.refresh();
  await s.click("use");
  await s.click("prepare");
  const prepared = s.ui.prepared;
  s.context.presentationPreference = "comfortable";
  s.ui.sync();
  assert.equal(s.ui.prepared, prepared);
});


test("same-tick Generation switch suppresses superseded selection GET before sending", async () => {
  const s = setup();
  s.ui.sync();
  s.context.generationId = "generation-b";
  s.ui.sync();
  await s.ui.refreshSelection();
  assert.deepEqual(s.calls.filter(c => c[0] === "status"), [["status", "project-a", "generation-b"]]);
});

test("Workflow Controls explain precedence, explicit preset application, and source labels", async () => {
  const s = setup();
  s.profiles[0].exposedControls = [{ id: "steps", label: "Steps", type: "integer", defaultValue: 20, minimum: 1, maximum: 100 }];
  s.profiles[0].workflowControlPresets = [{ name: "Quality", values: { steps: 20 } }];
  const project = {}, generation = {};
  const state = () => ({ profileId: "profile-a", warnings: [], controls: [{ id: "steps", label: "Steps", type: "integer", minimum: 1, maximum: 100,
    value: generation.steps ?? project.steps ?? 20, source: Object.hasOwn(generation, "steps") ? "generation_override" : Object.hasOwn(project, "steps") ? "project_default" : "profile_default",
    hasProjectOverride: Object.hasOwn(project, "steps"), hasGenerationOverride: Object.hasOwn(generation, "steps"), projectOverride: project.steps, generationOverride: generation.steps }] });
  s.api.workflowControls = async () => state();
  s.api.saveWorkflowControls = async (_p, _g, _profile, scope, values) => { const target = scope === "project" ? project : generation; Object.keys(target).forEach(key => delete target[key]); Object.assign(target, values); return state(); };
  await s.ui.refresh();
  assert.match(s.find("workflow-controls").textContent, /Effective values are applied during Prepare\/Send.*Profile default → Project default → Generation override/);
  assert.match(s.find("workflow-controls").textContent, /Selecting a preset does nothing until Apply preset is clicked/);
  assert.equal(s.find("control-steps").parentElement.parentElement.querySelector(".workflow-control-source").textContent, "Profile default");
  s.find("preset").value = "Quality";
  assert.deepEqual(project, {});
  await s.click("preset-apply");
  assert.deepEqual(project, { steps: 20 });
  assert.equal(s.find("control-steps").parentElement.parentElement.querySelector(".workflow-control-source").textContent, "Project default");
  s.find("control-scope").value = "generation"; await s.find("control-scope").emit("change");
  assert.equal(s.find("control-steps").parentElement.parentElement.querySelector(".workflow-control-source").textContent, "Project default");
  generation.steps = 12; await s.ui.refresh();
  assert.equal(s.find("control-steps").parentElement.parentElement.querySelector(".workflow-control-source").textContent, "This Generation");
});

test("compact primary layout owns exactly six actions and one selected-prompt region", async () => {
  const s = setup(); await s.ui.refresh();
  const layout = s.primaryRoot.querySelector(".execution-primary-layout");
  const grid = layout.querySelector(".execution-action-grid");
  const ids = ["use", "adopt", "clear", "prepare", "send", "refresh"];
  assert.deepEqual(grid.children.map(child => child.id), ids.map(id => `preparation-${id}`));
  for (const id of ids) {
    assert.equal(s.root.querySelectorAll(`#preparation-${id}`).length, 1);
    assert.equal(s.find(id).parentElement, grid);
  }
  const region = layout.querySelector(".execution-selected-region");
  assert.equal(s.root.querySelectorAll(".execution-selected-region").length, 1);
  assert.equal(s.root.querySelectorAll("#preparation-selected").length, 1);
  assert.equal(region.parentElement, grid.parentElement.parentElement);
  assert.equal(region.querySelector("#preparation-selected"), s.find("selected"));
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  assert.match(css, /\.execution-action-grid[^{}]*\{[^}]*repeat\(3,/);
  assert.match(css, /\.execution-action-grid[^{}]*\{[^}]*column-gap:\s*\.55rem;[^}]*row-gap:\s*\.5rem;/);
  assert.match(css, /\.execution-primary-layout[^{}]*\{[^}]*grid-template-columns:\s*minmax\(min-content,\s*\.8fr\)\s+minmax\(0,\s*1\.4fr\)/);
  assert.match(css, /@media \(max-width: 1200px\) \{\s*\.execution-primary-layout \{ grid-template-columns: minmax\(0, 1fr\); \}/);
  assert.match(css, /@media \(max-width: 600px\) \{\s*\.execution-action-grid \{ grid-template-columns: minmax\(0, 1fr\);/);
  const executionLayoutCss = css.slice(css.indexOf(".execution-primary-layout"), css.indexOf(".execution-settings"));
  assert.ok(executionLayoutCss.includes(".execution-primary-actions, .execution-selected-region"));
  assert.doesNotMatch(executionLayoutCss, /(?:min-)?height\s*:/);
});

test("mounted output hierarchy puts Validation and Saved H3 Prompts immediately before compact execution", async () => {
  const s = setup();
  const html = await readFile(new URL("../../web/index.html", import.meta.url), "utf8");
  const page = mountHtml(html, s.doc);
  const pane = page.querySelector(".right-stage");
  const order = domOrder(pane);
  const nodes = [page.querySelector(".output-card"), page.querySelector(".output-toolbar"), page.querySelector(".lower-grid"),
    page.querySelector("#workflow-preparation-primary"), page.querySelector("#workflow-preparation-details"), page.querySelector("#rendered-takes")];
  assert.ok(nodes.every(Boolean));
  assert.ok(nodes.every((node, index) => index === 0 || order.indexOf(node) > order.indexOf(nodes[index - 1])));
  assert.equal(nodes[2].querySelectorAll(".validation-panel, .saved-prompts-panel").length, 2);
  assert.equal(pane.children[pane.children.indexOf(nodes[2]) + 1], nodes[3]);
});

test("an older same-context selection read cannot invalidate a newer Prepare after a no-op Reference check", async () => {
  const s = setup({ syncReferences: async () => {} }); await s.ready();
  const late = deferred(), fresh = s.api.executionSelection;
  s.api.executionSelection = () => late.promise;
  const pending = s.ui.refreshSelection();
  await Promise.resolve(); s.api.executionSelection = fresh;
  await s.click("prepare"); const prepared = s.ui.prepared; assert.ok(prepared);
  late.resolve({ selected: null, stale: true, currentFingerprint: "older" }); await pending;
  assert.equal(s.ui.prepared, prepared); await s.click("send");
  assert.equal(s.calls.filter(call => call[0] === "send").length, 1);
});


test("Execution Settings have named groups, a prominent counted disclosure, compact Pictures and visible routing problems", async () => {
  const s = setup();
  s.profiles[0].exposedControls = Array.from({ length: 48 }, (_, i) => ({ id: `c${i}`, label: `Control ${i}`, type: "integer", defaultValue: i }));
  const routing = { routing: { assets: {} }, assets: Array.from({ length: 5 }, (_, i) => ({ id: `pic${i}`, kind: "image" })),
    referenceMap: Array.from({ length: 5 }, (_, i) => ({ assetId: `pic${i}`, channel: "image" })) };
  s.api.executionRouting = async () => structuredClone(routing);
  await s.ui.refresh();
  for (const id of ["connection-profile", "references", "prepared-workflow", "native-section"]) {
    const region = s.find(id), labelledBy = region.attributes["aria-labelledby"];
    assert.ok(labelledBy); assert.ok(region.querySelector(`#${labelledBy}`));
    assert.match(region.className, /execution-settings-group/);
  }
  const controls = s.find("workflow-controls");
  assert.notEqual(controls.open, true); assert.match(controls.querySelector("summary").textContent, /Workflow Controls \(48\)/);
  controls.open = true; await s.ui.refresh(); assert.equal(controls.open, true);
  assert.equal(s.find("routing").hidden, true);
  assert.match(s.find("reference-summary").textContent, /5 Pictures · 0 Audio · 0 Video/);
  assert.match(s.find("profile-status").textContent, /no continuity Profile applied/);
  routing.visualHandoff = { warnings: ["Predecessor media missing; choose a valid source."] };
  await s.ui.refresh();
  assert.equal(s.find("routing").hidden, false); assert.match(s.find("routing").textContent, /Predecessor media missing/);
  const css = await readFile(new URL("../../web/css/app.css", import.meta.url), "utf8");
  assert.match(css, /execution-settings-group[^}]*overflow-wrap: anywhere/);
  assert.match(css, /summary:focus-visible[^}]*outline:/);
  assert.match(css, /#preparation-workflow-controls > summary[^}]*padding: .9rem .8rem/);
});

test("prepared warnings stay outside the inspector while supplementary diagnostics and native IDs stay inside", async () => {
  const s = setup(), prepare = s.api.prepareWorkflow;
  s.api.prepareWorkflow = async body => ({ ...await prepare(body), information: ["Supplementary diagnostic"],
    nativeContinuation: { consume: true, segmentIndex: 2, sourceId: "long-technical-identifier", contextFrames: 22 } });
  await s.ready();
  const result = s.find("result"), inspector = result.querySelector("details");
  assert.match(result.querySelector(".preparation-warnings").textContent, /Unbound LoadImage/);
  const visible = result.children.filter(node => node.tagName !== "details").map(node => node.textContent).join(" ");
  assert.doesNotMatch(visible, /Supplementary diagnostic|long-technical|Control 0/);
  assert.match(inspector.textContent, /Supplementary diagnostic|long-technical/);
  inspector.open = true; await s.ui.refreshSelection(); assert.equal(s.find("result").querySelector("details").open, true);
});

test("continuation input drafts and disclosures survive ordinary status rerenders", async () => {
  const s = setup();
  s.profiles[0].nativeContinuation = { contextFrames: 25, contextGrid: { minimum: 1, step: 4, alignment: "ceil" }, audioContinuity: true, audioEnabled: false, inputs: { source_out_frame: {} } };
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a" }, executions: [], resolution: { reason: "Ready" } });
  await s.ui.refresh();
  const edit = s.find("native").querySelector(".native-settings").querySelector("details"); edit.open = true;
  s.find("native-frames").value = "49"; await s.find("native-frames").emit("input");
  s.find("native-audio").checked = true; await s.find("native-audio").emit("change");
  s.find("native-out").value = "120"; await s.find("native-out").emit("input");
  await s.ui.refreshContinuation();
  assert.equal(s.find("native-frames").value, "49"); assert.equal(s.find("native-audio").checked, true);
  assert.equal(s.find("native-out").value, "120");
  assert.equal(s.find("native").querySelector(".native-settings").querySelector("details").open, true);
  assert.deepEqual(s.calls.filter(call => /save|send/.test(call[0])), []);
});

test("saved Ref2VA and I2VA profiles filter by mode without modifying configuration", async () => {
  const s = setup(); s.profiles[1].compatibleModes = ["I2VA"];
  const before = structuredClone(s.profiles);
  await s.ui.refresh();
  assert.equal(s.find("profile").value, "profile-a");
  s.context.mode = "I2VA"; await Promise.all(s.ui.sync());
  assert.deepEqual(s.find("profile").children.map(item => item.value), ["profile-b"]);
  assert.equal(s.find("profile").value, "profile-b");
  assert.match(s.find("profile-status").textContent, /Only compatible profile selected/);
  s.context.mode = "L2VA"; await Promise.all(s.ui.sync());
  assert.equal(s.find("profile").value, "");
  assert.match(s.find("profile-status").textContent, /No compatible.*L2VA.*Execution \/ ComfyUI.*New profile/);
  assert.deepEqual(s.profiles, before);
});

test("mounted Duplicate and Save refresh available profiles without changing execution or native selection", async () => {
  const s = setup(); s.profiles[1].compatibleModes = ["Ref2VA"];
  const native = { profileId: "profile-a" };
  s.api.continuationStatus = async () => ({ settings: structuredClone(native), executions: [] });
  s.api.saveContinuationSettings = async () => { throw new Error("Duplication must not write native settings"); };
  s.api.executionCapabilities = async () => ({ compatibleModes: ["Ref2VA"], assetKinds: ["image"], maxWorkflowBytes: 2097152 });
  const draft = { name: "Capacity (Copy)", compatibleModes: ["Ref2VA"], description: "", bindings: [],
    workflowJson: '{"1":{"class_type":"Custom","inputs":{"prompt":"old"}}}',
    exposedControls: [], workflowControlPresets: [], nativeContinuation: null };
  s.api.duplicateExecutionProfileDraft = async () => structuredClone(draft);
  s.api.executionProfile = async id => structuredClone(s.profiles.find(item => item.id === id));
  s.api.saveExecutionProfile = async (id, body) => {
    assert.equal(id, null);
    const saved = { ...body, id: "duplicate", workflow: JSON.parse(body.workflowJson), fingerprint: "abc" };
    s.profiles.push(saved); return structuredClone(saved);
  };
  await s.ready();
  s.find("profile").value = "profile-b"; await s.find("profile").emit("change");
  const selected = structuredClone(s.statuses), generation = s.context.generationId;
  const root = new Element("div", s.root.ownerDocument);
  const config = createExecutionConfig({ root, api: s.api, onProfilesChanged: () => s.ui.refreshProfiles() });
  await config.refresh();
  await root.querySelector("#execution-profile-duplicate-profile-a").emit("click");
  assert.equal(s.profiles.length, 2);
  await root.querySelector("#execution-profile-save").emit("click");
  assert.match(root.querySelector("#execution-profile-status").textContent, /created/);
  assert.equal(s.profiles.length, 3);
  assert.equal(s.find("profile").value, "profile-b");
  assert.ok(s.find("profile").children.some(option => option.value === "duplicate"));
  assert.equal(s.context.generationId, generation); assert.deepEqual(s.statuses, selected);
  assert.deepEqual(native, { profileId: "profile-a" });
  assert.equal(s.calls.filter(call => call[0] === "send").length, 0);
});

test("explicit execution choice survives list and delayed continuity refreshes, invalidates Prepare, and resets on navigation", async () => {
  const s = setup(); s.profiles[1].compatibleModes = ["Ref2VA"];
  s.profiles[1].rendererSeedBinding = true;
  const settings = { profileId: "profile-a" };
  s.api.continuationStatus = async () => ({ settings: structuredClone(settings), executions: [] });
  s.api.saveContinuationSettings = async () => { throw new Error("Switching must not save continuity"); };
  await s.ready();
  const late = deferred(); s.api.continuationStatus = () => late.promise;
  const pending = s.ui.refreshContinuation();
  s.find("profile").value = "profile-b"; await s.find("profile").emit("change");
  assert.equal(s.ui.prepared, null);
  await s.click("send"); assert.equal(s.calls.filter(call => call[0] === "send").length, 0);
  late.resolve({ settings: structuredClone(settings), executions: [] }); await pending;
  await s.ui.refresh();
  assert.equal(s.find("profile").value, "profile-b");
  assert.equal(s.find("seed-mode").value, "random");
  await s.click("prepare"); await s.click("send");
  assert.equal(s.calls.filter(call => call[0] === "prepare").at(-1)[1].profileId, "profile-b");
  assert.equal(s.calls.find(call => call[0] === "send")[1].profileId, "profile-b");
  assert.deepEqual(settings, { profileId: "profile-a" });
  s.context.generationId = "generation-b"; await s.ui.refresh();
  assert.equal(s.find("profile").value, "profile-a");
  assert.equal(s.find("seed-mode").value, "workflow_default");
});

test("switching compatible profiles refreshes controls and route issues while same-profile refresh preserves drafts", async () => {
  const s = setup(); s.profiles[1].compatibleModes = ["Ref2VA"];
  for (const [index, profile] of s.profiles.entries()) profile.exposedControls = [{ id: "steps", label: "Steps", type: "integer", defaultValue: 10 + index }];
  s.profiles[1].preparationIssues = ["Video 1: classify route capacity before preparation."];
  s.api.workflowControls = async (_p, _g, id) => ({ profileId: id, warnings: [], controls: s.profiles.find(p => p.id === id).exposedControls.map(item => ({ ...item, value: item.defaultValue, source: "profile_default" })) });
  s.api.continuationStatus = async () => ({ settings: { profileId: "profile-a" }, executions: [] });
  await s.ui.refresh();
  s.find("profile").value = "profile-b"; await s.find("profile").emit("change");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.find("control-steps").value, "11");
  assert.match(s.find("profile-issues").textContent, /classify route capacity/);
  s.find("control-steps").value = "25"; await s.find("control-steps").emit("input");
  await s.ui.refresh();
  assert.equal(s.find("profile").value, "profile-b");
  assert.equal(s.find("control-steps").value, "25");
  s.find("profile").value = "profile-a"; await s.find("profile").emit("change");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.find("control-steps").value, "10");
  assert.equal(s.find("profile-issues").textContent, "");
});


for (const diagnosticCase of [
  { name: "no diagnostics", diagnostics: [] },
  { name: "several informational diagnostics", diagnostics: [
    { code: "preparation.offline", severity: "info", message: "Offline preparation." },
    { code: "native.save_only", severity: "info", message: "Save current native state only." },
    { code: "native.artifact_lifecycle", severity: "info", message: "Artifacts appear after rendering." },
  ] },
]) test(`mounted Prepare is ready with ${diagnosticCase.name}`, async () => {
  const s = setup();
  s.api.prepareWorkflow = async () => ({ mode: "Ref2VA", graph: {},
    diagnostics: diagnosticCase.diagnostics, warnings: [],
    information: diagnosticCase.diagnostics.map(d => d.message) });
  await s.ready();
  assert.equal(s.find("status").textContent, "Prepared \u2014 Ready to Send");
  assert.equal(s.find("status").attributes.role, "status");
  assert.equal(s.find("status").attributes["aria-live"], "polite");
  assert.equal(s.find("result").querySelector(".preparation-warnings"), null);
  const info = s.find("result").querySelector(".preparation-details");
  if (diagnosticCase.diagnostics.length) {
    assert.equal(info.open, false);
    assert.equal(info.querySelector("summary").textContent, "Preparation details (3)");
    // Native details/summary provide browser keyboard activation, with no custom key handler.
    assert.equal(info.tagName, "details");
    info.open = true;
    for (const d of diagnosticCase.diagnostics) {
      assert.match(info.textContent, new RegExp(d.message));
      assert.equal(s.find("result").textContent.split(d.message).length - 1, 1);
    }
    await s.ui.refreshSelection();
    assert.equal(s.find("result").querySelector(".preparation-details").open, true);
  } else assert.equal(info, null);
  await s.click("prepare");
  const repeated = s.find("result").querySelector(".preparation-details");
  if (repeated) {
    assert.equal(repeated.open, false);
    assert.equal(repeated.querySelectorAll("p").length, 3);
  }
  await s.click("send");
  assert.equal(s.calls.filter(c => c[0] === "send").length, 1);
  assert.doesNotMatch(s.find("status").textContent, /Ready to Send/);
});

test("mounted severity is structured, conservative for unknowns and deduplicated", async () => {
  const s = setup();
  s.api.prepareWorkflow = async () => ({ mode: "Ref2VA", graph: {}, diagnostics: [
    { code: "future", severity: "unexpected", message: "Future diagnostic" },
    { code: "explicit.info", severity: "info", message: "Warning words do not determine severity" },
    { code: "reference.routing", severity: "warning", message: "Check original Reference mapping" },
  ], warnings: ["Future diagnostic", "Check original Reference mapping", "Legacy unknown warning"],
  information: ["Warning words do not determine severity", "Future diagnostic"] });
  await s.ready();
  assert.equal(s.find("status").textContent, "Prepared \u2014 Review warnings");
  const warnings = s.find("result").querySelector(".preparation-warnings");
  assert.match(warnings.textContent, /Future diagnostic.*Check original Reference mapping.*Legacy unknown warning/);
  assert.doesNotMatch(warnings.textContent, /Warning words/);
  assert.equal(warnings.querySelectorAll("p").length, 3);
  assert.equal(s.find("result").querySelector(".preparation-details").querySelectorAll("p").length, 1);
});

test("mounted error severity is never masked by info and cannot Send", async () => {
  const s = setup();
  s.api.prepareWorkflow = async () => ({ mode: "Ref2VA", graph: {}, diagnostics: [
    { code: "bad.binding", severity: "error", message: "Invalid semantic binding" },
    { code: "duplicate", severity: "info", message: "Invalid semantic binding" },
  ], warnings: [], information: ["Invalid semantic binding"] });
  await s.ready();
  assert.equal(s.find("status").textContent, "Preparation failed");
  assert.match(s.find("result").querySelector(".preparation-errors").textContent, /Invalid semantic binding/);
  assert.equal(s.find("result").querySelector(".preparation-details"), null);
  await s.click("send");
  assert.equal(s.calls.some(c => c[0] === "send"), false);
  assert.doesNotMatch(s.find("status").textContent, /Ready to Send/);
});

test("mounted profile switches, failed Prepare and Reference changes clear prior details", async () => {
  const s = setup(); s.profiles[1].compatibleModes = ["Ref2VA"];
  s.api.prepareWorkflow = async () => ({ mode: "Ref2VA", graph: {}, diagnostics: [
    { code: "preparation.offline", severity: "info", message: "Old preparation detail" },
  ], warnings: [], information: ["Old preparation detail"] });
  await s.ready();
  s.find("result").querySelector(".preparation-details").open = true;
  s.find("profile").value = "profile-b"; await s.find("profile").emit("change");
  assert.equal(s.ui.prepared, null);
  assert.doesNotMatch(s.find("result").textContent, /Old preparation detail/);
  assert.equal(s.find("status").textContent, "");
  s.api.prepareWorkflow = async () => { throw new Error("Required Reference missing"); };
  await s.click("prepare");
  assert.equal(s.find("status").textContent, "Preparation failed");
  assert.doesNotMatch(s.find("result").textContent, /Old preparation detail/);
  await s.ui.invalidateReferences();
  assert.doesNotMatch(s.find("status").textContent, /Ready to Send/);
});


test("starting a re-Prepare clears old diagnostics before asynchronous Reference synchronization", async () => {
  let synchronize = async () => {};
  const s = setup({ syncReferences: () => synchronize() });
  await s.ready();
  const pendingSync = deferred(); synchronize = () => pendingSync.promise;
  const pending = s.click("prepare");
  assert.equal(s.ui.prepared, null);
  assert.equal(s.find("result").querySelector(".preparation-warnings"), null);
  assert.equal(s.find("status").textContent, "");
  pendingSync.reject(new Error("Reference synchronization failed")); await pending;
  assert.equal(s.find("status").textContent, "Preparation failed");
  assert.doesNotMatch(s.find("result").textContent, /Unbound LoadImage/);
});
