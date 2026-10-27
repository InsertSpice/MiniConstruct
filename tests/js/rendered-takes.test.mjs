import assert from "node:assert/strict";
import test from "node:test";
import { Element } from "./support/dom.mjs";
import { createRenderedTakes } from "../../web/js/rendered-takes.js";
import { api as realApi } from "../../web/js/api.js";
import { formatLocalTimestamp } from "../../web/js/local-time.js";
import { renderSubmissionLabels } from "../../web/js/render-submission-label.js";

function setup(options = {}) {
  const handlers = new Map(), windowHandlers = new Map();
  const doc = { visibilityState: "visible", createElement: tag => new Element(tag, doc),
    addEventListener: (name, fn) => handlers.set(name, fn), removeEventListener: name => handlers.delete(name),
    defaultView: { addEventListener: (name, fn) => windowHandlers.set(name, fn), removeEventListener: name => windowHandlers.delete(name) },
    dispatch: name => handlers.get(name)?.(), dispatchWindow: name => windowHandlers.get(name)?.() };
  const root = new Element("div", doc), liveRoot = new Element("section", doc);
  const context = { saved: true, projectId: "project", generationId: "one" };
  const empty = () => ({ takes: [], renderSubmissions: [], selectedTakeId: null, projectFingerprint: "new-fingerprint" });
  const states = new Map([["one", empty()], ["two", empty()]]), calls = [], reads = [], mutations = [];
  let selection = { selected: null }, allowDelete = true;
  const state = g => states.get(g), response = g => structuredClone(state(g));
  const api = {
    generationOutputs: async (p, g) => { reads.push(["outputs", p, g]); return response(g); },
    executionSelection: async (p, g) => { reads.push(["selection", p, g]); return structuredClone(selection); },
    takeMediaUrl: realApi.takeMediaUrl,
    importTake: async (p, g, file, provenance) => {
      calls.push(["import", p, g, file, provenance]);
      state(g).takes.push({ id: `take-${state(g).takes.length + 1}`, originalFilename: file.name,
        byteSize: 2048, createdAt: "2026-09-20", availability: "available", ...provenance,
        promptProvenance: provenance.sourceKind === "external_unknown" ? "unknown" : "known" });
      return response(g);
    },
    selectTake: async (p, g, id) => { state(g).selectedTakeId = id; return response(g); },
    clearSelectedTake: async (p, g) => { state(g).selectedTakeId = null; return response(g); },
    deleteTake: async (p, g, id) => { calls.push(["delete", id]); state(g).takes = state(g).takes.filter(t => t.id !== id);
      if (state(g).selectedTakeId === id) state(g).selectedTakeId = null; return response(g); },
    reattachTake: async (p, g, id, file) => { calls.push(["reattach", p, g, id, file]); state(g).takes.find(t => t.id === id).availability = "available"; return response(g); },
    takeDetail: async () => ({ promptSnapshot: { prompt: "<script>historical text</script>", sha256: "hash" } }),
  };
  const ui = createRenderedTakes({ root, liveRoot, api, getContext: () => context,
    onMutation: (response, c) => mutations.push([response, c]), confirmDelete: () => allowDelete, ...options });
  const find = id => { const node = root.querySelector(`#takes-${id}`); assert.ok(node, id); return node; };
  const inspect = async id => { find("inspected").value = id; await find("inspected").emit("change"); };
  const click = async id => {
    const takeAction = id.match(/^(?:select|preview|delete|provenance)-(.+)$/);
    if (takeAction && find("inspected").value !== takeAction[1]) await inspect(takeAction[1]);
    if (id.startsWith("watch-") && find(id).textContent === "Watching") {
      assert.equal(find(id).disabled, true); return;
    }
    assert.equal(find(id).disabled, false, id); await find(id).emit("click");
  };
  return { root, liveRoot, ui, context, api, states, calls, reads, mutations, find, click, inspect,
    setSelection: value => selection = value, allowDelete: value => allowDelete = value,
    async import(kind = "external_unknown", filename = "video.mp4") {
      find("source").value = kind; await find("source").emit("change");
      find("file").files = [{ name: filename }]; await find("file").emit("change"); await click("import");
    } };
}

test("Continue action uses explicitly selected Take even while inspecting another", async () => {
  const continued = [];
  const s = setup({ onContinue: async (...args) => { continued.push(args); } });
  await s.ui.refresh(); await s.import(); await s.import();
  await s.click("continue"); assert.equal(continued[0][0], undefined);
  await s.click("select-take-1"); await s.inspect("take-2");
  await s.click("continue");
  assert.equal(continued[1][0].id, "take-1");
  assert.equal(continued[1][1], undefined);
  assert.equal(s.states.get("one").selectedTakeId, "take-1");
  assert.deepEqual(s.calls.map(call => call[0]), ["import", "import"]);
});

test("saved and unsaved zero state; source is explicit and never defaults to latest", async () => {
  const s = setup(); await s.ui.refresh();
  assert.match(s.root.textContent, /No rendered Takes yet/);
  assert.equal(s.find("manual-import").open, false);
  assert.equal(s.find("source").value, ""); assert.equal(s.find("import").disabled, true);
  assert.match(s.root.textContent, /does not analyze.*later H3/);
  s.context.saved = false; s.ui.sync();
  assert.match(s.root.textContent, /Save a filesystem Project/);
});

test("import, multiple Takes, explicit selection switches and clear; only one preview", async () => {
  const s = setup(); await s.ui.refresh(); await s.import(); await s.import();
  assert.equal(s.root.querySelectorAll(".take-card").length, 1);
  assert.equal(s.root.querySelectorAll(".take-selected").length, 0);
  assert.match(s.root.textContent, /External \/ unknown source · Prompt provenance: unknown/);
  await s.click("select-take-1"); assert.equal(s.states.get("one").selectedTakeId, "take-1");
  await s.click("select-take-2"); assert.equal(s.root.querySelectorAll(".take-selected").length, 1);
  assert.equal(s.states.get("one").selectedTakeId, "take-2");
  await s.click("preview-take-1");
  assert.equal(s.root.querySelector("video").src, "/api/projects/project/generations/one/takes/take-1/media");
  await s.click("preview-take-2"); assert.equal(s.root.querySelectorAll("video").length, 1);
  await s.click("clear"); assert.equal(s.root.querySelectorAll(".take-selected").length, 0);
  assert.ok(s.mutations.length >= 5);
});

test("manual imports immediately inspect the new UUID, retain older Takes and preserve explicit selection", async () => {
  const s = setup(); await s.ui.refresh();
  const reads = s.reads.length;
  s.find("manual-import").open = true;
  await s.import("external_unknown", "same.mp4");
  assert.equal(s.find("inspected").value, "take-1");
  assert.equal(s.find("inspected").children.length, 1);
  assert.match(s.root.querySelector(".take-card").textContent, /Take ID: take-1/);
  assert.equal(s.states.get("one").selectedTakeId, null);
  await s.click("select-take-1"); await s.click("preview-take-1");
  // Reverse the canonical response to prove the new UUID is not inferred from order.
  const importTake = s.api.importTake;
  s.api.importTake = async (...args) => {
    const response = await importTake(...args); response.takes.reverse(); return response;
  };
  await s.import("external_unknown", "same.mp4");
  assert.equal(s.find("inspected").value, "take-2");
  assert.deepEqual(new Set(s.find("inspected").children.map(option => option.value)), new Set(["take-1", "take-2"]));
  assert.match(s.root.querySelector(".take-card").textContent, /Take ID: take-2/);
  assert.equal(s.states.get("one").selectedTakeId, "take-1");
  assert.equal(s.root.querySelector("video"), null);
  assert.equal(s.find("file").value, "");
  assert.equal(s.find("source").value, "external_unknown");
  assert.equal(s.find("manual-import").open, true);
  assert.equal(s.reads.length, reads, "imports use canonical mutation responses without extra refreshes");
  await s.inspect("take-1"); assert.match(s.root.querySelector(".take-card").textContent, /Selected Take/);
});

test("failed manual import retains useful form and existing Take without inventing output", async () => {
  const s = setup(); await s.ui.refresh(); await s.import();
  s.api.importTake = async () => { throw new Error("upload failed"); };
  s.find("file").value = "chosen.mp4"; await s.import();
  assert.match(s.find("status").textContent, /upload failed/);
  assert.equal(s.find("file").value, "chosen.mp4");
  assert.equal(s.find("source").value, "external_unknown");
  assert.equal(s.find("inspected").children.length, 1);
  assert.equal(s.find("inspected").value, "take-1");
});

test("pending Story authority guards selected Take mutations while preview stays available", async () => {
  let allowed = false;
  const s = setup({ canChangeObservedState: () => allowed });
  await s.ui.refresh(); await s.import();
  await s.click("select-take-1");
  assert.equal(s.states.get("one").selectedTakeId, null);
  assert.match(s.find("status").textContent, /Wait for the Story proposal request/);
  await s.click("preview-take-1");
  assert.ok(s.root.querySelector("video"));
  allowed = true; await s.click("select-take-1");
  assert.equal(s.states.get("one").selectedTakeId, "take-1");
  allowed = false; await s.click("clear");
  assert.equal(s.states.get("one").selectedTakeId, "take-1");
  await s.click("delete-take-1");
  assert.equal(s.states.get("one").takes.length, 1);
  allowed = true; await s.click("clear");
  assert.equal(s.states.get("one").selectedTakeId, null);
});

test("selected-prompt import sends captured selection ID; submission is never guessed", async () => {
  const s = setup(); s.setSelection({ selected: { id: "prompt-selection", prompt: "exact text" } });
  s.states.get("one").renderSubmissions.push({ id: "submission-1", comfyPromptId: "comfy-job", createdAt: "now" });
  await s.ui.refresh();
  assert.match(s.root.textContent, /ComfyUI Prompt ID: comfy-job/);
  assert.equal(s.find("submission").value, "");
  await s.import("selected_prompt");
  assert.deepEqual(s.calls[0][4], { sourceKind: "selected_prompt", selectedPromptId: "prompt-selection" });
  s.find("source").value = "comfyui_submission"; await s.find("source").emit("change");
  assert.equal(s.find("import").disabled, true);
  s.find("submission").value = "submission-1"; await s.find("submission").emit("change");
  await s.import("comfyui_submission");
  assert.deepEqual(s.calls[1][4], { sourceKind: "comfyui_submission", renderSubmissionId: "submission-1" });
  await s.click("provenance-take-1"); assert.match(s.root.textContent, /historical text/);
});

test("Generation switching restores canonical list and selection", async () => {
  const s = setup(); await s.ui.refresh(); await s.import(); await s.click("select-take-1");
  s.context.generationId = "two"; s.ui.sync(); await s.ui.refresh();
  assert.equal(s.root.querySelectorAll(".take-card").length, 0);
  s.context.generationId = "one"; s.ui.sync(); await s.ui.refresh();
  assert.equal(s.root.querySelectorAll(".take-selected").length, 1);
  assert.equal(s.root.querySelector("video"), null);
});

test("manual import is a collapsed fallback and provenance controls are conditional", async () => {
  const s = setup(); s.setSelection({ selected: { id: "prompt-selection", prompt: "exact text" } });
  s.states.get("one").renderSubmissions.push({ id: "submission-1", comfyPromptId: "comfy-job", createdAt: "now", job: { status: "completed", outputs: [] } });
  await s.ui.refresh();
  assert.equal(s.find("manual-import").open, false);
  s.find("manual-import").open = true;
  s.find("source").value = "selected_prompt"; await s.find("source").emit("change");
  assert.equal(s.find("submission").hidden, true);
  assert.match(s.root.textContent, /immutable currently selected execution prompt/);
  s.find("source").value = "external_unknown"; await s.find("source").emit("change");
  assert.equal(s.find("submission").hidden, true);
  assert.doesNotMatch(s.root.textContent, /currently selected execution prompt/);
  s.find("source").value = "comfyui_submission"; await s.find("source").emit("change");
  assert.equal(s.find("submission").hidden, false);
  assert.equal(s.find("submission").value, "");
});

test("renderer history defaults closed, preserves user disclosure state and summarizes active jobs", async () => {
  const s = setup();
  s.states.get("one").takes.push({ id: "take-1", originalFilename: "render.mp4", byteSize: 2,
    createdAt: "now", availability: "available", sourceKind: "comfyui_submission", promptProvenance: "known" });
  s.states.get("one").renderSubmissions.push({ id: "done", comfyPromptId: "done-job", createdAt: "now",
    job: { status: "completed", outputs: [{ id: "out", filename: "render.mp4", type: "output", importedTakeId: "take-1" }] } });
  await s.ui.refresh();
  assert.equal(s.root.querySelector("#takes-manual-import").open, false);
  assert.equal(s.root.querySelector("#takes-render-history").open, false);
  s.states.get("one").renderSubmissions.push({ id: "running", comfyPromptId: "running-job", createdAt: "now", job: { status: "running", outputs: [] } });
  await s.ui.refresh();
  assert.equal(s.root.querySelector("#takes-render-history").open, false);
  assert.match(s.find("render-history").querySelector("summary").textContent, /ComfyUI render history \(2\) · 1 running/);
  s.find("render-history").open = true; await s.ui.refresh();
  assert.equal(s.find("render-history").open, true);
  s.find("render-history").open = false; await s.ui.refresh();
  assert.equal(s.find("render-history").open, false);
  assert.match(s.root.textContent, /Running/);
  s.context.generationId = "two"; s.find("render-history").open = true;
  s.ui.sync(); await s.ui.refresh(); assert.equal(s.find("render-history").open, false);
  s.ui.dispose();
});

test("render submission and Take dates use the browser local readable format", async () => {
  const s = setup();
  const timestamp = "2026-09-21T18:08:51Z";
  s.states.get("one").renderSubmissions.push({ id: "submission-1", comfyPromptId: "comfy-job", createdAt: timestamp });
  s.states.get("one").takes.push({ id: "take-1", originalFilename: "clip.mp4", byteSize: 2048,
    createdAt: timestamp, availability: "available", sourceKind: "external_unknown", promptProvenance: "unknown" });
  await s.ui.refresh();
  assert.match(s.root.textContent, new RegExp(formatLocalTimestamp(timestamp).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.doesNotMatch(s.root.textContent, /2026-09-21T18:08:51Z/);
});

test("submission selector uses the shared human label while IDs stay in technical details", async () => {
  const s = setup();
  const records = [
    { id: "submission-late", comfyPromptId: "prompt-late", createdAt: "2026-09-21T18:08:51Z" },
    { id: "submission-early", comfyPromptId: "prompt-early", createdAt: "2026-09-21T15:44:39Z" },
  ];
  s.states.get("one").renderSubmissions.push(...records);
  await s.ui.refresh();
  const labels = renderSubmissionLabels(records);
  assert.equal(s.find("submission").children[1].textContent, labels.get("submission-late"));
  assert.equal(s.find("submission").children[2].textContent, labels.get("submission-early"));
  assert.doesNotMatch(s.find("submission").children[1].textContent, /prompt-late|submission-late/);
  assert.match(s.root.querySelectorAll("details").at(-1).textContent, /prompt-early|submission-early/);
});

test("context replacement gates reads until the new Generation is authoritative", async () => {
  const s = setup(); await s.ui.refresh(); s.reads.length = 0;
  s.context.saved = false; s.context.generationId = "old-generation"; s.ui.sync(); await Promise.resolve();
  assert.deepEqual(s.reads, []);
  s.context.saved = true; s.context.generationId = "two"; s.ui.sync(); await s.ui.refresh();
  assert.ok(s.reads.every(call => call[2] === "two"));
});

test("same Generation sync preserves the output and observation surface", async () => {
  const s = setup(); await s.ui.refresh(); await s.import();
  const reads = s.reads.length; s.ui.sync();
  assert.equal(s.root.querySelectorAll(".take-card").length, 1);
  assert.equal(s.reads.length, reads);
});

test("missing media, reattach and delete confirmation", async () => {
  const s = setup(); await s.ui.refresh(); await s.import();
  s.states.get("one").takes[0].availability = "missing"; await s.ui.refresh();
  assert.match(s.root.textContent, /Media is missing/);
  assert.equal(s.root.querySelector("video"), null);
  const input = s.root.querySelector(".take-card").querySelector("input"); input.files = [{ name: "same.mp4" }];
  await input.emit("change"); assert.doesNotMatch(s.root.textContent, /Media is missing/);
  assert.equal(s.calls.at(-1)[0], "reattach");
  s.allowDelete(false); await s.click("delete-take-1"); assert.equal(s.states.get("one").takes.length, 1);
  s.allowDelete(true); await s.click("delete-take-1"); assert.equal(s.states.get("one").takes.length, 0);
});

test("late import and read responses cannot contaminate another Generation", async () => {
  const s = setup(); await s.ui.refresh(); let resolve;
  s.api.importTake = () => new Promise(r => resolve = r);
  s.find("source").value = "external_unknown"; await s.find("source").emit("change");
  s.find("file").files = [{ name: "old-file.mp4" }]; await s.find("file").emit("change");
  const pending = s.click("import");
  s.context.generationId = "two"; s.ui.sync(); await s.ui.refresh();
  resolve({ takes: [{ id: "old", originalFilename: "old-file" }], renderSubmissions: [], projectFingerprint: "after-g1" }); await pending;
  assert.doesNotMatch(s.root.textContent, /old-file/); assert.equal(s.mutations.length, 1);
  assert.equal(s.mutations[0][0].projectFingerprint, "after-g1");
  assert.deepEqual(s.mutations[0][1], { saved: true, projectId: "project", generationId: "one" });
  assert.equal(s.root.querySelectorAll(".take-card").length, 0);
});

test("Project-scoped mutation receipts accept another Generation but reject another Project", () => {
  let activeProject = "project-p", fingerprint = "before";
  const apply = (response, context) => {
    if (context.projectId === activeProject && response.projectFingerprint) fingerprint = response.projectFingerprint;
  };
  apply({ projectFingerprint: "p-g1-after" }, { projectId: "project-p", generationId: "g1" });
  assert.equal(fingerprint, "p-g1-after");
  apply({ projectFingerprint: "q-after" }, { projectId: "project-q", generationId: "g1" });
  assert.equal(fingerprint, "p-g1-after");
});

test("API uploads raw files and constructs ID-only preview paths", async () => {
  const oldFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({ takes: [] }) }; };
  try {
    const file = { name: "take a.mp4", size: 50, type: "video/mp4" };
    await realApi.importTake("p", "g", file, { sourceKind: "external_unknown" });
    assert.equal(calls[0][1].body, file);
    assert.match(calls[0][0], /filename=take\+a.mp4&sourceKind=external_unknown/);
    assert.equal(realApi.takeMediaUrl("p", "g", "take/id"), "/api/projects/p/generations/g/takes/take%2Fid/media");
    await realApi.reattachTake("p", "g", "t", file); assert.equal(calls[1][1].method, "PUT");
  } finally { globalThis.fetch = oldFetch; }
});

function pollingSetup() {
  const timers = new Map(); let id = 0;
  const s = setup({ setTimer: (fn, ms) => { assert.equal(ms, 15000); timers.set(++id, fn); return id; },
    clearTimer: id => timers.delete(id) });
  s.states.get("one").renderSubmissions.push({ id: "send-1", comfyPromptId: "prompt-1", createdAt: "2026-09-22T01:00:00Z", job: { status: "queued", outputs: [] } });
  return { ...s, timers, async tick() { assert.equal(timers.size, 1); const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); } };
}

test("mounted exact job polling shows lifecycle, automatic Take and explicit selection", async () => {
  const s = pollingSetup(), calls = [];
  const stages = ["queued", "running", "completed"];
  s.api.reconcileRenderSubmission = async (p, g, id) => {
    calls.push([p, g, id]); const state = s.states.get(g), status = stages.shift();
    state.renderSubmissions[0].job = { status, outputs: [] };
    if (status === "completed") {
      state.takes.push({ id: "auto", originalFilename: "exact.mp4", renderSubmissionId: id,
        sourceKind: "comfyui_submission", promptProvenance: "known", availability: "available", byteSize: 20 });
      state.renderSubmissions[0].job.outputs.push({ id: "out-1", filename: "exact.mp4", type: "output", importedTakeId: "auto" });
    }
    return structuredClone(state);
  };
  await s.ui.refresh(); assert.match(s.root.textContent, /Queued/);
  await s.tick(); assert.match(s.root.textContent, /Running/);
  await s.tick(); assert.match(s.root.textContent, /Completed · Take imported/);
  assert.equal(s.timers.size, 0);
  assert.equal(s.root.querySelectorAll(".take-card").length, 1);
  assert.match(s.root.querySelector(".take-card").textContent, /ComfyUI send 1/);
  assert.equal(s.states.get("one").selectedTakeId, null);
  assert.deepEqual(calls, Array(3).fill(["project", "one", "send-1"]));
  await s.click("preview-auto"); assert.match(s.root.querySelector("video").src, /takes\/auto\/media$/);
  await s.click("select-auto"); assert.equal(s.states.get("one").selectedTakeId, "auto");
  s.ui.dispose();
});

test("failed, unknown, zero and multiple output statuses never guess a Take", async () => {
  const s = pollingSetup(); let status = "failed", outputs = [];
  s.api.reconcileRenderSubmission = async () => {
    const state = structuredClone(s.states.get("one")); state.renderSubmissions[0].job = { status, outputs }; return state;
  };
  await s.ui.refresh(); assert.match(s.root.textContent, /Failed/); assert.equal(s.timers.size, 0);
  status = "unknown"; await s.ui.refresh(); assert.match(s.root.textContent, /Unknown/); assert.equal(s.timers.size, 1);
  status = "completed"; await s.ui.refresh(); assert.match(s.root.textContent, /no retrievable video output/); assert.equal(s.timers.size, 0);
  outputs = ["one", "two"].map(id => ({ id, filename: `${id}.mp4`, subfolder: "safe", type: "output" }));
  await s.ui.refresh();
  assert.equal(s.root.querySelectorAll(".take-card").length, 0);
  let imported;
  s.api.importRenderOutput = async (...args) => { imported = args; return structuredClone(s.states.get("one")); };
  await s.click("output-send-1-two"); assert.deepEqual(imported, ["project", "one", "send-1", "two"]);
  s.ui.dispose();
});

for (const change of ["generation", "project", "unmount", "page-hidden"]) {
  test(`late renderer response and polling stop after ${change}`, async () => {
    const s = pollingSetup(); let finish, queried = 0;
    s.api.reconcileRenderSubmission = async () => { queried++; return new Promise(resolve => finish = resolve); };
    const pending = s.ui.refresh();
    while (!finish) await Promise.resolve();
    if (change === "generation") s.context.generationId = "two";
    if (change === "project") { s.context.projectId = "other"; s.context.generationId = "two"; }
    if (change === "unmount") s.ui.dispose();
    if (change === "page-hidden") { s.root.ownerDocument.visibilityState = "hidden"; s.ui.dispose(); }
    if (["generation", "project"].includes(change)) { s.ui.sync(); await s.ui.refresh(); }
    finish({ takes: [{ id: "stale", originalFilename: "stale-video.mp4" }], renderSubmissions: [], projectFingerprint: "old-project-receipt" });
    await pending;
    assert.doesNotMatch(s.root.textContent, /stale-video/);
    assert.equal(s.timers.size, 0); assert.equal(queried, 1);
    assert.equal(s.mutations[0][1].projectId, "project");
    s.ui.dispose();
  });
}

test("hidden or removed surfaces stop scheduled polling; explicit refresh reconciles reopened old sends", async () => {
  const s = pollingSetup(); let calls = 0;
  s.api.reconcileRenderSubmission = async () => { calls++; return structuredClone(s.states.get("one")); };
  await s.ui.refresh(); assert.equal(calls, 1);
  s.root.isConnected = false; await s.tick(); assert.equal(calls, 1); assert.equal(s.timers.size, 0);
  s.root.isConnected = true; await s.click("refresh"); assert.equal(calls, 2);
  s.root.ownerDocument.visibilityState = "hidden"; await s.tick(); assert.equal(calls, 2); assert.equal(s.timers.size, 0);
  s.ui.dispose();
});

test("renderer API sends only stable path IDs, no URL, prompt, filename or video payload", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({}) }; };
  try {
    await realApi.reconcileRenderSubmission("p", "g", "s");
    await realApi.recoverRenderSubmission("p", "g", "s");
    await realApi.retryRenderSubmission("p", "g", "s");
    await realApi.cancelRenderSubmission("p", "g", "s");
    await realApi.importRenderOutput("p", "g", "s", "candidate");
    assert.deepEqual(calls.map(([url]) => url), ["/api/projects/p/generations/g/render-submissions/s/reconcile", "/api/projects/p/generations/g/render-submissions/s/recover", "/api/projects/p/generations/g/render-submissions/s/retry", "/api/projects/p/generations/g/render-submissions/s/cancel", "/api/projects/p/generations/g/render-submissions/s/outputs/candidate/import"]);
    assert.ok(calls.every(([, options]) => options.method === "POST" && options.body === undefined));
  } finally { globalThis.fetch = previous; }
});

test("live API constructs same-origin MiniConstruct socket from canonical IDs", () => {
  const oldLocation = globalThis.location, oldWebSocket = globalThis.WebSocket, urls = [];
  globalThis.location = { protocol: "https:", host: "localhost:8000" };
  globalThis.WebSocket = class { constructor(url) { urls.push(url); } };
  try {
    realApi.watchRenderSubmission("project", "generation", "submission");
    assert.deepEqual(urls, ["wss://localhost:8000/api/projects/project/generations/generation/render-submissions/submission/live"]);
  } finally { globalThis.location = oldLocation; globalThis.WebSocket = oldWebSocket; }
});

function liveSetup(options = {}) {
  const s = setup(options), sockets = [], watched = [], cancelled = [];
  s.context.generations = ["one", "two", "three"].map(generationId => ({ generationId }));
  s.states.set("three", { takes: [], renderSubmissions: [], selectedTakeId: null });
  s.api.foregroundRenders = async (p, target) => {
    s.reads.push(["foreground", p, target]);
    const rows = s.context.generations.flatMap((generation, index) => {
      const submissions = [...s.states.get(generation.generationId).renderSubmissions].sort((a, b) =>
        String(a.createdAt).localeCompare(String(b.createdAt)) || String(a.id).localeCompare(String(b.id)));
      return submissions.map((item, send) => ({ ...structuredClone(item), generationId: generation.generationId,
        generationNumber: index + 1, sendNumber: send + 1 }));
    });
    const priorities = { running: 0, queued: 1 };
    const active = rows.filter(row => Object.hasOwn(priorities, row.job?.status)).sort((a, b) =>
      priorities[a.job.status] - priorities[b.job.status] || a.generationNumber - b.generationNumber
      || (Date.parse(b.createdAt) || 0) - (Date.parse(a.createdAt) || 0) || String(b.id).localeCompare(String(a.id)));
    return { candidate: active[0] || null,
      watched: rows.find(row => row.generationId === target?.generationId && row.id === target?.id) || null };
  };
  for (const id of ["a", "b"]) s.states.get("one").renderSubmissions.push({ id, comfyPromptId: `prompt-${id}`,
    createdAt: id === "a" ? "2026-10-04T01:00:00Z" : "2026-10-04T00:00:00Z", job: { status: "running", outputs: [] } });
  s.api.watchRenderSubmission = (p, g, id) => {
    watched.push([p, g, id]);
    const socket = { id, closed: false, close() { this.closed = true; },
      push(data) { this.onmessage?.({ data: typeof data === "string" || data instanceof ArrayBuffer ? data : JSON.stringify(data) }); } };
    sockets.push(socket); return socket;
  };
  s.api.cancelRenderSubmission = async (p, g, id) => { cancelled.push([p, g, id]); return structuredClone(s.states.get(g)); };
  return { ...s, sockets, watched, cancelled };
}

test("generated-state reset closes watcher, releases preview and rejects late same-Project reconciliation", async () => {
  const s = liveSetup();
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL, revoked = [];
  URL.createObjectURL = () => "blob:reset-preview"; URL.revokeObjectURL = value => revoked.push(value);
  try {
    await s.ui.refresh(); const socket = s.sockets[0];
    socket.push({ type: "preview-meta", mime: "video/mp4" }); socket.push(new Uint8Array([1]).buffer);
    let finish;
    s.api.reconcileRenderSubmission = () => new Promise(resolve => { finish = resolve; });
    socket.push({ type: "terminal", status: "completed" });
    assert.equal(typeof finish, "function");
    s.ui.reset();
    assert.equal(socket.closed, true); assert.deepEqual(revoked, ["blob:reset-preview"]);
    s.states.get("one").renderSubmissions = [];
    await s.ui.refresh();
    finish({ takes: [{ id: "old-take" }], renderSubmissions: [], projectFingerprint: "old-fingerprint" });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(s.mutations, []);
    assert.equal(s.liveRoot.hidden, true); assert.match(s.root.textContent, /No rendered Takes yet/);
    assert.doesNotMatch(s.root.textContent, /old-take/);
  } finally { s.ui.dispose(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
});

test("generated-state reset ignores a pending same-Generation read", async () => {
  const s = setup(); let finish;
  s.api.generationOutputs = () => new Promise(resolve => { finish = resolve; });
  const pending = s.ui.refresh(); s.ui.reset();
  finish({ takes: [{ id: "old-take" }], renderSubmissions: [] }); await pending;
  assert.doesNotMatch(s.root.textContent, /old-take/);
  s.ui.dispose();
});

test("promoted live mount is independent of the bottom history disclosure", async () => {
  const s = liveSetup(); await s.ui.refresh();
  const region = s.liveRoot.querySelector(".render-live-status"), history = s.find("render-history");
  assert.equal(s.root.querySelector(".render-live-status"), null);
  assert.equal(region.closest("#takes-render-history"), undefined);
  assert.equal(s.root.children.at(-1), history); assert.equal(history.open, false);
  assert.equal(s.liveRoot.hidden, false);
  const socket = s.sockets[0];
  socket.push({ type: "preview-meta", mime: "image/jpeg" }); socket.push(new Uint8Array([1]).buffer);
  const image = region.querySelector("img"), url = image.src;
  history.open = true; await s.ui.refresh(); history.open = false; await s.ui.refresh();
  socket.push({ type: "progress", nodeId: "sampler", value: 3, max: 10 });
  assert.equal(region.hidden, false); assert.equal(region.querySelector("progress").value, 3);
  assert.equal(socket.closed, false); assert.equal(s.sockets.length, 1);
  assert.equal(region.querySelector("img"), image); assert.equal(image.src, url);
  assert.match(region.textContent, /Elapsed/);
  assert.doesNotMatch(s.liveRoot.textContent, /prompt-a|prompt-b|Prompt ID|RenderSubmission/);
  s.ui.dispose(); assert.equal(s.liveRoot.hidden, true);
});

test("automatic observation ranks canonical status, timestamp, then stable ID without selecting output", async () => {
  const s = liveSetup(), state = s.states.get("one");
  state.renderSubmissions = [
    { id: "unknown", createdAt: "2026-10-04T12:00:00Z", job: { status: "unknown" } },
    { id: "queued", createdAt: "2026-10-04T11:00:00Z", job: { status: "queued" } },
    { id: "older", createdAt: "2026-10-03T01:00:00Z", job: { status: "running" } },
    { id: "tie-a", createdAt: "2026-10-04T01:00:00Z", job: { status: "running" } },
    { id: "tie-z", createdAt: "2026-10-04T01:00:00Z", job: { status: "running" } },
    { id: "completed", createdAt: "2026-10-04T13:00:00Z", job: { status: "completed" } },
  ];
  await s.ui.refresh(); assert.equal(s.watched.at(-1)[2], "tie-z");
  for (const id of ["tie-z", "tie-a", "older", "queued"]) {
    assert.equal(s.watched.at(-1)[2], id);
    state.renderSubmissions.find(item => item.id === id).job.status = "completed";
    await s.ui.refresh();
  }
  assert.deepEqual(s.watched.map(([, , id]) => id), ["tie-z", "tie-a", "older", "queued"]);
  assert.equal(s.liveRoot.hidden, true); assert.equal(state.selectedTakeId, null);
  assert.deepEqual(s.calls, []); assert.deepEqual(s.mutations, []);
  s.ui.dispose();
});

for (const [from, to] of [["queued", "running"], ["unknown", "queued"], ["unknown", "running"]]) {
  test(`automatic ${from} presentation yields to confirmed ${to} work without selecting output`, async () => {
    const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
    URL.createObjectURL = () => "blob:priority-upgrade";
    URL.revokeObjectURL = value => revoked.push(value);
    const s = liveSetup();
    try {
      const [a, b] = s.states.get("one").renderSubmissions;
      a.job.status = b.job.status = from; b.createdAt = "2026-10-04T02:00:00Z";
      await s.ui.refresh();
      if (from === "unknown") {
        assert.equal(s.liveRoot.hidden, true); assert.deepEqual(s.watched, []);
        a.job.status = to; await s.ui.refresh();
        assert.deepEqual(s.watched, [["project", "one", "a"]]);
        assert.equal(s.liveRoot.hidden, false); assert.equal(b.job.status, "unknown");
        assert.deepEqual(s.calls, []); assert.equal(s.states.get("one").selectedTakeId, null);
        return;
      }
      assert.deepEqual(s.watched, [["project", "one", "b"]]);
      const socketB = s.sockets[0];
      // Transport presentation must not outrank canonical submission job status.
      socketB.push({ type: "state", status: "running" });
      socketB.push({ type: "preview-meta", mime: "image/jpeg" }); socketB.push(new Uint8Array([1]).buffer);
      socketB.push({ type: "progress", nodeId: "old-b", value: 2, max: 8 });
      a.job.status = to; await s.ui.refresh();
      assert.equal(socketB.closed, true); assert.deepEqual(revoked, ["blob:priority-upgrade"]);
      assert.deepEqual(s.watched, [["project", "one", "b"], ["project", "one", "a"]]);
      assert.equal(s.liveRoot.hidden, false); assert.equal(s.liveRoot.querySelector("img"), null);
      assert.equal(s.liveRoot.querySelector("progress").hidden, true);
      s.sockets[1].push({ type: "progress", nodeId: "current-a", value: 1, max: 10 });
      assert.match(s.liveRoot.textContent, /current-a/); assert.doesNotMatch(s.liveRoot.textContent, /old-b/);
      assert.equal(s.find("watch-a").textContent, "Watching");
      assert.deepEqual(s.cancelled, []); assert.equal(s.states.get("one").selectedTakeId, null);
      assert.deepEqual(s.mutations, []);
    } finally { s.ui.dispose(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
  });
}

for (const status of ["running", "queued"]) {
  test(`automatic ${status} watcher ignores newer same-priority timestamps and later IDs`, async () => {
    const s = liveSetup();
    try {
      const state = s.states.get("one"), [a, b] = state.renderSubmissions;
      state.renderSubmissions = [a]; a.job.status = status; await s.ui.refresh();
      const socketA = s.sockets[0]; socketA.push({ type: "connected" });
      b.job.status = status; b.createdAt = "2026-10-04T02:00:00Z";
      state.renderSubmissions.push(b, { id: "z", createdAt: a.createdAt, job: { status } });
      await s.ui.refresh(); await s.ui.refresh();
      assert.equal(socketA.closed, false); assert.equal(s.sockets.length, 1);
      assert.deepEqual(s.watched, [["project", "one", "a"]]);
    } finally { s.ui.dispose(); }
  });
}

test("explicit queued Watch overrides running candidates until terminal, then selection is automatic again", async () => {
  const s = liveSetup();
  try {
    const state = s.states.get("one"), [a, b] = state.renderSubmissions;
    b.job.status = "queued"; await s.ui.refresh(); await s.click("watch-b");
    const explicitSocket = s.sockets[1];
    state.renderSubmissions.push({ id: "z", createdAt: "2026-10-04T02:00:00Z", job: { status: "running" } });
    await s.ui.refresh();
    assert.equal(explicitSocket.closed, false);
    assert.deepEqual(s.watched.map(([, , id]) => id), ["a", "b"]);
    // Reconcile the explicit attempt terminal; the next choice uses automatic mode.
    b.job.status = "completed"; explicitSocket.push({ type: "terminal", status: "completed" });
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(explicitSocket.closed, true); assert.equal(s.watched.at(-1)[2], "z");
    state.renderSubmissions.find(item => item.id === "z").job.status = "unknown";
    await s.ui.refresh();
    assert.equal(s.sockets[2].closed, true);
    assert.deepEqual(s.watched.map(([, , id]) => id), ["a", "b", "z", "a"]);
    assert.equal(a.job.status, "running"); assert.deepEqual(s.cancelled, []);
  } finally { s.ui.dispose(); }
});

test("terminal events reconcile the exact watcher even during an in-flight output refresh", async () => {
  const s = liveSetup(); s.states.get("one").renderSubmissions[1].job.status = "queued";
  s.states.get("one").renderSubmissions[1].createdAt = "2026-10-04T02:00:00Z";
  await s.ui.refresh();
  let release, blocked = true; const exact = [];
  s.api.reconcileRenderSubmission = async (p, g, id) => {
    exact.push([p, g, id]);
    if (blocked) { blocked = false; await new Promise(resolve => release = resolve); }
    else s.states.get(g).renderSubmissions.find(item => item.id === id).job.status = "completed";
    return structuredClone(s.states.get(g));
  };
  const pending = s.ui.refresh(); await new Promise(resolve => setImmediate(resolve));
  s.sockets[0].push({ type: "terminal", status: "completed" });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(exact, [["project", "one", "a"], ["project", "one", "a"]]);
  assert.equal(s.sockets[0].closed, true); assert.equal(s.watched.at(-1)[2], "b");
  release(); await pending; s.ui.dispose();
});

test("new Send automatically chooses the current Generation active attempt; separate submissions never merge progress", async () => {
  const s = liveSetup();
  await s.ui.watchSubmitted({ renderSubmissionId: "b" }, { ...s.context });
  assert.deepEqual(s.watched, [["project", "one", "a"]]);
  s.sockets[0].push({ type: "executing", nodeId: "105" });
  s.sockets[0].push({ type: "connected", nodeTypes: { "105": "SamplerCustomAdvanced" } });
  s.sockets[0].push({ type: "progress", nodeId: "105", value: 11, max: 20 });
  assert.match(s.liveRoot.querySelector(".render-live-status").textContent, /SamplerCustomAdvanced — 11 \/ 20 · 55%/);
  assert.match(s.liveRoot.textContent, /Elapsed \d{2,}:\d\d/);
  await s.click("watch-b");
  assert.equal(s.sockets[0].closed, true);
  assert.deepEqual(s.watched[1], ["project", "one", "b"]);
  assert.doesNotMatch(s.root.querySelector("#takes-watch-a").parentElement.textContent, /11 \/ 20/);
  s.ui.dispose();
});

test("watched SamplerCustomAdvanced uses exact node metadata and one semantic progress bar", async () => {
  const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
  const socket = s.sockets[0], region = s.liveRoot.querySelector(".render-live-status");
  socket.push({ type: "connected", nodeTypes: { "105": "SamplerCustomAdvanced" } });
  socket.push({ type: "executing", nodeId: "105" });
  socket.push({ type: "progress", promptId: "prompt-b", nodeId: "105", value: 7, max: 8 });
  assert.equal(region.querySelector("progress").hidden, true);
  socket.push({ type: "progress", nodeId: "105", value: 1, max: 8 });
  socket.push({ type: "progress", nodeId: "105", value: 4, max: 8 });
  const progress = region.querySelectorAll("progress");
  assert.equal(progress.length, 1);
  assert.equal(progress[0].value, 4); assert.equal(progress[0].max, 8);
  assert.equal(progress[0].attributes["aria-label"], "SamplerCustomAdvanced progress");
  assert.match(region.textContent, /SamplerCustomAdvanced — 4 \/ 8 · 50%/);
  s.ui.dispose();
});

test("node changes reset progress and a later sampler uses its own denominator", async () => {
  const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
  const socket = s.sockets[0], region = s.liveRoot.querySelector(".render-live-status");
  socket.push({ type: "connected", nodeTypes: { A: "SamplerCustomAdvanced", C: "SamplerCustomAdvanced" } });
  socket.push({ type: "executing", nodeId: "A" });
  socket.push({ type: "progress", nodeId: "A", value: 5, max: 8 });
  assert.equal(region.querySelector("progress").value, 5);
  socket.push({ type: "executing", nodeId: "B" });
  assert.equal(region.querySelector("progress").hidden, true);
  socket.push({ type: "executing", nodeId: "C" });
  socket.push({ type: "progress", nodeId: "C", value: 1, max: 12 });
  assert.equal(region.querySelector("progress").value, 1);
  assert.equal(region.querySelector("progress").max, 12);
  assert.match(region.textContent, /SamplerCustomAdvanced — 1 \/ 12 · 8%/);
  s.ui.dispose();
});

test("queued watched submission shows no invented progress and terminal clears transient bar", async () => {
  const s = liveSetup(); s.states.get("one").renderSubmissions[0].job.status = "queued";
  await s.ui.refresh(); await s.click("watch-a");
  const socket = s.sockets.at(-1), region = s.liveRoot.querySelector(".render-live-status");
  assert.equal(region.querySelector("progress").hidden, true);
  socket.push({ type: "progress", nodeId: "105", value: 2, max: 8 });
  assert.equal(region.querySelector("progress").hidden, false);
  socket.push({ type: "terminal", status: "failed" });
  assert.equal(region.querySelector("progress").hidden, true);
  s.ui.dispose();
});

test("queued Send keeps the running observer, preview and exact reconciliation", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL;
  URL.createObjectURL = () => "blob:active-a";
  URL.revokeObjectURL = () => {};
  try {
    const s = liveSetup();
    s.states.get("one").renderSubmissions[1].job.status = "queued";
    s.states.get("one").renderSubmissions[1].createdAt = "2026-10-04T02:00:00Z";
    const reconciled = [];
    s.api.reconcileRenderSubmission = async (p, g, id) => {
      reconciled.push(id);
      if (id === "b") {
        s.states.get(g).renderSubmissions[1].job = { status: "completed", outputs: [{ id: "output-b", importedTakeId: "take-b" }] };
        s.states.get(g).takes.push({ id: "take-b", originalFilename: "b.mp4", availability: "available" });
      }
      return structuredClone(s.states.get(g));
    };
    await s.ui.refresh(); await s.click("watch-a");
    const socketA = s.sockets[0];
    socketA.push({ type: "connected" });
    socketA.push({ type: "progress", nodeId: "node-a", value: 3, max: 10 });
    socketA.push({ type: "preview-meta", mime: "image/jpeg" });
    socketA.push(new Uint8Array([1]).buffer);
    await s.ui.watchSubmitted({ renderSubmissionId: "b" }, { ...s.context });
    assert.equal(socketA.closed, false);
    assert.deepEqual(s.watched, [["project", "one", "a"]]);
    assert.equal(s.find("watch-a").textContent, "Watching");
    assert.match(s.liveRoot.querySelector(".render-live-status").textContent, /Node node-a — 3 \/ 10 · 30%/);
    assert.equal(s.liveRoot.querySelector(".render-live-status").querySelector("img").src, "blob:active-a");
    assert.ok(reconciled.includes("b"));
    assert.match(s.root.textContent, /Completed · Take imported/);
    s.ui.dispose();
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("a queued Send does not defer a watcher transfer after A becomes terminal", async () => {
  const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
  s.states.get("one").renderSubmissions[1].job.status = "queued";
  s.states.get("one").renderSubmissions[1].createdAt = "2026-10-04T02:00:00Z";
  await s.ui.watchSubmitted({ renderSubmissionId: "b" }, { ...s.context });
  s.states.get("one").renderSubmissions[0].job.status = "completed";
  s.sockets[0].push({ type: "terminal", status: "completed" });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(s.sockets[0].closed, true);
  assert.deepEqual(s.watched, [["project", "one", "a"], ["project", "one", "b"]]);
  assert.equal(s.liveRoot.hidden, false);
  assert.equal(s.find("watch-b").textContent, "Watching");
  await s.ui.refresh();
  assert.deepEqual(s.watched.map(([, , id]) => id), ["a", "b"]);
  s.ui.dispose();
});

test("explicit Watch transfers the observer after an occupied Send", async () => {
  const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
  s.states.get("one").renderSubmissions[1].job.status = "queued";
  s.states.get("one").renderSubmissions[1].createdAt = "2026-10-04T02:00:00Z";
  await s.ui.watchSubmitted({ renderSubmissionId: "b" }, { ...s.context });
  await s.click("watch-b");
  assert.equal(s.sockets[0].closed, true);
  assert.deepEqual(s.watched.map(([, , id]) => id), ["a", "b"]);
  assert.equal(s.find("watch-b").textContent, "Watching");
  await s.ui.refresh();
  assert.deepEqual(s.watched.map(([, , id]) => id), ["a", "b"]);
  s.ui.dispose();
});

test("JPEG, PNG, animated WebP and MP4 previews replace one Blob URL", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  let serial = 0;
  URL.createObjectURL = () => `blob:preview-${++serial}`;
  URL.revokeObjectURL = value => revoked.push(value);
  try {
    const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
    const socket = s.sockets[0];
    for (const [mime, tag] of [["image/jpeg", "img"], ["image/png", "img"], ["image/webp", "img"], ["video/mp4", "video"]]) {
      socket.push({ type: "preview-meta", mime }); socket.push(new Uint8Array([1, 2]).buffer);
      assert.equal(s.liveRoot.querySelector(".render-live-status").querySelector(tag).src, `blob:preview-${serial}`);
    }
    assert.deepEqual(revoked, ["blob:preview-1", "blob:preview-2", "blob:preview-3"]);
    const video = s.liveRoot.querySelector(".render-live-status").querySelector("video");
    for (const attribute of ["autoplay", "muted", "loop", "playsInline"]) assert.equal(video[attribute], true);
    s.ui.dispose(); assert.deepEqual(revoked, ["blob:preview-1", "blob:preview-2", "blob:preview-3", "blob:preview-4"]);
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("disconnect preserves renderer state and reconnect targets the same submission", async () => {
  const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
  s.sockets[0].push({ type: "disconnected" });
  assert.match(s.liveRoot.textContent, /Live connection lost/);
  assert.match(s.root.textContent, /Running/);
  await s.click("watch-a");
  assert.deepEqual(s.watched, [["project", "one", "a"], ["project", "one", "a"]]);
  s.ui.dispose();
});

test("tab hiding suspends polling without closing the live socket or preview", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  URL.createObjectURL = () => "blob:tab-preview";
  URL.revokeObjectURL = value => revoked.push(value);
  const timers = new Map(); let serial = 0;
  try {
    const s = liveSetup({ setTimer: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; },
      clearTimer: id => timers.delete(id) });
    s.api.reconcileRenderSubmission = async (p, g) => structuredClone(s.states.get(g));
    await s.ui.refresh(); await s.click("watch-a");
    const socket = s.sockets[0];
    socket.push({ type: "progress", nodeId: "105", value: 2, max: 8 });
    socket.push({ type: "preview-meta", mime: "image/jpeg" }); socket.push(new Uint8Array([1]).buffer);
    assert.equal(timers.size, 1);
    s.root.ownerDocument.visibilityState = "hidden"; s.root.ownerDocument.dispatch("visibilitychange");
    assert.equal(timers.size, 0);
    assert.equal(socket.closed, false); assert.deepEqual(revoked, []);
    assert.match(s.liveRoot.querySelector(".render-live-status").textContent, /Node 105 — 2 \/ 8 · 25%/);
    assert.equal(s.liveRoot.querySelector(".render-live-status").querySelector("img").src, "blob:tab-preview");
    socket.push({ type: "progress", nodeId: "105", value: 3, max: 8 });
    s.root.ownerDocument.visibilityState = "visible"; s.root.ownerDocument.dispatch("visibilitychange");
    assert.equal(timers.size, 1);
    assert.equal(socket.closed, false); assert.deepEqual(s.watched.map(([, , id]) => id), ["a"]);
    assert.match(s.liveRoot.querySelector(".render-live-status").textContent, /Node 105 — 3 \/ 8 · 38%/);
    assert.equal(s.liveRoot.querySelector(".render-live-status").querySelector("img").src, "blob:tab-preview");
    s.ui.dispose(); assert.deepEqual(revoked, ["blob:tab-preview"]);
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("pagehide and dispose still close a live socket and release preview URLs", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  URL.createObjectURL = () => "blob:pagehide-preview";
  URL.revokeObjectURL = value => revoked.push(value);
  try {
    const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
    s.sockets[0].push({ type: "preview-meta", mime: "image/jpeg" }); s.sockets[0].push(new Uint8Array([1]).buffer);
    s.root.ownerDocument.dispatchWindow("pagehide");
    assert.equal(s.sockets[0].closed, true); assert.deepEqual(revoked, ["blob:pagehide-preview"]);
    s.ui.dispose();
    assert.equal(s.liveRoot.querySelector(".render-live-status").hidden, true);
    const second = liveSetup(); await second.ui.refresh(); await second.click("watch-a");
    second.sockets[0].push({ type: "preview-meta", mime: "image/jpeg" }); second.sockets[0].push(new Uint8Array([1]).buffer);
    second.ui.dispose();
    assert.equal(second.sockets[0].closed, true);
    assert.equal(second.liveRoot.querySelector(".render-live-status").hidden, true);
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("Cancel uses canonical submission and waits for reconciled cancelled state", async () => {
  const s = liveSetup(); let release;
  s.api.cancelRenderSubmission = (p, g, id) => {
    s.cancelled.push([p, g, id]); return new Promise(resolve => release = resolve);
  };
  await s.ui.refresh(); await s.click("watch-a");
  const pending = s.click("cancel-a");
  assert.match(s.root.textContent, /Cancel requested/);
  assert.doesNotMatch(s.root.textContent, /Cancelled/);
  s.states.get("one").renderSubmissions[0].job.status = "cancelled";
  release(structuredClone(s.states.get("one"))); await pending;
  assert.deepEqual(s.cancelled, [["project", "one", "a"]]);
  assert.match(s.root.textContent, /Cancelled/);
  assert.equal(s.states.get("one").selectedTakeId, null);
  s.ui.dispose();
});

test("cancelling queued B preserves watched A socket, preview and progress", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL;
  URL.createObjectURL = () => "blob:watched-a";
  URL.revokeObjectURL = () => {};
  try {
    const s = liveSetup(), exact = [];
    s.states.get("one").renderSubmissions[1].job.status = "queued";
    s.states.get("one").renderSubmissions[1].createdAt = "2026-10-04T02:00:00Z";
    s.api.cancelRenderSubmission = async (p, g, id) => {
      exact.push([p, g, id]); s.states.get(g).renderSubmissions[1].job.status = "cancelled";
      return structuredClone(s.states.get(g));
    };
    await s.ui.refresh(); await s.click("watch-a");
    const socket = s.sockets[0];
    socket.push({ type: "progress", nodeId: "node-a", value: 4, max: 10 });
    socket.push({ type: "preview-meta", mime: "image/jpeg" }); socket.push(new Uint8Array([1]).buffer);
    await s.click("cancel-b");
    assert.deepEqual(exact, [["project", "one", "b"]]);
    assert.equal(socket.closed, false); assert.deepEqual(s.watched, [["project", "one", "a"]]);
    assert.match(s.liveRoot.querySelector(".render-live-status").textContent, /Node node-a — 4 \/ 10 · 40%/);
    assert.equal(s.liveRoot.querySelector(".render-live-status").querySelector("img").src, "blob:watched-a");
    assert.match(s.root.textContent, /Cancelled/);
    s.ui.dispose();
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("Recover targets only the saved submission and preserves selection", async () => {
  const s = setup(), recovered = [];
  s.states.get("one").renderSubmissions.push({ id: "old", comfyPromptId: "prompt-old",
    createdAt: new Date().toISOString(), job: { status: "unknown", outputs: [] } });
  s.api.recoverRenderSubmission = async (p, g, id) => {
    recovered.push([p, g, id]);
    s.states.get(g).renderSubmissions[0].job.status = "running";
    return { ...structuredClone(s.states.get(g)), newJobSubmitted: false, recoveredSubmissionId: id };
  };
  await s.ui.refresh(); await s.click("recover-old");
  assert.deepEqual(recovered, [["project", "one", "old"]]);
  assert.equal(s.states.get("one").selectedTakeId, null);
  assert.equal(s.root.querySelector("#takes-retry-old"), null);
  assert.match(s.root.textContent, /Running/);
  s.ui.dispose();
});

test("Retry control appears only on failed or cancelled cards", async () => {
  const s = setup();
  s.api.retryRenderSubmission = async () => { throw new Error("not called"); };
  for (const status of ["queued", "running", "unknown", "completed", "failed", "cancelled"])
    s.states.get("one").renderSubmissions.push({ id: status, comfyPromptId: `prompt-${status}`,
      createdAt: new Date().toISOString(), job: { status, outputs: [] } });
  await s.ui.refresh();
  for (const status of ["queued", "running", "unknown", "completed"])
    assert.equal(s.root.querySelector(`#takes-retry-${status}`), null);
  for (const status of ["failed", "cancelled"])
    assert.ok(s.root.querySelector(`#takes-retry-${status}`));
  s.ui.dispose();
});

test("retried source shows saved lineage and no longer offers Retry", async () => {
  const s = setup();
  s.api.retryRenderSubmission = async () => { throw new Error("not called"); };
  s.states.get("one").renderSubmissions.push(
    { id: "source-attempt", comfyPromptId: "prompt-a", createdAt: "later", job: { status: "failed", outputs: [] } });
  await s.ui.refresh();
  assert.ok(s.root.querySelector("#takes-retry-source-attempt"));
  s.states.get("one").renderSubmissions.push(
    { id: "child-attempt", comfyPromptId: "prompt-b", createdAt: "earlier", retryOfSubmissionId: "source-attempt", job: { status: "queued", outputs: [] } });
  await s.ui.refresh();
  assert.equal(s.root.querySelector("#takes-retry-source-attempt"), null);
  assert.match(s.root.textContent, /Retried as child-at/);
  assert.ok(s.root.querySelector("#takes-recover-child-attempt") || s.root.querySelector("#takes-watch-child-attempt"));
  assert.equal(s.root.querySelectorAll(".render-submission").length, 2);
  s.ui.dispose();
});

test("live preview reuses image and video nodes across previews and progress", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  let serial = 0;
  URL.createObjectURL = () => `blob:stable-${++serial}`;
  URL.revokeObjectURL = value => revoked.push(value);
  try {
    const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
    const socket = s.sockets[0], panel = s.liveRoot.querySelector(".render-live-status");
    socket.push({ type: "progress", nodeId: "105", value: 1, max: 10 });
    assert.equal(panel.querySelector("img"), null);
    socket.push({ type: "preview-meta", mime: "image/jpeg" }); socket.push(new Uint8Array([1]).buffer);
    const image = panel.querySelector("img");
    assert.equal(image.src, "blob:stable-1");
    for (let i = 0; i < 20; i++) socket.push({ type: "progress", nodeId: "105", value: i, max: 20 });
    socket.push({ type: "executing", nodeId: "106" });
    assert.equal(panel.querySelector("img"), image);
    socket.push(new Uint8Array([2]).buffer);
    assert.equal(panel.querySelector("img"), image);
    assert.equal(image.src, "blob:stable-2");
    assert.deepEqual(revoked, ["blob:stable-1"]);
    socket.push({ type: "preview-meta", mime: "image/webp" }); socket.push(new Uint8Array([3]).buffer);
    assert.equal(panel.querySelector("img"), image);
    assert.equal(image.src, "blob:stable-3");
    socket.push({ type: "preview-meta", mime: "video/mp4" }); socket.push(new Uint8Array([4]).buffer);
    const video = panel.querySelector("video");
    assert.ok(video); assert.notEqual(video, image);
    video.currentTime = 3;
    for (let i = 0; i < 10; i++) socket.push({ type: "progress", nodeId: "200", value: i, max: 10 });
    socket.push({ type: "executing", nodeId: "201" });
    assert.equal(panel.querySelector("video"), video);
    assert.equal(video.currentTime, 3);
    socket.push(new Uint8Array([5]).buffer);
    assert.equal(panel.querySelector("video"), video);
    assert.equal(video.src, "blob:stable-5");
    assert.deepEqual(revoked, ["blob:stable-1", "blob:stable-2", "blob:stable-3", "blob:stable-4"]);
    s.ui.dispose();
    assert.deepEqual(revoked.at(-1), "blob:stable-5");
    assert.equal(panel.hidden, true);
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("same-Project Generation navigation preserves the exact observer and Project switching cleans up", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  URL.createObjectURL = () => "blob:g1";
  URL.revokeObjectURL = value => revoked.push(value);
  try {
    const s = liveSetup(); await s.ui.refresh();
    const old = s.sockets[0], staleMessage = old.onmessage;
    old.push({ type: "preview-meta", mime: "image/jpeg" }); old.push(new Uint8Array([1]).buffer);
    old.push({ type: "progress", nodeId: "g1", value: 4, max: 8 });
    s.states.get("two").renderSubmissions.push({ id: "c", comfyPromptId: "prompt-c",
      createdAt: "2026-10-04", job: { status: "queued", outputs: [] } });
    // The app briefly marks context replacement while adopting navigation data.
    s.context.replacing = true; s.context.saved = false; s.ui.sync();
    assert.equal(old.closed, false);
    s.context.generationId = "two"; s.context.saved = true; s.context.replacing = false; s.ui.sync();
    await s.ui.refresh();
    assert.deepEqual(s.watched, [["project", "one", "a"]]);
    assert.equal(old.closed, false); assert.deepEqual(revoked, []);
    assert.match(s.liveRoot.textContent, /Generation 1 · ComfyUI send 2/);
    assert.match(s.liveRoot.textContent, /g1/);
    assert.equal(s.liveRoot.querySelector("img").src, "blob:g1");
    assert.equal(s.liveRoot.querySelector("progress").hidden, false);
    assert.equal(s.states.get("one").renderSubmissions[0].job.status, "running");
    assert.deepEqual(s.cancelled, []);
    s.context.generationId = "one"; s.ui.sync(); await s.ui.refresh();
    assert.equal(old.closed, false); assert.equal(s.sockets.length, 1);
    s.context.projectId = "another-project"; s.ui.sync();
    assert.equal(old.closed, true); assert.equal(s.liveRoot.hidden, true);
    assert.deepEqual(revoked, ["blob:g1"]);
    staleMessage({ data: JSON.stringify({ type: "progress", nodeId: "stale", value: 7, max: 8 }) });
    assert.doesNotMatch(s.liveRoot.textContent, /stale/);
    s.ui.dispose();
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("superseded observer events cannot reconcile or update the new foreground preview", async () => {
  const s = liveSetup(), exact = [];
  s.api.reconcileRenderSubmission = async (p, g, id) => {
    exact.push([p, g, id]); return structuredClone(s.states.get(g));
  };
  await s.ui.refresh();
  const old = s.sockets[0], staleMessage = old.onmessage;
  await s.click("watch-b"); exact.length = 0;
  staleMessage({ data: JSON.stringify({ type: "terminal", status: "completed" }) });
  staleMessage({ data: JSON.stringify({ type: "progress", nodeId: "stale", value: 7, max: 8 }) });
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(exact, []); assert.equal(old.closed, true); assert.equal(s.liveRoot.hidden, false);
  assert.equal(s.watched.at(-1)[2], "b"); assert.doesNotMatch(s.liveRoot.textContent, /stale/);
  s.ui.dispose();
});

test("foreground transfers G1 → G2 → G3 on exact terminal reconciliation while the editor remains on G1", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  let serial = 0;
  URL.createObjectURL = () => `blob:transfer-${++serial}`;
  URL.revokeObjectURL = url => revoked.push(url);
  const s = liveSetup(), exact = [];
  try {
    s.states.get("one").renderSubmissions.splice(1);
    for (const [generation, id] of [["two", "c"], ["three", "d"]])
      s.states.get(generation).renderSubmissions.push({ id, comfyPromptId: `prompt-${id}`,
        createdAt: "2026-10-04T03:00:00Z", job: { status: "queued", outputs: [] } });
    s.api.reconcileRenderSubmission = async (p, g, id) => {
      exact.push([p, g, id]); return structuredClone(s.states.get(g));
    };
    const before = structuredClone(s.context);
    await s.ui.refresh();
    for (const [index, generation, nextLabel] of [[0, "one", "Generation 2"], [1, "two", "Generation 3"]]) {
      const socket = s.sockets[index], stale = socket.onmessage;
      socket.push({ type: "preview-meta", mime: "image/jpeg" }); socket.push(new Uint8Array([index]).buffer);
      socket.push({ type: "progress", nodeId: "current", value: 3, max: 9 });
      s.states.get(generation).renderSubmissions[0].job.status = "completed";
      socket.push({ type: "terminal", status: "completed" });
      await new Promise(resolve => setImmediate(resolve));
      assert.equal(socket.closed, true); assert.equal(revoked.at(-1), `blob:transfer-${index + 1}`);
      assert.match(s.liveRoot.textContent, new RegExp(`${nextLabel} · ComfyUI send 1`));
      assert.equal(s.liveRoot.querySelector("img"), null);
      assert.equal(s.liveRoot.querySelector("progress").hidden, true);
      stale({ data: JSON.stringify({ type: "progress", nodeId: "superseded", value: 7, max: 8 }) });
      stale({ data: new Uint8Array([9]).buffer });
      assert.doesNotMatch(s.liveRoot.textContent, /superseded/);
      assert.deepEqual(s.context, before); assert.equal(s.find("render-history").open, false);
    }
    assert.deepEqual(s.watched, [["project", "one", "a"], ["project", "two", "c"], ["project", "three", "d"]]);
    assert.ok(exact.some(([, g, id]) => g === "two" && id === "c"));
    s.sockets[2].push({ type: "preview-meta", mime: "video/mp4" }); s.sockets[2].push(new Uint8Array([3]).buffer);
    assert.ok(s.liveRoot.querySelector("video"));
    assert.deepEqual(s.calls, []); assert.deepEqual(s.cancelled, []);
    for (const outputs of s.states.values()) assert.equal(outputs.selectedTakeId, null);
  } finally { s.ui.dispose(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("one foreground poll discovers offscreen renders even when the visible Generation has no jobs", async () => {
  const timers = new Map(); let serial = 0;
  const s = liveSetup({ setTimer: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; },
    clearTimer: id => timers.delete(id) });
  try {
    s.states.get("one").renderSubmissions = [];
    const exact = [];
    s.api.reconcileRenderSubmission = async (p, g, id) => { exact.push([p, g, id]); return structuredClone(s.states.get(g)); };
    await s.ui.refresh(); assert.equal(s.liveRoot.hidden, true); assert.equal(timers.size, 1);
    s.states.get("two").renderSubmissions.push({ id: "c", comfyPromptId: "prompt-c", createdAt: "now", job: { status: "running" } });
    const [timerId, timer] = timers.entries().next().value; timers.delete(timerId);
    assert.equal(timer.ms, 15000); await timer.fn();
    assert.deepEqual(s.watched, [["project", "two", "c"]]); assert.equal(timers.size, 1);
    assert.deepEqual(exact, [["project", "two", "c"]]);
    assert.equal(s.reads.filter(([kind, , g]) => kind === "outputs" && g !== "one").length, 0);
    assert.equal(s.context.generationId, "one");
  } finally { s.ui.dispose(); }
});

for (const status of ["running", "queued", "unknown"]) {
  test(`normal polling reconnects the exact offscreen ${status} watcher without resubmitting`, async () => {
    let clock = 0, serial = 0; const timers = new Map();
    const s = liveSetup({ now: () => clock,
      setTimer: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; }, clearTimer: id => timers.delete(id) });
    try {
      s.states.get("one").renderSubmissions = [];
      s.states.get("two").renderSubmissions.push({ id: "c", comfyPromptId: "prompt-c", createdAt: "now", job: { status } });
      s.api.reconcileRenderSubmission = async (p, g) => structuredClone(s.states.get(g));
      s.api.retryRenderSubmission = s.api.send = () => assert.fail("Observation cannot submit or retry");
      if (status === "unknown") {
        await s.ui.refresh(); assert.equal(s.sockets.length, 0);
        s.context.generationId = "two"; await s.ui.refresh(); await s.click("watch-c");
        s.context.generationId = "one"; await s.ui.refresh();
      }
      await s.ui.refresh(); const old = s.sockets[0], stale = old.onmessage;
      old.onclose(); assert.match(s.liveRoot.textContent, /Live connection lost/);
      await s.ui.refresh(); assert.equal(s.sockets.length, 1);
      clock = 15000;
      const [id, timer] = timers.entries().next().value; timers.delete(id); await timer.fn();
      assert.equal(old.closed, true); assert.deepEqual(s.watched, [["project", "two", "c"], ["project", "two", "c"]]);
      stale({ data: JSON.stringify({ type: "progress", nodeId: "stale", value: 8, max: 10 }) });
      s.sockets[1].push({ type: "progress", promptId: "different-prompt", nodeId: "wrong", value: 2, max: 10 });
      s.sockets[1].push({ type: "progress", promptId: "prompt-c", nodeId: "current", value: 2, max: 10 });
      if (status === "unknown") {
        assert.match(s.liveRoot.textContent, /Status unknown/);
        assert.doesNotMatch(s.liveRoot.textContent, /Elapsed|current/);
      } else assert.match(s.liveRoot.textContent, /current/);
      assert.doesNotMatch(s.liveRoot.textContent, /wrong|stale/);
      assert.equal(s.states.get("two").renderSubmissions[0].job.status, status);
      assert.equal(s.states.get("two").selectedTakeId, null); assert.deepEqual(s.calls, []);
    } finally { s.ui.dispose(); }
  });
}

test("repeated connection failures back off 15/30/60/120 seconds; manual reconnect can override", async () => {
  let clock = 0; const attempts = [];
  const s = liveSetup({ now: () => clock });
  try {
    s.api.reconcileRenderSubmission = async (p, g) => structuredClone(s.states.get(g));
    await s.ui.refresh(); const closed = s.sockets[0].onclose; s.sockets[0].onerror(); closed();
    const connect = s.api.watchRenderSubmission;
    s.api.watchRenderSubmission = (...args) => { attempts.push([clock, ...args]); throw new Error("Endpoint offline"); };
    for (const due of [15000, 45000, 105000, 225000, 345000]) {
      clock = due - 1; await s.ui.refresh(); const count = attempts.length;
      for (let i = 0; i < 3; i++) await s.ui.refresh(); assert.equal(attempts.length, count);
      clock = due; await s.ui.refresh(); assert.equal(attempts.length, count + 1);
    }
    assert.deepEqual(attempts.map(([time]) => time), [15000, 45000, 105000, 225000, 345000]);
    assert.ok(attempts.every(([, p, g, id]) => p === "project" && g === "one" && id === "a"));
    s.api.watchRenderSubmission = connect; await s.click("watch-a");
    assert.deepEqual(s.watched.at(-1), ["project", "one", "a"]);
    assert.equal(s.states.get("one").renderSubmissions[0].job.status, "running");
    assert.deepEqual(s.calls, []); assert.deepEqual(s.cancelled, []);
  } finally { s.ui.dispose(); }
});

test("explicit Watch stays pinned across Generation navigation until exact terminal truth", async () => {
  const s = liveSetup();
  try {
    s.states.get("one").renderSubmissions[1].job.status = "queued";
    await s.ui.refresh(); await s.click("watch-b"); const socket = s.sockets.at(-1);
    s.context.generationId = "two"; s.ui.sync(); await s.ui.refresh();
    assert.equal(socket.closed, false); assert.deepEqual(s.watched.map(([, , id]) => id), ["a", "b"]);
    assert.match(s.liveRoot.textContent, /Generation 1 · ComfyUI send 1/);
    s.states.get("one").renderSubmissions[1].job.status = "cancelled";
    socket.push({ type: "terminal", status: "cancelled" }); await new Promise(resolve => setImmediate(resolve));
    assert.equal(socket.closed, true); assert.equal(s.watched.at(-1)[2], "a");
    assert.equal(s.context.generationId, "two");
  } finally { s.ui.dispose(); }
});

test("promoted Cancel targets the offscreen exact watcher and acceptance preserves preview until terminal", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  URL.createObjectURL = () => "blob:cancel-exact"; URL.revokeObjectURL = url => revoked.push(url);
  const s = liveSetup(); let finish;
  try {
    s.states.get("one").renderSubmissions = [];
    s.states.get("two").renderSubmissions.push({ id: "c", comfyPromptId: "prompt-c", createdAt: "now", job: { status: "running" } },
      { id: "newer", comfyPromptId: "prompt-newer", createdAt: "later", job: { status: "queued" } });
    s.api.reconcileRenderSubmission = async (p, g) => structuredClone(s.states.get(g));
    s.api.cancelRenderSubmission = (p, g, id) => { s.cancelled.push([p, g, id]); return new Promise(resolve => finish = resolve); };
    await s.ui.refresh(); const socket = s.sockets[0];
    socket.push({ type: "preview-meta", mime: "image/jpeg" }); socket.push(new Uint8Array([1]).buffer);
    const image = s.liveRoot.querySelector("img"), cancel = s.liveRoot.querySelector("#takes-live-cancel");
    const pending = cancel.emit("click"); assert.equal(cancel.disabled, true); assert.equal(cancel.textContent, "Cancel requested…");
    await cancel.emit("click"); assert.deepEqual(s.cancelled, [["project", "two", "c"]]);
    finish({ ...structuredClone(s.states.get("two")), cancelSupported: true, cancelDispatched: true }); await pending;
    assert.equal(socket.closed, false); assert.deepEqual(revoked, []); assert.equal(s.liveRoot.querySelector("img"), image);
    assert.equal(cancel.textContent, "Cancel requested…"); assert.equal(s.context.generationId, "one");
    s.states.get("two").renderSubmissions[0].job.status = "cancelled"; await s.ui.refresh({ pendingOnly: true });
    assert.equal(socket.closed, true); assert.deepEqual(revoked, ["blob:cancel-exact"]);
    assert.deepEqual(s.watched.at(-1), ["project", "two", "newer"]);
    assert.equal(s.states.get("two").selectedTakeId, null); assert.deepEqual(s.calls, []);
  } finally { s.ui.dispose(); URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("a terminal socket hint with active HTTP truth retains the exact watcher", async () => {
  let clock = 0; const s = liveSetup({ now: () => clock });
  try {
    s.api.reconcileRenderSubmission = async (p, g) => structuredClone(s.states.get(g));
    await s.ui.refresh(); const socket = s.sockets[0];
    socket.push({ type: "terminal", status: "completed" }); await new Promise(resolve => setImmediate(resolve));
    assert.equal(socket.closed, false); assert.deepEqual(s.watched, [["project", "one", "a"]]);
    assert.equal(s.states.get("one").renderSubmissions[0].job.status, "running");
    clock = 15000; await s.ui.refresh(); assert.deepEqual(s.watched.at(-1), ["project", "one", "a"]);
  } finally { s.ui.dispose(); }
});

test("a late pre-terminal HTTP snapshot cannot restore G1 after its observer transfers to G2", async () => {
  const s = liveSetup(); let release;
  try {
    s.states.get("one").renderSubmissions.splice(1);
    s.states.get("two").renderSubmissions.push({ id: "c", comfyPromptId: "prompt-c", createdAt: "now", job: { status: "queued" } });
    await s.ui.refresh();
    let first = true;
    s.api.reconcileRenderSubmission = async (p, g, id) => {
      if (first) {
        first = false; const stale = structuredClone(s.states.get(g));
        await new Promise(resolve => release = resolve); return stale;
      }
      s.states.get(g).renderSubmissions.find(row => row.id === id).job.status = "completed";
      return structuredClone(s.states.get(g));
    };
    const pending = s.ui.refresh(); while (!release) await Promise.resolve();
    s.sockets[0].push({ type: "terminal", status: "completed" }); await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(s.watched.at(-1), ["project", "two", "c"]);
    release(); await pending;
    assert.deepEqual(s.watched, [["project", "one", "a"], ["project", "two", "c"]]);
    assert.match(s.liveRoot.textContent, /Generation 2 · ComfyUI send 1/);
    assert.match(s.root.textContent, /Completed/); assert.equal(s.context.generationId, "one");
  } finally { s.ui.dispose(); }
});

test("watched reconciliation superseded by exact cancellation cannot publish an older fingerprint", async () => {
  const s = liveSetup(); let release;
  try {
    await s.ui.refresh(); const socket = s.sockets[0];
    const stale = { ...structuredClone(s.states.get("one")), projectFingerprint: "before-cancel" };
    s.api.reconcileRenderSubmission = () => new Promise(resolve => { release = resolve; });
    socket.push({ type: "terminal", status: "completed" });
    s.api.cancelRenderSubmission = async () => ({ ...structuredClone(s.states.get("one")),
      projectFingerprint: "after-cancel", cancelSupported: true, cancelDispatched: true });
    await s.click("cancel-a");
    assert.equal(socket.closed, false, "the exact job remains active while cancellation is pending");
    assert.equal(s.mutations.at(-1)[0].projectFingerprint, "after-cancel");
    const count = s.mutations.length;
    release(stale); await new Promise(resolve => setImmediate(resolve));
    assert.equal(s.mutations.length, count, "superseded reconciliation cannot publish its fingerprint");
  } finally { s.ui.dispose(); }
});

test("foreground API requests one read-only Project summary with exact optional watch IDs", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (url, options) => { calls.push([url, options]); return { ok: true, json: async () => ({}) }; };
  try {
    await realApi.foregroundRenders("project");
    await realApi.foregroundRenders("project", { generationId: "g/2", id: "exact/attempt" });
    assert.equal(calls[0][0], "/api/projects/project/foreground-renders");
    const url = new URL(calls[1][0], "http://localhost");
    assert.equal(url.searchParams.get("generationId"), "g/2");
    assert.equal(url.searchParams.get("submissionId"), "exact/attempt");
    for (const [, options] of calls) { assert.equal(options.method, "GET"); assert.equal(options.body, undefined); }
  } finally { globalThis.fetch = previous; }
});

test("terminal reconciliation releases the live Blob URL and media region", async () => {
  const oldCreate = URL.createObjectURL, oldRevoke = URL.revokeObjectURL, revoked = [];
  URL.createObjectURL = () => "blob:terminal-preview";
  URL.revokeObjectURL = value => revoked.push(value);
  try {
    const s = liveSetup(); await s.ui.refresh(); await s.click("watch-a");
    s.sockets[0].push({ type: "preview-meta", mime: "video/mp4" }); s.sockets[0].push(new Uint8Array([1]).buffer);
    for (const item of s.states.get("one").renderSubmissions) item.job.status = "completed";
    s.sockets[0].push({ type: "terminal", status: "completed" });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(revoked, ["blob:terminal-preview"]);
    assert.equal(s.liveRoot.querySelector(".render-live-status").hidden, true);
    assert.equal(s.liveRoot.querySelector("video"), null);
    s.ui.dispose();
  } finally { URL.createObjectURL = oldCreate; URL.revokeObjectURL = oldRevoke; }
});

test("Retry confirms, blocks a double click, and watches only the new submission", async () => {
  let confirmed = false, release;
  const t = setup({ confirmRetry: message => { confirmed = true; assert.match(message, /NEW ComfyUI job/); return true; } });
  t.states.get("one").renderSubmissions.push({ id: "a", comfyPromptId: "prompt-a",
    createdAt: new Date().toISOString(), job: { status: "failed", outputs: [] } });
  const watched = [], attempts = [];
  t.api.watchRenderSubmission = (p, g, id) => { watched.push(id); return { close() {} }; };
  t.api.retryRenderSubmission = (p, g, id) => new Promise(resolve => { attempts.push(id); release = resolve; });
  await t.ui.refresh();
  const pending = t.find("retry-a").emit("click");
  assert.equal(t.find("retry-a").disabled, true);
  await t.find("retry-a").emit("click");
  assert.deepEqual(attempts, ["a"]);
  t.states.get("one").renderSubmissions.push({ id: "new", comfyPromptId: "prompt-new",
    createdAt: new Date().toISOString(), job: { status: "queued", outputs: [] } });
  release({ submitted: true, renderSubmissionId: "new", promptId: "prompt-new" });
  await pending;
  assert.equal(confirmed, true);
  assert.deepEqual(watched, ["new"]);
  assert.equal(t.states.get("one").selectedTakeId, null);
  t.ui.dispose();
});

test("Retry records a new queued submission without replacing an occupied observer", async () => {
  const s = liveSetup({ confirmRetry: () => true });
  s.states.get("one").renderSubmissions.push({ id: "failed", comfyPromptId: "prompt-failed",
    createdAt: new Date().toISOString(), job: { status: "failed", outputs: [] } });
  s.api.retryRenderSubmission = async (p, g, id) => {
    assert.equal(id, "failed");
    s.states.get(g).renderSubmissions.push({ id: "retry", comfyPromptId: "prompt-retry",
      retryOfSubmissionId: id, createdAt: new Date().toISOString(), job: { status: "queued", outputs: [] } });
    return { submitted: true, renderSubmissionId: "retry", promptId: "prompt-retry" };
  };
  await s.ui.refresh(); await s.click("watch-a");
  await s.click("retry-failed");
  assert.equal(s.sockets[0].closed, false);
  assert.deepEqual(s.watched.map(([, , id]) => id), ["a"]);
  assert.match(s.find("watch-retry").parentElement.textContent, /Queued/);
  s.ui.dispose();
});


test("12 Takes use one labelled inspection selector/details area, with exact ID actions and Selected Take isolation", async () => {
  const s = setup(), state = s.states.get("one");
  state.renderSubmissions = [
    { id: "send-old", createdAt: "2026-10-03T00:00:00Z", comfyPromptId: "prompt-old" },
    { id: "send-new", createdAt: "2026-10-03T01:00:00Z", comfyPromptId: "prompt-new" },
  ];
  state.takes = Array.from({ length: 12 }, (_, index) => ({ id: `T${index}`, originalFilename: `Generation_${index}.mp4`,
    createdAt: `2026-10-03T02:${String(index).padStart(2, "0")}:00Z`, byteSize: 2048, availability: "available",
    sourceKind: "comfyui_submission", renderSubmissionId: index === 11 ? "send-old" : "send-new", promptProvenance: "known" }));
  state.selectedTakeId = "T2";
  const selected = [], detailReads = [];
  const originalSelect = s.api.selectTake;
  s.api.selectTake = async (p, g, id) => { selected.push([p, g, id]); return originalSelect(p, g, id); };
  s.api.takeDetail = async (p, g, id) => { detailReads.push([p, g, id]); return { promptSnapshot: { prompt: `exact ${id}` } }; };
  await s.ui.refresh();
  assert.equal(s.root.querySelectorAll("#takes-inspected").length, 1);
  assert.equal(s.find("inspected").children.length, 12);
  assert.equal(s.find("inspected").value, "T2");
  assert.equal(s.root.querySelectorAll(".take-card").length, 1);
  assert.ok(s.root.querySelectorAll("label").some(label => label.attributes.for === "takes-inspected"));
  assert.match(s.find("inspected").children.find(option => option.value === "T2").textContent, /✓ SELECTED.*2026.*ComfyUI send 2/);
  assert.equal(s.find("select-T2").disabled, true);
  await s.inspect("T11");
  assert.equal(state.selectedTakeId, "T2"); assert.deepEqual(selected, []);
  assert.match(s.root.querySelector(".take-card").textContent, /ComfyUI send 1/);
  assert.match(s.root.querySelector(".observed-end-state").textContent, /Generation_2.mp4/);
  await s.ui.refresh(); assert.equal(s.find("inspected").value, "T11");
  await s.click("preview-T11");
  assert.match(s.root.querySelector(".take-preview").querySelector("video").src, /T11\/media$/);
  assert.notEqual(s.root.querySelector(".take-preview").querySelector("video").autoplay, true);
  await s.inspect("T3"); assert.equal(s.root.querySelector(".take-preview").querySelector("video"), null);
  assert.equal(state.selectedTakeId, "T2");
  await s.click("provenance-T3"); assert.deepEqual(detailReads.at(-1), ["project", "one", "T3"]);
  assert.match(s.root.querySelector(".take-card").textContent, /exact T3/);
  await s.click("select-T3"); assert.deepEqual(selected.at(-1), ["project", "one", "T3"]);
  await s.inspect("T11"); await s.click("delete-T11");
  assert.deepEqual(s.calls.at(-1), ["delete", "T11"]); assert.equal(state.selectedTakeId, "T3");
  s.states.get("two").takes = [{ ...state.takes[0], id: "G2", originalFilename: "G2.mp4" }];
  s.states.get("two").selectedTakeId = "G2";
  s.context.generationId = "two"; s.ui.sync(); await s.ui.refresh();
  assert.equal(s.find("inspected").value, "G2"); assert.equal(s.find("inspected").children.length, 1);
});

test("newest Take is inspection only, missing media is labelled and stable Send provenance is never guessed", async () => {
  const s = setup(), state = s.states.get("one");
  state.renderSubmissions = [{ id: "send", createdAt: "2026-10-03", comfyPromptId: "prompt" }];
  state.takes = [
    { id: "external", originalFilename: "external.mp4", byteSize: 2048, createdAt: "2026-10-03T01:00:00Z", sourceKind: "external_unknown", availability: "available", promptProvenance: "unknown" },
    { id: "missing", originalFilename: "new.mp4", byteSize: 2048, createdAt: "2026-10-03T02:00:00Z", sourceKind: "comfyui_submission", renderSubmissionId: "no-match", availability: "missing", promptProvenance: "known" },
  ];
  await s.ui.refresh();
  assert.equal(s.find("inspected").value, "missing"); assert.equal(state.selectedTakeId, null);
  assert.match(s.find("inspected").children[0].textContent, /Send provenance unavailable.*Media missing/);
  const replacement = s.root.querySelector(".take-card").querySelector("input"); replacement.files = [{ name: "new.mp4" }]; await replacement.emit("change");
  assert.deepEqual(s.calls.at(-1).slice(0, 4), ["reattach", "project", "one", "missing"]);
  await s.inspect("external"); assert.match(s.find("inspected").children.find(item => item.value === "external").textContent, /External \/ unknown/);
  assert.match(s.root.querySelector(".take-card").textContent, /Prompt provenance: unknown/);
});

test("render history is the bottom outer disclosure; failed/cancelled jobs do not expand it", async () => {
  const s = setup(), state = s.states.get("one");
  state.renderSubmissions = [{ id: "failed", createdAt: "2026-10-03", comfyPromptId: "prompt", job: { status: "cancelled", outputs: [] } }];
  await s.ui.refresh();
  const history = s.find("render-history");
  assert.equal(s.root.querySelectorAll("#takes-render-history").length, 1);
  assert.equal(history.open, false); assert.equal(s.root.children.at(-1), history);
  assert.ok(s.root.children.indexOf(s.root.querySelector(".take-list")) < s.root.children.indexOf(s.find("manual-import")));
  assert.ok(s.root.children.indexOf(s.find("manual-import")) < s.root.children.indexOf(history));
  assert.match(history.querySelector("summary").textContent, /ComfyUI render history \(1\) · latest cancelled/);
  history.open = true; state.renderSubmissions[0].job.status = "failed"; await s.ui.refresh(); assert.equal(history.open, true);
});

test("foreground discovery outranks stale visible snapshots and hands off across Generations without navigation", async () => {
  const s = liveSetup(), queries = [];
  try {
    const a = s.states.get("one").renderSubmissions[0]; a.job.status = "queued";
    s.states.get("one").renderSubmissions = [a];
    const b = { id: "b", comfyPromptId: "prompt-b", createdAt: "2026-10-05", job: { status: "queued" } };
    s.states.get("two").renderSubmissions = [b];
    const summary = s.api.foregroundRenders;
    s.api.foregroundRenders = async (p, watched, discovery) => {
      queries.push(discovery);
      if (discovery?.refresh) {
        if (a.job.status !== "completed") a.job.status = "running";
        else b.job.status = "running";
      }
      return summary(p, watched);
    };
    s.api.sendExecution = s.api.retryRenderSubmission = () => assert.fail("Observation cannot submit");
    await s.ui.refresh(); assert.equal(s.watched.at(-1)[2], "a");
    assert.ok(queries.some(query => query?.refresh === true));
    s.states.get("one").renderSubmissions.push({ id: "c", createdAt: "2026-10-06", job: { status: "queued" } });
    await s.ui.watchSubmitted({ renderSubmissionId: "c" }, { ...s.context });
    assert.equal(s.watched.length, 1, "a newly queued C cannot replace the older running A");
    a.job.status = "completed";
    s.sockets[0].push({ type: "terminal", status: "completed" });
    await new Promise(resolve => setImmediate(resolve));
    assert.deepEqual(s.watched.at(-1), ["project", "two", "b"]);
    assert.equal(s.context.generationId, "one"); assert.deepEqual(s.calls, []);
  } finally { s.ui.dispose(); }
});

test("socket closing during HTTP reconciliation cannot strand recovery or accept delayed media", async () => {
  let clock = 0, release;
  const s = liveSetup({ now: () => clock });
  try {
    await s.ui.refresh(); const socket = s.sockets[0], lateMessage = socket.onmessage;
    s.api.reconcileRenderSubmission = () => new Promise(resolve => release = resolve);
    socket.push({ type: "reconcile" });
    socket.onclose();
    assert.equal(socket.closed, true);
    lateMessage({ data: JSON.stringify({ type: "connected" }) });
    lateMessage({ data: JSON.stringify({ type: "state", status: "running" }) });
    lateMessage({ data: JSON.stringify({ type: "preview-meta", mime: "video/mp4" }) });
    lateMessage({ data: new Uint8Array([1]).buffer });
    release(structuredClone(s.states.get("one"))); await new Promise(resolve => setImmediate(resolve));
    assert.match(s.liveRoot.textContent, /Live connection lost/);
    assert.equal(s.liveRoot.querySelectorAll("video, img").length, 0);
    s.api.reconcileRenderSubmission = async (p, g) => structuredClone(s.states.get(g));
    clock = 14999; await s.ui.refresh(); assert.equal(s.sockets.length, 1);
    clock = 15000; await s.ui.refresh(); assert.equal(s.sockets.length, 2);
    assert.deepEqual(s.watched.at(-1), ["project", "one", "a"]);
    s.sockets[1].push({ type: "progress", promptId: "prompt-a", value: 3, max: 8 });
    assert.match(s.liveRoot.textContent, /3 \/ 8/);
  } finally { s.ui.dispose(); }
});

test("foreground discovery HTTP cannot resurrect a previous Project or publish its revision", async () => {
  const s = liveSetup();
  try {
    await s.ui.refresh();
    let release, started;
    const entered = new Promise(resolve => started = resolve), summary = s.api.foregroundRenders;
    s.api.foregroundRenders = async (p, watched, discovery) => {
      const response = await summary(p, watched);
      if (p === "project" && discovery?.refresh) { started(); await new Promise(resolve => release = resolve); }
      return { ...response, projectFingerprint: p === "project" ? "old-project" : "new-project" };
    };
    const pending = s.ui.refresh(); await entered;
    s.context.projectId = "new-project"; await s.ui.refresh();
    release(); await pending;
    assert.ok(s.mutations.every(([response, context]) => response.projectFingerprint !== "old-project" || context.projectId !== "new-project"));
    assert.equal(s.watched.at(-1)[0], "new-project");
    assert.equal(s.sockets.filter(socket => !socket.closed).length, 1);
  } finally { s.ui.dispose(); }
});

test("an older foreground snapshot cannot replace a newer canonical candidate or revision", async () => {
  const s = liveSetup();
  try {
    const summary = s.api.foregroundRenders;
    let stale = false;
    s.api.foregroundRenders = async (p, watched) => {
      const response = await summary(p, watched);
      return { ...response, ...(stale ? { candidate: { ...response.candidate, id: "old-job" } } : {}),
        projectUpdatedAt: stale ? "2026-10-17T00:00:00Z" : "2026-10-17T00:00:01Z",
        projectFingerprint: stale ? "old-revision" : "current-revision" };
    };
    await s.ui.refresh(); assert.equal(s.watched.at(-1)[2], "a");
    const mutations = s.mutations.length;
    stale = true; await s.ui.refresh();
    assert.equal(s.watched.length, 1);
    assert.equal(s.mutations.length, mutations);
  } finally { s.ui.dispose(); }
});

function delayedHistorySetup() {
  const deferred = () => { let resolve; const promise = new Promise(done => resolve = done); return { promise, resolve }; };
  const held = deferred(), entered = deferred(), timers = new Map(); let serial = 0, clock = 0, revision = 0, pending;
  const s = liveSetup({ now: () => clock,
    setTimer: (fn, ms) => { const id = ++serial; timers.set(id, { fn, ms }); return id; }, clearTimer: id => timers.delete(id) });
  const renderer = new Map(["a", "b", "c", "d", "e"].map(id => [id, id === "a" ? "running" : "queued"]));
  // B is first in history; A is the oldest Send and all saved statuses are queued.
  s.states.get("one").renderSubmissions = ["b", "c", "d", "e", "a"].map(id => ({ id, comfyPromptId: `prompt-${id}`,
    createdAt: `2026-10-0${id.charCodeAt(0) - 96}T00:00:00Z`, job: { status: "queued", outputs: [] } }));
  const summary = s.api.foregroundRenders, exact = [];
  s.api.foregroundRenders = async (p, watched, discovery) => {
    assert.equal(discovery?.refresh, true);
    const stamp = `2026-10-17T00:00:${String(++revision).padStart(2, "0")}Z`;
    for (const outputs of s.states.values()) {
      outputs.projectUpdatedAt = stamp;
      for (const row of outputs.renderSubmissions) row.job.status = renderer.get(row.id) || row.job.status;
    }
    return { ...await summary(p, watched), projectUpdatedAt: stamp, projectFingerprint: `revision-${revision}` };
  };
  s.api.reconcileRenderSubmission = async (p, g, id) => {
    exact.push([p, g, id]);
    if (id === "b") {
      const stale = { ...structuredClone(s.states.get(g)), projectFingerprint: "older-history" };
      entered.resolve(); await held.promise; return stale;
    }
    if (id === "e") {
      renderer.set("e", "completed"); s.states.get(g).renderSubmissions.find(row => row.id === id).job.status = "completed";
      if (!s.states.get(g).takes.length) s.states.get(g).takes.push({ id: "auto-e", originalFilename: "completed-e.mp4",
        renderSubmissionId: id, sourceKind: "comfyui_submission", promptProvenance: "known", availability: "available", byteSize: 20 });
    }
    return structuredClone(s.states.get(g));
  };
  for (const name of ["sendExecution", "retryRenderSubmission", "cancelRenderSubmission"])
    s.api[name] = () => assert.fail(`observation cannot invoke ${name}`);
  return { ...s, exact, timers, renderer, clock: value => clock = value,
    async start() { pending = s.ui.refresh(); await entered.promise; },
    async finish() { held.resolve(); await pending; },
    async tick() { assert.equal(timers.size, 1); const [id, timer] = timers.entries().next().value;
      assert.equal(timer.ms, 15000); timers.delete(id); await timer.fn(); } };
}

test("fresh running preview and terminal handoff precede five-job history, and completed imports still appear", async () => {
  const s = delayedHistorySetup();
  let releaseTerminal, terminalSnapshot;
  try {
    await s.start();
    assert.deepEqual(s.exact, [["project", "one", "b"]], "unrelated B is still unresolved");
    assert.deepEqual(s.watched, [["project", "one", "a"]]);
    const old = s.sockets[0], staleMessage = old.onmessage;
    old.push({ type: "preview-meta", mime: "image/jpeg" }); old.push(new Uint8Array([1]).buffer);
    assert.ok(s.liveRoot.querySelector("img"));
    s.states.get("one").renderSubmissions.push({ id: "f", comfyPromptId: "prompt-f", createdAt: "2026-10-06", job: { status: "queued" } });
    s.renderer.set("f", "queued");
    await s.ui.watchSubmitted({ renderSubmissionId: "f" }, { ...s.context });
    assert.equal(s.watched.length, 1, "queued F does not steal A");
    // Even a pending terminal reconciliation/output download cannot hold the
    // observer after the normal cadence establishes canonical terminal truth.
    const reconcile = s.api.reconcileRenderSubmission;
    let holdTerminal = true;
    s.api.reconcileRenderSubmission = (...args) => {
      if (args[2] === "a" && holdTerminal) {
        holdTerminal = false; terminalSnapshot = structuredClone(s.states.get("one"));
        return new Promise(resolve => releaseTerminal = resolve);
      }
      return reconcile(...args);
    };
    old.push({ type: "terminal", status: "completed" });
    assert.equal(typeof releaseTerminal, "function");
    s.renderer.set("a", "completed"); s.renderer.set("b", "running");
    s.clock(15000); await s.tick();
    assert.deepEqual(s.watched.at(-1), ["project", "one", "b"]);
    assert.equal(old.closed, true); assert.equal(s.liveRoot.querySelector("img"), null);
    staleMessage({ data: JSON.stringify({ type: "progress", nodeId: "old-a", value: 1, max: 2 }) });
    assert.doesNotMatch(s.liveRoot.textContent, /old-a/);
    releaseTerminal(terminalSnapshot);
    await s.finish();
    assert.equal(s.watched.at(-1)[2], "b", "late queued/history snapshots cannot restore A");
    assert.match(s.root.textContent, /completed-e.mp4/);
    assert.equal(s.states.get("one").selectedTakeId, null);
    assert.equal(s.context.generationId, "one");
    assert.equal(s.sockets.filter(socket => !socket.closed).length, 1); assert.equal(s.timers.size, 1);
    assert.deepEqual(s.calls, []); assert.deepEqual(s.cancelled, []);
  } finally { s.ui.dispose(); releaseTerminal?.(terminalSnapshot); await s.finish(); }
});

test("the same foreground cadence reconnects with bounded backoff while unrelated history is stalled", async () => {
  const s = delayedHistorySetup();
  try {
    await s.start(); s.sockets[0].onclose();
    s.clock(14999); await s.ui.refresh(); assert.equal(s.sockets.length, 1);
    s.clock(15000); await s.tick(); assert.equal(s.sockets.length, 2);
    s.sockets[1].onerror();
    s.clock(44999); await s.tick(); assert.equal(s.sockets.length, 2);
    s.clock(45000); await s.tick(); assert.equal(s.sockets.length, 3);
    assert.ok(s.watched.every(([, , id]) => id === "a"));
    assert.deepEqual(s.exact, [["project", "one", "b"]], "polls do not duplicate the pending history batch");
    assert.equal(s.timers.size, 1); assert.equal(s.sockets.filter(socket => !socket.closed).length, 1);
  } finally { s.ui.dispose(); await s.finish(); }
});

test("explicit Watch remains pinned while history is stalled and releases only at canonical terminal truth", async () => {
  const s = delayedHistorySetup();
  try {
    await s.start(); await s.click("watch-c");
    s.clock(15000); await s.tick(); assert.equal(s.watched.at(-1)[2], "c");
    s.renderer.set("a", "completed"); s.renderer.set("b", "running"); s.renderer.set("c", "completed");
    s.clock(30000); await s.tick(); assert.equal(s.watched.at(-1)[2], "b");
    assert.deepEqual(s.exact, [["project", "one", "b"]]); assert.equal(s.timers.size, 1);
  } finally { s.ui.dispose(); await s.finish(); }
});

for (const navigation of ["generation", "project"]) {
  test(`stalled history cannot contaminate ${navigation} navigation or accumulate observers/timers`, async t => {
    const s = delayedHistorySetup();
    const revoked = [], revoke = URL.revokeObjectURL;
    t.mock.method(URL, "revokeObjectURL", url => { revoked.push(url); revoke.call(URL, url); });
    try {
      await s.start(); const old = s.sockets[0], lateMessage = old.onmessage;
      old.push({ type: "preview-meta", mime: "image/jpeg" }); old.push(new Uint8Array([1]).buffer);
      const previewUrl = s.liveRoot.querySelector("img").src;
      s.context.generationId = "two";
      if (navigation === "project") {
        s.context.projectId = "other";
        for (const outputs of s.states.values()) outputs.renderSubmissions = [];
      }
      await s.ui.refresh();
      assert.equal(old.closed, navigation === "project");
      await s.finish();
      if (navigation === "project") {
        lateMessage({ data: JSON.stringify({ type: "progress", nodeId: "old-project", value: 2, max: 3 }) });
        lateMessage({ data: new Uint8Array([2]).buffer });
        assert.equal(s.liveRoot.hidden, true); assert.doesNotMatch(s.liveRoot.textContent, /old-project/);
        assert.deepEqual(revoked, [previewUrl]); assert.equal(s.liveRoot.querySelector("img"), null);
      } else {
        assert.deepEqual(s.watched, [["project", "one", "a"]]);
        assert.equal(s.liveRoot.querySelector("img").src, previewUrl); assert.deepEqual(revoked, []);
      }
      assert.equal(s.timers.size, 1); assert.ok(s.sockets.filter(socket => !socket.closed).length <= 1);
      assert.doesNotMatch(s.root.textContent, /completed-e.mp4/);
    } finally { s.ui.dispose(); await s.finish(); }
  });
}

test("a foreground discovery failure does not suppress ordinary completed-output reconciliation", async () => {
  const s = pollingSetup(), exact = [];
  try {
    s.api.foregroundRenders = async () => { throw new Error("Foreground discovery is temporarily unavailable"); };
    s.api.reconcileRenderSubmission = async (p, g, id) => {
      exact.push([p, g, id]);
      const outputs = s.states.get(g); outputs.renderSubmissions[0].job.status = "completed";
      outputs.takes = [{ id: "auto", originalFilename: "recovered.mp4", sourceKind: "comfyui_submission",
        renderSubmissionId: id, promptProvenance: "known", availability: "available", byteSize: 12 }];
      return structuredClone(outputs);
    };
    await s.ui.refresh();
    assert.deepEqual(exact, [["project", "one", "send-1"]]);
    assert.match(s.root.textContent, /recovered.mp4/);
    assert.equal(s.states.get("one").selectedTakeId, null);
    assert.match(s.root.textContent, /Foreground discovery is temporarily unavailable/);
    assert.equal(s.timers.size, 1);
  } finally { s.ui.dispose(); }
});

test("concurrent foreground refreshes coalesce while one history batch is unresolved", async () => {
  const s = delayedHistorySetup();
  let release, started, requests = 0;
  const gate = new Promise(resolve => release = resolve), entered = new Promise(resolve => started = resolve);
  let pending = [];
  try {
    await s.start(); const discovery = s.api.foregroundRenders;
    s.api.foregroundRenders = async (...args) => { requests++; started(); await gate; return discovery(...args); };
    pending.push(s.ui.refresh()); await entered;
    pending.push(s.ui.refresh());
    assert.equal(requests, 1); assert.equal(s.timers.size, 0);
    release(); await Promise.all(pending);
    assert.equal(s.timers.size, 1); assert.equal(s.sockets.length, 1);
    assert.deepEqual(s.exact, [["project", "one", "b"]]);
  } finally { release(); await Promise.all(pending); s.ui.dispose(); await s.finish(); }
});

test("fresh running priority upgrades an automatic queued watcher even during its pending reconciliation", async () => {
  const s = delayedHistorySetup();
  try {
    for (const id of ["a", "c", "d", "e"]) s.renderer.set(id, "unknown");
    await s.start(); assert.equal(s.watched.at(-1)[2], "b");
    s.sockets[0].push({ type: "reconcile" });
    s.renderer.set("a", "running"); s.clock(15000); await s.tick();
    assert.equal(s.watched.at(-1)[2], "a");
    assert.equal(s.sockets.filter(socket => !socket.closed).length, 1); assert.equal(s.timers.size, 1);
    assert.deepEqual(s.exact, [["project", "one", "b"], ["project", "one", "b"]], "one history request and the exact socket hint request remain pending");
  } finally { s.ui.dispose(); await s.finish(); }
});

test("cached canonical active identity can reconnect during a discovery outage and stalled history", async () => {
  const s = delayedHistorySetup();
  try {
    await s.start(); s.sockets[0].onerror();
    s.api.foregroundRenders = async () => { throw new Error("Temporary foreground HTTP outage"); };
    s.clock(15000); await s.tick();
    assert.deepEqual(s.watched, [["project", "one", "a"], ["project", "one", "a"]]);
    assert.equal(s.sockets.filter(socket => !socket.closed).length, 1);
    assert.match(s.root.textContent, /Temporary foreground HTTP outage/);
    assert.equal(s.timers.size, 1); assert.deepEqual(s.exact, [["project", "one", "b"]]);
  } finally { s.ui.dispose(); await s.finish(); }
});


test("ancient unknown history stays recoverable and idle; explicit Watch remains uncertain until canonical running", async () => {
  let clock = Date.parse("2026-10-21T00:00:00Z");
  const intervals = new Map(), oldSet = globalThis.setInterval, oldClear = globalThis.clearInterval;
  globalThis.setInterval = fn => { const id = Symbol(); intervals.set(id, fn); return id; };
  globalThis.clearInterval = id => intervals.delete(id);
  const s = liveSetup({ now: () => clock });
  try {
    const state = s.states.get("one"), item = state.renderSubmissions[0];
    state.renderSubmissions = [item]; item.createdAt = "2000-01-01T00:00:00Z"; item.job.status = "unknown";
    s.api.recoverRenderSubmission = async () => { s.calls.push(["recover", item.id]); return structuredClone(state); };
    s.api.retryRenderSubmission = s.api.send = s.api.selectTake = () => assert.fail("Observation cannot execute or select");
    await s.ui.refresh();
    assert.equal(s.liveRoot.hidden, true); assert.equal(intervals.size, 0); assert.deepEqual(s.watched, []);
    assert.equal(s.find("recover-a").disabled, false); assert.equal(state.renderSubmissions.length, 1);
    await s.click("watch-a");
    s.sockets[0].push({ type: "connected" });
    s.sockets[0].push({ type: "state", status: "running" });
    assert.match(s.liveRoot.textContent, /Status unknown/); assert.doesNotMatch(s.liveRoot.textContent, /Elapsed/);
    assert.equal(intervals.size, 0); assert.equal(item.job.status, "unknown");
    item.job.status = "running"; await s.ui.refresh();
    assert.equal(intervals.size, 1); assert.match(s.liveRoot.textContent, /Elapsed 00:00 observed running/);
    clock += 65000; for (const fn of intervals.values()) fn();
    assert.match(s.liveRoot.textContent, /Elapsed 01:05/);
    s.sockets[0].push({ type: "state", status: "unknown" });
    assert.equal(intervals.size, 0); assert.doesNotMatch(s.liveRoot.textContent, /Elapsed/);
    clock += 900000; await s.ui.refresh();
    assert.match(s.liveRoot.textContent, /Elapsed 00:00/); assert.equal(intervals.size, 1);
    item.job.status = "completed"; await s.ui.refresh();
    assert.equal(intervals.size, 0); assert.equal(s.liveRoot.hidden, true); assert.equal(s.sockets[0].closed, true);
    assert.equal(s.liveRoot.querySelector("img"), null); assert.equal(state.selectedTakeId, null);
    assert.deepEqual(s.calls, []);
  } finally { s.ui.dispose(); globalThis.setInterval = oldSet; globalThis.clearInterval = oldClear; }
});

test("queued time is not running duration; disconnect and unknown release stale progress, media and clock", async () => {
  let clock = 100000;
  const s = liveSetup({ now: () => clock });
  try {
    const state = s.states.get("one"), item = state.renderSubmissions[0]; state.renderSubmissions = [item];
    item.createdAt = "2000-01-01T00:00:00Z"; item.job.status = "queued";
    s.api.recoverRenderSubmission = async () => structuredClone(state);
    await s.ui.refresh(); s.sockets[0].push({ type: "connected" });
    assert.match(s.liveRoot.textContent, /Queued.*waiting/); assert.doesNotMatch(s.liveRoot.textContent, /Elapsed/);
    clock += 1000000; item.job.status = "running"; await s.ui.refresh();
    assert.match(s.liveRoot.textContent, /Elapsed 00:00/);
    s.sockets[0].push({ type: "preview-meta", mime: "image/jpeg" }); s.sockets[0].push(new Uint8Array([1]).buffer);
    s.sockets[0].push({ type: "progress", nodeId: "sampler", value: 3, max: 10 });
    assert.ok(s.liveRoot.querySelector("img"));
    s.sockets[0].onclose();
    assert.doesNotMatch(s.liveRoot.textContent, /Elapsed|3 \/ 10/); assert.equal(s.liveRoot.querySelector("img"), null);
    item.job.status = "unknown"; await s.ui.refresh();
    assert.equal(s.liveRoot.hidden, true); assert.equal(s.sockets[0].closed, true); assert.equal(item.job.status, "unknown");
    assert.ok(s.find("recover-a")); assert.deepEqual(s.calls, []);
    item.job.status = "running"; await s.ui.refresh();
    assert.equal(s.sockets.length, 2); assert.equal(s.liveRoot.hidden, false);
    s.api.foregroundRenders = async () => { throw new Error("Renderer unavailable"); };
    await s.ui.refresh();
    assert.match(s.liveRoot.textContent, /status is uncertain/); assert.doesNotMatch(s.liveRoot.textContent, /Elapsed/);
  } finally { s.ui.dispose(); }
});


test("explicit unknown Watch pins exact provenance across navigation; terminal HTTP hands off to confirmed work", async () => {
  const s = liveSetup();
  try {
    const [a, b] = s.states.get("one").renderSubmissions; b.job.status = "unknown";
    await s.ui.refresh(); await s.click("watch-b");
    const socket = s.sockets[1];
    assert.match(s.liveRoot.textContent, /Status unknown/); assert.doesNotMatch(s.liveRoot.textContent, /Elapsed/);
    s.context.generationId = "two"; await s.ui.refresh();
    assert.equal(socket.closed, false); assert.deepEqual(s.watched.at(-1), ["project", "one", "b"]);
    b.job.status = "completed"; await s.ui.refresh();
    assert.equal(socket.closed, true); assert.deepEqual(s.watched.at(-1), ["project", "one", "a"]);
    assert.equal(a.job.status, "running"); assert.equal(s.context.generationId, "two");
    s.context.projectId = "other-project"; s.states.get("one").renderSubmissions = []; await s.ui.refresh();
    assert.equal(s.sockets.at(-1).closed, true); assert.equal(s.liveRoot.hidden, true);
    assert.deepEqual(s.calls, []); assert.deepEqual(s.cancelled, []);
  } finally { s.ui.dispose(); }
});
