import assert from "node:assert/strict";
import test from "node:test";
import { beginProviderActivity, createProviderActivity, formatActivityElapsed, mountProviderActivity } from "../../web/js/provider-activity.js";
import { api } from "../../web/js/api.js";
import { Element } from "./support/dom.mjs";

test("one active provider request reports elapsed time and blocks different and duplicate actions", () => {
  let now = 0, tick, timerCleared = false, cancelled = 0;
  const activity = createProviderActivity({ now: () => now, setTimer: callback => { tick = callback; return 1; }, clearTimer: () => { timerCleared = true; tick = null; } });
  const seen = [];
  activity.subscribe(active => seen.push(active ? `${active.label} ${formatActivityElapsed(activity.elapsed(active))}` : "idle"));
  const first = activity.begin({ id: "story-build", label: "Build Initial Range", owner: "story", cancel: () => cancelled++ });
  assert.equal(seen.at(-1), "Build Initial Range 00:00");
  now = 65000; tick();
  assert.equal(seen.at(-1), "Build Initial Range 01:05");
  assert.match(activity.conflict("h3-story", "Generate from Story"), /Cannot start Generate from Story while Build Initial Range/);
  assert.equal(activity.conflict("story-build", "Build Initial Range"), "Build Initial Range is already running.");
  assert.equal(activity.begin({ id: "h3-story", label: "Generate from Story", cancel: () => {} }), null);
  activity.cancelActive(); activity.cancelActive();
  assert.equal(cancelled, 1);
  assert.equal(activity.active().cancelling, true);
  assert.equal(activity.finish(first), true);
  assert.equal(timerCleared, true);
  assert.equal(tick, null);
  const second = activity.begin({ id: "repair", label: "Repairing H3 Format", cancel: () => {} });
  assert.equal(activity.finish(first), false);
  assert.equal(activity.active(), second);
  activity.finish(second);
  assert.equal(seen.at(-1), "idle");
});

test("mounted global widget shows the active label, elapsed time and Stop control", async () => {
  let now = 0, tick, cancelled = 0;
  const activity = createProviderActivity({ now: () => now, setTimer: callback => { tick = callback; return 1; }, clearTimer: () => { tick = null; } });
  const root = new Element("div"); root.hidden = true;
  const label = new Element("span"); label.id = "provider-activity-label";
  const cancel = new Element("button"); cancel.id = "provider-activity-cancel";
  root.append(label, cancel);
  mountProviderActivity(root, activity);
  const token = activity.begin({ id: "h3-story", label: "Generate from Story", owner: "app", cancel: () => cancelled++ });
  assert.equal(root.hidden, false);
  assert.equal(label.textContent, "Generating — Generate from Story · 00:00");
  assert.equal(cancel.textContent, "Stop");
  now = 65000; tick();
  assert.equal(label.textContent, "Generating — Generate from Story · 01:05");
  await cancel.emit("click");
  assert.equal(cancelled, 1);
  assert.match(label.textContent, /Cancelling/);
  activity.finish(token);
  assert.equal(root.hidden, true);
  assert.equal(tick, null);
  const translation = activity.begin({ id: "dialogue-translation", label: "Spoken Output — Japanese", requestLabel: "Translate / Refresh — Japanese", verb: "Translating", cancel: () => {} });
  assert.equal(label.textContent, "Translating — Spoken Output — Japanese · 00:00");
  assert.equal(cancel.textContent, "Cancel");
  activity.finish(translation);
});

test("newly cancellable provider transports forward their AbortSignal", async () => {
  const oldFetch = globalThis.fetch;
  const seen = [];
  globalThis.fetch = async (_url, options) => {
    seen.push(options.signal);
    return { ok: true, json: async () => ({}) };
  };
  try {
    const controller = new AbortController();
    await api.inferPlan("p", "g", {}, controller.signal);
    await api.revisePlan("p", "g", {}, controller.signal);
    await api.resolveDialogue({}, controller.signal);
    await api.repair({}, controller.signal);
    await api.analyzeSelectedTake("p", "g", {}, controller.signal);
    assert.equal(seen.length, 5);
    assert.ok(seen.every(signal => signal === controller.signal));
  } finally { globalThis.fetch = oldFetch; }
});

test("Infer from Previous activity prevents a Repair Format request", () => {
  const activity = createProviderActivity();
  const inference = beginProviderActivity(activity, { id: "infer-plan", label: "Inferring Plan from Previous", cancel: () => {} });
  let repairCalls = 0, conflict = "";
  const repair = beginProviderActivity(activity, { id: "h3-repair", label: "Repairing H3 Format", cancel: () => {} }, message => conflict = message);
  if (repair) repairCalls++;
  assert.equal(repairCalls, 0);
  assert.match(conflict, /while Inferring Plan from Previous/);
  activity.finish(inference);
});
