import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFileSync } from "node:fs";
import { snapshotGenerationRequest, generationClickIntent } from "../../web/js/streaming.js";
let source = readFileSync(new URL("../../web/js/app.js", import.meta.url), "utf8").replaceAll("\r\n", "\n");
const between = (a, b) => source.slice(source.indexOf(a), source.indexOf(b, source.indexOf(a)));
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { promise, resolve }; };
function fixture() {
  const gate = deferred();
  const state = { assets: [{ id: "old", kind: "image" }], subjects: [], outputs: [], sequenceGenerations: [],
    currentProjectId: "p", activeGenerationId: "g", planningRecommendations: [], referenceManifest: null };
  const elements = new Map();
  const element = key => { if (!elements.has(key)) elements.set(key, { textContent: "", classList: { add() {}, remove() {} } }); return elements.get(key); };
  let epoch = 0, activity, manifestId = 0;
  const noop = () => {};
  const context = { state, AbortController, structuredClone, Map, Set, Date,
    generationClickIntent, snapshotGenerationRequest,
    providerActivity: { active: () => activity, finish: () => { activity = null; }, cancelActive: () => activity.cancel(), conflict: noop },
    providerAttempt: (id, label, status, cancel) => { activity = { id, cancel }; return {}; },
    providerContextCurrent: e => e === epoch,
    invalidateProviderContext: () => { epoch++; activity?.cancel(); },
    navigationAuthority: { current: t => t.id === epoch },
    outputMutation: noop, adoptSavedMedia: async () => {}, renderAssets: noop,
    renderPlanningRecommendations: noop, markRevisionStale: noop,
    workflowPreparation: { invalidateReferences: async () => {}, sync: noop },
    storyPlanner: { invalidateReferences: noop }, savedPrompts: { load: noop },
    refreshReferenceManifest: async () => { const id = ++manifestId; await gate.promise; if (id === manifestId) state.referenceManifest = structuredClone(state.assets); },
    syncReferenceAuthority: async () => {}, persistSettings: noop, ensureDialogueResolution: async () => {},
    buildGenerateRequest: () => ({ workspace: { variations: 1, assets: state.assets }, executionRouting: { assets: {} } }),
    updateEjectButton: noop, renderWarnings: noop, setGenerationControls: noop, renderPerformance: noop,
    setGenerationStatus: text => { state.status = text; }, renderOutput: noop,
    updateStreamedOutput: () => { state.visibleText = state.outputs[0].prompt; }, toast: noop,
    renderedTakes: { sync: noop }, $: element,
    api: { streamGenerate: async (request, options) => { context.sent = request; context.stream = options; await context.finish.promise; } },
    finish: deferred(), providerContextEpoch: 0,
  };
  vm.createContext(context);
  vm.runInContext(between('function stopGeneration()', 'function updateStreamedOutput'), context);
  vm.runInContext(between('async function generate(', 'function renderWarnings('), context);
  const originalSource = source; source = source.slice(source.indexOf('  referenceAuthority = createReferenceAuthority'));
  const callback = between('    onMutation: async (response, context) => {', '\n  });\n  renderedTakes').trim().replace(/^onMutation: /, '').replace(/,$/, '');
  source = originalSource;
  context.mutate = vm.runInContext(`(${callback})`, context);
  return { state, context, gate, async start() { const p = context.generate(); for (let i=0; i<10 && !context.stream; i++) await Promise.resolve(); assert.ok(context.stream); return { done: p }; },
    change: () => context.mutate({ changed: true }, { projectId: "p", navigationTicket: { id: epoch } }),
    event: (event, data) => context.stream.onEvent({ event, data }),
    navigate: () => { context.invalidateProviderContext(); state.currentProjectId = "other"; },
  };
}
for (const action of ["add", "remove", "replace"]) test(`${action} reference while H3 streams preserves request, text and completion provenance`, async () => {
  const f = fixture(), { done } = await f.start();
  f.event("delta", { variation: 0, text: "first" });
  const session = f.state.generation;
  f.state.assets = action === "add" ? [...f.state.assets, { id: "new", kind: "image" }] : action === "remove" ? [] : [{ id: "replacement", kind: "image" }];
  const change = f.change();
  for (let i=0;i<10;i++) await Promise.resolve();
  assert.equal(session.controller.signal.aborted, false);
  assert.equal(f.state.generation, session);
  assert.equal(f.state.outputs[0].prompt, "first");
  f.event("delta", { variation: 0, text: " second" });
  assert.equal(f.state.visibleText, "first second");
  f.gate.resolve(); await change;
  assert.deepEqual(f.state.referenceManifest, f.state.assets);
  assert.deepEqual(f.context.sent.workspace.assets, [{ id: "old", kind: "image" }]);
  f.event("complete", { variation: 0, prompt: "first second", referenceMap: [{ id: "old" }], sourceFingerprint: "original" });
  f.context.finish.resolve(); await done;
  assert.equal(f.state.status, "Complete");
  assert.equal(f.state.outputs[0].sourceFingerprint, "original");
  assert.deepEqual(f.state.outputs[0].referenceMap, [{ id: "old" }]);
  f.context.finish = deferred();
  f.context.stream = null;
  const next = await f.start();
  assert.deepEqual(f.context.sent.workspace.assets, f.state.assets);
  f.context.finish.resolve(); await next.done;
});
test("explicit Stop aborts and late deltas are ignored", async () => {
  const f = fixture(), { done } = await f.start();
  f.context.stopGeneration();
  assert.equal(f.context.stream.signal.aborted, true);
  f.event("delta", { variation: 0, text: "late" });
  assert.equal(f.state.outputs[0].prompt, "");
  f.context.finish.resolve(); await done;
});
test("navigation cancels H3 and delayed reference invalidation cannot refresh the new context", async () => {
  const f = fixture(), { done } = await f.start();
  const wait = deferred(); f.context.workflowPreparation.invalidateReferences = () => wait.promise;
  const change = f.change(); await Promise.resolve(); await Promise.resolve();
  f.navigate(); wait.resolve(); await change;
  assert.equal(f.context.stream.signal.aborted, true);
  assert.equal(f.state.referenceManifest, null);
  f.event("complete", { variation: 0, prompt: "stale" });
  assert.equal(f.state.outputs[0].prompt, "");
  f.context.finish.resolve(); await done;
});


test("out-of-order manifest replies cannot replace latest references or affect H3 ownership", async () => {
  const f = fixture(), { done } = await f.start();
  const replies = [deferred(), deferred()]; let calls = 0;
  f.context.api.referenceManifest = () => replies[calls++].promise;
  Object.assign(f.context, { manifestRequestBody: () => ({}), renderHandoffUi() {}, applyCanonicalAssetLabels() {}, renderInputMap() {} });
  vm.runInContext('let referenceManifestRequest = 0;\n' + between('async function refreshReferenceManifest()', 'function requestReferenceManifestRefresh()'), f.context);
  const old = f.context.refreshReferenceManifest(); await Promise.resolve();
  const latest = f.context.refreshReferenceManifest(); await Promise.resolve();
  replies[1].resolve({ entries: [{ id: "new" }] }); await latest;
  replies[0].resolve({ entries: [{ id: "old" }] }); await old;
  assert.equal(f.state.referenceManifest.entries[0].id, "new");
  assert.equal(f.context.stream.signal.aborted, false);
  f.event("delta", { variation: 0, text: "still streaming" });
  assert.equal(f.state.visibleText, "still streaming");
  f.context.finish.resolve(); await done;
});
test("a superseded session cannot accept late events", async () => {
  const f = fixture(), { done } = await f.start();
  f.state.generation = { outputs: [] };
  f.event("delta", { variation: 0, text: "stale" });
  f.event("complete", { variation: 0, prompt: "stale" });
  assert.equal(f.state.outputs[0].prompt, "");
  f.context.finish.resolve(); await done;
  assert.ok(f.state.generation, "old finalizer cannot clear a replacement session");
});
