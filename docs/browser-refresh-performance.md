# Browser refresh request lifecycle

The startup amplification came from frontend request scheduling. It was not caused by Uvicorn access logging. The fix leaves render history intact and makes automatic reconciliation proportional to recoverable attempts rather than the total historical collection.

## Confirmed call chain

1. `app.js:init()` mounts Rendered Takes and Workflow Preparation. Opening a saved Project calls `adoptProjectResponse()`, which restores the Workspace and Generation, then calls both components' `sync()` methods. Generation navigation and `setDirty()` also call these sync methods.
2. `rendered-takes.js:sync()` detects the new saved context and starts `refresh()`. It reads Generation outputs and execution selection, and independently discovers foreground work through `refreshForeground()` → `foregroundRenders(..., {refresh: true})`.
3. Previously, `refresh()` put **every saved render submission** into its sequential reconciliation loop. Only the existing periodic `pendingOnly` refresh excluded terminal jobs. Initial loading and ordinary refresh used the unrestricted default.
4. Each iteration called `api.reconcileRenderSubmission()` → `POST /api/projects/{p}/generations/{g}/render-submissions/{s}/reconcile` → `take_routes.reconcile()` → `render_reconciliation.reconcile_submission()`. The backend loaded the exact recorded submission/Connection, reconciled its native record, optionally read renderer status/imported output, and returned the entire Generation output summary. Finalized imported renders and terminal failures still incurred these application POSTs even when no renderer HTTP call was needed.
5. Each loop response invoked `onReconciled`. The application callback called `workflowPreparation.refreshContinuation()` → `GET /api/execution/projects/{p}/generations/{g}/continuation` → `execution_routes.continuation_status()` → `ProjectStore.continuation_status()` → the existing native resolver.
6. Every successful continuation response called `renderProfiles()`, which unconditionally refreshed Workflow Controls and potentially fetched missing Profile details. It also invoked `onRoutingChanged()`, which requested a reference manifest. Concurrent detail reads could repeat before the first response supplied bindings. Context sync also explicitly requested controls after `renderProfiles()` had already started that request.

With 42 historical rows, that initial loop explains 42 reconciliation POSTs plus the ordinary initial continuation read and 42 callback reads: **43 continuation GETs**. The offline fixture reproduces exactly the same N and N+1 pattern with 50 rows. The provided log itself was not available as a file, so this is a code-path and instrumented-fixture confirmation of the reported pattern, not a replay of that log.

## Other request triggers audited

| Request | Triggers and retained behavior |
|---|---|
| Output/selection reads | Rendered Takes startup/context switch, manual refresh, existing foreground cadence; preparation context sync/manual refresh, selection changes, execution setup, reference invalidation. Two independent selection readers remain. |
| Reconciliation | Coordinated history refresh; exact watcher terminal/reconcile events; explicit Recover; output import; exact cancellation recovery. Retry remains an explicit separate action that creates a new attempt. |
| Foreground | Initial refresh, existing 15-second cadence, final refresh after recovery, exact watcher/cancellation reads. Foreground discovery is bounded to four recorded Connections and three exact lookups per pass. |
| Continuation | Preparation startup/context switch/manual refresh, successful Send, explicit continuation/renderer-seed actions, relevant reconciliation/discovery changes, saved Profile changes and reference invalidation. |
| Profile list/details | Preparation refresh, execution configuration save/delete callback, selected Profile changes, and Profile rendering when details are absent. Existing Profile summary and selected detail are distinct required requests. |
| Controls | Context/setup changes, selecting a Profile, saved Profile refresh, preparation and explicit control/preset mutations. Ordinary unchanged continuation rendering reuses already loaded controls. |
| Routing | Context/manual refresh, reference invalidation, explicit continuation changes and explicit routing mutations. |
| Reference manifest | Asset/Workspace restoration and edits, Project adoption, reference synchronization that changes canonical data, routing changes and changed continuation truth. Same-turn application refreshes now yield once and discard superseded requests before issuing HTTP. |
| Reference authority | Explicit Reference edits and before execution selection/preparation; existing content/revision checks already prevent redundant writes. No authority cache was relaxed. |

Selection changes still invalidate preparation and refresh Rendered Takes. Reference synchronization still requires explicit reselection when saved inputs change. Profile deletion, changed saved bindings, authoring/setup changes and navigation retain their invalidation paths.

## New lifecycle and recovery

The output summary now supplies a derived `reconciliationRequired` flag. It is not persisted and does not change the Project schema. Active/unknown attempts require recovery. Completed records with no video metadata, or one candidate without an import association, remain recoverable. Terminal records also require recovery when their exact Project, Generation, submission and Connection match a still-pending native sidecar record. Settled completed/imported, failed and cancelled records are skipped. Ambiguous multiple outputs retain explicit output selection; deleted imported Takes are never resurrected.

The history coordinator keeps its existing concurrency limit of one and publishes at most one continuation notification after a batch with meaningful changes. Concurrent initial refreshes share their local reads. Slow history downloads do not block the independent foreground lane. An exact terminal event can deliberately make a fresh read while an older history request is pending: this is necessary to observe a change that occurred after the earlier request started, and is covered by the existing terminal/out-of-order tests.

Backend reconciliation returns `continuationChanged`, ignoring `lastCheckedAt` alone. Native sidecar repairs count as changes even if Project output metadata is identical. Foreground terminal discovery also propagates changes. The frontend suppresses notifications for unchanged results. Concurrent continuation reads share in-flight work; a relevant mutation retires the earlier response and coalesces callers into one final fresh read. There is no eligibility cache after a request settles.

Completed jobs without recorded outputs now re-read exact renderer metadata on recovery. A one-way terminal-state merge can add discovered output descriptors only while existing descriptors are empty. It cannot downgrade completion, reopen a failed/cancelled job, overwrite imported associations, or queue a workflow. Failed downloads remain recoverable. Existing explicit Recover and output-import controls remain available.

Navigation tickets, reset epochs, setup/Profile identity and request epochs reject stale responses, including leaving and returning to the same Project/Generation. Project revision checks also prevent an old watched response from overwriting newer output truth. Saved Profile and Reference changes retire pending eligibility reads. Accept/revoke mutations cannot be overwritten by older GETs.

No acceptance is inferred from completion. The native resolver, explicit confirmation/revocation, accepted predecessor selection, exact render/Connection/Profile identity, model/codec identity, saved latent keys/namespace/segment indices, stale source checks and output/artifact verification remain authoritative. Existing native-only I2VA and G1 → G2 → G3 tests are included in validation.

## Measured comparison

The identical offline fixture mounts the production Rendered Takes and Workflow Preparation components, wires their application callbacks, and counts production API calls through a mocked `fetch`. It contains 50 terminal historical submissions: 40 completed with imported output associations and 10 failed. No real Projects, media, renderer or provider are used.

| Request category | Before | After |
|---|---:|---:|
| Reconciliation POSTs | 50 | 0 |
| Continuation GETs | 51 | 1 |
| Profile GETs, list + selected detail | 2 | 2 |
| Controls/routing requests | 52 | 2 |
| Reference-manifest requests | 53 | 3 |
| Execution selection GETs | 2 | 2 |
| Total application API requests in this fixture | 214 | 13 |

Counts were identical across five runs per version. The baseline frontend modules were read from Git revision `2db6a5947c39eb386354fdcf9edd2c1f95434cc3` into a temporary fixture directory, with the same test harness and DOM helper. No checkout or Git state was changed. Raw results are in `refresh-request-measurements.json`. Run `node tools/measure_refresh_requests.mjs` to repeat the current measurement, or pass an isolated baseline fixture checkout as its argument.

Median fixture startup time was **261.9 ms before and 8.4 ms after** (five runs; before range 252.0–290.4 ms, after 7.4–23.4 ms). This measures mocked HTTP and a minimal test DOM without artificial network delay. It does **not** measure real browser startup, the reported 5–10-second delay, backend filesystem latency, media downloads or renderer latency. The fixture excludes unrelated full-app bootstrap requests and static JavaScript module loads; 13 is not a claimed complete-browser request total.

Regression fixtures with 0, 1, 50 and 100 settled submissions all have the same request count. A 50-row mixture with four recoverable attempts makes four reconciliation POSTs and two continuation GETs (initial plus final changed batch), rather than 50 and 51. Four unchanged recoveries make four POSTs and only the initial continuation GET. Manual refresh retries unresolved records and stops revisiting them once their outputs settle.

## Regression coverage and limits

New frontend coverage counts real API wrapper calls and exercises concurrent startup, repeated mounts, delayed Profile details, delayed output recovery/manual refresh, reference changes during reconciliation, unchanged versus changed batches, saved Profile changes, setup changes, revoked acceptance and out-of-order continuation/navigation responses. Existing watched-terminal, foreground/cancel, fingerprint/navigation, native-state compatibility, missing-state, selected-render and lineage tests remain in the full suites. New backend coverage verifies read-only reconciliation hints, 50 settled records, delayed output discovery, terminal monotonicity, restart recovery and timestamp-only no-change behavior.

Remaining costs are intentional or outside this focused change: unresolved attempts still need exact recovery; a completed attempt with no output metadata remains eligible for a later recovery check; full Generation summaries/history DOM construction still scale with history size; separate components still read execution selection; initial/context/routing/continuation manifest notifications can issue more than one request. The existing foreground cadence and external status/download latency remain. No new polling, endpoint, background job, persistent cache or arbitrary delay was introduced.

## Changed files

- `web/js/rendered-takes.js`: recovery filtering, startup sharing, batch notification and watched revision guard.
- `web/js/workflow-preparation.js`: in-flight request sharing, changed-status notifications, Profile/control loading and invalidation guards.
- `web/js/app.js`: meaningful-reconciliation invalidation and same-turn manifest request suppression.
- `miniconstruct/take_store.py`: transient recovery hints and monotonic late-output discovery.
- `miniconstruct/project_store.py`: pass exact Project identity into output summaries.
- `miniconstruct/native_continuation.py`: report whether a pending native status changed.
- `miniconstruct/render_reconciliation.py`: meaningful-change receipts and late metadata recovery.
- `tests/js/refresh-lifecycle.test.mjs`, `tests/js/support/refresh-lifecycle.mjs`, `tests/js/workflow-preparation.test.mjs`: instrumented lifecycle and race regressions.
- `tests/test_render_reconciliation.py`: offline recovery/idempotency regressions.
- `tools/measure_refresh_requests.mjs`, this report and `docs/refresh-request-measurements.json`: reproducible component measurement and review evidence.

All changes remain uncommitted. No live ComfyUI instance/provider was contacted and no application render was started. The working repository was not committed, pushed or released; existing release integration tests operate only on disposable local fixture repositories.

## Final validation and Git status

- Targeted JavaScript lifecycle, preparation and Rendered Takes run: **245 passed**. The fingerprint/navigation integration run also passed before the full final run.
- Targeted Python Project Store and reconciliation run: **137 passed, 1 skipped**. Native I2VA/lineage and foreground targeted tests also passed before the complete run.
- `./.venv/Scripts/python.exe -m pytest`: **2,802 passed, 3 skipped, 2 warnings**, 430.71 seconds. Warnings were the dependency's Starlette/httpx deprecation and denied pytest cache writing; neither affected test results.
- `node --test tests/js/*.test.mjs`: **703 passed**, zero failures/cancellations/skips, 41.15 seconds.
- `git diff --check`: **passed**, no whitespace errors. Git also reported its normal LF-to-CRLF advisory for two existing files.

The initial working tree was clean. Final status: **9 modified tracked files and 5 new untracked files**, all belonging to this change:

```text
 M miniconstruct/native_continuation.py
 M miniconstruct/project_store.py
 M miniconstruct/render_reconciliation.py
 M miniconstruct/take_store.py
 M tests/js/workflow-preparation.test.mjs
 M tests/test_render_reconciliation.py
 M web/js/app.js
 M web/js/rendered-takes.js
 M web/js/workflow-preparation.js
?? docs/browser-refresh-performance.md
?? docs/refresh-request-measurements.json
?? tests/js/refresh-lifecycle.test.mjs
?? tests/js/support/refresh-lifecycle.mjs
?? tools/measure_refresh_requests.mjs
```
