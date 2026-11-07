// Offline component-startup measurement; never contacts a renderer/provider.
// Optional argument: a checkout containing the same fixture harness.
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const root = process.argv[2] ? resolve(process.argv[2]) : resolve(import.meta.dirname, "..");
const { mountRefreshLifecycle } = await import(pathToFileURL(resolve(root, "tests/js/support/refresh-lifecycle.mjs")));
const results = [];
for (let i = 0; i < 5; i++) {
  const s = mountRefreshLifecycle({ count: 50 });
  const start = performance.now();
  try {
    await s.initialize();
    results.push({ counts: s.counts(), durationMs: performance.now() - start });
  } finally { s.dispose(); }
}
const times = results.map(r => r.durationMs).sort((a, b) => a - b);
console.log(JSON.stringify({ fixture: "50 finalized submissions (40 completed/imported, 10 failed)",
  counts: results[0].counts, stableCounts: results.every(r => JSON.stringify(r.counts) === JSON.stringify(results[0].counts)),
  durationMs: { median: times[2], minimum: times[0], maximum: times[4] },
  scope: "mounted Rendered Takes + Workflow Preparation and manifest callbacks; mocked HTTP, minimal DOM, no artificial latency" }, null, 2));
