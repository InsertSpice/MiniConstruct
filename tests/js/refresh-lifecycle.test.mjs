import assert from "node:assert/strict";
import test from "node:test";
import { mountRefreshLifecycle } from "./support/refresh-lifecycle.mjs";

for (const count of [0, 1, 50, 100]) {
  test(`startup with ${count} finalized historical submissions has constant API cost`, async () => {
    const s = mountRefreshLifecycle({ count });
    try {
      await s.initialize();
      assert.deepEqual(s.counts(), { reconciliation: 0, continuation: 1, profiles: 2,
        controlsRouting: 2, manifests: 3, selection: 2, total: 13 });
      assert.equal(s.outputs.renderSubmissions.length, count);
      assert.equal(s.outputs.selectedTakeId, null);
      assert.ok(s.requests.every(r => r.method === "GET" || r.url === "/api/reference-manifest"));
    } finally { s.dispose(); }
  });
}

test("50 mixed historical records reconcile only recoverable attempts and refresh continuation once after the batch", async () => {
  const s = mountRefreshLifecycle({ mutate: (outputs, url) => {
    const id = url.split("/").at(-2), row = outputs.renderSubmissions.find(r => r.id === id);
    row.job = { status: "completed", outputs: [{ id: `out-${id}`, filename: "recovered.mp4", importedTakeId: `take-${id}` }] };
    row.reconciliationRequired = false;
    return true;
  } });
  for (const [i, status] of ["queued", "running", "unknown", "completed"].entries()) {
    s.outputs.renderSubmissions[i].job = { status, outputs: [] };
    s.outputs.renderSubmissions[i].reconciliationRequired = true;
  }
  s.outputs.renderSubmissions[4].job = { status: "cancelled", outputs: [] };
  try {
    await s.initialize();
    assert.equal(s.counts().reconciliation, 4);
    assert.equal(s.counts().continuation, 2);
    assert.equal(s.outputs.renderSubmissions.length, 50);
    assert.equal(s.outputs.selectedTakeId, null);
    assert.equal(s.continuation.acceptedId, undefined);
    await s.takes.refresh(); await s.settle();
    assert.equal(s.counts().reconciliation, 4, "manual refresh does not recheck settled history");
    assert.equal(s.counts().continuation, 2);
  } finally { s.dispose(); }
});

test("unchanged recovery results do not refresh continuation or controls", async () => {
  const s = mountRefreshLifecycle();
  for (const row of s.outputs.renderSubmissions.slice(0, 4)) row.job = { status: "unknown", outputs: [] };
  try {
    await s.initialize();
    assert.equal(s.counts().reconciliation, 4);
    assert.equal(s.counts().continuation, 1);
    assert.equal(s.counts().controlsRouting, 2);
    await s.takes.refresh(); await s.settle();
    assert.equal(s.counts().reconciliation, 8);
    assert.equal(s.counts().continuation, 1);
  } finally { s.dispose(); }
});

test("concurrent startup refreshes share local reads and the history coordinator", async () => {
  const s = mountRefreshLifecycle();
  try {
    await Promise.all([s.initialize(), s.takes.refresh(), s.takes.refresh(), s.preparation.refresh(), s.preparation.refresh()]);
    await s.settle();
    assert.equal(s.counts().reconciliation, 0);
    assert.equal(s.counts().continuation, 1);
    assert.equal(s.requests.filter(r => r.url.endsWith("/outputs")).length, 1);
    assert.equal(s.requests.filter(r => r.url.includes("/foreground-renders")).length, 1);
    assert.equal(s.counts().profiles, 2);
  } finally { s.dispose(); }
});

test("repeated browser mounts always revalidate continuation rather than reuse eligibility", async () => {
  for (let i = 0; i < 3; i++) {
    const s = mountRefreshLifecycle();
    s.profile.nativeContinuation = { contextFrames: 22, contextGrid: { minimum: 1, step: 1, alignment: "reject" }, inputs: {} };
    s.continuation.resolution.reason = i ? "Native state missing or acceptance revoked" : "Accepted predecessor";
    try {
      await s.initialize();
      assert.equal(s.counts().continuation, 1);
      assert.equal(s.counts().reconciliation, 0);
      assert.ok(s.root.textContent.includes(s.continuation.resolution.reason));
    } finally { s.dispose(); }
  }
});

test("reference changes during reconciliation revalidate authority without a second refresh for unchanged reconciliation", async () => {
  let release, entered;
  const started = new Promise(resolve => entered = resolve), gate = new Promise(resolve => release = resolve);
  const s = mountRefreshLifecycle({ beforeRequest: async url => {
    if (url.endsWith("/reconcile")) { entered(); await gate; }
  } });
  s.outputs.renderSubmissions[0].job = { status: "unknown", outputs: [] };
  try {
    const startup = s.initialize(); await started;
    await s.preparation.invalidateReferences();
    release(); await startup;
    assert.equal(s.counts().reconciliation, 1);
    assert.equal(s.counts().continuation, 2, "one initial read and one required Reference invalidation");
    assert.equal(s.outputs.selectedTakeId, null);
  } finally { release(); s.dispose(); }
});

test("manual refresh discovers a late output while preserving stable history and acceptance", async () => {
  let available = false;
  const s = mountRefreshLifecycle({ mutate: outputs => {
    if (!available) return false;
    outputs.renderSubmissions[0].job.outputs = [{ id: "late", filename: "late.mp4", importedTakeId: "late-take" }];
    return true;
  } });
  s.outputs.renderSubmissions[0].job = { status: "completed", outputs: [] };
  s.continuation.acceptedId = "existing-accepted-attempt";
  try {
    await s.initialize(); assert.equal(s.counts().reconciliation, 1);
    available = true;
    await s.takes.refresh(); await s.settle();
    assert.equal(s.counts().reconciliation, 2); assert.equal(s.counts().continuation, 2);
    await s.takes.refresh(); await s.settle();
    assert.equal(s.counts().reconciliation, 2);
    assert.equal(s.continuation.acceptedId, "existing-accepted-attempt");
    assert.equal(s.outputs.renderSubmissions.length, 50);
  } finally { s.dispose(); }
});
