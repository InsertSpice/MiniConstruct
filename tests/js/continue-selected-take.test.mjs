import assert from "node:assert/strict";
import test from "node:test";
import { createTakeContinuation } from "../../web/js/continue-selected-take.js";
import { createNavigationAuthority } from "../../web/js/navigation-authority.js";
import { api } from "../../web/js/api.js";

const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
function setup() {
  const state = { projectId: "A", generationId: "g1", projectFingerprint: "saved", dirty: false,
    generations: [{ generationId: "g1" }, { generationId: "later" }] };
  const take = { id: "selected", sha256: "exact-sha", originalFilename: "external.mp4" };
  const calls = [], messages = [], adopted = [], confirmations = [], response = deferred();
  const navigationAuthority = createNavigationAuthority();
  const continueTake = createTakeContinuation({
    api: { continueSelectedTake: (...args) => { calls.push(args); return response.promise; } },
    getContext: () => state, navigationAuthority,
    ready: () => !state.dirty && Boolean(state.projectId),
    confirm: message => { confirmations.push(message); return true; },
    notify: message => messages.push(message), adopt: project => adopted.push(project),
  });
  return { state, take, calls, messages, adopted, confirmations, response, navigationAuthority, continueTake };
}

test("exact selected authority, confirmation, later continuity warning and explicit next actions", async () => {
  const s = setup();
  const pending = s.continueTake(s.take, { status: "user_edited" });
  await Promise.resolve();
  assert.deepEqual(s.calls, [["A", "g1", { expectedProjectFingerprint: "saved", takeId: "selected", takeSha256: "exact-sha" }]]);
  assert.match(s.confirmations[0], /immediately after/);
  assert.match(s.confirmations[0], /affect later/);
  s.response.resolve({ id: "new" }); await pending;
  assert.deepEqual(s.adopted, [{ id: "new" }]);
  assert.match(s.messages[0], /Saved observation available/);
  assert.match(s.messages[0], /Generate.*Prepare and Send explicitly/);
});

test("no selection does not infer newest; dirty edits prevent mutation", async () => {
  const s = setup();
  await s.continueTake(null); assert.match(s.messages[0], /Select a Take first/);
  s.state.dirty = true; await s.continueTake(s.take);
  assert.equal(s.calls.length, 0);
});

test("duplicate clicks share one pending operation", async () => {
  const s = setup(), pending = s.continueTake(s.take);
  await Promise.resolve(); await s.continueTake(s.take);
  assert.equal(s.calls.length, 1);
  s.response.resolve({}); await pending;
  assert.match(s.confirmations[0], /No observation is available/);
});

test("confirmed continuation supersedes an older navigation operation", async () => {
  const s = setup();
  const older = s.navigationAuthority.begin("A", "g1");
  const pending = s.continueTake(s.take);
  await Promise.resolve();
  assert.equal(s.navigationAuthority.current(older), false);
  s.response.resolve({}); await pending;
  assert.equal(s.adopted.length, 1);
});

test("production API sends one guarded mutation with no authoring payload", async () => {
  const originalFetch = globalThis.fetch, calls = [];
  globalThis.fetch = async (...args) => { calls.push(args); return { ok: true, json: async () => ({ id: "new" }) }; };
  try {
    const authority = { expectedProjectFingerprint: "exact", takeId: "selected", takeSha256: "sha" };
    assert.deepEqual(await api.continueSelectedTake("A", "g1", authority), { id: "new" });
    assert.equal(calls.length, 1);
    assert.equal(calls[0][0], "/api/projects/A/generations/g1/continue-selected-take");
    assert.equal(calls[0][1].method, "POST");
    assert.deepEqual(JSON.parse(calls[0][1].body), authority);
  } finally { globalThis.fetch = originalFetch; }
});

for (const change of ["generation", "project", "ABA"]) {
  test(`late response after ${change} navigation cannot adopt or notify`, async () => {
    const s = setup(), pending = s.continueTake(s.take);
    await Promise.resolve();
    s.navigationAuthority.invalidate();
    if (change === "generation") s.state.generationId = "later";
    if (change === "project") s.state.projectId = "B";
    s.response.resolve({}); await pending;
    assert.equal(s.adopted.length, 0); assert.equal(s.messages.length, 0);
  });
}

for (const change of ["dirty", "projectFingerprint"]) {
  test(`new ${change} during the request stays in editor`, async () => {
    const s = setup(), pending = s.continueTake(s.take);
    await Promise.resolve(); s.state[change] = change === "dirty" ? true : "newer";
    s.response.resolve({}); await pending;
    assert.equal(s.adopted.length, 0);
    assert.match(s.messages[0], /current edits were kept/);
  });
}

test("changes during confirmation do not send the stale request", async () => {
  const s = setup();
  const confirmation = deferred();
  const run = createTakeContinuation({ api: { continueSelectedTake: () => assert.fail("stale mutation") },
    navigationAuthority: s.navigationAuthority, getContext: () => s.state, ready: () => true,
    confirm: () => confirmation.promise, adopt: () => assert.fail("adopt"), notify() {} });
  const pending = run(s.take); s.state.projectFingerprint = "changed";
  confirmation.resolve(true); await pending;
});

test("unsaved observation edits made during creation stay in the source editor", async () => {
  const s = setup(); let dirty = false;
  const pending = s.continueTake(s.take, null, () => dirty);
  await Promise.resolve(); dirty = true;
  s.response.resolve({}); await pending;
  assert.equal(s.adopted.length, 0);
  assert.match(s.messages[0], /current edits were kept/);
});
