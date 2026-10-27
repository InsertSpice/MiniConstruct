import assert from "node:assert/strict";
import test from "node:test";
import { createContinuationDrafting, mountContinuationDrafting } from "../../web/js/continuation-drafting.js";
import { createProviderActivity } from "../../web/js/provider-activity.js";
import { createNavigationAuthority } from "../../web/js/navigation-authority.js";
import { api } from "../../web/js/api.js";
import { shouldMarkWorkspaceDirty } from "../../web/js/sequence-workflow.js";

const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return { promise, resolve, reject }; };
test("ephemeral direction and preview edits do not dirty the Project before adoption", () => {
  assert.equal(shouldMarkWorkspaceDirty({ closest: selector => [".left-rail", "#continuation-drafting"].includes(selector) }), false);
  assert.equal(shouldMarkWorkspaceDirty({ closest: selector => selector === ".left-rail" }), true);
});
function setup() {
  const context = { projectId: "p", generationId: "g2", projectFingerprint: "full", projectEditFingerprint: "saved", eligible: true };
  const response = deferred(), calls = [], previews = [], applied = [], notices = [], confirmations = [];
  const activity = createProviderActivity({ setTimer: () => 1, clearTimer: () => {} });
  const navigationAuthority = createNavigationAuthority();
  const s = { context, response, calls, previews, applied, notices, confirmations, activity, navigationAuthority,
    text: "", accept: true, validation: null };
  s.options = { api: {
    draftContinuation: (...args) => { calls.push(args); return response.promise; },
    validateContinuationDraft: (...args) => { calls.push(args); return s.validation?.promise ?? Promise.resolve({ valid: true }); },
  }, activity, navigationAuthority, getContext: () => context, getSettings: () => ({ modelId: "configured" }),
    getCreativeRequest: () => s.text, applyCreativeRequest: text => { s.text = text; applied.push(text); },
    confirm: message => { confirmations.push(message); return s.accept; },
    present: result => previews.push(result), notify: message => notices.push(message) };
  s.control = createContinuationDrafting(s.options);
  s.result = { candidate: "Continue from the observed ending.", authority: { projectId: "p", generationId: "g2",
    projectEditFingerprint: "saved", evidenceRevision: "a".repeat(64),
    sourceGenerationId: "g1", takeId: "selected", takeSha256: "sha" }, warnings: [], evidence: {} };
  s.finish = () => response.resolve(s.result);
  return s;
}

test("one explicit call previews separately, validates exact authority, adopts locally only", async () => {
  const s = setup(), pending = s.control.generate("Open the door.");
  assert.equal(s.activity.active().id, "continuation-draft");
  assert.equal(s.calls[0][2].direction, "Open the door.");
  s.finish(); await pending;
  assert.equal(s.text, ""); assert.equal(s.calls.length, 1);
  assert.equal(s.previews.at(-1), s.result);
  await s.control.apply("My edited candidate.");
  assert.deepEqual(s.applied, ["My edited candidate."]);
  assert.deepEqual(s.calls[1], ["p", "g2", s.result.authority]);
  assert.equal(s.activity.active(), null);
});

for (const text of ["Existing saved request", "New unsaved edit during request"]) {
  test(`explicit replacement required: ${text}`, async () => {
    const s = setup(), pending = s.control.generate();
    s.text = text; s.finish(); await pending;
    assert.equal(s.text, text); s.accept = false;
    await s.control.apply(s.result.candidate);
    assert.equal(s.text, text); assert.equal(s.calls.length, 1);
    s.accept = true; await s.control.apply(s.result.candidate);
    assert.equal(s.text, s.result.candidate);
    assert.match(s.confirmations[0], /unsaved edits/);
  });
}

for (const change of ["project", "generation", "fingerprint", "ABA", "replacing"]) {
  test(`late response rejected after ${change} navigation/authority change`, async () => {
    const s = setup(), pending = s.control.generate();
    if (change === "project") s.context.projectId = "other";
    if (change === "generation") s.context.generationId = "g3";
    if (change === "fingerprint") s.context.projectEditFingerprint = "new-source-take";
    if (change === "replacing") s.context.replacing = true;
    if (change === "ABA") s.navigationAuthority.invalidate();
    s.finish(); await pending;
    assert.equal(s.previews.at(-1), null);
    await s.control.apply(s.result.candidate);
    assert.deepEqual(s.applied, []);
  });
}

test("apply revalidation preserves newer edits and rejects changed source", async () => {
  const s = setup(), pending = s.control.generate(); s.finish(); await pending;
  s.validation = deferred();
  const applying = s.control.apply(s.result.candidate);
  s.text = "New edit while rechecking saved source";
  s.validation.resolve({ valid: true }); await applying;
  assert.deepEqual(s.applied, []);
  s.validation = deferred(); const failed = s.control.apply(s.result.candidate);
  await Promise.resolve(); s.validation.reject(new Error("Selected Take removed")); await failed;
  assert.deepEqual(s.applied, []); assert.match(s.notices.at(-1), /Selected Take removed/);
});

test("preview edits during validation cannot apply an obsolete candidate or duplicate Apply", async () => {
  const s = setup(), pending = s.control.generate(); s.finish(); await pending;
  s.validation = deferred();
  let preview = "Candidate before validation";
  const applying = s.control.apply(preview, () => preview);
  await s.control.apply("second click", () => "second click");
  preview = "New candidate edit during validation";
  s.validation.resolve({ valid: true }); await applying;
  assert.deepEqual(s.applied, []);
});

test("renderer fingerprint advances without invalidating a current candidate", async () => {
  const s = setup(), pending = s.control.generate();
  s.context.projectFingerprint = "renderer-status-update";
  s.finish(); await pending;
  assert.equal(s.previews.at(-1), s.result);
  await s.control.apply(s.result.candidate);
  assert.deepEqual(s.applied, [s.result.candidate]);
});

test("cancellation ignores even a late success; provider failure releases shared activity", async () => {
  const s = setup(), pending = s.control.generate();
  s.activity.cancelActive(); assert.equal(s.calls[0][3].aborted, true);
  s.finish(); await pending; assert.equal(s.previews.at(-1), null);
  const f = setup(), failing = f.control.generate();
  f.response.reject(new Error("Provider offline")); await failing;
  assert.equal(f.activity.active(), null); assert.match(f.notices[0], /Provider offline/);
});

test("shared activity prevents duplicate drafts and hidden concurrent model work", async () => {
  const s = setup(), pending = s.control.generate();
  await s.control.generate("another"); assert.equal(s.calls.length, 1);
  s.finish(); await pending;
  s.activity.begin({ id: "h3-main", label: "H3", cancel() {} });
  await s.control.generate(); assert.equal(s.calls.length, 1);
});

test("mounted editable preview and explicit apply control", async () => {
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ""; }
    append(el) { this.children.push(el); }
    addEventListener(type, callback) { this.listeners[type] = callback; }
    setAttribute() {}
  }
  const s = setup(), root = new Element("details");
  root.ownerDocument = { createElement: tag => new Element(tag) };
  const mounted = mountContinuationDrafting({ root, ...s.options }); mounted.sync();
  const buttons = root.children.filter(c => c.tag === "button");
  const pending = buttons[0].listeners.click(); s.finish(); await pending;
  const preview = root.children.find(c => c.tag === "textarea");
  assert.equal(preview.hidden, false); assert.equal(preview.value, s.result.candidate);
  assert.deepEqual(s.applied, []);
  preview.value = "Edited in preview"; await buttons[1].listeners.click();
  assert.deepEqual(s.applied, ["Edited in preview"]);
  s.context.eligible = false; mounted.sync(); assert.equal(root.hidden, true);
});

test("real API adapters forward cancellation and exact adoption authority", async () => {
  const previous = globalThis.fetch, calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return { ok: true, json: async () => ({}) }; };
  try {
    const controller = new AbortController();
    await api.draftContinuation("p", "g", { direction: "Next" }, controller.signal);
    await api.validateContinuationDraft("p", "g", { takeId: "selected" });
    assert.equal(calls[0][0], "/api/projects/p/generations/g/draft-continuation");
    assert.equal(calls[0][1].signal, controller.signal);
    assert.equal(calls[1][1].body, JSON.stringify({ takeId: "selected" }));
  } finally { globalThis.fetch = previous; }
});
