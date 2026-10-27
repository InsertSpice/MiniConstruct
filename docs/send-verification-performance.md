# Send verification investigation — 2026-10-21

**Latest:** [Local-directory verified staging](#local-directory-verified-staging)
adds zero-transfer repeat staging for explicitly configured local input roots.
The measurements and Git states in earlier sections are historical checkpoints.

**Current follow-up:** the historical measurements below describe the previous change.
The [staged-media reuse follow-up](#staged-media-reuse-follow-up) supersedes its
no-cross-Send-cache statements and records the current behavior and limits.

All changes are uncommitted. The starting working tree and index were clean at
`2d4e0e090cb5bfcad50bb4b618a765bd1b748dfe`. No live ComfyUI Send or provider call
was made. The results below use synthetic temporary Projects and mocked HTTP.

## Demonstrated conflict and corrected authority

The exact error originated in `ProjectStore.execution_preparation_snapshot`:
`_manifest_fingerprint(_read_manifest(directory)) != fingerprint` after unlocked
`_hydrate_workspace`. Both Send compiler calls entered this function. The digest
covered the entire Project manifest, including renderer jobs, submissions, Takes,
names and update timestamps, rather than just execution inputs.

The benchmark reproduces the old error on **all three runs** by writing an unrelated
existing job's status during a media hash. No prompt, Reference, Profile, controls,
seed policy or predecessor changes. The revised code accepts all three runs. This
establishes a real false conflict, independent of browser Reference synchronization
or edit-fingerprint receipts.

Reconciliation writes, newly recorded submissions, output imports and Project metadata
could trigger it. `foreground_renders` reads alone could not. Native attempts live in
the execution sidecar, so sidecar-only bookkeeping could not directly change that
manifest digest; accepted-predecessor changes remain genuine execution conflicts.

Offline Prepare now compares the selected authoring fingerprint, exact selected prompt
and provenance, and canonical Reference mappings across hydration. Send shares the
existing compiler but uses canonical descriptors instead of repeated hydrated pixels.
It verifies current canonical bytes on open handles before uploads and reopens/hashes
current paths afterwards. It then recompiles metadata under the Project/configuration
locks and checks execution fingerprint plus exact prompt/provenance. The Connection is
reloaded and compared as well. `/prompt` remains inside this final authority boundary.

Project/Generation identity, Profile graph and identity, canonical content digests,
routing, effective controls, seed configuration and exact accepted predecessor stay
protected. Random seed values are allocated once after validation. Send-specific native
attempt IDs/save keys are excluded from semantic comparison while the actual graph and
record retain the original Send's identity. Handoff reuse is operation-local and requires
fresh canonical source-identity resolution. Changed execution is rejected before queueing
with a component-specific explanation. No blind retry or second pipeline was introduced.

## Method and reproducibility

From the repository root, using its existing virtual environment:

```powershell
.\.venv\Scripts\python.exe benchmarks/send_critical_path.py --baseline --repeats 3 > benchmarks/send-baseline.json
.\.venv\Scripts\python.exe benchmarks/send_critical_path.py --repeats 3 > benchmarks/send-after.json
```

`--baseline` reads the original revision into a temporary directory with `git archive`;
it does not modify the current checkout/index. Both runs use the identical benchmark
instrumentation, real FastAPI `/api/execution/send`, real ProjectStore/compiler/Send,
one backend HTTP request per Send, and `httpx.MockTransport` for ComfyUI. The final
measurements were run sequentially without the test suites running alongside them.

Each case sends the same preparation three times without operational waits/retries.
Pictures are three distinct deterministic 1024×1024 RGB noise PNGs, **9,453,339 bytes**
total; these deliberately exceed many ordinary processed JPEG reference sizes. Video
is a **67,108,864-byte** synthetic opaque payload; no codec is needed for canonical
transport. Temporary local files are warm after Prepare. The mutation case uses those
Pictures plus one prior submission. The slow case uses the same Pictures and deliberately
defers each mocked upload and queue response through an Event/Timer fixture by 250 ms
(four responses, 1,000 ms nominal injected latency). These are measured simulation
conditions, not a claim about any real Connection. Production has no added timer/wait.

Raw measurements and all counters are in [baseline JSON](../benchmarks/send-baseline.json)
and [revised JSON](../benchmarks/send-after.json). There are no CI wall-clock thresholds.

## Measured results

Medians in milliseconds, three runs per case. Queue acknowledgment includes in-process
request routing/validation up to validation of ComfyUI's `/prompt` response. Total also
includes canonical local recording and the in-process API receipt.

| Case | Before queue ack | After queue ack | Before total | After total |
| --- | ---: | ---: | ---: | ---: |
| Same prepared prompt, no media | 24.029 | 23.093 | 32.856 | 33.350 |
| Three unchanged Pictures | 169.065 | 71.832 | 181.764 | 84.202 |
| 64 MiB Video | 182.107 | 147.722 | 192.105 | 160.026 |
| Renderer mutation during verification | rejected 3/3; no queue | 82.402; accepted 3/3 | 52.188 (failure) | 93.958 |
| Three Pictures, controlled slow transport | 1,208.856 | 1,116.718 | 1,221.845 | 1,129.327 |

The Picture case reduces acknowledgment time by **57.5%** in this fixture; Video by
**18.9%**. The no-media case is effectively unchanged. Individual stage medians do not
necessarily sum to the overall median.

| Stage | Pictures before | Pictures after | Video before | Video after |
| --- | ---: | ---: | ---: | ---: |
| A. In-process HTTP routing/validation | 2.085 | 1.828 | 1.994 | 1.849 |
| B. Initial execution reconstruction | 72.990 | 20.603 | 47.792 | 13.370 |
| C. Separate transport byte verification | 9.886 | 19.620 | 33.329 | 68.103 |
| D. Uploads, including multipart reads | 6.152 | 6.562 | 45.392 | 44.661 |
| E. Post-upload execution reconstruction | 72.867 | 20.901 | 49.178 | 15.600 |
| F. Graph transport preflight | 0.075 | 0.067 | 0.061 | 0.060 |
| G. `/prompt` acceptance | 0.251 | 0.215 | 0.219 | 0.211 |
| H. Submission persistence | 10.880 | 11.040 | 9.184 | 9.720 |
| I. In-process API receipt | 1.406 | 1.181 | 1.222 | 1.176 |

Before: B/E included media hydration/hashing and Picture base64 encode/decode/hash
work; their nested `_reference_payload` time was 12.506 ms aggregate for Pictures,
64.506 ms for Video. C was one additional transport hash. After: B/E use descriptors
and C contains **both** the before-upload and after-upload hash passes. A includes
the TestClient bridge and is not a measurement of real browser networking. F's remaining
graph construction is inside B/E; final seed assignment is separately available in the
opt-in production timer. I is API serialization/receipt, not browser rendering.

Mounted deferred JavaScript tests establish the browser boundary: pending submission
shows no queue success; acceptance renders synchronously before a held continuation
refresh resolves. Previously the receipt was assigned but not rendered until that
refresh finished. No real browser wall-clock improvement is claimed.

## Actual per-Send work counts

The read counts below are full-file verification reads, with upload-source reads shown
separately; they do not represent OS cache misses. Picture hashing includes passes
over decoded byte arrays as well as files. Wire counts include multipart framing.

| Work | Three Pictures before → after | 64 MiB Video before → after |
| --- | --- | --- |
| Verification file reads | 9 → 6 | 3 → 2 |
| Bytes read for verification | 28,360,017 → 18,906,678 | 201,326,592 → 134,217,728 |
| Full media hash passes | 15 → 6 | 3 → 2 |
| Bytes hashed | 47,266,695 → 18,906,678 | 201,326,592 → 134,217,728 |
| Upload-source read passes | 3 → 3 | 1 → 1 |
| Upload-source bytes | 9,453,339 → 9,453,339 | 67,108,864 → 67,108,864 |
| Total media file reads including upload | 12 → 9 | 4 → 3 |
| Total media file bytes including upload | 37,813,356 → 28,360,017 | 268,435,456 → 201,326,592 |
| Upload requests | 3 → 3 | 1 → 1 |
| Upload wire bytes | 9,454,902 → 9,454,902 | 67,109,385 → 67,109,385 |
| `/prompt` requests | 1 → 1 | 1 → 1 |
| Total ComfyUI HTTP requests | 4 → 4 | 2 → 2 |
| Backend Send HTTP requests | 1 → 1 | 1 → 1 |
| Metadata file reads (manifest + sidecars/config) | 55 → 57 | 53 → 53 |
| Full Project manifest reads/deserializations | 36 → 37 | 34 → 33 |

The baseline mutation case reads/hashes three Pictures once and fails before any
ComfyUI request. Its revised counts match a successful Picture Send plus the controlled
metadata write. Slow-transport media/HTTP counts match the ordinary Picture case.
No-media Sends perform zero media reads/hashes/uploads and exactly one `/prompt` request.

There is no cross-Send media or remote-file cache. Identical files share verification
and upload within a single Send, but later Sends upload again and use the returned
validated filename. Restart, cleanup or foreign remote bytes cannot bypass upload based
on a prior receipt/filename. Native/handoff decoding/materialization costs remain on
their existing canonical path; this benchmark makes no claim about their runtime cost.

## What explains latency, and what remains unknown

The demonstrated local Picture bottleneck was repeated compiler hydration and base64
processing, not image codec decoding, Project size or a slow renderer. Video spends
substantial time in actual byte hashing and multipart reads; one full hash pass was
eliminated. Graph preflight, mock queue acceptance and API receipt are sub-millisecond
to roughly one millisecond in these fixtures. Metadata reads were not eliminated:
there are still 37 full manifest parses in the revised Picture fixture, plus
sidecar/config reads. Connection revalidation adds one configuration read. No global
execution cache was added, and these small-Project measurements do not establish what
metadata parsing costs in a live Project.

In the controlled slow case, upload responses consume about 792 ms after the change
and queue acceptance 265 ms: the explicitly injected transport delay dominates.
The removed local work cannot remove required remote upload or queue-response time.

**The dominant source of the user's actual 5+ second delay has not been measured.**
No local fixture reproduced five seconds. Neither real ComfyUI slowness nor a large
Project is established. The frontend's auxiliary-refresh dependency could extend
visible latency by that refresh's actual duration, which is also unmeasured live.

Enable `MINICONSTRUCT_SEND_TIMING=1` for a later user-authorized live observation.
The normal Uvicorn info log emits one bounded `Send timing` record per Send, including
failures. It excludes all content, credentials, IDs and paths. Its stage/counter fields
distinguish file verification, upload, metadata reconstruction/lock wait, `/prompt`
acceptance and local bookkeeping. Compare those with the browser's `/api/execution/send`
request/receipt timing and continuation refresh. That will distinguish external transfer,
Connection/timeout, lock contention, canonical media work and browser feedback costs.
No live observation or submission was performed as part of this fix.

The existing Project/configuration locks span only final metadata revalidation and
`/prompt` acceptance, closing the old check/dispatch race. They do not span bulk hashing
or uploads for canonical content-addressed inputs. Because these locks are shared,
slow queue responses can delay other mutations/dispatches; the existing 120-second
HTTP timeout (10-second connect timeout) still bounds that request. Legacy Pictures
without a digest retain fallback reads. These limits are explicit; no persistence or
queue-scheduling redesign was introduced.

## Regression and verification record

`tests/test_send_verification.py` uses real orchestration and Event/Barrier-controlled
concurrency, covering immediate and three repeated Prepare/Send cycles, renderer status,
new submissions, imported outputs and metadata during both byte-verification passes,
ordinary Prepare hydration, actual bytes/canonical hashes, selected prompt/Profile/
Connection/seed conflicts, effective controls during upload, one random seed allocation
per Send, simultaneous same/different-Generation Sends, held uploads with concurrent
import, final transaction ownership, remote restart/foreign-file cases, and bounded
opt-in timing. Existing Send/exact-retry tests cover outages, ambiguous responses,
accepted-but-local-recording failures and duplicate retry prevention. Native tests now
also revoke the exact predecessor during hashing. JavaScript adds held-refresh immediate
receipt and pending-outage regressions; existing tests protect duplicate clicks, reset,
navigation ownership and accepted receipts after local bookkeeping warnings.

Final focused Python run: **454 passed** in 55.65 seconds. Focused JavaScript:
**119 passed**. Full JavaScript: **644 passed** in 35.48 seconds. Final full Python:
**2,523 passed, 1 skipped** in 274.08 seconds. The skip is the existing directory-symlink
test: this environment cannot create directory symlinks. `git diff --check` passes.

No changes are staged or committed. Implementation, test and documentation files remain
available for diff review. Full-suite warnings include the existing httpx/TestClient
deprecation and an inaccessible optional `.pytest_cache` directory; neither prevents
tests from running.

## Files and final review state

Modified: `miniconstruct/execution_send.py`, `miniconstruct/project_store.py`,
`miniconstruct/workflow_preparation.py`, `web/js/workflow-preparation.js`,
`tests/test_media_hashing.py`, `tests/test_native_continuation.py`,
`tests/js/workflow-preparation.test.mjs`, `docs/execution-orchestration.md`, and
`docs/MiniConstruct-Roadmap.md`.

New: `miniconstruct/send_timing.py`, `tests/test_send_verification.py`,
`benchmarks/send_critical_path.py`, `benchmarks/send-baseline.json`,
`benchmarks/send-after.json`, and this report.

Final Git state: **0 staged, 9 modified tracked files, 6 untracked task files**.
All changes are left uncommitted. Orchestration documentation describes the corrected
boundary and its lock/performance limits; the roadmap distinguishes provider-free
reliability validation from the remaining live latency measurement.

## Staged-media reuse follow-up

### API findings and scope of the improvement

The starting working tree and index were clean on `master`. Previously every Send
created an operation-local upload map, discarded it on completion, and uploaded all
distinct active media again. Deterministic names reduced duplicate stored files but
could not reduce transfers. No live ComfyUI request or model generation was performed.

Inspected [ComfyUI server.py](https://github.com/comfyanonymous/ComfyUI/blob/master/server.py)
on 2026-10-21: `/upload/image` with `overwrite=false` compares existing content and
chooses a suffix for a different-content collision. The returned `name/subfolder/type`
is authoritative. `/view` without preview/channel transforms serves original file bytes;
missing files return 404. HEAD/existence, Last-Modified and file-response ETags are not
fresh SHA-256 attestations. Newer optional asset-manager metadata/hash lookup does not
establish immutable filename storage or a fresh byte check for every supported server.
It is not required by this implementation.

The compatible file API supplies no immutable input lease or portable fresh digest
endpoint. Consequently **the one-POST, transfer-free repeat-Send goal is not achieved**.
Safe reuse here avoids upload parsing/writing and outbound multipart media transfers,
but requires a full verification download. It may help asymmetric connections or upload
processing costs; it can also be no faster or slower. No real-world speedup is claimed.
Local/loopback URLs use the same HTTP verification: they do not prove where ComfyUI's
input directory is or whether it is the same filesystem (containers, proxies and remote
forwarding are possible). No guessed local path or new custom node is used.

### Design and trust boundary

Approach A is implemented: first Send stages, later Sends verify and reuse. Approach B
(explicit staging before Send) would move the first upload earlier but still need the
same remote revalidation. It adds an action without resolving identity/latency, so was
not added. Ordinary offline Prepare makes no external requests.

The process-local LRU holds at most 512 receipt locations, keyed by the **complete
Connection record**, SHA-256, length, deterministic extension/name and MIME type. It
contains no payloads and persists nothing. Full configuration changes miss the index.
Old entries, even after switching back, must pass a fresh original-byte download before
reuse; a receipt from one instance cannot authorize another instance's bytes. Matching
content across Projects/Generations can reuse only after independent current Project
validation. Applicable format/loader mapping, handoff identity and execution requirements
still pass the existing compiler and final fingerprint checks.

Every reuse downloads the receipt's exact input location with a unique query token,
no-cache/no-store request headers, no conditional headers and no preview conversion.
Streaming SHA-256 and exact length must match the locally verified canonical payload.
Memory is bounded to chunks; oversized responses stop after exceeding expected length.
Partial, changed, missing or non-200 responses cannot authorize reuse. Such responses
discard the hint and upload once; network errors stop before `/prompt` with an accurate
error. No failed/invalid upload is cached. Successful upload receipts retain the existing
server-write trust contract and strict path/type validation. Collision responses are
followed exactly, never overwritten or replaced with the requested deterministic name.

Fixed-size staging locks prevent simultaneous same-key initial uploads. A waiter freshly
verifies the resulting remote bytes; no remote-verification result is cached across
Sends. Each Send independently allocates its prompt UUID/seed and records its exact
RenderSubmission. `/prompt` is never retried, including ambiguous acceptance. Existing
canonical pre/post byte checks, final execution/configuration lock boundary, native
lineage, visual handoff and accepted-but-recording-failed behavior are preserved.

ComfyUI restart with retained input bytes passes revalidation. Cleanup, a different
instance behind the same URL, or changed bytes causes restaging as needed. MiniConstruct
restart or eviction forgets hints and uploads again. External writes/deletion **after**
verification/upload and before the queued loader runs remain possible; ordinary ComfyUI
does not lock these files for queued workflows. The same limitation exists after fresh
uploads. Keep staged inputs unchanged while queued/running jobs need them.

### Behavioral counts and regression evidence

For five distinct unchanged Pictures, actual Send orchestration against mocked storage:

| Per Send | Previous behavior | First Send now | Second/third Send now |
| --- | ---: | ---: | ---: |
| Full uploads | 5 | 5 | 0 |
| Full remote verification downloads | 0 | 0 | 5 |
| Prompt POSTs | 1 | 1 | 1 |
| Total ComfyUI requests | 6 | 6 | 6 |

Three Sends reduce uploads from 15 to 5, while total requests remain 18. Media payload
transfer volume remains roughly the same, with ten repeat transfers changing direction;
multipart overhead is avoided on repeats. A missing/changed remote input adds one
verification request before its replacement upload. Replacing one local Reference
misses only that content identity (after new selection/Prepare); unchanged inputs remain
eligible. An unchanged file on an unsupported `/view` server requires verification plus
upload, an explicit compatibility/performance limitation.

`tests/test_staged_media.py` covers five Pictures/three Sends, distinct acceptance and
records, independent seeds, same-filename Reference replacement, complete Connection
scope, Project/Generation sharing, Video/Audio, same-size remote corruption, short/long
responses, cleanup/restart, collision receipts, unsupported verification, concurrent
staging with Events, local corruption and transport failure/no retries. Native tests
also revoke accepted predecessor authority during remote reuse; existing compiler,
handoff, media-integrity and mounted frontend tests remain applicable. Frontend busy
protection and acceptance-only queue status are unchanged.

The offline benchmark now models stored original bytes and reports verification counts
and transferred bytes separately, with a new five-Picture case. Historical baseline and
after JSON files are retained. [Current mock results](../benchmarks/send-staged.json)
confirm five uploads followed by zero/zero, five verification downloads on each repeat,
and six total requests per Send. Each five-Picture transfer contains 15,755,856 media
bytes. The benchmark's `media_hashes` counters describe local canonical verification;
the remote downloads add SHA-256 work over `remote_verification_bytes`. Its fake server
parses multipart uploads in Python and keeps remote data in memory, so its timing
difference is not a representative ComfyUI speed estimate. Run:

```powershell
.\.venv\Scripts\python.exe benchmarks/send_critical_path.py --repeats 3 > benchmarks/send-staged.json
```

### Manual acceptance on the existing installation

1. Prepare a current H3 prompt with five distinct saved References. Prepare stays offline.
2. Explicitly Send once and retain the accepted prompt ID. Initial staging is expected.
3. Send the same prepared prompt twice more, waiting for the button to become available.
   Confirm three distinct accepted IDs and the intended References in their graphs/renders.
4. Check ComfyUI's HTTP/proxy access log or optional `MINICONSTRUCT_SEND_TIMING=1` server
   records: first Send has five uploads; each repeat has zero uploads, five verification
   requests and one prompt request. Timing logs are optional, not a delivery prerequisite.
5. Replace one Picture, save/select the current prompt and Prepare again. Send explicitly:
   expect one new upload and four verified reuses. Old prepared authority must not execute.
6. After jobs using the files finish, restart ComfyUI with inputs retained and repeat:
   expect verified reuse. Remove one known staged input only when no job needs it; repeat
   and expect that input to be uploaded again before queue acceptance.
7. If desired, replace a staged file's bytes with different bytes while idle; the next
   explicit Send must restage and use the returned collision location. Restore/clean up
   only files deliberately changed for this test.

Real latency and rendered-reference validation remain pending. Do not interpret the
mock upload counts as evidence of a live speed improvement.

### Changed files in this follow-up

- `miniconstruct/execution_send.py`: bounded receipt hints, remote SHA verification,
  staging synchronization, reuse counters and Send integration.
- `tests/test_staged_media.py`: new orchestration regressions.
- `tests/test_native_continuation.py`: authority revocation during reuse and updated
  pre-queue request assertions.
- `tests/test_execution_send.py`, `tests/test_send_verification.py`: explicit unsupported
  remote-verification fallback in legacy transport fixtures.
- `benchmarks/send_critical_path.py`, `benchmarks/send-staged.json`: stored-byte mock
  transport, five-Picture case and current request-count evidence.
- `docs/execution-orchestration.md`, `docs/MiniConstruct-Roadmap.md`, and this report:
  trust boundary, limitations, validation status and manual acceptance.

No frontend, Project/Reference schema, native architecture or offline Prepare changes.

### Final follow-up verification and Git state

Focused Python: **450 passed** (62.32 seconds), covering staged media, Send, verification,
native continuation, visual handoff, media hashing, preparation and exact retry. Focused
JavaScript: **119 passed**. Final full Python: **2,545 passed, 1 skipped** (291.50 seconds).
Full JavaScript: **644 passed** (37.09 seconds). `git diff --check` passes. The initial full
run exposed four legacy assertions that only allowed upload requests before a rejected
Send; they now allow verification GETs too while still forbidding queueing, and the full
rerun passes. There are **22 added regression cases**.

The existing directory-symlink capability skip and httpx/TestClient deprecation/cache
directory warnings remain. No live ComfyUI Sends, model generation, subagents or commits
were used. Final Git state: **8 modified tracked files, 2 untracked task files, 0 staged**,
all left uncommitted on `master`. Temporary test logs were removed; the benchmark JSON
and this report retain the useful verification evidence.

## Local-directory verified staging

This follow-up starts from committed staged-media/Execution Settings checkpoint `823d96d`,
with a clean working tree and index. Local verified staging is feasible when the
backend can directly read the selected ComfyUI Connection's actual input root.
No live Sends, provider requests, ComfyUI renders, commits or subagents were used.

### Configuration, mapping and integrity

Connections gain additive nullable `localInputDirectory`, edited in the existing
Connections configuration rather than Execution Settings. The read-only local
status endpoint validates directory access without contacting ComfyUI. Blank means
existing HTTP behavior. Invalid/inaccessible mappings warn in configuration/Send;
relative paths fail validation. An accessible root still needs a successful exact
staged-byte verification before local reuse.

The existing upload API stages the first Send. Only a returned `type=input` is valid.
For returned `subfolder=actual/nested`, `name=collision-adjusted.png`, the loader
gets `actual/nested/collision-adjusted.png`; local verification reads
`<configured-input-root>/actual/nested/collision-adjusted.png`. The returned location
always wins over the requested `miniconstruct/mc-<sha>.<ext>` name. MiniConstruct
does not directly write to the configured root.
The mapping was checked against [ComfyUI's upstream server implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py).

Safe components reject traversal, absolute/drive/UNC injection, Windows device
names, alternate streams, trailing dots/spaces and unsafe characters. Every child
link/reparse point is rejected; Windows final opened-handle containment uses platform
case handling, before any content read. Linux uses the opened `/proc/self/fd` path;
unsupported/unexpected mappings use HTTP. Regular staged files are streamed and
hashed with exact length, SHA-256 and before/after metadata checks. Timestamps never
substitute for a byte hash. The receipt remains a bounded, Connection-settings-scoped
location hint, including whether its upload established the local mapping.

Missing or changed once-verified files evict the hint and restage through ComfyUI.
An upload that cannot establish the local mapping warns and is HTTP-verified; future
reuse of that receipt uses verified HTTP until exact local verification establishes
the mapping (including recovery after unavailable storage returns). Changed canonical References/settings use
new keys. Backend restart/index eviction uploads again. ComfyUI cleanup restages;
retained valid bytes remain reusable across renderer restart. Two Generations can
share staged bytes while retaining independent canonical validation and submissions.

Canonical before/after payload checks, Profile/selected prompt/provenance, controls,
seed allocation, execution fingerprint, native lineage and final locked authority
revalidation remain intact. Queue acceptance still requires the actual `/prompt`
response. Ambiguous acceptance never retries automatically. External mutation after
verification cannot be ruled out by ordinary ComfyUI APIs. The configured mapping
is explicit user authority: matching bytes in an unrelated copy do not prove it is
the server's actual input root. See [orchestration and manual acceptance](execution-orchestration.md#local-directory-verified-staging-2026-10-21).

### Repeatable provider-free comparison

```powershell
.\.venv\Scripts\python.exe benchmarks/send_critical_path.py --local-comparison --repeats 3 > benchmarks/send-local-staged.json
```

Both modes run the current real API/ProjectStore/compiler/Send against MockTransport,
with five distinct deterministic 1024×1024 processed PNG Picture inputs totaling
**15,755,856 bytes**. The local mock writes upload bytes into a temporary input root;
production code only reads that root. No injected delays or absolute timing assertions
are used in this comparison. Run without competing suites for comparable timings.
The older default benchmark's explicitly slow historical scenario is not selected.

The [raw JSON](../benchmarks/send-local-staged.json) records every run's queue
acknowledgment, local verification bytes/time, multipart request bytes, remote media
bytes, total HTTP request/response body bytes, upload/download counts and prompt
requests. Body-byte counts exclude HTTP headers/framing; media counters distinguish
media from the remaining small graph/acceptance messages. Missing transfer counters
mean zero. Canonical media verification remains two full passes, **31,511,712 bytes**
per Send, separately from staged-file verification.

| Mode | First Send uploads / downloads / prompts | Second Send | Third Send | Repeat media HTTP bytes |
| --- | --- | --- | --- | ---: |
| HTTP-verified | 5 / 0 / 1 | 0 / 5 / 1 | 0 / 5 / 1 | 15,755,856 |
| Local-verified | 5 / 0 / 1 | 0 / 0 / 1 | 0 / 0 / 1 | 0 |

Each unchanged repeat checks all five staged files locally: **15,755,856 bytes**.
Local hashing has a measurable cost. Controlled queue latency is an in-process/mock
measurement, not a live ComfyUI speed claim. The primary acceptance is demonstrated
zero repeat media HTTP transfers. Live end-to-end speed and rendered-reference
acceptance remain for the user's manual test.

| Mode | First queue ack (ms) | Second (ms) | Third (ms) | Second / third local staged verification (ms) |
| --- | ---: | ---: | ---: | --- |
| HTTP-verified | 438.953 | 102.277 | 103.772 | 0 / 0 (HTTP verification instead) |
| Local-verified | 417.800 | 103.297 | 113.907 | 9.310 / 9.664 |

The local first-upload mapping checks took 30.927 ms. On each repeat, HTTP mode
made six ComfyUI requests versus one in local mode. Request/response body bytes
were **1,817 / 15,755,936** for HTTP repeats, versus **1,817 / 80** locally.
Both first Sends uploaded 15,755,856 media bytes (15,758,461 multipart bytes).
The local repeat median queue acknowledgment was 108.602 ms versus 103.025 ms
for in-memory HTTP verification. This fixture does **not** demonstrate a latency
improvement; it demonstrates removal of all five media transfers. Real network,
server and storage behavior must be measured separately.

### Regression coverage and changed files

`test_staged_media.py` exercises first/second/third five-Picture Sends, local hash
byte counts, single replacement among five, missing and same-size/longer/shorter
corruption, collision names, malformed server paths, directory/file/permission/wrong
mapping fallback, Connection UUID/URL/name/root changes, shared Generation/Project
content, Video/Audio, concurrent Sends, independent seed/submission provenance,
authority mutation and ambiguous queue acceptance. Remote-only cases remain active.
Windows junction and opened-handle escape tests run; ordinary symlink tests skip
where creation privileges are unavailable. `test_visual_handoff.py` covers generated
endpoint/tail local reuse and source-selection invalidation. Existing full regressions
retain offline Prepare, native lineage, renderer recovery and no-retry semantics.

Configuration API regressions check persistence/defaults, local status and invalid
paths without HTTP. Mounted JavaScript tests cover local root save/edit/new/delete,
fallback guidance, and simultaneous accepted-queue/provenance/staging messages.

Changed production files: `miniconstruct/local_staging.py`, `execution.py`,
`execution_send.py`, `api/execution_routes.py`; `web/js/api.js`, `execution-config.js`,
`workflow-preparation.js`. Tests: `tests/test_staged_media.py`, `test_execution.py`,
`test_visual_handoff.py`, `tests/js/execution-config.test.mjs`,
`workflow-preparation.test.mjs`. Benchmark/report: `benchmarks/send_critical_path.py`,
`send-local-staged.json`, this report, `execution-orchestration.md` and
`MiniConstruct-Roadmap.md`. Everything remains uncommitted for diff review.

### Final local-staging verification and Git state

- Focused Send/staging/authority/native/handoff Python: **314 passed, 2 skipped**
  in 61.64 seconds. Configuration Python: **74 passed** as part of the earlier
  126-passed focused configuration/staging run; final full coverage follows.
- Final full Python (`.\.venv\Scripts\python.exe -m pytest`): **2,582 passed,
  3 skipped**, 311.61 seconds. The skips are symlink capability cases; junction
  and opened-handle escape regressions execute successfully on this Windows host.
- Final full JavaScript (`node --test tests/js/*.test.mjs`): **652 passed**, zero
  failures/skips, 38.60 seconds.
- `git diff --check`: passes. Existing httpx/TestClient deprecation and pytest
  cache-access warnings remain. Git also reports normal LF/CRLF conversion notices.
- All six benchmark Sends accepted; stored-result assertions confirm local repeat
  verification of 15,755,856 bytes, no media transfers and exactly one prompt each.
- Final Git state on `master`: **15 modified tracked files, 2 new untracked files,
  zero staged files**. No commits. New files are `miniconstruct/local_staging.py`
  and `benchmarks/send-local-staged.json`. All work remains reviewable in the diff.

For manual setup/acceptance, use the intended Connection's actual accessible input
root (including any ComfyUI input-directory override), save/review local status,
then explicitly Send five unchanged saved Pictures three times. Check real queue IDs,
submissions, seeds, timing counters and reference correctness. Repeat while idle
after deliberately removing/replacing one test staging file; confirm safe restaging.
Clear the setting to confirm normal remote verification. Live speed, rendered output
and browser visual/layout acceptance remain untested here. A correct root mapping
and external file stability after verification remain operational requirements.
