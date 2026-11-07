// Offline mount of the two startup components, using the production HTTP API.
import { Element } from "./dom.mjs";
import { api as productionApi } from "../../../web/js/api.js";
import { createRenderedTakes } from "../../../web/js/rendered-takes.js";
import { createWorkflowPreparation } from "../../../web/js/workflow-preparation.js";

export function mountRefreshLifecycle({ count = 50, mutate = null, beforeRequest = null } = {}) {
  const doc = { visibilityState: "visible", createElement: tag => new Element(tag, doc) };
  const root = new Element("main", doc), takesRoot = new Element("section", doc), prepRoot = new Element("section", doc);
  root.append(takesRoot, prepRoot);
  const context = { saved: true, projectId: "fixture-project", generationId: "fixture-generation", mode: "T2VA",
    durationSeconds: 8, aspectRatio: "16:9", dirty: false };
  const outputs = { takes: [], selectedTakeId: null, renderSubmissions: Array.from({ length: count }, (_, i) => ({
    id: `submission-${i}`, comfyPromptId: `prompt-${i}`, createdAt: "2026-10-01T00:00:00Z",
    job: { status: i % 5 === 0 ? "failed" : "completed", outputs: i % 5 === 0 ? [] : [
      { id: `output-${i}`, filename: `${i}.mp4`, type: "output", subfolder: "", importedTakeId: `take-${i}` }] },
  })) };
  const requests = [], tasks = new Set();
  const track = promise => { tasks.add(promise); promise.finally(() => tasks.delete(promise)); return promise; };
  const profile = { id: "fixture-profile", name: "Fixture", compatibleModes: ["T2VA"] };
  const continuation = { settings: { profileId: profile.id }, executions: [], resolution: { reason: "No accepted predecessor" } };
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    requests.push({ url, method: options.method });
    await beforeRequest?.(url, options);
    await Promise.resolve();
    let data;
    if (url.endsWith("/outputs")) data = outputs;
    else if (url.endsWith("/selection")) data = { selected: null };
    else if (url.includes("/foreground-renders")) data = { candidate: null, watched: null };
    else if (url.endsWith("/reconcile")) { const changed = mutate?.(outputs, url); data = { ...outputs, continuationChanged: Boolean(changed) }; }
    else if (url.endsWith("/continuation")) data = continuation;
    else if (url.endsWith("/routing")) data = { routing: { assets: {} }, assets: [] };
    else if (url.endsWith("/controls")) data = { controls: [], warnings: [] };
    else if (url.endsWith("/profiles")) data = [profile];
    else if (url.endsWith(`/profiles/${profile.id}`)) data = { ...profile, bindings: [] };
    else if (url.endsWith("/connections")) data = [];
    else if (url.endsWith("/reference-manifest")) data = { entries: [] };
    else throw new Error(`Unexpected fixture request: ${url}`);
    return { ok: true, json: async () => structuredClone(data) };
  };
  let preparation;
  const manifest = () => track(productionApi.referenceManifest({ filesystemProjectId: context.projectId,
    generationId: context.generationId, mode: context.mode, assets: [], subjects: [], executionRouting: preparation?.routing }));
  const takes = createRenderedTakes({ root: takesRoot, api: productionApi, getContext: () => context,
    setTimer: () => 1, clearTimer: () => {},
    onReconciled: () => track(preparation.refreshContinuation({ afterChange: true })) });
  preparation = createWorkflowPreparation({ root: prepRoot, api: productionApi, getContext: () => context,
    getOutput: () => null, onRoutingChanged: manifest });
  return { context, outputs, continuation, profile, requests, takes, preparation, root, manifest,
    async settle() { while (tasks.size) await Promise.all([...tasks]); await new Promise(resolve => setImmediate(resolve)); },
    async initialize() { await Promise.all([takes.refresh(), preparation.refresh(), manifest()]); await this.settle(); },
    counts() {
      const count = re => requests.filter(r => re.test(r.url)).length;
      return { reconciliation: count(/\/reconcile$/), continuation: count(/\/continuation$/),
        profiles: count(/\/profiles(?:\/[^/]+)?$/), controlsRouting: count(/\/(?:controls|routing)$/),
        manifests: count(/\/reference-manifest$/), selection: count(/\/selection$/), total: requests.length };
    },
    dispose() { takes.dispose(); globalThis.fetch = previousFetch; },
  };
}
