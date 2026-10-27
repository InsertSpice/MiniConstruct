import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, existsSync } from "node:fs";
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { mountHtml } from "./support/html.mjs";
import { shouldMarkWorkspaceDirty } from "../../web/js/sequence-workflow.js";
import { Element } from "./support/dom.mjs";
import { createNavigationAuthority } from "../../web/js/navigation-authority.js";
import { createRenderedTakes } from "../../web/js/rendered-takes.js";
import { createWorkflowPreparation } from "../../web/js/workflow-preparation.js";
import { createReferenceAuthority } from "../../web/js/reference-authority.js";
import { markOutputManuallyEdited } from "../../web/js/manual-edit.js";
import { createSavedPrompts } from "../../web/js/saved-prompts.js";

const source = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8");
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

async function fixture(t, legacy = false, discovery = false) {
  const windowsPython = fileURLToPath(new URL("../../.venv/Scripts/python.exe", import.meta.url));
  const unixPython = fileURLToPath(new URL("../../.venv/bin/python", import.meta.url));
  const python = process.env.PYTHON || (existsSync(windowsPython) ? windowsPython : existsSync(unixPython) ? unixPython : "python");
  const child = spawn(python, [fileURLToPath(new URL("./support/project-fingerprint.py", import.meta.url)), ...(legacy ? ["--legacy"] : []), ...(discovery ? ["--discovery"] : [])]);
  const pending = []; let stderr = "";
  child.stderr.on("data", data => { stderr += data; });
  createInterface({ input: child.stdout }).on("line", line => {
    const { result, error, type } = JSON.parse(line), task = pending.shift();
    if (error) task.reject(Object.assign(new Error(error), { type, status: type === "StaleProjectSnapshotError" ? 409 : 422 })); else task.resolve(result);
  });
  child.on("exit", () => { for (const task of pending.splice(0)) task.reject(new Error(stderr || "Bridge exited")); });
  t.after(() => { child.stdin.end(); });
  const request = (op, generationId, body, extra = {}) => new Promise((resolve, reject) => {
    pending.push({ resolve, reject }); child.stdin.write(JSON.stringify({ op, generationId, body, ...extra }) + "\n");
  });
  const initial = await request("init");
  if (discovery) {
    const verified = await request("aspectCapabilities", initial.generationId, { nodeId: "115", inputName: "aspect_ratio", connectionId: initial.connection.id });
    const { id, workflow, fingerprint, ...body } = initial.profile;
    const binding = body.bindings.find(b => b.source.kind === "aspect_ratio");
    binding.source.valueMap = { ...verified.valueMap, ...binding.source.valueMap };
    binding.source.enumChoices = verified.choices;
    initial.profile = await request("profileSave", initial.generationId, body);
  }

  const state = { currentProjectId: initial.projectId, activeGenerationId: initial.generationId,
    activeProjectType: "filesystem", projectFingerprint: initial.projectFingerprint,
    projectEditFingerprint: initial.projectEditFingerprint, projectUpdatedAt: initial.projectUpdatedAt, contextReplacing: false };
  const navigationAuthority = createNavigationAuthority();
  const sandbox = { state, navigationAuthority };
  vm.createContext(sandbox);
  // Exercise the actual app callback and context supplier, not a copied adoption rule.
  if (source.includes("function projectMutationContext(")) {
    vm.runInContext(source.slice(source.indexOf("function projectMutationContext("), source.indexOf("async function init()")), sandbox);
  }
  const start = source.indexOf("  const outputMutation =", source.indexOf("async function init()"));
  vm.runInContext(source.slice(start, source.indexOf("  referenceAuthority =", start)) + "\nglobalThis.mutation = outputMutation;", sandbox);
  const doc = { createElement: tag => new Element(tag, doc), visibilityState: "visible" };
  const mounted = mountHtml(readFileSync(new URL("../../web/index.html", import.meta.url), "utf8"), doc);
  const $ = selector => mounted.querySelector(selector);
  for (const node of [$("#custom-ratio-wrap"), $("#dirty-indicator")])
    node.classList = { toggle() {} };
  const visible = {};
  Object.defineProperties(visible, {
    durationSeconds: { get: () => Number($("#duration").value), set: value => { $("#duration").value = value; }, enumerable: true },
    aspectRatio: { get: () => $("#aspect-ratio").value === "custom" ? $("#custom-ratio").value.trim() : $("#aspect-ratio").value,
      set: value => { $("#aspect-ratio").value = value; }, enumerable: true },
  });
  visible.durationSeconds = 8; visible.aspectRatio = "16:9";
  const getContext = () => ({ saved: true, projectId: state.currentProjectId, generationId: state.activeGenerationId,
    projectFingerprint: state.projectFingerprint, mode: "T2VA", ...visible, dirty: Boolean(state.dirty),
    projectEditFingerprint: state.projectEditFingerprint,
    ...(sandbox.projectMutationContext?.() || {}) });
  const root = new Element("section", doc), takesRoot = new Element("section", doc);
  let output = { prompt: initial.prompt, sourceFingerprint: initial.sourceFingerprint,
    origin: "generated_prompt", generationStatus: "complete" };
  const referenceCalls = [], setupCalls = [], prepareFingerprints = [], sendCalls = [];
  const api = {
    syncReferenceAssets: (p, g, body) => { referenceCalls.push(body); return request("references", g, body); },
    updateExecutionSetup: (p, g, body) => { setupCalls.push(body); return request("duration", g, body); },
    executionSelection: (p, g) => request("selection", g),
    workflowControls: (p, g) => request("controls", g),
    adoptManualEditExecutionPrompt: (p, g, body) => request("adopt", g, body),
    selectExecutionPrompt: (p, g, body) => request("select", g, body),
    executionProfiles: async () => [initial.profile], executionProfile: async () => initial.profile,
    executionConnections: async () => [initial.connection],
    prepareWorkflow: body => { prepareFingerprints.push(state.projectFingerprint); return request("prepare", body.generationId); },
    sendExecution: body => { sendCalls.push(body); return request("send", body.generationId, body); },
    generationOutputs: (p, g) => request("outputs", g),
    reconcileRenderSubmission: (p, g, id) => request("reconcile", g, undefined, { submissionId: id }),
    cancelRenderSubmission: (p, g, id) => request("cancel", g, undefined, { submissionId: id }),
    savedH3Prompt: (p, g) => request("saved", g), saveH3Prompt: (p, g, body) => request("save", g, body),
    loadH3Prompt: (p, g, id) => request("loadSaved", g, { id }),
  };
  const references = { assets: [], subjects: [] };
  const adoptEdit = (response, context) => sandbox.mutation(response, { ...context, projectEditMutation: true });
  const authority = createReferenceAuthority({ api, getContext, getReferences: () => references,
    onMutation: async (response, context) => { adoptEdit(response, context); if (response.changed) await workflow.invalidateReferences(); } });
  const workflow = createWorkflowPreparation({ root, api, getContext, getOutput: () => output,
    syncReferences: () => authority.sync(), onProjectMutation: adoptEdit, onSubmitted: sandbox.mutation });
  Object.assign(sandbox, { $, workflowPreparation: workflow, renderedTakes: null, storyPlanner: null });
  vm.runInContext(source.slice(source.indexOf("function setDirty("), source.indexOf("function captureContinuity(")), sandbox);
  vm.runInContext(source.split("\n").find(line => line.includes('$("#aspect-ratio").addEventListener("change"')), sandbox);
  doc.addEventListener = (name, handler) => { (doc.events ||= {})[name] = handler; };
  sandbox.document = doc; sandbox.shouldMarkWorkspaceDirty = shouldMarkWorkspaceDirty;
  vm.runInContext(source.split("\n").find(line => line.includes('document.addEventListener("input", event => { if (shouldMarkWorkspaceDirty')), sandbox);
  const uiEvent = async (id, value, event = "change") => {
    const node = $(`#${id}`); node.value = value;
    await node.emit(event);
    // The suite's minimal DOM does not bubble; dispatch the actual document input listener.
    if (event === "input") doc.events.input({ target: node });
  };
  const takes = createRenderedTakes({ root: takesRoot, api, getContext, onMutation: sandbox.mutation,
    setTimer: () => 1, clearTimer() {} });
  t.after(() => takes.dispose());
  const click = async id => { const button = root.querySelector(`#preparation-${id}`); assert.equal(button.disabled, false, id); await button.emit("click"); };
  const savedRoot = new Element("section", doc);
  for (const id of ["save-h3-prompt", "save-h3-prompt-as-new", "saved-h3-prompts", "load-saved-h3-prompt",
    "delete-saved-h3-prompt", "saved-h3-prompt-status", "saved-h3-prompt-details"]) {
    const node = new Element(id === "saved-h3-prompts" ? "select" : "button", doc); node.id = id; savedRoot.append(node);
  }
  const saved = createSavedPrompts({ root: savedRoot, api, getContext, getOutput: () => output,
    setOutput: value => { output = value; workflow.sync(); }, confirmReplace: () => true });
  const activations = [], loads = [];
  Object.assign(sandbox, { api: {
    activateGeneration: (p, g, fingerprint) => { activations.push(fingerprint); return request("activate", g, { expectedProjectFingerprint: fingerprint }); },
    loadProject: p => { loads.push(p); return request("load"); },
  }, storyPlanner: { refresh() {}, confirmContextChange: () => true, clearDraft() {} }, invalidateProviderContext() {},
    renderSequenceControls() {}, toast: message => assert.fail(message),
    adoptProjectResponse: project => {
      navigationAuthority.invalidate(); state.currentProjectId = project.id;
      state.activeGenerationId = project.sequence.activeGenerationId; state.projectFingerprint = project.projectFingerprint;
      state.projectEditFingerprint = project.projectEditFingerprint; state.projectUpdatedAt = project.projectUpdatedAt;
    } });
  vm.runInContext(source.slice(source.indexOf("async function switchGeneration("), source.indexOf("function structuralSequenceReady(")), sandbox);
  return { request, state, initial, navigationAuthority, sandbox, getContext, api, authority, workflow, takes, root, takesRoot,
    referenceCalls, setupCalls, prepareFingerprints, sendCalls, click, saved, savedRoot, visible, activations, loads, uiEvent, mounted, references, adoptEdit,
    switchGeneration: id => sandbox.switchGeneration(id),
    edit: text => { output = markOutputManuallyEdited(output, text); workflow.sync(); saved.sync(); },
    output: () => output };
}

test("live H3 ratio and duration replace saved authority before Prepare and exact mocked Send", async t => {
  const s = await fixture(t);
  s.state.projectFingerprint = (await s.request("setupSaved")).projectFingerprint;
  assert.equal((await s.request("workspace")).aspectRatio, "4:3");
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare");
  assert.ok(s.workflow.prepared, s.root.querySelector("#preparation-message").textContent);
  assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, "16:9 (Widescreen)");
  assert.equal(s.workflow.prepared.graph.h3.inputs.duration, 8);
  const resolution = s.workflow.prepared.resolutions.find(item => item.nodeId === "ratio");
  assert.equal(resolution.sourceValue, "16:9");
  assert.equal(resolution.rendererValue, "16:9 (Widescreen)");
  assert.equal((await s.request("workspace")).aspectRatio, "16:9");
  await s.click("send");
  assert.equal((await s.request("submitted"))[0].prompt.ratio.inputs.aspect_ratio, "16:9 (Widescreen)");
});

test("ratio changes invalidate Send until Prepare compiles the new ratio", async t => {
  const s = await fixture(t);
  s.visible.aspectRatio = "4:3";
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare");
  assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, "4:3 (Standard)");
  assert.equal(s.root.querySelector("#preparation-send").disabled, false);
  s.visible.aspectRatio = "16:9"; s.workflow.sync();
  assert.equal(s.workflow.prepared, null);
  await s.click("send");
  assert.match(s.root.querySelector("#preparation-message").textContent, /Prepare the current selection before Send/);
  assert.equal((await s.request("submitted")).length, 0);
  await s.click("prepare"); await s.click("send");
  assert.equal((await s.request("submitted"))[0].prompt.ratio.inputs.aspect_ratio, "16:9 (Widescreen)");
});

test("Reference then bounded H3 Setup receipts supply successive same-tab fingerprints", async t => {
  const s = await fixture(t);
  s.adoptEdit(await s.request("externalReference"), s.getContext());
  await s.workflow.refresh(); await s.click("use");
  s.visible.durationSeconds = 9; s.visible.aspectRatio = "4:3"; s.workflow.sync();
  // Restore a canonical reference independently, then adopt its receipt as this tab's authority.
  s.adoptEdit(await s.request("externalReference"), s.getContext());
  const before = s.state.projectFingerprint;
  await s.click("use");
  const afterReference = s.state.projectFingerprint;
  await s.click("prepare");
  assert.ok(s.workflow.prepared, s.root.querySelector("#preparation-message").textContent);
  assert.equal(s.referenceCalls.at(-1).expectedProjectFingerprint, before);
  assert.equal(s.setupCalls.at(-1).expectedProjectFingerprint, afterReference);
  assert.notEqual(s.prepareFingerprints.at(-1), s.setupCalls.at(-1).expectedProjectFingerprint);
  assert.equal(s.prepareFingerprints.at(-1), s.state.projectFingerprint);
  assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, "4:3 (Standard)");
});

test("late pre-navigation reconciliation cannot roll the active Project fingerprint back and block Prepare", async t => {
  const s = await fixture(t);
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare"); await s.click("send");
  const outputs = await s.request("outputs", s.initial.generationId);
  const submissionId = outputs.renderSubmissions[0].id;
  const late = deferred(), started = deferred();
  s.api.reconcileRenderSubmission = async () => {
    const response = await s.request("reconcile", s.initial.generationId, undefined, { submissionId });
    started.resolve(response); return late.promise;
  };
  const refresh = s.takes.refresh();
  const reconciled = await started.promise;
  assert.equal(reconciled.takes.length, 1, "status reconciliation really imports output and mutates project.json");
  assert.equal(await s.switchGeneration(s.initial.secondGenerationId), true);
  const activated = { projectFingerprint: s.state.projectFingerprint };
  assert.equal(s.loads.length, 1, "real switch observes reconciliation via its existing guarded 409 activation recovery");
  assert.equal(s.activations[1], reconciled.projectFingerprint);
  assert.notEqual(activated.projectFingerprint, reconciled.projectFingerprint);
  late.resolve(reconciled); await refresh;
  const browserAfterLate = s.state.projectFingerprint;
  // Current HEAD accepts the late receipt solely by Project ID, rolling back to
  // reconciled.projectFingerprint. The next real Reference sync rejects it.
  await s.workflow.refresh();
  s.edit(s.initial.prompt.replace("Rain and distant traffic.", "Steady rain and distant traffic."));
  await s.click("use"); await s.click("prepare");
  assert.ok(s.workflow.prepared, s.root.querySelector("#preparation-message").textContent);
  assert.equal(browserAfterLate, activated.projectFingerprint, "late receipt cannot undo navigation authority");
  assert.equal(s.referenceCalls.at(-1).expectedProjectFingerprint, activated.projectFingerprint);
  assert.equal(s.state.projectFingerprint, (await s.request("outputs", s.state.activeGenerationId)).projectFingerprint);
});

test("same-tab output import propagates its receipt; edited exact prompt and bounded duration Prepare and Send succeed", async t => {
  const s = await fixture(t);
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare"); await s.click("send");
  const before = s.state.projectFingerprint;
  await s.takes.refresh();
  const outputs = await s.request("outputs", s.state.activeGenerationId);
  assert.equal(outputs.takes.length, 1);
  assert.notEqual(outputs.projectFingerprint, before);
  assert.equal(s.state.projectFingerprint, outputs.projectFingerprint);
  const edited = s.initial.prompt.replace("Rain and distant traffic.", "Steady rain and distant traffic.") + "\n  ";
  s.edit(edited); s.visible.durationSeconds = 9; s.workflow.sync();
  await s.click("use");
  assert.equal(s.state.projectFingerprint, outputs.projectFingerprint, "prompt adoption is separate from manifest concurrency");
  await s.click("prepare");
  assert.ok(s.workflow.prepared, s.root.querySelector("#preparation-message").textContent);
  assert.equal(s.workflow.prepared.graph.h3.inputs.prompt, edited);
  assert.equal(s.workflow.prepared.graph.h3.inputs.duration, 9);
  const durationFingerprint = s.state.projectFingerprint;
  assert.notEqual(durationFingerprint, outputs.projectFingerprint);
  assert.equal(durationFingerprint, (await s.request("outputs", s.state.activeGenerationId)).projectFingerprint);
  const beforeSendReferences = s.referenceCalls.length;
  await s.click("send");
  assert.ok(s.referenceCalls.length <= beforeSendReferences + 1, "changed setup permits one normal revalidation, never a retry");
  assert.equal(s.sendCalls.length, 2);
  assert.match(s.root.querySelector("#preparation-receipt").textContent, /Queued in ComfyUI/);
  assert.doesNotMatch(s.root.textContent, /No queue acceptance receipt|Project changed since loading/);
  assert.equal(s.output().prompt, edited, "execution preserves exact visible authoring");
});

test("an independent client's Reference change still rejects stale browser synchronization without retry", async t => {
  const s = await fixture(t), before = s.state.projectFingerprint;
  const external = await s.request("externalReference", s.state.activeGenerationId);
  assert.notEqual(external.projectFingerprint, before);
  await assert.rejects(s.authority.sync(), /Project changed since loading; reload before changing Reference Assets/);
  assert.equal(s.referenceCalls.length, 1);
  assert.equal(s.referenceCalls[0].expectedProjectFingerprint, before);
  assert.equal(s.state.projectFingerprint, before);
  assert.equal((await s.request("outputs", s.state.activeGenerationId)).projectFingerprint, external.projectFingerprint);
});

test("Saved H3 Prompt load/edit/Save and Save As New remain sidecar-only through Prepare and mocked Send", async t => {
  const s = await fixture(t);
  s.state.projectFingerprint = (await s.request("setupSaved")).projectFingerprint;
  await s.workflow.refresh(); await s.saved.load(); await s.saved.saveCurrent(); await s.saved.loadSelected();
  const target = s.output().savedPromptTarget.id, before = s.state.projectFingerprint;
  s.edit("Transient text to replace"); await s.saved.loadSelected();
  assert.equal(s.output().prompt, s.initial.prompt, "Load uses the real saved sidecar snapshot");
  const edited = s.initial.prompt.replace("Rain and distant traffic.", "Steady rain and distant traffic.");
  s.edit(edited); await s.saved.saveCurrent();
  assert.equal(s.output().savedPromptTarget.id, target);
  assert.equal((await s.request("outputs", s.state.activeGenerationId)).projectFingerprint, s.state.projectFingerprint);
  await s.click("use"); await s.click("prepare");
  assert.equal(s.workflow.prepared.graph.h3.inputs.prompt, edited);
  const setupFingerprint = s.state.projectFingerprint;
  assert.notEqual(setupFingerprint, before);
  await s.saved.saveCurrent(true);
  assert.notEqual(s.output().savedPromptTarget.id, target);
  assert.equal((await s.request("outputs", s.state.activeGenerationId)).projectFingerprint, setupFingerprint);
  await s.click("use"); await s.click("prepare"); await s.click("send");
  assert.ok(s.workflow.prepared);
  assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, "16:9 (Widescreen)");
  assert.equal((await s.request("submitted"))[0].prompt.ratio.inputs.aspect_ratio, "16:9 (Widescreen)");
  assert.match(s.root.querySelector("#preparation-receipt").textContent, /Queued in ComfyUI/);
  assert.doesNotMatch(s.root.textContent, /Project changed since loading/);
});

test("late duration receipt after Generation navigation cannot update fingerprint or compile in the old context", async t => {
  const s = await fixture(t);
  await s.workflow.refresh(); await s.click("use");
  const late = deferred(), started = deferred();
  s.api.updateExecutionSetup = async (p, g, body) => {
    const response = await s.request("duration", g, body); started.resolve(response); return late.promise;
  };
  s.visible.durationSeconds = 9; s.workflow.sync();
  const prepare = s.click("prepare");
  const old = await started.promise;
  s.navigationAuthority.begin(s.state.currentProjectId, s.state.activeGenerationId);
  const activated = await s.request("activate", s.initial.secondGenerationId);
  s.navigationAuthority.invalidate(); s.state.activeGenerationId = activated.generationId;
  s.state.projectFingerprint = activated.projectFingerprint; s.workflow.sync();
  s.state.projectEditFingerprint = activated.projectEditFingerprint; s.state.projectUpdatedAt = activated.projectUpdatedAt;
  late.resolve(old); await prepare;
  assert.equal(s.state.projectFingerprint, activated.projectFingerprint);
  assert.equal(s.workflow.prepared, null);
});

test("mutation receipt guards reject another Project, superseded navigation and same-Project ABA", async t => {
  const s = await fixture(t), original = s.getContext();
  s.sandbox.mutation({ projectFingerprint: "known-current" }, original);
  assert.equal(s.state.projectFingerprint, "known-current");
  const pending = s.getContext();
  s.navigationAuthority.begin(s.state.currentProjectId, s.state.activeGenerationId);
  s.sandbox.mutation({ projectFingerprint: "superseded" }, pending);
  assert.equal(s.state.projectFingerprint, "known-current");
  s.state.currentProjectId = "other"; s.navigationAuthority.invalidate();
  s.sandbox.mutation({ projectFingerprint: "wrong-project" }, pending);
  assert.equal(s.state.projectFingerprint, "known-current");
  s.state.currentProjectId = original.projectId; s.navigationAuthority.invalidate();
  s.sandbox.mutation({ projectFingerprint: "old-ABA" }, pending);
  assert.equal(s.state.projectFingerprint, "known-current");
});

test("fresh background reconciliation of an offscreen Generation propagates Project-wide authority", async t => {
  const s = await fixture(t);
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare"); await s.click("send");
  const first = await s.request("outputs", s.initial.generationId);
  s.navigationAuthority.begin(s.state.currentProjectId, s.state.activeGenerationId);
  const activated = await s.request("activate", s.initial.secondGenerationId);
  s.navigationAuthority.invalidate(); s.state.activeGenerationId = activated.generationId;
  s.state.projectFingerprint = activated.projectFingerprint;
  s.state.projectEditFingerprint = activated.projectEditFingerprint; s.state.projectUpdatedAt = activated.projectUpdatedAt;
  s.api.foregroundRenders = async () => ({ candidate: { id: first.renderSubmissions[0].id,
    generationId: s.initial.generationId, generationNumber: 1, sendNumber: 1, job: { status: "queued" } } });
  await s.takes.refresh();
  const after = await s.request("outputs", s.initial.generationId);
  assert.equal(after.takes.length, 1);
  assert.notEqual(after.projectFingerprint, activated.projectFingerprint);
  assert.equal(s.state.projectFingerprint, after.projectFingerprint);
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare");
  assert.ok(s.workflow.prepared, s.root.querySelector("#preparation-message").textContent);
  assert.equal(s.referenceCalls.at(-1).expectedProjectFingerprint, after.projectFingerprint);
});

test("a delayed running poll superseded by exact cancellation cannot roll back the active fingerprint before Prepare", async t => {
  const s = await fixture(t);
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare"); await s.click("send");
  const outputs = await s.request("outputs", s.state.activeGenerationId), id = outputs.renderSubmissions[0].id;
  const late = deferred(), started = deferred();
  s.api.reconcileRenderSubmission = async () => {
    const response = await s.request("running", s.state.activeGenerationId, undefined, { submissionId: id });
    started.resolve(response); return late.promise;
  };
  const refresh = s.takes.refresh();
  const running = await started.promise;
  const cancel = s.takesRoot.querySelector(`#takes-cancel-${id}`);
  assert.equal(cancel.disabled, false); await cancel.emit("click");
  const cancelled = await s.request("outputs", s.state.activeGenerationId);
  assert.equal(cancelled.renderSubmissions[0].job.status, "cancelled");
  assert.notEqual(cancelled.projectFingerprint, running.projectFingerprint);
  assert.equal(s.state.projectFingerprint, cancelled.projectFingerprint);
  late.resolve(running); await refresh;
  await s.workflow.refresh(); s.edit(s.initial.prompt + "\n ");
  await s.click("use"); await s.click("prepare");
  assert.ok(s.workflow.prepared, s.root.querySelector("#preparation-message").textContent);
  assert.equal(s.state.projectFingerprint, cancelled.projectFingerprint);
});


test("mounted actual dropdown 4:3 -> 9:16 invalidates preparation and keeps Send attemptable", async t => {
  const s = await fixture(t);
  await s.uiEvent("aspect-ratio", "4:3");
  await s.workflow.refresh(); await s.click("use"); await s.click("prepare"); await s.click("send");
  assert.equal((await s.request("submitted"))[0].prompt.ratio.inputs.aspect_ratio, "4:3 (Standard)");
  await s.uiEvent("aspect-ratio", "9:16");
  assert.equal(s.visible.aspectRatio, "9:16");
  assert.equal(s.state.dirty, true);
  assert.equal(s.workflow.prepared, null);
  assert.equal(s.root.querySelector("#preparation-send").disabled, false);
  await s.click("prepare");
  assert.equal(s.root.querySelector("#preparation-send").disabled, false);
  await s.click("send");
  assert.equal(s.setupCalls.at(-1).aspectRatio, "9:16");
  assert.equal((await s.request("workspace")).aspectRatio, "9:16");
  assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, "9:16 (Portrait Widescreen)");
  const resolution = s.workflow.prepared.resolutions.find(item => item.nodeId === "ratio");
  assert.equal(resolution.sourceValue, "9:16"); assert.equal(resolution.rendererValue, "9:16 (Portrait Widescreen)");
  assert.equal((await s.request("submitted"))[1].prompt.ratio.inputs.aspect_ratio, "9:16 (Portrait Widescreen)");
});



const ratioVocabulary = { "auto": "auto", "16:9": "16:9 (Widescreen)", "4:3": "4:3 (Standard)", "9:16": "9:16 (Portrait Widescreen)", "1:1": "1:1 (Square)", "3:4": "3:4 (Portrait Standard)", "3:2": "3:2 (Photo)" };
// Renderer vocabulary verified read-only from the saved Profile's enum choices for node 115 / aspect_ratio.
for (const [ratio, renderer] of Object.entries(ratioVocabulary)) {
  test(`mounted standard ratio ${ratio} reaches canonical setup, inspection and exact /prompt`, async t => {
    const s = await fixture(t); await s.workflow.refresh();
    await s.uiEvent("aspect-ratio", ratio);
    await s.click("prepare");
    assert.equal(s.setupCalls.at(-1).aspectRatio, ratio);
    assert.equal((await s.request("workspace")).aspectRatio, ratio);
    assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, renderer);
    const inspected = s.workflow.prepared.resolutions.find(item => item.nodeId === "ratio");
    assert.equal(inspected.sourceValue, ratio); assert.equal(inspected.rendererValue, renderer);
    assert.equal(s.root.querySelector("#preparation-send").disabled, false);
    await s.click("send");
    assert.equal((await s.request("submitted"))[0].prompt.ratio.inputs.aspect_ratio, renderer);
  });
}

test("actual custom-ratio input and duration input invalidate old preparation", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare");
  await s.uiEvent("aspect-ratio", "custom");
  assert.equal(s.workflow.prepared, null);
  await s.uiEvent("custom-ratio", "2.39:1", "input"); await s.click("prepare");
  assert.equal(s.workflow.prepared.graph.ratio.inputs.aspect_ratio, "2.39:1");
  assert.ok(s.workflow.prepared.warnings.some(w => w.includes("raw value will be sent")));
  await s.uiEvent("custom-ratio", "2:1", "input");
  assert.equal(s.workflow.prepared, null); assert.equal(s.root.querySelector("#preparation-send").disabled, false);
  await s.click("prepare"); await s.click("send");
  assert.equal((await s.request("submitted"))[0].prompt.ratio.inputs.aspect_ratio, "2:1");
  await s.uiEvent("duration", "9", "input"); assert.equal(s.workflow.prepared, null);
  await s.click("prepare"); assert.equal(s.workflow.prepared.graph.h3.inputs.duration, 9);
});

for (const legacy of [false, true]) {
for (const duplicate of [false, true]) {
  test(`mounted ${legacy ? "legacy" : "modern"} ratio change after ${duplicate ? "Duplicate Generation" : "Generation switch"} stays isolated`, async t => {
    const s = await fixture(t, legacy); await s.uiEvent("aspect-ratio", "4:3");
    await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
    const g1 = s.initial.generationId;
    const target = duplicate ? await s.request("duplicate", g1) : { generationId: s.initial.secondGenerationId };
    if (duplicate) s.state.projectFingerprint = target.projectFingerprint;
    await s.switchGeneration(target.generationId);
    // Load the destination's canonical setup as project adoption does, then emit a real user change.
    const setup = await s.request("workspace", target.generationId);
    await s.uiEvent("aspect-ratio", setup.aspectRatio);
    if (duplicate) assert.equal(setup.aspectRatio, "4:3");
    await s.uiEvent("aspect-ratio", "9:16"); await s.workflow.refresh();
    await s.click("prepare"); await s.click("send");
    assert.equal((await s.request("workspace", g1)).aspectRatio, "4:3");
    assert.equal((await s.request("workspace", target.generationId)).aspectRatio, "9:16");
    assert.equal(s.workflow.prepared.graph[s.initial.ratioNodeId].inputs.aspect_ratio, "9:16 (Portrait Widescreen)");
    assert.equal((await s.request("submitted"))[1].prompt[s.initial.ratioNodeId].inputs.aspect_ratio, "9:16 (Portrait Widescreen)");
    await s.switchGeneration(g1); await s.uiEvent("aspect-ratio", "4:3");
    await s.workflow.refresh(); await s.click("prepare");
    assert.equal(s.workflow.prepared.graph[s.initial.ratioNodeId].inputs.aspect_ratio, "4:3 (Standard)");
    assert.equal((await s.request("workspace", target.generationId)).aspectRatio, "9:16");
  });
}

}

test("Send handler rejects current DOM setup even when its change event was missed", async t => {
  const s = await fixture(t); await s.uiEvent("aspect-ratio", "4:3");
  await s.workflow.refresh(); await s.click("prepare");
  s.mounted.querySelector("#aspect-ratio").value = "9:16"; // deliberately omit the event to test the second defense
  await s.root.querySelector("#preparation-send").emit("click");
  assert.equal(s.workflow.prepared, null);
  assert.equal((await s.request("submitted")).length, 0);
  assert.equal(s.root.querySelector("#preparation-send").disabled, false);
});

for (const mode of ["random", "fixed", "workflow_default"]) {
  test(`three immediate Prepare/Send cycles preserve exact identity and ${mode} renderer seeds`, async t => {
    const s = await fixture(t);
    const seedConfig = await s.request("seed", s.initial.generationId, { mode, ...(mode === "fixed" ? { fixedSeed: 42 } : {}) });
    Object.assign(s.initial.profile, seedConfig.profile);
    await s.workflow.refresh(); await s.click("use");
    for (let i = 0; i < 3; i++) {
      await s.click("prepare"); assert.ok(s.workflow.prepared, s.root.textContent);
      await s.click("send"); assert.match(s.root.querySelector("#preparation-receipt").textContent, /Queued in ComfyUI/);
      assert.equal(s.state.projectFingerprint, (await s.request("outputs", s.initial.generationId)).projectFingerprint);
    }
    const submitted = await s.request("submitted"), outputs = await s.request("outputs", s.initial.generationId);
    assert.equal(submitted.length, 3); assert.equal(s.sendCalls.length, 3);
    assert.equal(new Set(submitted.map(row => row.prompt_id)).size, 3);
    assert.equal(new Set(outputs.renderSubmissions.map(row => row.id)).size, 3);
    assert.equal(s.referenceCalls.length, 1, "unchanged saved references are verified once");
    assert.deepEqual(submitted.map(row => row.prompt.seed.inputs.seed), mode === "random" ? [1000, 1001, 1002] : mode === "fixed" ? [42, 42, 42] : [123, 123, 123]);
    for (const record of await s.request("submissions", s.initial.generationId)) {
      const request = submitted.find(row => row.prompt_id === record.comfyPromptId);
      assert.equal(request.client_id, record.comfyPromptId);
      assert.equal(record.rendererSeed, mode === "workflow_default" ? null : request.prompt.seed.inputs.seed);
      assert.equal(record.promptSnapshot.prompt, s.initial.prompt);
      assert.match(record.executionFingerprint, /^[0-9a-f]{64}$/);
    }
  });
}

test("background status changes during Reference and H3 Setup sync permit one guarded write each", async t => {
  const s = await fixture(t);
  await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
  const id = (await s.request("outputs", s.initial.generationId)).renderSubmissions[0].id;
  // A new navigation ticket deliberately requires revalidation of the collection.
  s.navigationAuthority.invalidate();
  const originalReferences = s.api.syncReferenceAssets, originalSetup = s.api.updateExecutionSetup;
  let referenceWrites = 0, setupWrites = 0;
  s.api.syncReferenceAssets = async (...args) => {
    referenceWrites++;
    await s.request("running", s.initial.generationId, undefined, { submissionId: id });
    return originalReferences(...args);
  };
  s.api.updateExecutionSetup = async (...args) => {
    setupWrites++;
    await s.request("running", s.initial.generationId, undefined, { submissionId: id });
    return originalSetup(...args);
  };
  await s.click("prepare"); assert.ok(s.workflow.prepared, s.root.textContent);
  assert.equal(referenceWrites, 1); assert.equal(setupWrites, 1);
  await s.click("send"); assert.equal(s.sendCalls.length, 2);
  assert.equal(referenceWrites, 1, "Send does not repeat the guarded Reference write");
});

test("late older same-navigation receipts cannot undo newer authority before Prepare", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
  const context = s.getContext(), id = (await s.request("outputs", context.generationId)).renderSubmissions[0].id;
  const older = await s.request("running", context.generationId, undefined, { submissionId: id });
  const newer = await s.request("reconcile", context.generationId, undefined, { submissionId: id });
  s.sandbox.mutation(newer, context); s.sandbox.mutation(older, context);
  assert.equal(s.state.projectFingerprint, newer.projectFingerprint);
  await s.click("prepare"); assert.ok(s.workflow.prepared, s.root.textContent);
});

test("revision ordering uses timestamp instants across legacy precision and time-zone formats", async t => {
  const s = await fixture(t), context = s.getContext();
  s.state.projectUpdatedAt = "2026-10-17T00:00:00Z";
  s.sandbox.mutation({ projectFingerprint: "newer", projectUpdatedAt: "2026-10-17T00:00:00.001Z" }, context);
  assert.equal(s.state.projectFingerprint, "newer");
  s.sandbox.mutation({ projectFingerprint: "older", projectUpdatedAt: "2026-10-17T10:00:00+10:00" }, context);
  assert.equal(s.state.projectFingerprint, "newer");
});

test("external authoring conflict survives a renderer receipt and never retries a sync or Send", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
  const context = s.getContext(), before = s.state.projectFingerprint;
  const external = await s.request("externalAuthoring");
  const id = (await s.request("outputs", context.generationId)).renderSubmissions[0].id;
  const rendererReceipt = await s.request("running", context.generationId, undefined, { submissionId: id });
  s.sandbox.mutation(rendererReceipt, context);
  assert.equal(s.state.projectFingerprint, before, "output receipt cannot acknowledge an unseen authoring edit");
  const writes = s.setupCalls.length;
  await s.click("prepare"); assert.equal(s.workflow.prepared, null);
  assert.match(s.root.textContent, /Project changed since loading/);
  assert.equal(s.setupCalls.length, writes + 1); assert.equal(s.sendCalls.length, 1);
  assert.equal((await s.request("workspace")).creativeRequest, "External authoring edit");
  assert.notEqual(external.projectFingerprint, before);
});

test("real Reference changes synchronize once and invalidate selection and prepared execution", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare");
  Object.assign(s.references, await s.request("picture"));
  await s.authority.sync();
  assert.equal(s.workflow.prepared, null);
  assert.equal((await s.request("selection", s.initial.generationId)).selected, null);
  assert.equal((await s.request("workspace")).assets.length, 1);
  const writes = s.referenceCalls.length;
  await s.authority.sync(); assert.equal(s.referenceCalls.length, writes);
  await s.click("send"); assert.equal(s.sendCalls.length, 0);
});

test("Main Prompt draft after Prepare reaches exact mocked Send without saving or revalidating References", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare");
  const prepared = s.workflow.prepared, saved = await s.request("workspace");
  const writes = [s.referenceCalls.length, s.setupCalls.length];
  await s.uiEvent("creative-request", "Unsaved Main Prompt draft", "input");
  assert.equal(s.state.dirty, true); assert.equal(s.workflow.prepared, prepared);
  await s.click("send");
  assert.deepEqual([s.referenceCalls.length, s.setupCalls.length], writes);
  assert.deepEqual(await s.request("workspace"), saved);
  assert.equal(s.mounted.querySelector("#creative-request").value, "Unsaved Main Prompt draft");
  assert.equal(s.state.dirty, true);
  const submitted = await s.request("submitted"), records = await s.request("submissions");
  assert.equal(submitted.length, 1); assert.equal(records.length, 1);
  assert.equal(submitted[0].prompt.h3.inputs.prompt, prepared.selectedPrompt.prompt);
  assert.equal(records[0].executionFingerprint, prepared.executionFingerprint);
  assert.equal(records[0].comfyPromptId, submitted[0].prompt_id);
});

test("unseen unrelated Project edit and delayed renderer receipt cannot block an already prepared Send", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
  // Prepare's setup write advances edit authority after the Reference cache check.
  // The former Send path would re-enter the Reference PUT with that newer receipt.
  s.visible.durationSeconds = 9; s.workflow.sync();
  await s.click("prepare"); const prepared = s.workflow.prepared, context = s.getContext();
  const id = (await s.request("outputs")).renderSubmissions[0].id;
  const late = deferred(), captured = deferred();
  const poll = (async () => {
    const response = await s.request("running", context.generationId, undefined, { submissionId: id });
    captured.resolve(); await late.promise; s.sandbox.mutation(response, context);
  })();
  await captured.promise;
  await s.request("externalName");
  late.resolve(); await poll;
  assert.equal(s.workflow.prepared, prepared);
  // Reproduce the former pre-Send failure through the real Reference authority:
  // setup advanced its cached revision, then the external rename made its guard stale.
  await assert.rejects(s.authority.sync(), /Project changed since loading; reload before changing Reference Assets/);
  assert.equal(s.workflow.prepared, prepared);
  const writes = s.referenceCalls.length;
  await s.click("send");
  assert.equal(s.referenceCalls.length, writes); assert.equal(s.sendCalls.length, 2);
  assert.match(s.root.querySelector("#preparation-receipt").textContent, /Queued in ComfyUI/);
  assert.equal((await s.request("workspace")).projectName, "Renamed elsewhere");
  assert.equal((await s.request("submissions")).length, 2);
  assert.equal((await s.request("submitted"))[1].prompt.h3.inputs.prompt, prepared.selectedPrompt.prompt);
});

test("external Reference edit after Prepare rejects Send at backend without queueing or overwriting", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare");
  await s.request("externalReference");
  const writes = s.referenceCalls.length;
  await s.click("send");
  assert.equal(s.sendCalls.length, 1); assert.equal(s.referenceCalls.length, writes);
  assert.equal((await s.request("submitted")).length, 0);
  assert.equal((await s.request("workspace")).assets.length, 1);
  assert.match(s.root.querySelector("#preparation-message").textContent, /[Ss]elect|[Pp]repare/);
  assert.doesNotMatch(s.root.textContent, /Project changed since loading/);
});

test("unsaved Story draft uses the app dirty callback without invalidating or saving prepared execution", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare");
  const prepared = s.workflow.prepared, workspace = await s.request("workspace");
  const writes = [s.referenceCalls.length, s.setupCalls.length];
  s.state.storyIdea = "Unsaved Story text"; s.sandbox.setDirty();
  assert.equal(s.state.dirty, true); assert.equal(s.workflow.prepared, prepared);
  await s.click("send");
  assert.equal(s.state.storyIdea, "Unsaved Story text"); assert.equal(s.state.dirty, true);
  assert.deepEqual(await s.request("workspace"), workspace);
  assert.deepEqual([s.referenceCalls.length, s.setupCalls.length], writes);
  assert.equal((await s.request("submitted")).length, 1);
  assert.equal((await s.request("submissions"))[0].executionFingerprint, prepared.executionFingerprint);
});

test("Reference write committed but awaiting its UI receipt cannot execute the stale preparation", async t => {
  const s = await fixture(t); await s.workflow.refresh(); await s.click("prepare");
  Object.assign(s.references, await s.request("picture"));
  const committed = deferred(), late = deferred(), sync = s.api.syncReferenceAssets;
  s.api.syncReferenceAssets = async (...args) => {
    const response = await sync(...args); committed.resolve(); await late.promise; return response;
  };
  const pending = s.authority.sync(); await committed.promise;
  assert.ok(s.workflow.prepared, "Reference receipt has not reached the UI yet");
  await s.click("send");
  assert.equal(s.sendCalls.length, 1); assert.equal((await s.request("submitted")).length, 0);
  late.resolve(); await pending;
  assert.equal(s.workflow.prepared, null);
  assert.equal((await s.request("selection")).selected, null);
});

for (const ratio of ["2:3", "21:9"]) {
  for (const duplicate of [false, true]) {
    test(`mounted discovered ${ratio} survives reopen and ${duplicate ? "duplication" : "Generation switching"}`, async t => {
      const s = await fixture(t, false, true);
      await s.uiEvent("aspect-ratio", ratio); await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
      const original = s.initial.generationId;
      await s.request("load");
      assert.equal((await s.request("workspace", original)).aspectRatio, ratio);
      const destination = duplicate ? await s.request("duplicate", original) : { generationId: s.initial.secondGenerationId };
      if (duplicate) s.state.projectFingerprint = destination.projectFingerprint;
      await s.switchGeneration(destination.generationId);
      if (duplicate) assert.equal((await s.request("workspace", destination.generationId)).aspectRatio, ratio);
      await s.uiEvent("aspect-ratio", "1:1"); await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
      await s.switchGeneration(original);
      const saved = await s.request("workspace", original);
      await s.uiEvent("aspect-ratio", saved.aspectRatio); await s.workflow.refresh(); await s.click("prepare"); await s.click("send");
      assert.equal(saved.aspectRatio, ratio);
      const renderer = ratio === "2:3" ? "2:3 (Portrait Photo)" : "21:9 (Ultrawide)";
      assert.equal(s.workflow.prepared.graph["115"].inputs.aspect_ratio, renderer);
      assert.equal((await s.request("submitted")).at(-1).prompt["115"].inputs.aspect_ratio, renderer);
      assert.equal((await s.request("workspace", destination.generationId)).aspectRatio, "1:1");
    });
  }
}
