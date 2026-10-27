# Execution orchestration

## Reusable Workflow Profiles and mode switching

Manage saved profiles in **Execution / ComfyUI**. A profile contains an imported
ComfyUI API graph and mappings from MiniConstruct's semantic inputs to graph
inputs. **Edit** opens configuration; it does not activate the profile. Select the
profile for the current **Prepare / Send** under **Generated H3 Prompt → ComfyUI
Execution Settings → Workflow Profile**. No separate Load action is necessary.

**Duplicate** beside a saved profile opens the same editor as an independent new
draft. It copies all typed Profile configuration: the exact API graph (including
large integers), modes, semantic/reference/aspect-ratio/seed bindings, verified
enum choices, Workflow Controls, Profile presets and native mappings. Graph node
IDs and profile-local control IDs stay intact. The suggested name is **(Copy)**,
then **(Copy 2)**, etc., truncated to the normal name limit when necessary.
Display names may repeat under existing rules; every Save creates a separate UUID
and never overwrites another profile because of its name, even during concurrent
creation. Rename freely before **Save profile**. Duplicate itself writes nothing;
**Cancel profile edits** or closing setup discards the unsaved duplicate.

For a graph variant, import replacement API JSON into the duplicate, adjust affected
bindings, Validate, then Save. Replacement retains declarations and stable targets;
missing targets are reported for correction, never inferred or silently removed.
Both saved profiles persist independently through reload, edits and deletion.
Duplication does not copy Project/Generation overrides, execution selections,
native preferences/attempts or submissions. Saving refreshes the available list;
choose the new profile explicitly when ready. Existing explicit selection survives.

Create separate Ref2VA and I2VA profiles once, then reuse them. **Compatible H3
modes** declares what that graph and its bindings genuinely support. Multiple
modes are allowed; the checkboxes neither merge workflows nor convert graphs.
An API JSON import proves graph shape, not correct custom-node wiring.

The editor guides naming, mode declaration, API import, required generated-prompt
and media mappings, optional duration/aspect-ratio/seed/Workflow Controls, then
validation and saving. For I2VA, export an actual I2VA workflow. Choose I2VA in
H3 Setup, attach a first-frame anchor Picture, and map Picture ordinal 1 to the
appropriate loader filename input and consumer route. Node titles, scalar hints
and route inspection help identify targets; users must confirm custom wiring.
Generate H3 normally, select the compatible profile, Prepare, then Send.

The selector filters by the Generation's H3 mode and selects a unique compatible
profile automatically. If none exists it explains how to create one. Explicit
selection survives list, continuity and ordinary UI refreshes for the current
Generation. Navigation resets that explicit choice and uses existing compatible
continuity preference/fallback behavior. No new persisted preference is added.
Switching invalidates preparation and refreshes controls, routing capabilities
and seed support. Existing profile/scope draft boundaries remain in force:
same-profile refreshes retain drafts; changing profile discards its unsaved draft.

**Save as native-continuation profile** separately saves the continuation
preference. It is unnecessary for ordinary rendering. Execution selection never
changes native settings, accepted attempts or lineage. Existing backend binding,
reference, preparation and Send authority checks remain unchanged.

Provider-free mounted-handler and backend tests cover selection and binding
safety. Manual browser/layout review and real custom-node I2VA/Ref2VA rendering
remain separate validation gates.

## Draft Continuation

On a saved continuation Generation, expand **Draft Continuation** beneath
**One-shot Main Prompt / Idea**. Optionally describe the next action, then press
**Draft Continuation** to request one candidate using the configured LLM profile.
No completed Story Plan is required. One-shot, Story-authored predecessors and
externally imported videos are supported. The immediate predecessor must have
an explicit Selected Take. Independent and first Generations are ineligible.

Review and edit the candidate, optionally inspect its source evidence and
authority, then choose **Apply to Creative Request**. An existing Main Prompt,
including unsaved edits, requires explicit replacement confirmation. Applying
changes only this local field and marks it unsaved. Save and **Generate from
Main Prompt** remain separate actions. **Generate from Story** continues to use
its saved Scene Plan; this feature does not write or replace that plan.

The request separates effective observed evidence (including saved corrections),
historical user intent, verified exact renderer-submitted H3 text, and new user
direction. Planned actions and H3 endings do not prove what a video depicted.
Observed state wins over contradictory plans; new direction describes a subsequent
transition rather than rewriting the old ending. Trusted predecessor Story Memory
and Character Profiles use existing projections and remain unchanged. Current
selected H3 text is never assumed to have rendered the Selected Take. Only a
matching Take/RenderSubmission snapshot is labeled exact renderer submission;
a manually associated prompt is historical authoring intent. External videos can
have no H3 provenance at all.

Without a valid observation, the model is instructed to avoid certainty about final
pose, composition, object positions and completed actions. A warning recommends
**Analyze Selected Take** for stronger evidence. Analysis remains explicit and
optional; no video or image pixels are sent by the drafting request.

The text-only request uses existing endpoint/model, reasoning, timeout, seed and
context-capacity controls; vision capability is unnecessary. A usable chat model
ID is required. Context is inspectable and bounded to 160,000 JSON characters;
oversized context fails before a provider call. The ordinary capacity preflight
also applies. The model is asked for concise Creative Request prose, not H3.
Empty, oversized, JSON or fenced responses fail; no format repair, hidden retries,
automatic acceptance or fallback model request runs. The shared provider activity
bar supplies cancellation and excludes concurrent prompt-writing operations.

The full Project fingerprint and Project/source/target Generation UUIDs are
checked at request assembly. Browser ownership then follows the existing Project
edit fingerprint, which excludes automatic renderer job and output history while
retaining saved authoring and Sequence changes. Response and adoption validation
require that edit fingerprint plus a deterministic digest of the canonical
evidence actually supplied to the writer to remain unchanged.
It includes the Selected Take and its prompt provenance, saved observation,
historical authoring, Story and Subject context, and target Generation inputs.
Renderer job-status changes and unrelated renderer history are omitted from this
digest, so those updates do not discard an otherwise current candidate. Exact
Selected Take prompt provenance remains in the digest even though Project edit
fingerprints omit renderer submission records. Navigation tickets reject late responses,
including away-and-back navigation. Edits during generation remain in the editor
while the candidate appears separately; edits to Main Prompt or the preview during
adoption validation prevent application. Duplicate Apply clicks are guarded.
Changes to evidence used for drafting require a fresh candidate. Candidates are
ephemeral and are discarded on context changes or a new drafting request.

Drafting does not save the Project, select Takes, write observations or Story
Memory, accept native state, generate/save H3, Prepare or Send. Existing Visual
Handoff and native continuation remain downstream authorities. Text-shape checks
and mocked regression tests cannot guarantee semantic compliance by a live model.
Live LLM review (especially contradictory plans/corrections, unknown endings,
explicit actions and compatible expansion), browser usability review and actual
rendered-continuity tests remain separate, pending validation gates.

## Continue from Selected Take

In **Rendered Takes**, explicitly Select a Take, then choose **Continue from
Selected Take**. Save or discard authoring changes first; save or reset any
Observed End State edits. Confirm creation immediately after the source
Generation. Inserting before existing later Generations warns about continuity
dependencies; those Generations stay in their existing relative order.

This is a single guarded Project mutation: exact Project/Generation UUIDs,
Selected Take UUID/SHA-256, active Generation and the captured Project fingerprint
must still match. Source media must exist and match its stored bytes. A stale or
repeated request is rejected, rather than continuing another Take or inserting a
duplicate. No authoring fields are implicitly saved. Late responses cannot
replace another navigation context or overwrite edits made during the request.

The activated new Generation has **Continue previous shot**, **Auto** Visual
Handoff and the canonical **1.5-second** tail default (editable within **0.5–2.0
seconds**). It starts with empty Main Prompt, dialogue, planned states, Scene
Plan, prompts, Takes, observations and execution history. Describe the next
action, then explicitly **Generate**, **Prepare**, and **Send**. Creation alone
does not establish that the Workflow Profile can execute the handoff.

Attached Subject Identity, Style / Appearance and Environment Pictures share
canonical project media. Local stable Subject IDs/numbers, aspect ratio,
duration, visual style, identity fidelity, tone/performance, prompt detail and
creative interpretation carry. Sequence Cast and names remain canonical. Frame
anchors, other reference roles, prior tails, audio and music do not automatically
carry. The Selected Take tail is supplied by Visual Handoff, never saved as an
ordinary Reference Asset.

Generic and external/unknown sources initialize **Ref2VA**. An accepted exact
native predecessor can retain a compatible T2VA/Ref2VA native-only profile mode;
ordinary native resolution still validates exact execution lineage. Saved
Workflow Profiles are not rewritten. Arbitrary imported video has no accepted
native latent state. Auto uses bounded Video-tail handoff when supported, or the
existing textual fallback/compatibility warning. Check Visual Handoff and the
active Workflow Profile: this workflow does not promise seamless overlap.

Source observations resolve through existing predecessor context, with saved
user corrections retaining precedence. Without an observation, planned state
and historical Main/H3 prompts remain intent, not observed visual truth. The
creation confirmation explains whether an observation exists; **Analyze Selected
Take** remains a separate explicit action on the source. Nothing is analyzed or
generated by continuation setup. Source authoring and provenance remain available
under the immediate predecessor UUID for inspection and future assistance.

AI-assisted drafting of the next segment is outside this implementation. Local
provider-free tests cover setup and race handling; live profile compatibility,
render quality and browser presentation still require manual validation.

## Workflow preparation

MiniConstruct can prepare a saved Generation for a reusable ComfyUI Workflow
Profile, then explicitly send it to a saved ComfyUI Connection.
**Prepare is offline. NOT SENT TO COMFYUI.** It does not need a Connection and changes nothing in ComfyUI.
Open **Execution / ComfyUI** in the toolbar to manage profiles and connections.
Use **ComfyUI preparation** below the complete core prompt-authoring workflow to
select and prepare a prompt.
Saving a profile does not contact ComfyUI.
After Send, **Rendered Takes** tracks the exact recorded job and imports a unique
video automatically. See [Phase F1](#exact-comfyui-job-tracking-and-output-import-phase-f1).

## Fresh working copy and Clear generated state

Save As defaults to **Full copy**, preserving existing fork semantics: all ordered
Generation authoring, references, Takes/selections, Render Submissions and their
job/output/import/retry bookkeeping, observations/corrections, Saved H3 Prompt
libraries and selected execution prompt maps are remapped into a new Project.
Managed Take media uses hard links or bounded copy fallback. Disposable handoff
and observation evidence is omitted and can regenerate from copied raw Takes.
Native attempts/accepted pointers are omitted under the existing new-chain rule;
Full copy's native settings retain their previous behavior.

**Fresh working copy** uses `GeneratedStateReset` before destination staging.
The same policy is reused by **Clear generated state**. It preserves canonical
Project/Sequence Workspaces and planning, Main Prompt, Story premise/Scene Plans,
creative/dialogue/H3 controls, planned incoming/outgoing state, Sequence Context,
Story Memory snapshots, cast/profiles/narrative names/aliases and Subject numbering
watermarks, Reference Asset IDs and semantic configuration. Canonical Picture,
saved Video and saved Audio files transfer through the existing safe storage
helpers. The destination does not depend on source paths. Visual Handoff strategy,
tail duration and immediate-cut intent remain authored; without predecessor Takes,
derived handoff may be unavailable until new renders exist.

The manifest projection clears every Generation's `takes`, `selectedTakeId`,
`takeObservations` and `renderSubmissions` (including output candidates, auto-import
receipts and terminal reconciliation/retry/watch history). The sidecar projection
clears `selections` (including exact selected-prompt reference maps),
`continuationExecutions`, `acceptedContinuations`, and each native setting's
execution-specific `sourceOutFrame`. It preserves `routing`,
`rendererSeedSettings`, `workflowControlDefaults`, `workflowControlOverrides`,
and native `profileId`/`contextFrames`/`audioEnabled` preferences. Workflow Profiles
and Connections remain machine-local configuration and are not deleted.
Preparation graphs/upload receipts are transient rather than additional persisted
Project sidecars. Observed truth is never promoted into planned authoring.

Fresh is a filtered transfer, **never Full copy followed by deletion**. Only the
projected authoring references enter staging; the Take transfer helper is not
called. No historical managed Take, observation evidence, handoff cache, import
scratch file or native attempt record is copied/linked/streamed. With **Keep Saved
H3 Prompts** (default off), text libraries, active saved pointers and numbering
use the existing Generation-ID remapping. Selection/preparation remain empty.
Without it libraries are absent, active pointers null and `nextSaveNumber` is 1.

Clear requires an explicit confirmation distinguishing MiniConstruct-managed
Project media from external ComfyUI output files. **Also clear Saved H3 Prompts**
defaults off; opt-in applies the same empty-library projection. Clear preserves
Project/Generation IDs and active Generation. The loaded manifest fingerprint is
checked under the shared transaction lock, and active Take imports block cleanup.
Both metadata files commit before any generated-media reclamation; a write failure
restores the previous metadata. Existing Take GC handles media/evidence/handoff
reachability, retaining blobs referenced by surviving canonical records. A locked
file may remain as a harmless orphan after commit, without resurrecting metadata.
This is not an arbitrary sweep of pre-existing orphan/reserved files.

An in-process reset epoch prevents a Send already awaiting ComfyUI from republishing
old Project provenance after Clear. If ComfyUI accepted that job, the existing
truthful acceptance/provenance-warning response remains; MiniConstruct does not
cancel or delete renderer-owned data. Reconciliation and observation writes also
require their still-owned canonical Submission/Take.

Browser adoption clears generated candidates/validation, preparation receipts,
native/Take/observation inspection, live watcher/socket and preview URLs, then loads
the new fingerprint and retained settings. Saved libraries reload without automatic
historical text restoration on reset. Late responses from before reset are ignored.
The user explicitly Generate → Use for execution → Prepare → Send again.

API additions (no persisted schema bump):

- `POST /api/projects/{projectId}/save-as`: existing body plus
  `copyMode: "full" | "fresh"` (default `"full"`) and
  `keepSavedPrompts: boolean` (default false, relevant only to Fresh).
- `POST /api/projects/{projectId}/clear-generated-state`:
  `expectedProjectFingerprint` is required; `clearSavedPrompts` defaults false.
  Returns the normal filesystem Project response. Stale fingerprints return 409
  without changing metadata or deleting files.

Neither operation opens/deletes/modifies external ComfyUI output descriptors or
native/custom-node artifacts. Physical cleanup only targets known managed paths
under the Project root. Delete Project remains a separate action. Versions remain
Project v17, Workspace v1, execution configuration v7, execution sidecar v4.

## Reference Asset authority

The browser previously generated H3 from its current Workspace while Prepare/Send
resolved saved filesystem assets. Remove only filtered the browser array; Add was
also local until Project Save. Duration had its own bounded synchronization
before Prepare, so it did not suffer that authority gap.

`PUT /api/projects/{projectId}/generations/{generationId}/reference-assets` now saves
the complete Reference Asset collection under a Project fingerprint check and the
Project transaction lock. It copies only assets and their required structural Subject
records into the saved Generation, calculating the allocation watermark from valid
records. The canonical Sequence cast and Character Profiles survive Picture removal;
unrelated authoring, planning, dialogue, profiles and creative-request edits are never
adopted by this endpoint. The UI commits Add, Remove, Replace and semantic control
changes immediately. Notes synchronize on field completion; Generate, Use, Adopt,
and Prepare verify the collection again before binding authority. Filesystem
writer assembly rejects browser snapshots whose references differ from saved authority;
an already-open Revision cannot submit its stale reference snapshot.

The browser remembers the complete verified collection (including canonical Picture
SHA-256, semantic metadata and Subjects) for the current navigation and saved edit
authority. An identical collection skips the Reference PUT; filename equality is
insufficient. Unknown authority, edits and navigation replacements retain ordinary
synchronization. Queued sync requests cannot migrate to a different navigation.

Reference and bounded H3 Setup synchronization accept optional
`expectedProjectEditFingerprint` alongside the unchanged full
`expectedProjectFingerprint`. The computed edit digest excludes only renderer history,
submission-associated Takes and Project/Generation update timestamps. It retains
references, all authoring, cast, Sequence order/active Generation, other Takes,
Selected Take and observed continuity. Under the same transaction lock, a stale full
fingerprint is accepted only when that edit digest still matches. A real edit still
returns 409; clients without the additional guard retain strict full-fingerprint checks.
There is no conflict retry, grace period, or overwrite of a stale authoring snapshot.
Scoped writes merge only their existing Reference or H3 Setup fields into current state.

Project loads expose `projectEditFingerprint`; scoped mutations, output reconciliation,
foreground refresh and successful Send return it with the full fingerprint and
`projectUpdatedAt`. Existing manifest `updatedAt` advances strictly at each commit,
including commits in the same clock tick. This orders receipts, not conflict tolerance.
The browser rejects older receipts and superseded navigation tickets, and renderer
receipts cannot acknowledge an unseen edit-authority change. No persisted fields or
schema versions are added. Prepare supersedes outstanding selection reads, so a
delayed pre-action read cannot invalidate its newer execution selection/preparation.

Send consumes the existing preparation without Reference or H3 Setup synchronization.
Its only submission request carries Project/Generation, Profile and Connection IDs
and the prepared execution fingerprint. The backend rebuilds saved execution, compares
that fingerprint, verifies canonical payload bytes, and rechecks execution after uploads
before the single queue request. It does not adopt browser authoring or alter the selected
H3 prompt. Random renderer seed allocation and native attempt keys retain their existing
Send-time semantics and exact RenderSubmission provenance.

### Payload verification and queue boundary (2026-10-21)

`execution_preparation_snapshot` previously captured the **entire manifest** digest,
released the Project lock for hydration, then required the manifest digest to remain
identical. Renderer job reconciliation, submission recording, Take import and Project
rename all change that digest without changing an execution. This caused
`Project changed during payload verification; Prepare again.` inside either of Send's
two compiler calls. Foreground reads alone and native sidecar bookkeeping alone did
not change that manifest digest.

Offline Prepare still verifies canonical media outside the lock. Its return boundary
now compares the selected authoring-path fingerprint, exact selected H3 prompt and
provenance, and canonical Reference file mappings. It reloads the same Project and
Generation under the lock; renderer history, unrelated imports and names are outside
this execution authority. Other authoring-path fingerprints do not invalidate the
selected path. Actual authoring, selection or Reference changes reject the snapshot.

Send uses the same compiler with canonical metadata descriptors instead of hydrating
Pictures into base64 or hashing A/V in each compiler pass. These descriptors assert
identity, **not readiness**. The transport opens and hashes every active canonical file
before the first upload, retains verified handles during upload, then reopens current
canonical paths and hashes them again afterwards. Same-size corruption, replacement,
missing files and changed canonical digests cannot use an earlier Prepare as proof of
current bytes. Shared files are verified once per pass, while each semantic slot must
resolve independently. No digest/stat cache survives the operation. Legacy Pictures
without a stored/content-addressed digest still require their existing fallback reads.

After media verification, Send acquires the existing Project transaction and execution
configuration locks in Project → configuration order. It reloads the exact Profile and
Connection and recompiles metadata, checking execution fingerprint and exact selected
prompt/provenance before the single `/prompt` request. The fingerprint retains controls,
seed configuration, Sequence and exact accepted native lineage. An operation-local
handoff projection is reused only after resolving its canonical source identity again;
bulk extraction and verification occur outside this final transaction. New random seeds
are allocated once after validation. Fresh proposed native attempt IDs/save namespaces
remain excluded from comparison; the submitted graph and recorded native attempt keep
the original Send's exact identity. Conflicts explain the changed authority and do not
retry Send. No Project/Reference/Profile/native schema changes are involved.

The final locks extend through `/prompt` acceptance to close the former validation/queue
race. They never span bulk uploads or content-addressed Reference file hashing. Since
the existing lock is shared across Projects, a slow `/prompt` response can block other
canonical mutations and queue dispatches until its existing timeout. This is a deliberate
correctness limit, not a throughput optimization or new scheduling system. Submission
and native recording retain their canonical transactions, generated-state reset epoch,
and accepted-but-bookkeeping-failed receipt. An ambiguous response is never retried.

Uploads remain content-addressed and deduplicated within one Send. A bounded in-memory
receipt index (512 locations) now supports cross-Send reuse, scoped to the complete
Connection record, canonical SHA-256, byte length, deterministic filename and MIME type.
Each reuse streams the original bytes from `/view?type=input` at the validated returned
name/subfolder and checks the complete length and SHA-256. No preview/channel conversion,
conditional GET, filename-only, HEAD or ETag authority is used. Unique verification query
tokens and cache-control headers prevent ordinary stale HTTP-cache reuse. Identical
content may share staging across Projects/Generations only after each Send independently
validates its canonical authority. Fixed-size staging locks serialize matching keys;
each intentional Send still queues its own prompt and records its own provenance.

First Send uploads; later Sends verify and reuse. Missing, changed or unsupported remote
responses discard the hint and restage without overwriting foreign bytes. Transport
errors during verification stop before queueing. MiniConstruct restart/eviction forgets
hints and uploads again; ComfyUI restart with retained bytes can reuse after verification.
Ordinary Prepare stays offline. Existing final local authority checks remain unchanged.

This is an upload-avoidance improvement, not a transfer-free fast path: standard ComfyUI
requires full downloads to establish current remote identity. Five unchanged Pictures
still require five verification requests plus `/prompt`. There is no fresh digest or
immutable input lease in the broadly compatible file API, and a local Connection URL
does not prove a shared input directory. External writes/deletion after verification or
upload and before rendering remain possible, as with ordinary ComfyUI submissions.
See [API investigation and manual acceptance](send-verification-performance.md#staged-media-reuse-follow-up).

The browser displays `Submitting to ComfyUI…` while the request is pending and renders
`Queued in ComfyUI` only after the server returns an acknowledged prompt ID. It now
renders that receipt **before** awaiting continuation/history refresh. Busy protection,
navigation ownership and generated-state reset ownership remain in force during the
auxiliary refresh; no fake percentages or waits are added.

Set `MINICONSTRUCT_SEND_TIMING=1` on the server to emit one bounded `Send timing` JSON
record through its normal Uvicorn info logger, including failure paths. Default is off.
It records configuration load, reconstruction, before/after byte verification, upload,
metadata revalidation, graph preflight/finalization, queue acceptance and persistence;
`authorityAndQueue` also includes lock wait and nests revalidation/queue durations.
Counters include hash passes/bytes, uploads/bytes, ComfyUI requests and acknowledgments.
Staging also reports `verifyRemote`, `remoteVerificationRequests`,
`remoteVerificationBytes`, and `stagedReuses`; remote SHA work is additional to local
`mediaHashPasses`. The existing `upload` stage encompasses staging decisions as well.
Records contain no prompts, credentials, IDs, payloads or filesystem paths. HTTP parsing,
browser networking and browser render time are outside this server function's timer.

See [provider-free timings and reproduction](send-verification-performance.md) and
`benchmarks/send_critical_path.py` for measured stage costs and read/hash/upload counts.
Synthetic local timings demonstrate reduced media work, not the cause of every reported
5+ second live Send. Actual Connection/upload/queue latency and browser receipt timing
still need a user-authorized live measurement; this change makes no live submissions.

General editor dirtiness is not execution identity. Unsaved Main Prompt, Story and notes
remain local and do not clear preparation or its acceptance receipt. Mode, duration,
aspect ratio, Profile, selection, saved References, controls, renderer seed settings and
native lineage retain their invalidation/fingerprint checks. Unrelated Project revisions
whose execution fingerprint remains identical do not block Send. A saved authoring edit
that changes that fingerprint still requires Prepare; the fingerprint is not relaxed.
Reference edits still require explicit prompt reselection, and conflicting Reference or
H3 Setup writes still reject stale Project edit authority without retries.

The remaining spurious conflict had two client causes: Send re-entered the Reference
PUT path when the verification cache's edit revision differed. Prepare checks References
before writing bounded H3 Setup, so that setup write can already leave the cache behind
the current edit receipt. An unseen unrelated Project edit then makes the redundant PUT
fail with `Project changed since loading; reload before changing Reference Assets`.
A clean-to-dirty editor transition also cleared preparation and launched selection/routing
reads. Revision-ordered receipts and renderer
history exclusions alone could not remove those dependencies. Send now has neither
dependency; outstanding Reference writes remain guarded, and changed saved execution
is rejected by the existing backend before queue submission.

Unchanged image slots can send a digest instead of repeated pixels. The server
accepts that digest only for the exact current Generation/asset pair. It cannot resolve
a removed slot from an orphan blob or historical record. Replacement retains asset ID,
role/order, Notes/options, Subject, focus/view/layout and comparison bindings.
Add and Replace share the browser processing path: decode, scale the longest side
to at most 1600 pixels, and encode a JPEG at quality 0.88. These processed canonical
bytes replace physical metadata and the content-addressed mapping. Historical saved
payloads are not rewritten. Upload names remain `mc-{sha256}.{extension}`; equal filenames with different bytes produce different
identities. Writer vision separately derives JPEGs bounded to 768 pixels at quality 84 from
the canonical bytes.

Every reference mutation clears the active execution selection and prunes removed
routing entries. Normal Project Save has the same reference invalidation. Browser
prepared/send state is invalidated, and Send's existing execution fingerprint check
rejects older preparations. Old generated Picture maps that resolve differently are
rejected at selection and preparation; current canonical numbering is regenerated.
An explicit new selection is required after a reference edit. Visible H3 text and
saved prompt/render/Take provenance remain historical; saved fingerprints report stale
instead of rewriting prompts. Unrelated authoring staleness retains its existing rules.

Story recommendations/candidates resolve current sourceGenerationId + assetId pairs.
Reference changes invalidate relevant server Story snapshot fingerprints and pending
browser drafts; stale draft prose remains inspectable but cannot Apply/Revise. Current
ordinary-plan recommendations are discarded after a reference edit. In-flight authoring
is cancelled before it can publish obsolete reference results. Subject identity survives.

Story materialization namespaces copied IDs by target Generation and source pair;
Environment carry-forward also creates target-owned IDs. Duplicate Generation may
retain local asset IDs, so the authority scope is always Generation + asset, never a
project-global filename or digest. Other Generations' independent copies remain intact.
The storage layer already retains harmless immutable orphans and has no safe orphan
collector: this change deliberately retains blobs, including shared ones. Correctness
depends on canonical mappings rather than physical deletion.

No persisted version changes: Project v17, Workspace v1, execution configuration v7,
execution sidecar v4. First Save, Save As, duplicate, Story creation and portable
Workspace export/import retain their existing boundaries.

## Native H3 continuation capability

The continuation hierarchy distinguishes four kinds of authority:

| Mechanism | Purpose and authority |
| --- | --- |
| Native H3 AV continuation | Strongest supported same-shot handoff, using the immediate predecessor's accepted execution state. The workflow carries synchronized native context, samples a fresh window with protected/guided overlap, generates a suffix, and removes hidden repeated context before appending where supported. |
| Tail continuation reference | A bounded Ref2VA fallback from the Selected Take's ending state; ordinary MP4 conditioning does **not** protect an exact overlap. |
| Endpoint Picture | An opening/state anchor, especially useful for a state-preserving new shot. |
| Effective Observed / Planned continuity | Universal textual/structured fallback. |

Native state is an execution capability, not an H3 prompt relationship, media asset,
Picture, Story Memory, Observed End State or Planned Continuity field. MiniConstruct
does not load tensors or assemble long videos. The workflow owns AV latent storage,
context extraction, sampling, hidden-head removal and any append operation.

### Profile contract and adapter

Workflow configuration **v4** adds optional `nativeContinuation` metadata to the
existing Profile's imported API graph, compatible modes and scalar semantic mappings.
The initial adapter is `opaque_keys`, kind `native_h3_av`, capability version `1`.
It does not inspect custom-node class names, import a node package, use a package's
directories or discover files. Profile authors map the semantics to their own graph.

The collapsed **Native H3 continuation** section in the Profile editor accepts this
capability JSON. A complete fictional, provider-free profile is available at
[`tests/fixtures/native-continuation-profile.json`](../tests/fixtures/native-continuation-profile.json).
A second provider-free fixture,
[`tests/fixtures/h3-pure-latent-continuation-profile.json`](../tests/fixtures/h3-pure-latent-continuation-profile.json),
models the topology of a pure H3 AV-latent workflow: conditioning, exact native
loader directly connected to a continuation/context adapter, sampler, native saver, overlap trim,
decode and output. It is a structural integration example, not a vendored workflow
or a MiniConstruct dependency. Its 22-frame setting is profile-owned; core has no
universal H3 context default.

| Semantic input | Meaning |
| --- | --- |
| `enable` | Consume predecessor state; false for G1, None, independent and fallback executions. |
| `storage_namespace` | Safe generated relative namespace for one Project / Generation / execution attempt; never browser input or a canonical Project path. |
| `save_key`, `load_key` | Deterministic current save prefix and exact predecessor load namespace/key; an empty load key when consumption is disabled. |
| `save_enabled` | Always true when mapped; a profile without this input promises that supplying `save_key` always saves state. |
| `chain_id` | Stable Project-scoped UUID identity. |
| `generation_id`, `predecessor_generation_id` | Stable target/source Generation UUIDs, with empty source when not consuming. |
| `segment_index` | Legacy single one-based slot for profiles that use one number in both directions; never sole artifact identity. |
| `save_segment_index`, `load_segment_index` | Directional target/source slots for workflows whose Save and Load controls are separate. G1's source slot is unset (normally mapped to `0`), never an authority fallback. |
| `context_frames` | Profile-aligned requested native context frames. Omit mapping only for fixed workflow context matching profile metadata. |
| `extension_frames`, `extension_seconds` | New suffix request derived from Generation duration. A frames mapping requires `framesPerSecond` and `extensionGrid`. |
| `audio_enabled` | Synchronized audio continuation when consuming and enabled; mandatory mapping for declared audio support. |
| `source_out_frame` | Optional, recorded effective source out-point; blank uses the mapping's `unsetValue` (default `-1`). Never alters a Take. |

Some native loaders execute whenever their output remains graph-connected, even if
the receiving continuation widget is configured as disabled. Such a Profile declares
`sourceLinks`: exact `{nodeId, inputName, expectedSourceNodeId, expectedOutputIndex}`
edges that represent predecessor-native consumption. MiniConstruct validates each
edge against the imported API graph. For G1 and every other non-consuming execution,
it removes only those declared target inputs from the prepared graph; it does not
delete or recursively prune nodes. Therefore G1 is **save-only**: its native source
graph dependency is absent, while the native Save mapping remains enabled with the
current attempt namespace and current one-based save slot. An empty load key or an
unset loader slot is never permission to execute a loader or infer a newest artifact.
For a consuming execution, the declared links remain and the loader receives the
exact accepted predecessor namespace and positive recorded source slot.

Each mapping supplies `nodeId` and `inputName`. Boolean controls may supply
`enabledValue` / `disabledValue` for string or numeric switch widgets. The adapter
requires `enable`, `save_key` and `load_key`; `requiredInputs` declares any additional
profile requirements. Validation rejects absent nodes/inputs, links/structured
targets, collisions with ordinary bindings/reference routes, and missing required
semantics. All mapped inputs must resolve to existing scalar widgets.

`compatibilityKey` names the workflow author's model/mode/state contract. Source
resolution requires the immediate predecessor's explicitly accepted, available
execution state, a recorded RenderSubmission, and a matching H3/native mode. Profile
and graph fingerprints remain diagnostic metadata: if they drift, MiniConstruct warns
and keeps the accepted state eligible. The current Profile supplies all current Load,
Save, enable/consume, context and audio mappings; the accepted execution supplies its
artifact namespace and saved slot. A current Profile with no mechanically usable Load
mapping can still fail preparation. Send additionally requires the source artifact's
Connection.

`contextGrid` defines `minimum`, `maximum`, `step` and `alignment` (`reject`, `ceil`,
or `floor`). Valid values follow `minimum + n × step`; out-of-range or overflowing
snaps fail. The inspector records requested and effective frames. `extensionGrid`
uses the same contract; optional `sourceOutGrid` can align an explicit out-point.
There are no universal package-specific grids in core. `videoContinuity` is true;
`audioContinuity` and the default `audioEnabled` are independent. Unsupported audio
cannot be enabled. Disabled/unsupported audio is never described as phase-continuous.

### Authority, persistence and confirmation

Project `execution-state.json` **v4** owns `continuationSettings` per Generation,
historical `continuationExecutions`, and default `acceptedContinuations` IDs.
Records contain Project/Generation IDs, Profile/graph/compatibility fingerprints,
mode, RenderSubmission and Connection IDs, source artifact/Generation identity,
context/audio/out-point decisions, creation time, execution/authoring fingerprints
and `pending` / `available` / `unavailable` renderer status. Each attempt also stores
an explicit user `confirmed` flag, independently of the default accepted pointer.
They contain no tensors, absolute paths, or user-supplied paths; a generated relative renderer namespace is
an opaque execution address.

Keys are server-generated `mc-<Project UUID>-<Generation UUID>-<attempt UUID>`;
chain identity is `mc-<Project UUID>`. Each Prepare/Send creates a fresh proposed
attempt key. No artifact is recorded by Prepare. Send recompiles the graph and uses
a fresh attempt key, preventing rerolls from overwriting their own source. The
semantic execution fingerprint excludes that disposable attempt allocation.

H3 prompt-writing vision uses a disposable, bounded JPEG derivative of each canonical Picture. The derivative appears only in the OpenAI-compatible model request; workflow preparation and Send still hash, resolve and upload the saved canonical Picture bytes. This does not change Picture identity, authoring provenance, or reference routing. The cache-input fingerprint follows the actual model-facing derivative.

ComfyUI `/prompt` acceptance proves only that a job was queued. After that receipt,
MiniConstruct records the RenderSubmission, then a **pending** continuation record.
If provenance persistence fails, the accepted receipt carries a warning and must
not cause automatic resend. A rejected Send creates no successful continuation
authority. Phase F1 reconciles that exact job and its video outputs. Queued,
running and unknown jobs leave the associated native execution pending. Exact
successful completion makes only its matching pending native execution available;
failure or cancellation makes it unavailable. This lifecycle eligibility does not
independently verify that an external Save artifact exists. Explicit acceptance
remains the only way to grant native continuation authority. User-marked
unavailable attempts stay unavailable after later completion. No chronological
promotion or automatic acceptance occurs.

After the concrete workflow has saved its state, use **Accept native state** on
the available attempt. This is a user attestation, not an automatic filesystem
verification. Acceptance confirms that exact attempt and makes it the default;
accepting a later attempt does not revoke earlier confirmations. **Revoke acceptance**
removes that attempt's confirmation and clears the default pointer if it points to
that attempt, while retaining its available renderer status. It does not select a
replacement default. Marking an attempt unavailable likewise revokes its trust.
Confirmed attempts must be revoked before removal, including non-default attempts.
Deterministic keys identify an artifact but cannot prove its presence. API clients use:

- `GET /api/execution/projects/{projectId}/generations/{generationId}/continuation`
- `PUT .../continuation/settings` with `profileId` and optional `contextFrames`, `audioEnabled`, `sourceOutFrame`
- `PUT .../continuation/{recordId}` with `status: "available" | "unavailable"`, and `accept` (default true)

These APIs accept no user-supplied artifact path/key or client-created execution
record. Confirmation requires a server-recorded submission in that Generation.

The `confirmed` flag is an additive v4 sidecar field; Project v17, Workspace v1,
execution configuration v7 and sidecar v4 remain unchanged. Reading older sidecars
adapts copies without rewriting files. Only an available attempt identified by the
persisted accepted pointer gains legacy confirmation. Earlier confirmations erased
by the old single-pointer model cannot be recovered; those attempts need explicit
acceptance again. Availability alone never grants confirmation. An explicitly false
flag is never overridden by a pointer. The next canonical mutation persists the flags.

The downstream invariant is **current canonical immediate predecessor → exactly
one compatible confirmed artifact from its accepted execution → current Generation**:

- A Selected Take with known RenderSubmission provenance requires the artifact
  from that same submission **and that exact attempt's explicit confirmation**.
  It overrides a different default accepted pointer only for lineage selection.
  A previously confirmed matching attempt remains eligible after another attempt
  becomes the default. An unconfirmed available, pending, unavailable or missing
  matching attempt cannot consume native state or substitute another accepted
  execution. Take import/selection never confirms native state.
- An external/unknown Selected Take (including selected-prompt-only provenance)
  has no proven native association and uses existing fallback behavior.
- Without a Selected Take, the explicitly accepted execution ID is authoritative.
  Pending or merely available rerolls and creation timestamps never displace it.
- Reorder resolves UUID order again; neither display numbers, newest files,
  newest outputs nor newest global submissions identify a predecessor.
- Save As clears native artifacts/accepted pointers because the new Project owns
  a different chain. It preserves profile preferences and existing Take behavior.
  Generation deletion removes its sidecar records. No renderer files are deleted.

### Preparation, authoring and UI

Apply the active Workflow Profile in ComfyUI preparation **before generating the
prompt**. This saved execution preference lets authoring, the Input Map, routing,
Prepare and Send use the same handoff projection. Auto + `continue_previous_shot`
prefers native state only with an explicitly capable profile and compatible confirmed
source. Otherwise it tries the existing Ref2VA tail if the mode/profile has capacity,
then warns and retains Observed/Planned text. Other Auto relationships retain their
endpoint behavior. None consumes nothing. Independent never consumes native state.

The compact Visual Handoff selector offers explicit Native only with profile
support; a previously saved unavailable choice remains visible with guidance.
Explicit Native can opt a non-independent new-shot relationship into native
execution, but missing state/support blocks Prepare/Send. Auto may fall back.
G1 and non-consuming executions still export their own state for successors.

Native-only preparation injects no synthetic Picture/Video asset and uploads no
generic handoff tail. Identity Pictures remain independent. A manually supplied
first-frame anchor conflicts unless `allowEndpointAnchor` is declared. A supplied
Video continuation source conflicts unless `requiresTailReference` is declared.
Profiles declaring the latter must have a real compatible Selected Take and a
usable mapped tail; preparation blocks if that required media is unavailable.

Native mappings do not waive I2VA's first-frame Picture requirement. A Required
Picture may also supply renderer dimensions and does not need a removable branch;
an Optional Picture must still satisfy every safe-deactivation check. See the
[I2VA graph inspection and manual validation guide](i2va-native-continuation.md)
for the scaled-Picture dependency, supported operations and current limits on
Picture-free I2VA successors.

The inspector shows the source Generation number/UUID, Profile, requested/effective
context, audio support/enabled state, save-only versus consume/save and recorded
out-point. Opaque keys and namespaces are generated relative renderer addresses,
not browser-supplied filesystem paths. No motion detector, automatic
truncation or destructive trimming is performed.

The writer still receives same-shot motion/state intent from the existing H3
relationship. It never receives fake latent media labels. Native-only context uses
normal shot wording; `[video continuation]` remains for an actual source Video.
Changing native context/audio/out-point or the effective confirmed source changes
`executionFingerprint`, not ordinary textual authoring identity. Changing only the
default pointer while a Selected Take still selects the same confirmed attempt
does not change execution identity. Revocation/removal cannot be bypassed by Take
selection; Send recompiles and rechecks identity before transport and after uploads.
Changing actual prompt-visible tail/native selection or observed/planned text still stales the
prompt. Native cache bytes/mtime are neither read nor fingerprinted.

Prepared execution identity includes current Sequence order, selected prompt,
profile/graph/capability, routing and native source/settings. The UI sends
`expectedExecutionFingerprint`; Send rejects a changed preparation before upload.
It also rechecks after upload and before queueing. Direct API clients omitting
that optional expectation request fresh preparation at Send time.

Native capability was introduced in configuration **v4** and sidecar **v2**
(existing strategy enum gained Native, no canonical lineage fields). Current
configuration is **v7**, sidecar **v4**, Project **v17**, Workspace **v1**. Older files acquire empty optional defaults in memory without writes;
mutations upgrade the relevant execution file. Profiles without the capability
retain ordinary semantic mappings and fallback paths.

### Deterministic pure-latent integration example

**Live gate passed, 2026-09-22 (user manual validation).** Native-capable Ref2VA G1
consumed no predecessor, correctly suppressed its Load edge, completed in ComfyUI,
saved native state and was explicitly accepted. G2 used Continue previous shot,
Auto and the same profile; Prepare resolved that exact accepted G1 execution with
22 context frames. More than one G2 pure-latent continuation render sent through
MiniConstruct succeeded. This example validates the concrete integration, without
making core depend on its custom-node package or claiming every visual strategy
has passed live validation.

`opaque_keys` remains the adapter: it maps arbitrary scalar inputs without naming
or depending on a node package. Its minimal extension is directional
`save_segment_index` / `load_segment_index`, plus generated `storage_namespace`.
This is required by the public pure H3 AV Save/Load shape, where a Save node owns
`filename_prefix` and a current clip slot while a Load node owns `latent_path` and
a predecessor clip slot. One shared segment index cannot honestly fill both G2
controls.

For each attempt MiniConstruct creates a relative namespace like
`mc/<project UUID>/<generation UUID>/<attempt UUID>`. The target Save prefix is
that namespace's `clip` prefix and its target slot is the current canonical
Sequence position. A consumer uses the accepted predecessor execution's namespace
and recorded save slot. Thus G2 loads G1-A's exact namespace/slot while saving to
its own namespace/slot. A pending or rejected G1-B is not selected and cannot be
resolved; accepting B updates explicit lineage and a fresh Prepare resolves it.
No newest-file, mtime, directory-entry, queue-order, or `clip_index=0` inference
is used by MiniConstruct.

The fixture's continuation gate makes G1 save-only: consumption is false, loader
identity is empty and its source slot is unset, while Save remains enabled. G2
enables the gate, loads the accepted source, uses 22 profile-owned context frames,
enables audio only when supported, and saves its own native state. The workflow's
overlap-trim node owns hidden repeated-context removal from the visible suffix;
MiniConstruct does not trim or concatenate MP4 files.

Provider-free regressions use the production-shaped 164/165/167 mapping and inspect
both Prepare and the mocked `/prompt` body. They verify an accepted G1's exact
namespace and positive save slot reach G2's Load input, while G2 gets a distinct
Save prefix/slot; a separate G1 test verifies `0` only as the disabled Load sentinel.
These tests are provider-free. The user subsequently reported a successful live
2026-09-25 rerun with fresh G1 and G2 under the same current Profile graph:
accepted G1 state produced seamless native G2 continuation. The old-graph G1
artifact remains incompatible with the current graph.

The native-attempt UI is one visibly labelled, keyboard-accessible selector plus one details/action area with collapsed technical details. Options use `ContinuationExecution.renderSubmissionId` to join canonical RenderSubmission summaries: native status, **✓ ACCEPTED** for confirmed attempts with **Default** on the default pointer, Send number/local timestamp, and one known output filename or a truthful output count. Missing provenance stays explicit. The default accepted pointer is the mount default, otherwise newest for inspection only; refresh retains a still-existing selected ID and Generation switches reset inspection. Selection never accepts native state, selects a Take or changes handoff resolution. Available unaccepted attempts offer Accept/Remove, accepted attempts offer Revoke, and unavailable/pending attempts offer Remove under existing backend eligibility. Clear unavailable is one secondary action only when multiple removable unavailable attempts exist. Rendered Takes and renderer Watch/Reconnect, progress, Cancel, Recover, Retry and import remain separate.

Native Render attempts can be removed individually from the execution sidecar.
An accepted attempt must first have its acceptance revoked, even if another attempt
has become the default. Revoke sends `status=available, accept=false`; it preserves availability. Bulk cleanup removes only unavailable, non-accepted attempts;
pending and available attempts remain. These actions preserve ComfyUI jobs and
history, RenderSubmissions, Takes, videos, observations and external native latent
files. Removing a pending attempt does not cancel its ComfyUI job.

### Manual acceptance recipe for a pure-latent H3 workflow

1. Import the Comfy API workflow, make normal prompt/duration/reference bindings,
   paste the native mapping, validate it, and save the Profile. Browsing a Profile
   is non-persistent; **Use this profile for continuity** is the one explicit
   action that applies it to a Generation.
2. For G1, generate/select the prompt for execution, Prepare, Send, wait for the
   actual Comfy workflow to finish, then confirm its saved native artifact.
   Optionally import and select the output Take.
3. For G2, set Relationship to **Continue previous shot** and Visual Handoff to
   **Auto**, use the same applied Profile, generate/select the next prompt, and
   Prepare. Inspect the exact G1 execution, effective context, audio state and G2
   save status before Send.
4. Human-review seam, subject/object motion, camera momentum, audio phase, frozen
   starts and repeated/missing motion. These provider-free checks use no LLM judge
   and remain separate from the user-reported native G1 → G2 live gate and the
   later user-reported Phase F1 closed-loop live validation on 2026-09-22.

### Research basis and manual validation gate

Reviewed public mechanisms on 2026-09-21: native AV slices can be carried into a
new target and protected by per-stream masks, or supplied as guided native context;
the decoded repeated head is handled by the workflow. Different implementations
choose different synchronized AV grids. See the primary implementation descriptions
in [native masked AV continuation](https://github.com/HerrgottMargott/Herrgotts-H3-Infinite-Continuation-Suite)
and [masked versus reference context](https://github.com/ukr8b3g-cmyk/ComfyUI-H3-Continuum).
These are research references, not dependencies or required/endorsed packs.
The [official H3 Ref guide](https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/docs/VIDEO_PROMPT_WRITING_GUIDE_ref_en.md)
ties task wording and media labels to actual reference roles; this motivates the
native-only authoring choice above.

Provider-free tests establish mapping, lineage, preparation, fallback, staleness,
UI and mocked transport behavior, not rendered seam quality. The manual recipe in
[`benchmarks/README.md`](../benchmarks/README.md#native-h3-continuation-manual-execution-gate)
compares an initial clip, native extension and tail fallback with human motion/audio
inspection. No LLM judge or live rendering is part of the automated tests.

## Start with your normal visual workflow

1. Build and edit your workflow normally in ComfyUI. Keep that visual workflow as
   your master. Give similar nodes descriptive titles, such as First reference or Dog.
2. Export an API-format copy and import it into a MiniConstruct Workflow Profile.
   You do **not** load the API export back into ComfyUI just to execute it.
3. Map Generated H3 prompt and Duration to the appropriate scalar inputs. Map
   reference slots using MiniConstruct Picture, Audio or Video numbering.
4. The node picker shows API node ID, class, optional title and short scalar hints,
   including filenames. For example, `154 / image` might show `cyclist.png`, while
   `157 / image` shows `dog.png`. IDs come from the imported API JSON; you do not
   need to find them on the ordinary ComfyUI canvas.
5. Reference ordinal means Picture 1, Picture 2, etc. It is **not** a Comfy node ID.
   Reference kind, ordinal and presence appear only for Reference asset bindings.
6. Configure all master reference capacity once, including currently unused media
   loaders. Remove abandoned test media nodes from the master if they are not
   reference capacity, then update the imported API copy once. Standard unbound
   media loaders cannot be sent with their imported placeholder files.

## Authoring and execution boundary

MiniConstruct owns semantic authoring: H3 mode, creative request, references,
Subjects, duration and Sequence planning/continuity. ComfyUI owns the execution
graph, including custom nodes, loaders, samplers, patches and outputs. A Workflow
Profile describes how semantic inputs target a particular imported graph; it does
not hard-code an H3 node implementation.

Connections answer **where** execution could happen: UUID, name and base URL.
Workflow Profiles answer **how** a graph receives semantic input: UUID, name,
optional description, imported API graph, SHA-256 fingerprint, compatible modes
and typed bindings. Profiles are independent of connections and Projects. Duplicate
display names are allowed; UUIDs identify records throughout the API.

## Machine-local persistence

The existing LLM endpoint/settings store is browser localStorage
(`web/js/settings-persistence.js`, `persistSettings` in `web/js/app.js`). It is not
a server-side application settings store. The execution configuration therefore
uses a dedicated `ExecutionStore`, following ProjectStore's platform data-root and
atomic JSON-write conventions without depending on ProjectStore or its root.

The default file is:

- Windows: `%LOCALAPPDATA%\MiniConstruct\Execution\execution.json`
  (fallback: `~/AppData/Local/MiniConstruct/Execution/execution.json`).
- Other platforms: `$XDG_DATA_HOME/miniconstruct/execution/execution.json`
  (fallback: `~/.local/share/miniconstruct/execution/execution.json`).

`MINICONSTRUCT_EXECUTION_ROOT` overrides the execution directory. It is independent
of `MINICONSTRUCT_PROJECTS_ROOT`. No browser storage is canonical for execution.
The execution file has its own schema version **7**. Supported versions 1–6 load
through in-memory migration and are written as v7 on the next configuration mutation.
Workflow Controls were introduced in v5; older configurations acquire empty controls.
Version 2 direct `controlledConsumers` mappings migrate losslessly to exact
`deactivationTargets`; no transitive target is invented. Legacy mappings and
imported graphs are preserved. Reference presence defaults to
Optional with no invented deactivation destinations; Validate draft and preparation
summaries identify incomplete optional-slot configuration. A present legacy slot
can still resolve. A missing slot cannot prepare until its contract is configured
or it is made Required. Non-reference bindings keep their previous meaning.
Current Project schema is **v17**, including durable outputs and Project Planning Style; Workspace remains **v1**.

### Exposed Workflow Controls

A Workflow Profile explicitly declares each editable API-workflow scalar input or
dictionary leaf by node ID, input name and optional bounded string-key path.
The Profile editor discovers available leaves from the imported graph and suggests
boolean, integer, float or explicit-choice types. Profile save checks the exact
target, type, bounds, choices, duplicate IDs/targets, graph links and conflicts
with semantic or native mappings. No node title or class name has execution
authority. For example, a Power Lora-style `lora_1.on` or `lora_1.strength`,
Spectrum-style `enabled`, sampling `steps`, `sampler_name` and `shift_video`
are ordinary declared targets, not hardcoded integrations.

The Project execution sidecar is v4. Its sparse Project/Profile defaults and
Generation/Profile overrides remain outside Workspace. Effective values inherit
Profile declaration default → Project default → Generation override. Obsolete or
invalid saved overrides warn and fall back; they do not block Prepare. Prepare
applies valid values to a copy of the graph before disjoint semantic bindings;
Send uses that same preparation and records exact control values and declaration
fingerprint in the immutable RenderSubmission. Exact Retry reuses those recorded
values. These additive nested fields remain compatible with Project v16. The
compact Prepare area edits values by declared control ID only. A Profile may own
ordered, named Workflow Control presets with exactly one validated value per
declaration. Applying one explicitly copies its values into the existing Project
default or complete Generation override map; Clear restores inheritance. Matching
and Custom labels are derived from values. Preset edits, renames, deletion and
order do not change applied values, graph or Profile fingerprints, native
compatibility, execution fingerprints, RenderSubmission provenance or exact Retry.
Only changed effective concrete values change execution identity. Presets are
neither graph variants nor node bypass. Execution configuration is v7; earlier
v1–v6 configurations load with empty aspect-ratio value maps and an empty preset list.
Machine-local profiles/connections are not included in Project copies or exports.

Each mutation reloads the file under a shared process lock, validates changes,
writes a unique temporary sibling file, flushes and fsyncs it, then uses atomic
`os.replace`. Failed writes leave the previous configuration intact. Reads return
detached models, with fingerprints and bindings checked again on restart. Corrupt
configuration is reported rather than overwritten. Like the local ProjectStore,
this supports one server process; it is not a distributed/multi-process settings
database. HTTP clients cannot supply filesystem paths.

## Connections and Test Connection

URLs must be HTTP or HTTPS with a valid hostname/address and port (if supplied).
Localhost, IPv6, LAN, Tailscale and remote hosts are supported, including reverse
proxy base paths. Leading/trailing whitespace and trailing slashes are normalized;
the scheme and explicit port are preserved. Credentials, query strings, fragments,
invalid ports, malformed hosts and internal whitespace/backslashes are rejected.
Authentication and API keys are not supported.

**Test Connection** uses the saved connection, through the MiniConstruct backend.
It sends only `GET <baseUrl>/system_stats`. The standard ComfyUI server implements
this as a read-only response containing `system` and `devices`; it does not queue a
prompt. See [ComfyUI's server implementation](https://github.com/comfyanonymous/ComfyUI/blob/master/server.py).
The client requires HTTP 200 and the system-stats object shape (including `system.os`
text and a devices array), with a five-second total deadline and HTTP timeouts.
Responses are capped at 64 KiB. Redirects and environment proxy inheritance are
disabled; response bodies, device details and server arguments are not returned.

Results distinguish `connected`, `unreachable`, `timeout` and `invalid_response`.
This checks reachability/protocol shape, not installed nodes, models, workflow
compatibility or available memory. Testing is explicit; opening settings and
saving records never probe ComfyUI. Tests use fake HTTP transport only.

## Importing workflows

Import **ComfyUI API-format workflow JSON**, not the ordinary editor workflow.
The root must be a non-empty object keyed by non-empty string node IDs. Each node
must be an object with non-empty `class_type` text and an `inputs` object. A root
with `nodes` or `links` arrays is recognized as editor format and rejected with an
API-export instruction. No automatic editor-to-API conversion is attempted.

Workflow text and compact graph encoding are limited to **2 MiB** each, with a
64-level nesting limit. API request envelopes are bounded at 8 MiB before JSON
decoding, allowing room for escaped JSON text. Invalid JSON, duplicate object keys,
non-finite numbers, invalid text, malformed nodes and oversized imports are rejected.
Error responses omit Pydantic's input echo to avoid returning enormous workflows.

The graph remains opaque JSON: arbitrary classes, custom inputs, unknown node
fields and metadata (including `_meta.title`) survive import. Node IDs remain
strings. Link values such as `["42", 0]` and structured inputs are preserved.
No template, Python or shell evaluation occurs.

The saved graph preserves JSON values, not original whitespace. Fingerprinting
uses SHA-256 over UTF-8 JSON with recursively sorted object keys, compact separators
(`,` and `:`), `ensure_ascii=False` and `allow_nan=False`, using Python's JSON number
encoding. Object key order, indentation and Unicode escaping do not affect the
hash. Arrays retain their order. Changed input values change the fingerprint;
integer `1` and float `1.0` retain their distinct JSON encodings. The hash covers
the complete graph, including metadata, but excludes profile name, modes and bindings.

Metadata/binding edits do not round-trip the saved graph through JavaScript JSON
serialization. This preserves custom-node integers beyond JavaScript's exact
integer range. Only an explicit new import replaces the source graph.

## Semantic bindings and compatibility

Every binding has a typed `source`, a string `nodeId`, and string `inputName`:

```json
{
  "source": {"kind": "generated_prompt"},
  "nodeId": "prompt:alpha",
  "inputName": "text"
}
```

Supported sources:

| Source | Meaning |
| --- | --- |
| `{"kind":"generated_prompt"}` | The explicitly selected, current generated H3 prompt from the Project sidecar. |
| `{"kind":"duration"}` | Authoring `durationSeconds`. |
| `{"kind":"reference_asset","assetKind":"image","ordinal":1}` | Picture 1 under canonical reference numbering. |
| `{"kind":"reference_asset","assetKind":"audio","ordinal":1}` | Audio 1 under canonical reference numbering. |
| `{"kind":"reference_asset","assetKind":"video","ordinal":1}` | Video 1 under canonical reference numbering. |

Reference kind uses the existing `AssetKind` enum; `image` is displayed as Picture.
Ordinals are strict positive integers, counted independently per kind. They follow
`miniconstruct.h3.builder.numbered_assets`, including its mode filtering: T2VA has
no active references; I2VA uses first-frame Pictures; L2VA uses last-frame Pictures;
FL2VA orders first-frame before last-frame Pictures; Ref2VA uses the configured
references. Within each kind, references are sorted by `(order, id)`; FL2VA adds
anchor role priority. Subject numbering is separate. Profiles never contain
Project-specific asset UUIDs. F3 resolves this existing numbering directly. It never enumerates the raw asset array
independently or substitutes one ordinal for another missing profile slot.

Compatibility uses the existing `H3Mode` enum: `T2VA`, `I2VA`, `FL2VA`, `L2VA`,
`Ref2VA`. A profile must explicitly select at least one mode, without duplicates.
The frontend obtains modes and asset kinds from the backend capabilities route;
node names never determine compatibility automatically.

Targets must exist and be scalar inputs (including JSON null). Graph links and
structured inputs appear in the inspector and are disabled as mapping choices.
Offline validation rejects missing nodes/inputs, duplicate targets, unsupported
sources, invalid ordinals and invalid modes. It does not infer custom-node types
or guarantee that a scalar has the runtime type expected by a particular node.
Profiles can be saved as incomplete drafts. Prepare requires a generated-prompt
binding and a binding for every canonical active reference. Configured prompt and
duration/aspect-ratio bindings resolve from the active Generation's current H3 Setup at Prepare time. Prepare synchronizes current execution-relevant H3 Setup duration and aspect ratio together through `execution-setup`, against the loaded Project fingerprint before compiling; it does not save other unsaved Workspace fields. Each successful Reference or H3 Setup mutation receipt supplies the fingerprint for the next same-tab operation. A duration or aspect-ratio change after Prepare invalidates that preparation. Selected prompt provenance may become stale when these fields change, but the exact selected prompt remains explicit and is not rewritten.

A modern Aspect Ratio (H3 Setup) semantic binding is preferred and may declare an explicit `valueMap` to
translate H3 Setup values into the renderer input's exact string vocabulary. H3
Setup remains the aspect-ratio authority; the mapping changes vocabulary only.
Fresh Workspace authoring defaults to canonical `4:3`; existing saved Generation
values, including explicit `auto`, remain unchanged when loaded.
The H3 Setup selector includes native `3:2` alongside the existing standard ratios.
For example, a Profile can map `16:9` to `16:9 (Widescreen)` for a ComfyUI core
`ResolutionSelector`. If a nonempty value map omits the chosen built-in standard
ratio (16:9, 9:16, 1:1, 4:3, 3:4, or 3:2), Prepare blocks with an actionable error:
add the exact accepted renderer value to that Profile. No enum labels are inferred.
An empty map, custom ratios, and Automatic retain raw-value advisory behavior;
the renderer remains responsible for accepting or rejecting them. Prepared
inspection shows both source and renderer values.

Saved enum declarations can differ from the live ComfyUI node. In the reported
Ref2VA regression, the existing Profile declared only `4:3 (Standard)` and
`16:9 (Widescreen)`, while a read-only query of its exact `ResolutionSelector`
class and `aspect_ratio` input returned eight choices including `3:2 (Photo)`.
The on-disk, loaded, and running-backend declarations agreed. The earlier complete-enum
test fixtures could therefore pass without repairing this existing Profile.

To repair an incomplete declaration without reimporting the workflow: open
**Execution / ComfyUI → Workflow Profiles → Edit**, choose **Enum capability
connection**, and click **Sync choices from ComfyUI** beside the enum control.
Review the exact returned choices, then click **Save profile**. This explicit
configuration action calls `POST /api/execution/profiles/{id}/controls/{controlId}/enum-capabilities`
with `connectionId`, which reads the exact saved node class via ComfyUI's
`GET /object_info/{node_class}` and its required/optional input definition.
It accepts legacy list enums and V3 `COMBO`/`options` metadata, with bounded
responses/timeouts and no redirects. Missing, duplicate, non-scalar or unverifiable
declarations fail closed. A missing saved default also blocks synchronization;
the editor blocks replacement when an existing preset would become invalid.
No profile is saved by the capability request: the existing save/reload verification
persists the draft and preserves graph, bindings, other controls, defaults and presets.
No node ID or ratio label is hardcoded into discovery. Ambiguous ratio labels remain
blocked by Prepare even if the server declares both. Prepare/Send never discover
capabilities automatically; normal offline Prepare makes no ComfyUI requests.
Profile declaration changes invalidate the existing execution fingerprint.

Existing explicit legacy `aspect_ratio` enum Workflow Controls are bridged automatically
at runtime when there is no modern aspect-ratio binding: exactly one canonical control
ID, a direct scalar string input named `aspect_ratio` or `aspectRatio`, declared string
choices, and no conflicting binding/control/native authority are required. H3 Setup
projects into that target after ordinary controls, overriding saved/default values only
in the prepared graph. Profile bytes, Project defaults, and Generation control overrides
remain untouched; there is no schema/version migration. Inspection reports the effective
value and H3 Setup compatibility source, and the redundant legacy editor is suppressed
without hiding unrelated controls.

The renderer option must have the exact leading H3 ratio token with a token boundary,
and exactly one declared option must match. Missing or ambiguous options block Prepare
and Send recompilation; the old/default ratio is never used as fallback. Automatic must
have an explicitly matching accepted option. A custom ratio such as `3:2` resolves by
the exact leading-token rule when one option is declared; undeclared custom ratios block.
Modern bindings retain their configured valueMap and raw-value advisory behavior. A modern binding
never activates legacy projection; a separate explicit canonical legacy ratio target
must be resolved rather than silently competing with the modern authority. Similar IDs,
fuzzy labels, node classes and titles never qualify. An explicitly declared ratio control
without safe authority blocks; workflows with no declared ratio capability remain valid
and clearly report that H3 Setup did not control their dimensions.

Primary actions, including Send, remain clickable and explain unavailable actions.
Send's handler rechecks current H3 Setup and requires a current preparation; backend
fresh compilation rejects stale execution fingerprints.
Megapixels remains an ordinary Workflow Control and is independent of this binding.

Replacing a workflow keeps the draft bindings visible. Missing or non-scalar
targets are marked in the UI, and a save with invalid bindings is rejected
transactionally: the previous graph, fingerprint and mappings remain intact.
Users can fix/remove bindings and save the replacement and mappings together.
There is no retargeting by class/title. The dedicated workflow replacement API
retains saved bindings and rejects a replacement that makes them invalid.

## Select a generated prompt for this saved Generation

Choose **Generate from Main Prompt** for one-shot Workspace creative authority or
**Generate from Story** for saved Story Plan / Premise, ordered Scene Plans and
current Story context. Both actions produce the same H3 result, validation,
Revision and execution workflow. New results, History and selected execution
prompts carry `authoringPath` (`one_shot` or `story`) separately from `sourceKind`.
Manual edits keep their authoring path. Each path has its own current authoring
fingerprint: Story edits do not stale one-shot prompts, and Main Prompt edits do
not stale Story prompts. Backend adoption checks the matching current authority;
legacy History without a path remains subject to explicit adoption.

Generate prompts normally, choose the completed variation you want, and click
**Use for execution**. This is the finished H3 prompt, not Creative Request or a
Story Planner request. There is one selection per filesystem Generation. Selecting
another replaces it; **Clear selection** removes it. Nothing runs. The current H3
candidate and selected execution prompt remain separate: editing does not change
the selection while typing. When their exact text differs, **Update execution
selection** snapshots the visible prompt exactly. **Prepare** also selects that
visible candidate automatically. H3 Format Audit findings, including ERROR,
are advisory and never block explicit execution.

Generate (including streaming), Repair and completed Revision responses include a
`sourceFingerprint` for the request Workspace. Applying a Revision carries its
candidate fingerprint to the output. Editing raw output ends model-output
provenance and invalidates validation for the previous bytes, but keeps the exact
edited prompt available for validation, a new text-selection Revision, Repair, or
execution.
Legacy completed History rows can retain a generated result's `sourceFingerprint` and
reference map. New results no longer populate browser History; these adoption contracts remain compatibility support. A provenance-preserving History prompt uses **Use for execution**
when its fingerprint exactly matches the current saved Generation and its
text remains unedited. A legacy item without a fingerprint,
or a modern item with a stale fingerprint, can instead use **Adopt for execution**.
History adoption sends only the exact prompt: the backend loads the current saved
Generation, computes its fingerprint and resolves current references before creating the normal execution
selection. It does not reconstruct or claim the prompt's original generation
provenance. If a History prompt is edited, it becomes a `manual_edit` candidate and
uses the same current-snapshot selection authority; stale History reference maps
are discarded. The execution sidecar records `adopted_history` or `manual_edit`
honestly. History alone is not canonical render provenance; authority begins with
the explicit action and backend-created sidecar selection. There is no signing or
response archive, so neither path proves which model emitted particular bytes.

The prompt workflow is **Generate → manual editing and/or LM Revision → Validate /
optional Repair → Use / Update execution selection → Prepare → Send**. Manual
editing, Revision, Repair and saved-prompt loading can each produce the current
candidate. An edited History candidate drops its historical reference map and can
be explicitly adopted as `manual_edit` against the current saved
Generation; History provenance itself is not restored. H3 structure and reference
validation remain visible in Format Audit. A failed validation leaves the visible
prompt available for correction, Repair or exact execution.

You can generate before saving, then save and select that result. If its source
fingerprint differs from current saved authoring, explicit selection records it
as `manual_edit` provenance while preserving prompt bytes. The existing
`validate_workspace_prompt` remains the Format Audit authority.
The existing FL2VA validator had contradictory bare-label and required-alignment
checks. The shared validator now exempts only its required alignment Picture tokens;
body labels, duration and final-shot checks remain enforced, as do the guides.

## Snapshot identity and Project-local persistence

ProjectStore owns `execution-state.json` at the Project root. Its independent
schema version 2 (reading version 1 without rewriting) contains `selections`, keyed by stable Generation UUID. Each
record contains `id`, exact `prompt`, `selectedAt` and `sourceFingerprint`.
Sidecar writes use ProjectStore's transaction lock and atomic file replacement.
They never write `project.json`, update Project timestamps, or enter Story Planner's
`baseSequenceFingerprint`. Genuine authoring saves retain normal Story stale protection.

Execution-authoring fingerprint version 1 is SHA-256 of sorted canonical JSON:
Workspace authoring fields (mode, duration, shots, aspect, variations, Creative
Request, dialogue, labels and creative controls), Subjects, and active references
from `numbered_assets`, including roles, options, relationship metadata and content
identity. Picture data URLs are decoded using ProjectStore's parser and represented
by SHA-256 of the bytes plus MIME/dimensions. ProjectStore hydration supplies those
same bytes after saving. Inline-to-file storage changes therefore do not change the
fingerprint. The canonical reference label represents ordering; raw order integers,
Project ID/name, subject allocation counter, selected output, timestamps and Story
draft metadata are excluded. Actual Picture content changes change the fingerprint.

Later authoring saves retain the selection as provenance. A changed fingerprint
shows that the selected prompt was created for different saved authoring.
Prepare still uses its exact text. Reverting to the matching semantic snapshot
makes the original provenance current again.

| Operation | Selection behavior |
| --- | --- |
| New Generation | No selection |
| Duplicate one Generation | Authoring copied; no selection copied |
| Delete active Generation | Its selection is pruned |
| Full copy Save As / duplicate whole filesystem Project | Selections preserved with remapped Generation IDs; changed authoring overrides can make them stale |
| Copy / backup Project directory | Includes the sidecar naturally |
| Delete Project | Removes the sidecar with the directory |
| Reopen Project / browser reload | Selection status loads from ProjectStore |
| Active Workspace Export / Import | Does not carry the Sequence or execution sidecar |

The existing Export action writes a `MiniConstruct Project` JSON envelope v1 with
only the active Workspace, Project Story Idea and export timestamp. Picture data
URLs travel with that Workspace. Import validates that envelope and creates a new
filesystem Project; it does not restore whole-Project execution provenance. There
is no whole-Project ZIP export route. Copy the Project directory or use Save As to
preserve the Sequence and selections. Profiles remain separate machine-local config.

**Saved H3 Prompts** supersede automatic browser History in the normal UI. Existing IndexedDB records stay intact; no new ordinary generation or Repair results are automatically written, no legacy records are bulk-migrated, and History/Load History/Clear History controls are no longer surfaced. Legacy IndexedDB helpers and History adoption remain dormant compatibility capabilities.

The former v3 `savedPromptDrafts` map stored one working copy per stable Generation UUID. Canonical `execution-state.json` **v4** stores `savedPrompts[generationUuid] = {prompts: [...], activeSavedPromptId: UUID | null, nextSaveNumber: N}`. Each item has a stable per-Generation `saveNumber` (Save N), UUID, exact UTF-8 prompt, SHA-256, last-saved timestamp (`savedAt`), source/authoring fingerprint and authoring path; there is no saved mode or model-output provenance field. Durable entries retain append order; GET returns descending Save N with per-entry staleness, so updates do not reorder entries. **Save H3 Prompt** updates an explicit existing target by UUID, preserving its ID/number, refreshing all saved content metadata and timestamp. Without a target, it creates a first save, or reuses an exact hash duplicate without refreshing that original entry's metadata (`alreadySaved=true`). **Save As New** always appends a new UUID/number, even with identical text. Hashes may therefore repeat; IDs and numbers remain unique. `nextSaveNumber` increases only on creation, survives all deletions, and is preserved by Project Save As. Independent/new/duplicate Generations begin at Save 1. The bound remains **100 prompts per Generation**, each up to 100,000 characters. Updating remains allowed at capacity; creation rejects with deletion guidance and no eviction.

Reading v1/v2/v3 sidecars adapts to v4 in memory without rewriting the file. Each v3 saved draft becomes one entry with a deterministic UUIDv5 derived from its Generation UUID and exact hash; its active pointer targets that entry, preserving every former snapshot field. Provisional development-v4 collections without numbering adapt by oldest-first append order to Save 1, Save 2, etc., with the next counter one above the highest. IDs, active pointers and snapshot metadata remain unchanged; reading alone never rewrites either compatibility shape. The next actual sidecar mutation writes canonical final v4, with no v5 bump. Project remains **v17**, Workspace **v1**, execution configuration **v7**.

- `GET .../generations/{generationId}/saved-prompt`: this Generation's list, active pointer and staleness.
- `PUT .../saved-prompt`: update `savedPromptId` in this Project/Generation, or first-save the exact visible text (with deduplication) when omitted. `saveAsNew=true` always appends and cannot be combined with an update ID. Each successful operation sets the active restore pointer. The existing Project transaction and atomic sidecar write protect mutations; stale/deleted/wrong-owner IDs fail without creating a replacement.
- `POST .../saved-prompt/{promptId}/load`: set the active pointer for an owned entry, without execution adoption.
- `DELETE .../saved-prompt/{promptId}`: delete only that snapshot; clear the pointer if it was active.

The compact Saved H3 Prompts panel uses one ordinary dropdown, concise Save N/time/path/source options, a selected-entry detail preview, and matching Load/Delete buttons. The prompt action row exposes adjacent **Save H3 Prompt** and **Save As New** buttons with explicit “Updated Save N” / “Saved as Save N” outcomes. Selection is inspection only; Load asks before discarding unsaved visible text. Save/Save As New/Load never select execution text. The persisted `activeSavedPromptId` is restore authority, separate from the transient candidate's `savedPromptTarget = {key: projectId/generationId, id}`. Only successful saves, explicit Load, or restoration of that exact saved entry establish an edit target. New Generate/Regenerate candidates lack a target; manual edits, applied Revisions, Repair Format and Format Audit preserve it. Returning to a Generation restores its newer in-memory candidate (including its own target or lack of target) before the persisted active entry. Restored saved text is a `manual_edit` candidate without fabricated model provenance. Deleting a target clears its edit authority while leaving visible text/execution selection unchanged; no replacement is automatically loaded. Reset clears candidate memory/targets and honors the existing library keep/clear policy.

Full copy Save As remaps library ownership onto corresponding copied Generation UUIDs, retaining saved prompt IDs/numbers and active pointers; restored candidates acquire a target scoped to the destination Project/Generation. New, duplicate and Story-created Generations start empty. Generation deletion removes its library. Portable active-Workspace export excludes the execution sidecar, including saved prompts and selected execution prompts. F3 has no IndexedDB dependency.

## Optional reference capacity

A profile describes capacity, not an exact required count. One profile can provide
Picture 1–3 while different Generations use one, two or three Pictures. Every active
reference must have a binding, but unused optional capacity is allowed. A mapping
for Picture 3 never falls back to Picture 2 or to an imported test filename.

Reference presence defaults to **Optional**. **Required** means the exact ordinal
must exist. Presence is hidden for prompt/duration. Missing mode-required anchors
still fail H3 readiness even when profile slots are optional.

For an optional loader, configure **deactivation destinations** using the prompt
“When this reference is missing, disconnect it from:”. MiniConstruct discovers a
bounded **reference-branch boundary**, not every downstream edge. It follows the
private branch until the first destination that also has an independently sourced
graph-link dependency, then exposes only the incoming reference-branch input links
and stops. A graph sink without that independent merge is not a safe boundary;
an ambiguous branch produces an error instead of guessed destinations.

The user chooses exact boundary input destinations; MiniConstruct never silently
selects one, even when there is only one. Node IDs remain opaque technical
identity; class/title text is only a readability aid. In particular,
`LoadVideo → GetVideoComponents.video` is normally private adapter plumbing, not
the cut: the boundary is the `GetVideoComponents` output entering the H3
reference input. Links after H3 are outside the reference picker.

The additive v3 route-capacity contract is:

```json
"routes": [{
  "channel": "video_frames",
  "nodeId": "136",
  "inputName": "ref_videos.ref_video_0",
  "expectedSourceNodeId": "171",
  "expectedOutputIndex": 0
}]
```

Each route is an exact graph input link with a semantic channel (`image`,
`video_frames`, `embedded_audio`, or `audio`). The expected source node and output
index are checked against the imported workflow, so replacing a workflow cannot
silently disconnect a different relationship. The old v2
`controlledConsumers: [{nodeId, inputName, outputIndex}]` field is accepted and
migrated losslessly as a direct target whose expected source is the mapped
loader. It does not invent transitive targets. Current local v3
`deactivationTargets` remain readable and are exposed as route capacity. Picture
and standalone Audio channels follow their declared asset kind. Legacy Video
edges retain their identity with a null channel: classify them once in the
MiniConstruct profile editor. No workflow import or export is needed.

Pictures often connect directly:

```text
LoadImage → MiniMax H3 Reference to Video / ref_images.ref_image_0
```

Videos commonly use an adapter branch:

```text
LoadVideo → Get Video Components
                  ├→ MiniMax H3 Reference to Video / ref_videos.ref_video_0
                  └→ MiniMax H3 Reference to Video / ref_video_audios.ref_video_audio_0
```

MiniConstruct binds the Video file once. If both terminal inputs are assigned
to Video 1, one Video `AssetRequirement` is created and both links are removed
when that one optional slot is absent. The paired soundtrack is not a second
MiniConstruct Audio asset. Standalone Audio 1 is separate and can instead bind
`LoadAudio → ... / ref_audios.ref_audio_0`.

When an optional reference is absent, preparation removes only its declared
target input links and replaces the mapped loader scalar with an inactive marker.
It never recursively deletes Get Video Components, prunes orphan nodes, or runs
generic graph garbage collection. A conservative bounded reachability check
then verifies that every remaining path from the loader into an executable
terminal crosses a declared target. An undeclared shared branch fails clearly:

> Optional Video 1 still reaches executable workflow output through an undeclared path.

Required means the file must be present. Configured route capacity remains
editable, and present files may have inactive routes. A newly seen Video enables
frames and disables embedded audio. Standalone Audio defaults on independently.

One master Ref2VA profile may describe up to the workflow's configured reference
capacity. Each Generation can use a subset; unused optional slots are inactive.

### Master capacity and Generation routing

A Workflow Profile is reusable **capacity**, configured once. Keep one unchanged
ComfyUI master workflow with all desired Picture, Video-frame, paired soundtrack,
and standalone Audio links. MiniConstruct chooses routes per Generation; no
per-Generation Comfy editing, export, second workflow, or dummy asset is needed.

Asset presence, profile route capacity, and Generation route activation are
separate. A present Video with frames enabled and embedded audio disabled produces
one Video AssetRequirement: its loader-to-adapter link and frame route remain,
and only the exact soundtrack boundary link is removed. Enabling the soundtrack
still produces one Video requirement. A standalone Audio file produces its own
Audio requirement. An absent Video removes both routes, produces no requirement,
and receives an inactive loader marker. A present file with every route disabled
also receives an inactive marker; its imported test filename is never fallback data.

The Project-local `execution-state.json` sidecar adds `routing`, keyed by
Generation ID, containing `assets`, keyed by durable asset ID. Each preference
has `useFrames` (default true), `useEmbeddedAudio` (default false), and `useAudio`
(default true). These additive fields retain sidecar schema 1 compatibility.
Embedded audio requires frames to be enabled, matching H3 paired presentation.
Updates write only the sidecar, preserve authoring timestamps and fingerprints,
and leave selected prompts intact. The preparation panel invalidates its result
immediately on a toggle and ignores responses from an earlier Generation.

Restart reloads saved choices. Switching profiles retains semantic choices and
Prepare explains missing route capacity. Duplicate Generation intentionally
starts with default routing and no selected prompt; Save As copies routing with
remapped Generation IDs and preserved asset IDs. Deleting a Generation removes
its routing. Removed assets are filtered out of the routing read projection.
Routing remains outside the Project manifest and Workspace; Story Planner fingerprints do not contain routing.

### Effective H3 input labels

Choose execution routes before writing a prompt. The H3 Input Map, assembly,
generation, revision, repair and validation use a transient routing projection.
It represents enabled soundtracks only while writing/validating prompts; it never
persists a synthetic Audio asset or creates an Audio requirement for a Video.

Presentation follows Pictures, then Videos with each enabled soundtrack counted
as Audio immediately before its Video, then enabled standalone Audio files.
Category numbering compacts over active routes. With Video 1 frames on and its
soundtrack off, standalone Audio 1 has effective `<Audio 1>`. With its soundtrack
on, that soundtrack is `<Audio 1>` and the standalone file is `<Audio 2>`.
All enabled soundtracks and standalone clips share a maximum of **three** Audio
references. Routing updates and prompt-generation requests enforce that limit;
preparation checks it again. Binding ordinals declare physical slot order;
recognized H3 socket indices must match those ordinals and channel classifications.
Class names are never used as correctness authority.

Generated outputs and selected prompts carry a `referenceMap` of effective label,
durable asset ID and channel, separate from their authoring fingerprint. Older
outputs use the original frames-only/standalone-audio map. Changing routing does
not mark a selected prompt stale. Reference tags in H3 text are advisory, even
when a label is dangling or routing changes its meaning. Prepare never rewrites
or renumbers completed text. A Workflow Profile requiring an actual bound asset
can still fail when the asset is unavailable. Normal summaries show semantic route status; exact targets remain in
detailed inspection. `transportReady` and `sentToComfyUI` remain false.

Offline smoke test: configure the master profile once and classify the Video's
two routes. Save a Generation with a Video, leave frames on/soundtrack off, write
and select its prompt, then Prepare. Enable its soundtrack and repeat, checking
the effective Audio map first. In another saved Generation add a standalone Audio
file and disable the Video soundtrack; Prepare should show one Video and one Audio
requirement. Finally use a Generation with Pictures and no Video: the same profile
should report both Video routes inactive. These tests require no Comfy workflow
changes and do not send anything to ComfyUI.

### Why preserved orphan adapters are acceptable offline

Current ComfyUI source identifies executable outputs from node classes marked
`OUTPUT_NODE`, then recursively validates the inputs needed by those outputs.
Its execution list is seeded from those output nodes as well. This means a
known-node `LoadVideo` or `Get Video Components` that becomes unreachable after
its H3 terminal links are removed may remain in the API prompt without being
executed merely because it is still present. ComfyUI still validates that every
included class is registered, and live server validation remains the final
authority for a custom node pack:

- [ComfyUI prompt validation and output selection](https://github.com/Comfy-Org/ComfyUI/blob/master/execution.py)
- [ComfyUI dependency graph execution](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_execution/graph.py)

MiniConstruct therefore preserves orphan nodes and does not claim live runtime
validity in F3.2. The prepared result is an offline typed document only.

## Picture payloads, Audio and Video

Pictures, Audio and Video have canonical file payload support. A reference's
filename, MIME type, duration, role, notes, options and Subject relationships remain
Workspace metadata. Filename and duration alone do not establish file authority.

Picture ingestion continues to retain the processed image data URL and ProjectStore
continues its existing Picture storage. Audio/Video selection sends the browser
`File` directly as a binary body to MiniConstruct's `POST /api/media/{kind}`.
This is local application ingestion, **not an upload to ComfyUI**. Duration is still
read with browser media metadata APIs; no ffmpeg or content analysis is required.
The browser holds only metadata and a small `mediaPayload` descriptor (SHA-256,
byte length and opaque upload or Project ID), never A/V base64 or a local file path.

MiniConstruct streams the request to a temporary file and computes SHA-256 over
exact bytes. Audio is limited to **128 MiB** and Video to **512 MiB**, enforced on
both declared length and bytes received, including requests without Content-Length.
Empty files and mismatched media-kind/MIME prefixes are rejected with useful errors.
The MIME declaration is not codec validation: files are not decoded or analyzed by
the server. Filenames never select a server path or shell command.

Unsaved uploads live in `.media-uploads` under the configured Projects root. They
are temporary transfer staging, not canonical shared media storage. Files older
than 24 hours are removed on subsequent uploads; reattach if an unsaved upload has
expired. Failed uploads clean up immediately where practical. Save promptly to
establish durable Project authority. Saving copies bytes into:

- `assets/pictures/<sha256>.<existing-image-extension>`
- `assets/audio/<sha256>.bin`
- `assets/videos/<sha256>.bin`

The existing `AssetFileManifest` now accepts image/audio/video kinds and additive
`contentSha256` and `byteLength` fields for A/V. Picture manifest serialization is
unchanged. Project schema is now **v17**, retaining the Phase B output fields described below. Workspace snapshots in `project.json`
contain semantic metadata, not A/V bytes or transient upload descriptors. Internal
manifests carry safe Project-relative paths; portable Workspace/API data does not.
Renaming a file does not duplicate its blob; different bytes have different hashes.
Identical bytes reuse one blob within a Project and kind.

Save writes and verifies blobs before atomically replacing `project.json`. Creation
and Save As use the existing staging-directory publication. A failed commit leaves
the old Project valid. Temporary copy files are cleaned up; harmless unreferenced
Project blobs are retained, matching Picture policy. No aggressive garbage collection
can remove a blob shared by another Generation.

Reopen validates stored content using bounded reads and restores backend Project
payload authority without returning full A/V bytes to the browser. Save again,
Generation duplication and deletion retain valid shared references. Save As copies
all referenced files into the destination Project, including active unsaved changes;
it does not depend on the source Project surviving. Copying the whole Project
directory is a portable backup. Deleting a Project removes its canonical blobs with
its directory; temporary upload staging follows its separate expiry policy.

Legacy metadata-only A/V Projects still open. Their UI says **File payload unavailable:
reattach file before execution**. Preparation fails clearly until the user reattaches
the file and saves. Saved files show **File payload saved**. Missing or corrupted Reference files open in a recoverable authoring state:
semantic cards, Subject bindings and the original manifest remain intact, with a
transient missing/corrupt status and no usable pixels or A/V descriptor. Reads do
not rewrite the Project. Remove and Replace Picture remain available; A/V Reattach
uses the existing upload path (replacement bytes are allowed). Content-addressed
files are never overwritten to repair corruption: exact reattachment to an occupied
corrupt path is rejected; a replacement with a new digest can recover it.

Execution and enabled Picture vision fail closed until usable media is restored.
Text-only Story planning retains semantic metadata. Normal Save preserves unavailable
manifest entries. Full and fresh Save As retain those entries while copying only
verified Reference files; absent bytes remain unavailable in the destination. Compact
Picture digests carry current manifest identity without restoring missing bytes.
Stale browser pixels with the same digest cannot silently repair canonical storage.
No persisted schema version changes are required.

Active Workspace JSON Export/Import is **not a whole-Project binary backup**. It
preserves A/V metadata and strips payload descriptors; importing cannot reconstruct
those files and requires reattachment. Pictures retain the existing JSON behavior.
Use Save As or a whole Project directory copy to preserve all files. No ZIP archive
format is introduced.

Execution-authoring fingerprints use verified A/V content SHA-256 and MIME, excluding
upload IDs, Project IDs and storage paths. Browser-only `attached` state is normalized
for A/V, so upload, save and reload have equivalent semantic identity. Filename,
role and options still contribute authoring semantics. Execution selections remain
outside that fingerprint. Story Planner still receives **A/V metadata only** and
has not inspected the content; payload descriptors are explicitly excluded from its
metadata projection. The existing committed-Project snapshot/concurrency fingerprint
algorithm is unchanged. No transcription, sampled frames, embeddings or new model
semantics are added.

## Inspect the prepared result

Use a saved filesystem Project and select a compatible profile in **ComfyUI
preparation**, then click **Prepare**. Unsaved authoring shows **Save before
preparing for ComfyUI.** The browser sends only Project, Generation and Profile IDs.
The backend resolves the canonical saved Workspace, selected prompt, fingerprints,
profile, duration and canonical references. No Connection is required.

The result shows selected prompt status, duration, actual reference filenames,
node/input targets, active and inactive optional slots, warnings and an expandable
inspection document. It says **Prepared workflow is ready according to profile
semantics** and **NOT SENT TO COMFYUI**. It is then available for the separate,
explicit **Send to ComfyUI** action; Prepare itself has no Send, Run or Queue side
effect.
This means structural checks, semantic resolution and configured deactivation
succeeded. It does **not** mean live Comfy runtime validation succeeded.

A present Picture, Audio or Video with a saved payload gets a deterministic `AssetRequirement`: ID, source asset ID,
content SHA-256, semantic label, safe display filename, kind and node/input target.
Its graph scalar is replaced by `{"$assetRequirement": "..."}`. Missing optional
loaders use `{"$inactiveReference": "Picture 3"}`. These typed markers cannot be
mistaken for old imported filenames. Requirements contain no bytes, base64 or
absolute local paths. The result has `transportReady: false` and `sentToComfyUI: false`.

Unbound model, LoRA, sampler, dimensions and custom values remain imported
constants. Exact known `LoadImage/image`, `LoadAudio/audio` and `LoadVideo/file`
inputs retaining unbound filenames get advisory warnings; arbitrary custom-node
names are never guessed to be loaders. Warnings render as literal text.
The response inspection view omits recognized secret fields, absolute paths and
base64 values; that redaction does not change the stored constants. It warns when
values are omitted, and must not be treated as a submission graph.

Prepared results are ephemeral. Project/Generation/mode changes, dirty authoring,
selection changes or stale status, profile choice, and profile list refresh all
remove the displayed ready result. Profile save/update/replacement/deletion in the
configuration dialog refreshes preparation immediately through a small callback.
Selection reads wait one microtask for synchronous Project/Generation adoption to
finish and discard superseded requests before sending. Requests already in flight
can still finish (including a 404 for a deleted Generation); context and request
checks prevent late responses from restoring obsolete readiness.
**Refresh preparation status** also rechecks saved selection and profile summaries.

## Send to ComfyUI

Choose a saved **ComfyUI Connection**, save the Generation, select its current
H3 prompt, and click **Prepare**. It selects the current visible candidate when
one exists, otherwise the existing selection. Inspect the
offline result if desired, then click **Send to ComfyUI**. This is a real external
action: it uploads active saved media, fills the master workflow loaders, and
submits the finalized graph. ComfyUI can immediately queue or start the generation.

Connections remain independent of Workflow Profiles. One saved Connection is
selected automatically; with several, choose one explicitly. Changing Connection
clears Send readiness and any receipt while retaining the offline inspection graph.
Click Prepare again for the new Connection. Profile changes, routing changes,
selected prompt changes, Project/Generation changes and dirty authoring also clear
readiness. Connection edits and deletion refresh the selector. Send is disabled
while sending and after every attempt; Prepare again before deliberately sending
another copy. The preparation-panel receipt is transient; Phase B separately persists immutable accepted RenderSubmission provenance in the Project, without tracking job progress.

The backend independently recomputes preparation from saved state on every Send.
The browser sends only Project, Generation, Profile and Connection IDs. Browser
graphs, paths, prompt text and media bytes are not accepted by Send. Prepare itself
still makes zero ComfyUI requests. Test Connection remains a read-only system-stats
check and never queues a workflow.

Only active payloads are uploaded. A Video soundtrack route uses the **same
uploaded Video file** as its frames, with no separate Audio upload. Identical
content with the same storage extension is uploaded once within a Send. Files are
streamed from verified Project files, so large Audio/Video files are not loaded
whole into Python memory or passed back through the browser.

Inputs use names such as `input/miniconstruct/mc-<sha256>.mp4`, with a safe media
extension and a content hash as identity. Original filenames and Project names
are not storage paths. Uploads request no overwrite. ComfyUI can reuse identical
files or return a different filename; MiniConstruct validates and uses that actual
returned name. Absolute paths, traversal, invalid types and unsafe filename
annotations in upload responses are rejected.

ComfyUI's compatibility endpoint is historically called `/upload/image` even for
these Audio/Video file inputs. Its core handler writes the uploaded file bytes;
the newer Assets API and `--enable-assets` are not required. This detail stays out
of the user controls. The backend preserves proxy base paths, disables redirects
and environment proxies, bounds responses to 64 KiB and uses explicit timeouts.
See the authoritative [ComfyUI server source](https://github.com/comfyanonymous/ComfyUI/blob/master/server.py).

Send is stricter than offline Prepare about placeholder safety. Any unbound
standard LoadImage, LoadVideo or LoadAudio retaining an imported file blocks Send
with the node ID and setup instructions. Bind all reference capacity once in the
Profile; no per-Generation edits or workflow exports are needed. Active loaders
receive real uploaded filenames. Absent or disabled capacity keeps the established
route deactivation, so an imported placeholder cannot become a reference. There
is no Send-anyway bypass. Private path, encoded payload or secret constants also
block Send rather than being silently rewritten.

The transport graph is a separate copy. Ordinary model, LoRA, sampler, seed,
dimension and custom constants remain unchanged. Active requirement markers are
replaced only at their declared targets. Inactive markers remain on disconnected
branches under the existing boundary contract; adapters are not recursively
pruned. ComfyUI checks class availability globally and validates inputs upstream
of outputs; see [ComfyUI validation](https://github.com/comfyanonymous/ComfyUI/blob/master/execution.py).

A successful receipt says **Queued in ComfyUI**, with Connection, prompt ID and
queue number when returned. MiniConstruct generates a fresh UUID before its single
`/prompt` attempt and checks that ComfyUI acknowledges that ID. Node errors are
reduced to bounded node IDs and error categories, excluding echoed input values
and tracebacks. A validation rejection is shown clearly, with relevant categories
when supplied; inspect ComfyUI for full details.

If any upload fails, no prompt is submitted. Earlier uploaded files may remain
for reuse; MiniConstruct does not delete them. A server upload-size rejection
suggests increasing ComfyUI's limit or using a smaller file.

If the queue response is lost or cannot be trusted, the result says **submission
status is unknown**, includes the expected prompt ID, and asks you to check ComfyUI
before manually sending again. There is no automatic retry. If the browser loses
its connection to MiniConstruct itself, it may not receive that ID; check ComfyUI
before another attempt in that case too.

The explicit Send operation stops at queue acceptance. Continuity 2.0 **Phase F1**
now separately tracks the recorded exact job and retrieves its outputs. It does
not open a WebSocket, inspect global history/queue, cancel jobs, render Sequences
automatically or manage VRAM handoff. The older Send milestone labels F4/F5 are
superseded for tracking by the Phase F roadmap below.

## Application API

All routes start with `/api/execution`. Request models forbid unknown fields.
Invalid requests/mappings return 422, oversized envelopes 413, unknown UUIDs 404,
and storage failures 500 with concise messages that omit filesystem paths.
Connection tests return HTTP 200 with the status described above.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/capabilities` | Canonical modes/kinds and workflow size bound |
| GET / POST | `/connections` | List / create connections |
| GET / PUT / DELETE | `/connections/{id}` | Get / replace name and URL / delete |
| POST | `/connections/{id}/test` | Read-only system stats test |
| GET / POST | `/profiles` | List summaries / create profile with API workflow |
| POST | `/profiles/validate` | Validate an unsaved complete profile offline |
| GET / PUT / DELETE | `/profiles/{id}` | Get / update metadata and bindings / delete |
| PUT | `/profiles/{id}/workflow` | Replace graph with saved bindings revalidated |
| POST | `/profiles/{id}/validate` | Validate saved profile or supplied draft edits |
| GET / PUT / DELETE | `/projects/{projectId}/generations/{generationId}/selection` | Read status / select generated prompt / clear selection |
| POST | `/prepare` | Offline preparation from Project, Generation and Profile IDs |
| POST | `/send` | Upload active saved inputs and submit once using those IDs plus Connection ID |

Connection writes take `name` and `baseUrl`. Profile creation takes `name`,
optional `description`, `compatibleModes`, optional `bindings` (default empty),
and `workflowJson` (the raw imported text). Profile PUT supplies all metadata and
bindings, with optional `workflowJson` for atomic graph-and-binding replacement.
Workflow-only PUT takes `workflowJson`. Saved-profile validation accepts no body,
or the same draft body as profile PUT. Validation reports contain `valid`, `errors`
and `fingerprint`. Create returns 201, successful delete returns 204. Profile GET
includes `workflow` as an object; lists omit graphs/bindings to keep responses small.

The UI provides labelled fields and fieldsets, action-local live status regions,
literal text rendering for imported names/titles, and deletion confirmation.
Unsaved profile drafts survive dialog closure; explicit New/Edit actions confirm
discarding a changed profile. Saved records survive application restarts.

Selection PUT accepts only `prompt` (nonblank, maximum 100,000 characters) and
`sourceFingerprint` (64 lowercase SHA-256 hex characters). GET/PUT/DELETE return
`currentFingerprint`, `selected` and `stale`. Missing Projects/Generations return
404; empty prompts, missing payloads and mechanical preflight failures return 422.
Source mismatches become honest `manual_edit` selections. No endpoint accepts a
path or browser Workspace as execution authority.

Prepare accepts `{projectId, generationId, profileId}`. Its typed result includes
format/version, those IDs, mode, profile name, imported workflow fingerprint,
full profile fingerprint (including bindings), current authoring fingerprint,
selected prompt provenance, safe graph inspection, semantic resolutions,
`assetRequirements`, `inactiveSlots`, warnings, ready status and false transport/
sent flags. The result is deterministic for identical saved inputs and selection;
it is not persisted. Profiles/graphs and execution selections remain separate.

Send accepts `{projectId, generationId, profileId, connectionId}` and returns
`submitted: true`, those IDs, `profileName`, `connectionName`, `promptId`, optional
`queueNumber`, bounded `nodeErrors`, and `uploadedInputs` (requirement ID, semantic
label, returned filename). It returns no graph, file bytes or local paths. Preflight
and definite validation rejections return 422; transport failures and ambiguous
queue acceptance return 502 with a safe explanatory message. Phase B adds `renderSubmissionId`, `projectFingerprint` and an optional `provenanceWarning` to the Send receipt. Project schema v13 stores the accepted submission; the selected-prompt sidecar remains separate.


## Rendered Takes (Continuity 2.0 Phase B)

These four concepts have separate authority:

| Concept | Meaning |
| --- | --- |
| Generated H3 Prompt | Textual render instruction, selected separately with Use for execution |
| Render Submission | A renderer job accepted by ComfyUI; not proof that any video exists |
| Take | One actual imported output video, with an opaque UUID and immutable provenance |
| Selected Take | Zero or one actual Take explicitly chosen as the Generation's visual result |

**Selected generated prompt != Selected Take. Selected Take != Observed End State.** Selection never watches or analyzes video. Phase C resolves only the selected observation for later same-scene H3 continuity; changing it can stale dependent prompts. Source-Generation historical prompts, Planned Continuity and Story Memory remain unchanged.

### Persistence and immutable provenance

`project.json` schema **v13** adds `renderSubmissions`, `takes`, and nullable `selectedTakeId` to each stable Generation UUID. Workspace remains v1. Old v12 (and older supported) Projects receive empty collections and null selection in memory; reads do not rewrite files. The next successful canonical mutation now writes v17, retaining observations, visual handoff and Project Planning Style. Take operations persist immediately under the existing shared single-server-process transaction lock and atomic manifest replacement. Workspace Save carries these server-owned records forward; it cannot replace them from client authoring data.

A RenderSubmission contains its UUID/time, renderer (`comfyui`), Connection and Workflow Profile UUIDs, imported workflow and full profile fingerprints, actual accepted Comfy prompt UUID, and `promptSnapshot`. Send captures the exact selection used by Prepare, rather than re-reading the current selection after transport. Optional `renderer_seed` Profile binding identifies one exact integer workflow input. A Generation-owned execution-side preference offers **Workflow default** (preserve the imported value), **Random each Send** (fresh 0–2147483647 value per send), or **Fixed** (repeat the entered value). Existing Generations without a saved preference default to Random when the binding exists, and Workflow default when it does not. Controlled modes require the binding; MiniConstruct never scans sampler nodes. The exact submitted Random/Fixed value is stored in `RenderSubmission.rendererSeed`, shown in the Send receipt, and retained through reconciliation. The ordinary Settings Seed is for the prompt-writing LLM only. Changing the renderer preference invalidates Prepare through the execution fingerprint; it does not change H3 prompt history, the imported Profile graph, or native-artifact compatibility. Failed or ambiguous acceptance produces no successful record. If acceptance succeeds but local persistence fails, the receipt still reports acceptance and its prompt ID with an explicit provenance warning; it never automatically retries. Phase F1 adds bounded mutable `job` state alongside these unchanged provenance fields, as described below.

`TakePromptSnapshot` contains exact `prompt`, its UTF-8 SHA-256, `sourceFingerprint`, known H3 `mode`, real execution `selectionId`/`selectedAt`, `capturedAt`, and existing `referenceMap` where available. New execution selections remember their mode. A stale legacy selection without a stored mode retains unknown mode rather than borrowing the current authoring mode. Execution-selection IDs are real sidecar identities, **not** invented History IDs. IndexedDB History has browser-local row IDs, but the current generation-to-selection pipeline does not carry a trustworthy History link, so snapshots make no History-ID claim. The exact prompt is persisted canonically even if browser History disappears. Later authoring edits, regenerated prompts or changed selections never rewrite historical snapshots.

A Take stores UUID/import time, original filename (metadata only), safe MIME, byte size, exact SHA-256, `managedMediaPath`, `sourceKind`, optional `promptSnapshot` and optional `renderSubmissionId`. Provenance choices are explicit:

- `comfyui_submission`: choose an existing submission from this Generation. The server copies its immutable snapshot; the client cannot replace it. Multiple rerolls may reference one submission or prompt.
- `selected_prompt`: choose the prompt previously selected with **Use for execution**, supplying that actual selection ID. The server captures it before upload begins; a different selected ID is rejected. Changes while bytes arrive cannot relabel the output.
- `external_unknown`: valid actual media with null prompt and submission provenance.

Only `selectedTakeId` defines selection; there are no competing per-Take booleans. Select validates ownership; Clear writes null; deleting the selected Take also clears it. No import or Send automatically selects anything. New/manual/inserted/Story-created Generations and Duplicate Generation begin with empty output history. Reorder keeps outputs with their UUID. Deleting a Generation deletes its output metadata.

### Managed media and recovery

Import uses the existing raw-body streaming convention, with bounded query metadata. Supported extensions are MP4, WebM, MOV, MKV, AVI, M4V, MPEG and MPG; MIME comes from the safe extension allowlist, never an arbitrary client type. Browser playback depends on the contained codec. Files are capped at **8 GiB**, filenames at 255 characters, and path/traversal filenames are rejected. No client chooses a filesystem destination.

Bytes stream into a uniquely created `outputs/.imports/*.tmp`, with incremental SHA-256 and flush/fsync. After the stream finishes, Project/Generation ownership is revalidated under the transaction lock and `os.replace` publishes `outputs/media/<sha256>.<safe-extension>`. Identical bytes can reuse a blob even with a different filename/extension. The manifest commits once. In-process failures/disconnects remove staging files; failed metadata writes remove newly published unreferenced blobs where possible. Aged interrupted staging files are reclaimed on later imports. A process crash between blob publication and manifest replacement can leave a harmless unreferenced blob, never a manifest that names a partially uploaded file.

Take and Generation deletion prune only previously referenced blobs that have no remaining reference in the Project. If the OS temporarily prevents deletion (for example, a busy player), committed metadata stays valid and the unreferenced file may remain. Project deletion removes all its owned outputs; an active upload must finish first so an open staging file cannot cause partial Project deletion on Windows. A missing file leaves its Take intact and reports `availability: missing`. Reattach requires the exact stored hash and size, retaining the same Take UUID and provenance. Different bytes are rejected with instructions to add a new Take. No alternate file is substituted automatically.

Save As forks the full filesystem Project, preserving Takes, selected choices and submissions while following existing new Project/Generation UUID behavior. Each distinct available output is verified and hard-linked within the destination staging Project when supported; otherwise it is copied using bounded streams. There are no symlink dependencies. Deleting the source leaves the destination usable. Missing media remains missing in the fork. Hard links share immutable bytes; external in-place edits can affect both links, so manual media changes must use Take imports rather than overwriting managed blobs.

No videos are embedded in `project.json` or the portable `.miniconstruct-project.json`. The portable format remains active-Workspace authoring export/import and excludes filesystem output metadata and Take media.

### Output API and UI

Routes below start with `/api/projects/{projectId}/generations/{generationId}`. IDs are validated UUIDs and ownership is checked server-side. Lists and active Project responses expose summaries without prompt text, media paths or bytes. Full Take provenance is available only from its detail endpoint.

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/outputs` | Take/submission summaries, availability, selectedTakeId, current Project fingerprint |
| POST | `/takes?filename=...&sourceKind=...` | Stream raw video; add renderSubmissionId or selectedPromptId only for that explicit source |
| GET | `/takes/{takeId}` | Full immutable Take provenance, without managed filesystem path |
| GET | `/takes/{takeId}/media` | ID-resolved video, safe FileResponse MIME and Range support in the installed Starlette |
| PUT | `/selected-take/{takeId}` | Explicit atomic selection |
| DELETE | `/selected-take` | Clear selection |
| DELETE | `/takes/{takeId}` | Remove Take, clear selection if necessary, prune final blob reference |
| PUT | `/takes/{takeId}/media?filename=...` | Stream exact bytes to recover missing media |

**Rendered Takes** sits below ComfyUI preparation, outside Story Planner. It shows filename, import time, size, provenance, recorded Comfy prompt ID, selected badge, missing-media status and explicit actions. One manually expanded preview avoids loading several large players at once. Prompt details are loaded on demand. Generation switches discard stale in-flight UI responses. Mutation receipts update the local Project fingerprint without overwriting unsaved authoring. **Refresh render status** now reconciles exact renderer jobs as well as retrieving local output state.

## Live ComfyUI observation and exact Cancel (Phase F2a)

F2a adds a transient **Project-scoped foreground Live Render Preview** between Prepare / Send to ComfyUI and ComfyUI Execution Settings. `rendered-takes.js` owns both this separate mount and the later Rendered Takes surface; the live panel is outside render history and labels its exact render **Generation N · ComfyUI send N**. Send gives ComfyUI the same MiniConstruct-generated UUID as both `prompt_id` and `client_id`. The backend resolves the recorded Project, Generation, RenderSubmission and Connection, then opens `/ws?clientId={promptId}` under the saved Connection's base path. The browser connects only to MiniConstruct's ID-scoped `/api/projects/{projectId}/generations/{generationId}/render-submissions/{submissionId}/live` WebSocket. At most one foreground observer exists; every session retains exact Project, Generation, RenderSubmission and prompt IDs, while UUIDs remain technical history details.

The normal Project/Sequence response contains Generation identities/order but only the edited Generation's outputs. `GET /api/projects/{projectId}/foreground-renders` supplies a bounded discovery model: `candidate` is the best canonical active Project submission; optional `generationId` plus `submissionId` identify `watched`, including terminal truth. At most two rows contain Generation UUID/Sequence number, submission UUID, human send number, exact prompt UUID, createdAt and canonical job status. Send numbering includes terminal history and matches the existing chronological labels. The default summary reads canonical state under the existing transaction lock without contacting ComfyUI or writing files.

The same endpoint's optional `refresh=true` performs renderer-read-only discovery before ranking. One pass reads bounded `/queue` metadata from at most four recorded Connections in parallel (10-second bounds), matches only exact recorded prompt IDs, and confirms at most three candidate/watched or unresolved historical jobs not already established active by that fresh queue response, each bounded to 10 seconds. Positive exact queued/running identity is sufficient for active observation; redundant `/api/jobs` or history confirmation cannot delay an already-established running candidate. Queue status changes are published together in one guarded Project commit. Queue absence remains unknown and still requires exact confirmation, including terminal truth for a retained explicit watcher. Exact terminal results also apply the existing associated native-attempt availability lifecycle; acceptance remains explicit. Discovery never downloads outputs or invokes renderer actions. A returned `discoveryCursor` rotates recorded Connections on the next poll so Projects with more Connections are progressively checked. The existing exact history fallback also checks `/queue` for running/queued truth on older ComfyUI servers without `/api/jobs`. Concurrent older status responses cannot overwrite a newer job snapshot. Candidate rows and revision metadata come from the same locked snapshot, and the browser rejects older candidate snapshots. An outage returns a safe transient `discoveryError` and retains active truth; ordinary foreground polling continues. No graph, credentials or renderer URLs are returned.

Each refresh starts local output/selection reads alongside refreshed foreground discovery. Discovery selects or reconnects the observer before waiting for those local reads or the sequential visible/offscreen history reconciliation batch. The same mounted 15-second cadence remains armed during a slow history request: a tick refreshes only foreground discovery/observation while the original history batch continues. Same-context in-flight foreground refreshes are coalesced; a tick never starts a duplicate history batch. There is one polling timer and one foreground socket, with the existing bounded reconnect backoff.

History reconciliation still runs through its existing exact path and completes eligible video downloads/imports, provenance and native-attempt transitions. A foreground discovery failure does not suppress this history path. Fresh canonical priority can upgrade an automatic queued watcher while its reconciliation is pending. A canonical terminal summary can hand off to the next render even while the former watcher's output download remains pending; socket terminal hints alone cannot release an active pin. Fresh summary rows are not replaced by older local or late history snapshots. Navigation/epoch guards still discard stale history presentation. Discovery continues while no render is active, without independent per-Generation loops, persistent workers or a parallel queue. Project v17, Workspace v1, execution configuration v7 and execution sidecar v4 are unchanged.

With no retained watcher, automatic selection prefers running → queued, canonical Sequence order across Generations, then newest createdAt and descending stable ID within one Generation. An automatic watcher remains stable within its canonical status class and upgrades only to a strictly better status; socket presentation state is never ranked. Running A remains watched when newer queued B arrives. Historical unknown jobs do not automatically open Live Preview. They remain in history with exact Recover and Watch actions, and remain eligible for bounded queue/exact background discovery. An explicit **Watch / Reconnect** choice pins that exact unresolved or active submission regardless of other status priorities or chronology. Exact terminal HTTP reconciliation clears its transient choice/socket/media and selects the next active Project submission; G1 → G2 → G3 transfers require no history click and never navigate the editor. A socket terminal hint alone cannot clear a canonically active observer. Same-Project Generation navigation, including temporary context replacement while loading, preserves the valid observer. Project switching closes it, revokes its Blob URL and clears progress, media and choice. When no Project render is active, the panel hides; no watched pointer is persisted.

Genuine disconnect with canonical running/queued state (or an explicitly watched unknown submission) recovers automatically on a later foreground refresh/poll of the same exact submission. Repeated failures use 15/30/60/120-second delays, capped at 120 seconds and gated by the normal polling cadence. Neither repeated refreshes nor an endpoint that connects then immediately fails bypass the delay. Useful progress or preview media resets the failure count; connection acknowledgment alone does not. Manual **Watch / Reconnect** can force a fresh connection, preserving exact targeting. Tab hiding pauses HTTP polling without intentionally closing the socket or releasing its preview; returning resumes foreground polling. Pagehide/dispose still closes sockets and revokes Blob URLs. Disconnect is not renderer failure and never causes Send or Retry.

A failed/closed transport is retired immediately, including closure while HTTP
reconciliation is pending. Its delayed state, connection acknowledgments and media
cannot erase disconnected state or affect the next observer. Disconnect clears transient media/progress and stops the elapsed clock while retaining
the exact session for recovery. Queued jobs show waiting without a running timer.
Unknown jobs, including explicit Watch, show uncertainty without a running timer;
a socket acknowledgment or state message alone never establishes canonical running.
Only confirmed running work shows elapsed time, measured from this browser
observing the current running interval, rather than from submission createdAt.
Discovery unavailability also suspends elapsed time and clears stale progress/media.
No age threshold changes persisted renderer status; later canonical running evidence
can start normal observation again. A terminal hint with canonically
active HTTP truth retains an open transport; a closed one uses the same bounded
recovery path. Reconnection waits for fresh progress/media; historical preview frames
that ComfyUI did not retain cannot be recovered.
The proxy acknowledges connection only after ComfyUI opens. Browser disconnect is
monitored while awaiting renderer frames, so retiring an idle observer also promptly
closes its upstream socket; it does not leave another observer waiting for that job.

Promoted **Cancel render** captures the live session's exact Project/Generation/RenderSubmission identity and shares the history card's cancellation handler. **Cancel requested…** blocks duplicate clicks and keeps the preview while accepted cancellation awaits canonical terminal truth; HTTP acceptance alone is not proof. Reconciliation retains the existing confirmed-cancellation/native-attempt lifecycle. Cancelling a different queued record does not replace the useful foreground watcher. There is no global ComfyUI interrupt fallback. Foreground ordering/observation is presentation only: it does not select a Take, accept/revoke native state, change Observed End State, Visual Handoff, Workflow Controls, execution selection, authoring or navigation, or invoke Prepare/Send/Retry.

The bridge normalizes exact-job executing node, progress, terminal events, standard JPEG/PNG binary previews (event types 1 and 4), and optional KJNodes `kj_preview_override` JPEG, animated WebP or MP4 previews. It negotiates ComfyUI preview metadata support. JSON `prompt_id` values and event-4 metadata must match the recorded prompt; the old event-1 format and KJ events rely on the unique per-submission client socket. It bounds text (64 KiB for normal events), preview metadata (8 KiB), decoded previews (12 MiB), KJ text (about 16 MiB), and the upstream receive queue (4 frames). Malformed or oversized previews are dropped. Only the latest preview has a browser Blob URL; the prior URL is revoked. Previews are neither persisted Project media nor Takes. No traceback, workflow, path, credential or raw event object is forwarded.

The watched live region can show exact ComfyUI node progress when a progress event provides a valid current value and maximum. When the referenced Workflow Profile still has the exact graph fingerprint recorded on that RenderSubmission, its node ID is labelled with the canonical `class_type`; otherwise the UI uses `Node <id>` or `Sampling`. `SamplerCustomAdvanced` displays its current step, total steps, a native progress bar and a rounded percentage. This is transient state scoped to the watched submission, does not alter Project schema, and is progress only; it is not an ETA. Other custom nodes may not emit useful progress. A new node clears the previous node's progress, reconnects wait for fresh live events, and the live preview remains visible alongside progress.

Live disconnect means observation was lost, not render failure. Terminal events and disconnect request F1 exact HTTP reconciliation. That persisted reconciliation remains the authority for completion, output discovery, unique-video Take import and native continuation status. Live events cannot select a Take or accept a native artifact.

**Cancel render** uses only the recorded submission ID at MiniConstruct's `POST .../render-submissions/{submissionId}/cancel`; the backend posts to ComfyUI `/api/jobs/{exactPromptId}/cancel`. It never uses global `/interrupt`. HTTP 405/501 means unsupported; HTTP 404 triggers read-only exact job, route and history checks to distinguish a missing job from unsupported capability where evidence permits. An ambiguous 404 remains uncertain. A dispatched request is not a confirmed cancellation: the response is followed by exact HTTP reconciliation. A confirmed `cancelled` job is terminal and makes its associated pending native execution unavailable. Already-terminal cancellation is an idempotent no-op. There is no automatic retry or resubmission. Current Project schema is v17; `cancelled` extends the defaulted `RenderSubmission.job.status` values without rewriting old Projects on read. The async `websockets>=15,<18` dependency is declared explicitly.

## Exact Recover and explicit Retry (Phase F2b)

`POST .../render-submissions/{submissionId}/recover` accepts only canonical Project, Generation and RenderSubmission IDs. It calls the F1 exact HTTP reconciliation path with the persisted prompt ID and Connection. It never calls `/prompt`, creates a submission, selects a Take, accepts native continuation, or invokes prompt writing. Active jobs remain active; unknown jobs remain unknown unless exact job or history evidence establishes a state. Unique completed video import remains idempotent under the existing Project transaction; multiple videos stay explicit choices. Rendered Takes offers Recover on the relevant card, including after a live disconnect.

`POST .../render-submissions/{submissionId}/retry` is an explicit, confirmed new Send for a persisted `failed` or `cancelled` attempt. Queued, running, unknown and completed jobs are ineligible. The backend uses the old Connection and Profile IDs, checks current selected prompt text/hash, source and mode, workflow and full Profile fingerprints, and the saved execution fingerprint when available. Normal Prepare and Send revalidate current media, routing, graph and native predecessor before `/prompt`; changed setup must use normal Prepare / Send. The existing Send transport handles upload, bindings and ambiguous acceptance. An internal seed override preserves the original controlled renderer seed; normal Send still rerolls random seeds. Retry creates a new prompt ID, submission ID, created time and fresh native save namespace, records `retryOfSubmissionId`, and watches the new submission. The old attempt remains. Browser busy handling and backend source-attempt locking plus persisted lineage block accidental duplicate retries. Neither Recover nor Retry chooses a Selected Take or accepts native state. No automatic retries or Sequence scheduling are added.

The additive, defaulted `executionFingerprint` and `retryOfSubmissionId` fields on nested RenderSubmission records keep Project v16 compatible; older records load with null fields and are not rewritten merely by reading.

### One-shot Sequence render preparation

**Render next ready Generation** scans the saved canonical Sequence once. It stops at the earliest unresolved render step, reports the exact Generation UUID/number, saved prompt selection, inherited or explicit Profile preference, Connection choice, renderer state and handoff source, then runs ordinary `prepare_workflow()` in inspection mode. A successful result opens that Generation and populates the existing Prepare UI; the user must click **Send to ComfyUI** separately. Readiness is advisory automation state, never an added restriction on manual Use for execution, Prepare or Send. A queued/running or unknown exact job stops duplicate automatic preparation; completed work awaiting output choice is not silently rerendered or skipped. Failed/cancelled attempts may use ordinary new Prepare, while exact Retry remains explicit. The current Profile maps any mechanically usable accepted predecessor native state even when Profile graph fingerprints differ; Selected Take and visual handoff retain ordinary Prepare authority. Effective Workflow Controls are displayed but never changed, and random renderer seeds are allocated only at Send. This adds no scheduler, worker, automatic Retry or persistent readiness state.

## Exact ComfyUI job tracking and output import (Phase F1)

**Implemented; Phase F remains in progress.** This is request-driven reconciliation,
with no background daemon, renderer retry, worker pool, dependency scheduler or Sequence automation.

### Upstream compatibility and exact identity

Upstream inspected on **2026-09-22**: self-hosted ComfyUI
[`server.py`](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py),
[`comfy_execution/jobs.py`](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_execution/jobs.py)
and the official [WebSocket example](https://github.com/Comfy-Org/ComfyUI/blob/master/script_examples/websockets_api_example.py).
`GET /api/jobs/{job_id}` returns a flat object with `id`, `status`, optional
execution timestamps, and terminal `outputs` keyed by node. States include
`pending`, `in_progress`, `completed`, `failed` and `cancelled`; MiniConstruct maps
them to Queued, Running, Completed, Failed and Cancelled respectively. Returned `id`
must exactly equal the recorded Prompt UUID. A 404 can mean the job is absent,
not just that the endpoint is unsupported.

Only HTTP 404/405/501 permits `GET /history/{recordedPromptId}` fallback. Only that
exact dictionary key is read; success requires `status_str: success` and
`completed: true`, while `status_str: error` means failure. Empty/missing history
means Unknown, because legacy history cannot distinguish active queued/running
jobs. There is no global-history, queue-order, latest-job, filename chronology or
filesystem search. The official example likewise retrieves exact prompt history
and file bytes via `/view`. Paths are appended to the saved Connection base URL,
including any configured deployment prefix.

### Persistence and API

`RenderSubmission.job` stores status (Queued for a new Send; Unknown when an older
serialized submission has no `job` field), last checked time, optional
upstream execution timestamps in epoch milliseconds, static safe messages, safe
failed node ID, and at most 128 output candidates with imported Take UUID receipts.
Prompt text, credentials, raw workflow/history/node dictionaries, tracebacks and
absolute renderer paths are not copied into job state. Existing provenance stays
immutable. Terminal states survive stale concurrent polls and renderer history
eviction. New defaulted fields use the existing additive compatibility pattern:
Current Project **v17**, Workspace **v1**, configuration **v7**, execution sidecar **v4**,
application version **0.4.0**. Reads do not rewrite older records.

Routes use the existing `/api/projects/{projectId}/generations/{generationId}` base:

| Method | Route | Purpose |
| --- | --- | --- |
| POST | `/render-submissions/{submissionId}/reconcile` | Reconcile exact recorded job; auto-import a unique video |
| POST | `/render-submissions/{submissionId}/outputs/{outputId}/import` | Import an explicitly chosen persisted candidate |

Both routes reject query/body metadata. The backend resolves ownership, saved
Connection and Prompt ID; clients cannot supply a URL, Connection, prompt ID,
filename or descriptor. Responses contain the normal output summary and Project
fingerprint plus a safe `reconciliationError` when transport fails. A download
failure does not change a completed render into a failed job. Explicit refresh
can retry retrieval; it never resubmits the render.

### Discovery, import and idempotency

Only the exact completed job's `outputs` tree is traversed. Container names are
generic (`images`, `gifs`, `videos`, `files`, or arbitrary nested dicts/lists).
A candidate must contain a safe filename, relative slash-separated subfolder and
`type` of `output` or `temp`, with one of the existing eight supported video
extensions. Comfy `input` descriptors are uploaded/reference media and are
excluded from Rendered Take discovery, even when echoed inside a custom node's
nested output metadata. Paths, traversal, control characters, encoded separators,
backslashes and malformed descriptors are rejected. Strings and arbitrary URLs
are ignored. The extractor visits at most 10,000 values, depth 16 and 128 unique
candidates; exceeding a limit discards the entire candidate set and reports a
manual-import message, so a truncated set cannot masquerade as one unique video.
Only safe node IDs and descriptor fields are retained, without custom-node names.

Exactly one candidate triggers backend `/view?filename=...&subfolder=...&type=...`
retrieval and the existing `import_take` stream → staging → SHA-256 → fsync → atomic
managed publication pipeline. The Take has `sourceKind: comfyui_submission`, exact
`renderSubmissionId` and a deep copy of that submission's immutable prompt snapshot.
Zero videos stay completed with no fake Take. Multiple videos require explicit
Import output choices, with node ID, filename, relative subfolder and type shown.

Candidate identity is SHA-256 of canonical JSON containing the RenderSubmission
UUID and exact `{filename, subfolder, type}`. Duplicate descriptor appearances in
different nodes collapse to one candidate. The imported Take UUID receipt commits
in the same manifest transaction as the Take; concurrent imports recheck it under
the existing transaction lock before publication. Content identity is the Take's
SHA-256 and existing managed blob path. Reconciliation never downloads an already
imported candidate again. Deletion retains the receipt to prevent resurrection;
missing media uses existing exact-byte Reattach. Save/Save As preserve receipts.

### Trust boundary and UI lifecycle

The saved Connection uses the same configured HTTP(S) host/deployment trust model
as Send (including explicitly configured local/LAN servers). HTTPX disables
environment proxies and redirects. GET metadata is capped at 2 MiB, with 10-second
connect, 30-second idle and 30-second total job-query limits. Output retrieval has
the same connect/idle limits, a 600-second total deadline and the existing 8 GiB
stream cap; oversized/invalid lengths and incomplete downloads fail before
publication. Declared MIME must match the allowed video extension (documented
video aliases or generic binary are allowed); missing MIME is treated as generic
binary, then canonical MIME comes from the extension. Compressed responses are
rejected. Error/redirect bodies are not consumed. Failures clean temporary storage
and never expose arbitrary remote errors, tracebacks, inputs or paths. No remote
filesystem is read and no custom-node URL is followed.

Rendered Takes shows each send's compact status, **Completed · Take imported**,
or **Completed — no retrievable video output**. Imported Takes share the existing
**ComfyUI send N · local time** label and player. Selection is always explicit.
Actual Takes use one labelled native dropdown and one inspected-Take details/action area, with at most one Take preview. `inspectedTakeId` is UI-only: initial/Generation-switch inspection chooses `selectedTakeId`, otherwise newest; ordinary refresh preserves it when still present. Options visibly mark **✓ SELECTED**, missing media and known/external/unknown provenance; Send labels join only by `renderSubmissionId`. Explicit Preview/Select/Delete/provenance/Reattach actions apply to the inspected UUID, while Observed End State and continuity/Story context continue to use only Project `selectedTakeId`. Inspection changes no authority and closes an old Take preview without autoplay.

The completed Take is the primary result. The outer **ComfyUI render history (N)** disclosure is at the bottom after native management, actual Takes and manual import, closed initially and on Generation switch regardless of failure/cancellation. Its collapsed summary reports count and compact running/queued/unknown or latest failed/cancelled status. Ordinary polling/refresh preserves the user's open/closed choice. Expanded cards retain all exact Watch/Reconnect, Cancel, Recover, Retry, output import and technical diagnostics. The separate promoted live panel retains progress and media regardless of history disclosure state. Manual import remains available under a
collapsed **Manual import / fallback** disclosure with provenance-dependent fields;
the recorded-submission selector appears only for that explicit provenance choice.
Refresh runs immediately when the saved Generation is opened; subsequent checks
are spaced 15 seconds after the previous pass, only while unresolved submissions
exist and the surface/page is visible. Requests run sequentially, stop issuing on
context change/unmount/page hide, and ignore stale response UI. The server may
finish an already-started import safely after navigation. Browser timers carry no
authority. Reopening reconciles saved pending/unknown jobs from any earlier session.
Native status refreshes alongside render receipts; exact completion makes only its
associated pending attempt available. External artifact storage is not independently
verified, acceptance remains explicit, and chronology never replaces accepted authority.

Acceptance uses deterministic storage/migration/API tests, mounted frontend state tests and mocked Comfy transport. No provider call or model-facing benchmark is required. The user manually validated MP4 import, preview, multiple Takes, explicit selection, Generation switching and persistence, including the external_unknown workflow without Send. Built-in ComfyUI Send was not part of that manual validation.


## Observed End State (Continuity 2.0 Phase C)

Status: implemented; the behavioral H3 handoff gate passed and real video analysis/persistence was manually validated. Phase D visual handoff is implemented; native G1 → G2 passed live manual validation on 2026-09-22, with further review useful for other strategies.

Intended Outgoing State is author intent. Selected Take is actual chosen output. Observed End State is model evidence about that output's visible endpoint; Effective Observed End State is the model result after explicit user correction. Planned continuity is not observed truth.

### Canonical authority and lifecycle

Observations were introduced in Project schema **v14**; canonical writes now use **v17**, with Workspace **v1** unchanged. Each Generation owns `takeObservations`; immutable Takes and their provenance are unchanged. Each record binds `takeId` and `takeSha256`, analysis timestamp/contract, analyzer model ID, bounded offered Subject IDs/labels, evidence metadata, `modelObservedState`, `effectiveObservedState` and `model_observed` / `user_edited` status. All supported older schemas, including v13, read with no observations and without writes; the next canonical mutation upgrades them.

Only the selected Take's matching UUID/hash observation is active. Recognition candidates are bounded to 16: current local Subjects, immediate-predecessor observed/local Subjects, earlier local Subjects newest-first, then eligible canonical cast members in stable order; Subjects first materialized by a future Generation are excluded. Up to four identity Pictures are analysis-only, one per candidate, preferring current then newest prior evidence. Candidate availability neither materializes a Subject/asset nor creates canonical retention: only IDs actually present in the model or effective observed state block Subject deletion. Switching to an unanalyzed Take or clearing selection produces no active observation. Corrections use the existing Project fingerprint convention, preserve the machine result and become visual continuity authority immediately. Reset restores the model result. Re-analysis requires explicit confirmation to replace saved corrections and rejects a concurrently changed observation even after confirmation.

Save and Save As preserve observations, corrections, source Take identities and raw video. Save As creates new Project/Generation IDs as before, retains Take IDs/hashes and omits disposable derived evidence; review regenerates that cache. Take/Generation deletion removes its observation and derived files, retaining unrelated Takes. Busy OS files may defer reclamation. Missing raw media retains its existing hash-bound observation for continuity, with an H3 review warning, but prevents new analysis; exact-byte Reattach restores capability. Missing derived evidence never marks a Take missing.

### Local extraction and cache

Runtime dependencies are PyAV (binary wheels with FFmpeg libraries; no manually installed ffmpeg) and Pillow. Extraction launches the current Python runtime with an argument array, no shell, hidden console on Windows, in a separate worker with a **120-second timeout**, **100,000 decoded-frame limit**, **4096 × 4096 pixel ceiling** and two decoder threads. The worker reads a local file object, allows only supported video demuxers, and disables nested protocol access. Corrupt/unsupported media and unreliable timestamps fail without an observation. The raw Take is hash/size checked and never transcoded or rewritten.

The worker streams frames through EOF, retaining at most ten downscaled rolling samples plus the current frame, rather than loading the entire video. Actual presentation timestamps establish the final decodable frame; no seek past estimated EOS is used. Nonmonotonic or absent timestamps fail safely. Candidates are sampled about every quarter-second and discarded outside the final two seconds; up to four unique samples nearest endpoint minus 1.5, 1.0, 0.5 seconds and the endpoint are retained. Short clips use only available time. The endpoint is at most **960 pixels** on its longest side. A **960 × 588** chronological 2×2 sheet labels actual timestamps. Each JPEG is capped at **1 MiB**.

Cache layout is `outputs/observations/<take-uuid>/<take-sha256>/v1/{endpoint.jpg,contact-sheet.jpg,evidence.json}`. The source UUID/hash is recorded in the cache sidecar, cache key and enclosing persisted observation; metadata records extraction version, exact sampled/endpoint timestamps and both image SHA-256 hashes. Existing cache bytes are verified before reuse. Missing/corrupt images regenerate outside the transaction lock. If regenerated metadata differs from historical evidence, review asks for explicit re-analysis rather than silently substituting evidence. Files publish only after ownership revalidation; no derived paths or image bytes are in normal Project responses.

### Explicit vision analysis

`Analyze Selected Take` uses normal OpenAI-compatible LLM settings, requires `supportsVision: true`, turns reasoning off and performs exactly one provider request, including on compatibility rejection. No automatic selection-triggered call, provider repair or hidden retry occurs. All three provenance paths (`external_unknown`, `selected_prompt`, `comfyui_submission`) use the same media authority and path. Prompt provenance, RenderSubmission, execution selection and browser History are optional historical metadata only.

The vision payload contains the endpoint JPEG, tail contact sheet, at most **16 local canonical Subject candidates**, and at most **four** identity Pictures (one per Subject, resized to 512 pixels). Candidate names and technical labels identify people; reference wardrobe/pose never proves current state. Planning context is bounded to 4,000 characters each for Incoming State, Intended Outgoing State and Creative Request, and 2,000 for Camera Intent. Optional missing identity Pictures are omitted; unrelated authoring media is not hydrated or required. No raw video, Project dump, future Generations or Story history is included. The image/text content payload has a hard 5 MiB limit. Output token budget is at most 6,000 and accepted response text at most 80,000 characters.

The supplied JSON schema forbids unknown fields, requires the full shape and bounds each prose field to 800 characters: summary, environment, up to 16 unique Subject states (offered IDs only, four visibility values, appearance/wardrobe/condition/pose/held objects/action), up to 12 props, action momentum, camera, lighting, planned-outcome status (`matches`, `partial`, `conflicts`, `not_verifiable`) plus explanation, and up to eight unresolved entities. Unknown people never become invented canonical Subjects. Planning is comparison context only; unseen state and unsupported medical/causal conclusions must remain unknown. Invalid JSON, IDs, enums or bounds fail visibly without a write or retry.

Snapshot Project/Generation/Take UUID, Take hash, saved authoring, participant/identity context and prior observation under the Project lock. Release it for hash checking, decoding, image preparation and inference. Commit reacquires the lock, validates Project/Generation/Take existence and exact hash, and rejects concurrent observation edits. Selection may change: A's result can cache under A while B remains selected. Deleting A prevents commit. The frontend discards obsolete Generation responses but accepts same-Project fingerprint receipts.

### Textual continuity and bounded Story context

H3ContinuityContext includes predecessor intended outcome and selected effective observation only for `continue_previous_shot`, `new_shot_preserve_continuity` and `match_previous_ending_frame`. Independent omits both. Precedence is current explicit authoring / Incoming State > user-corrected observation > machine observation > predecessor intended outcome > trusted Story Memory > stable profile/reference defaults. The effective state already incorporates corrections. Nonvisual Story Memory remains authoritative for knowledge, motives and relationships; unseen Subjects do not establish outfit state.

The existing execution-authoring fingerprint includes this exact semantic projection. Effective edits, selected-observation switches, clearing and deletion stale dependent prompts; the source Generation's own prompt is unchanged. Warnings and cache availability do not fingerprint. An unanalyzed predecessor selection warns and falls back to planned state without blocking. Missing source media warns while retaining its hash-bound observation.

Plan Next's terminal anchor and bounded local Insert/Replan projections receive only active effective observation as read-only evidence. Their policy gives observed visual state priority over conflicting intended outcome. No observation is editable in a Story proposal and analysis never mutates `storyMemoryAfter`. Phase C itself contributes structured text. Phase D independently injects transient endpoint/tail evidence without changing Workspace assets.

### API, UI and validation

Routes share `/api/projects/{projectId}/generations/{generationId}`:

| Method | Route | Body / result |
| --- | --- | --- |
| POST | `/selected-take/analyze` | Normal `llm`, `expectedProjectFingerprint`, optional explicit `replaceCorrections`; returns output state/fingerprint |
| PUT | `/takes/{takeId}/observation` | `expectedProjectFingerprint` and either `effectiveObservedState` or `reset: true` |
| GET | `/takes/{takeId}/evidence/{identity}` | ID-scoped `endpoint` or `contact-sheet` JPEG; ownership and cache identity checked |

The adjacent Observed End State panel shows source, analysis time/model/status, endpoint/contact sheet, structured editable fields, Subject names/visibility, planned-vs-observed assessment, Save Corrections, Reset, re-analysis confirmation and original-model inspection. Missing selection, disabled vision, progress and missing media have explicit states. UI never treats unknown prompt provenance as an analysis blocker.

Provider-free regressions use generated local videos for final-frame/tail bounds, immutable bytes, cache regeneration, malformed/missing media and cleanup. Mocked vision tests cover payload/identity limits, all provenance paths, single-call failure, corrections, migration, Save As, races, H3 staleness and read-only Plan Next. Mounted frontend tests cover selection/Generation switching and late replies. `h3-observed-continuity` is an **opt-in, not automatically run** model-facing suite for failed jacket removal and explicit current override, using synthetic observations with exact model-input checks and human review; no LLM judge. Offline checks do not establish live model compliance.


## Visual Handoff (Continuity 2.0 Phase D)

**Implemented; initial rendered behavior reviewed.** Native G1 → G2 passed user manual validation on 2026-09-22. Generic tail testing disproved exact prefix duplication, and endpoint → early-cut testing found an early cut without frame-exact timing; broader strategy validation remains useful. Textual continuity records what MiniConstruct observed; Visual Handoff supplies lightweight actual predecessor media; identity references establish who Subjects are. None substitutes for the others. No analysis, Story Memory, prompt provenance or provider call is needed to derive handoff media.

### Saved policy and authority

Project **v15** adds `planning.visualHandoff`: `strategy` (Auto by default), `tailSeconds` (1.5, range 0.5–2.0) and `immediateCutMilliseconds` (100, integer range 0–500). Workspace stays v1. Older Projects, including v14, read with these defaults without writes; the next canonical mutation writes v17. New/manual/Story slots default to Auto. Duplicate preserves policy but creates no outputs. Existing Story Replan targets retain their policy; no new Story model output field is introduced.

| Auto relationship | Resolved strategy | H3 authoring intent |
| --- | --- | --- |
| `continue_previous_shot` | Native when supported and confirmed; otherwise `tail_reference_continuation`, then text | Supply a bounded actual predecessor tail as a Video continuation reference. Begin from its ending state; preserve compatible body/object/camera momentum and do not restart an event. |
| `new_shot_preserve_continuity` | `endpoint_then_immediate_cut` | Match the opening endpoint, then request an immediate cut to current Camera / Shot Intent. Preserve state; do not linger on predecessor composition. |
| `match_previous_ending_frame` | `endpoint_frame` | Strong opening endpoint/keyframe anchor; no forced immediate cut. |
| `independent` or unspecified | `none` | No automatic predecessor media or policy dependency. |

Explicit strategies override Auto, including None. An explicit visual override deliberately establishes predecessor evidence even if the relationship would otherwise resolve to none. Current explicit authoring/Incoming State resets remain higher semantic authority than evidence; canonical identity Pictures and Environment references retain their identity roles. A bounded tail continuation reference gives Ref2VA recent state and motion but does not guarantee a frame-identical or seamless target opening. Cut milliseconds are requested timing, not a frame-perfect guarantee. No automatic trimming, concatenation or edited Sequence is produced.

Generic Ref2VA continuation reference and true workflow overlap are distinct. A generic tail reference begins the target from or after the source ending state and has no exact-seam guarantee. An endpoint Picture is a stronger composition/state anchor, still subject to model adherence. Endpoint → early new shot requests an early camera change from endpoint evidence; its exact timing is not guaranteed. A true repeated-overlap continuation needs execution-level latent/context conditioning and hidden-overlap handling. The optional native capability below maps that contract into an explicitly capable Workflow Profile; the concrete workflow performs it.

Source authority follows canonical Sequence order and the immediate predecessor UUID. An explicit `selectedTakeId` wins. For endpoint strategies, when selection is null, the explicitly accepted/default native attempt may identify the source render: its Project/Generation ownership and exact `renderSubmissionId` must match that predecessor's RenderSubmission, and exactly one associated `comfyui_submission` Take must have available managed media. This operation-local use does not select the Take. Missing output requires recover/import or explicit selection; multiple available matching Takes require explicit selection. Available-but-unaccepted, revoked and unavailable attempts cannot supply fallback authority. No filename, newest Take/job, timestamp, future or distant Generation is a fallback. Accepted native state is not endpoint image media: the ordinary Take extractor supplies the endpoint, and a new-shot handoff does not consume latent state or require a native-capable Profile. Auto missing selection/media warns and omits visual evidence while retaining existing Phase C text. Explicit visual choices fail with selection/reattach guidance. Independent Auto has no warning or predecessor dependency. Source existence is required even if disposable cache survives.

Planned Continuity remains textual authoring evidence. Endpoint extraction does not Analyze or fabricate Observed End State; the existing explicit Selected Take UUID/SHA-bound observation precedence remains unchanged. Duplicate Generation still starts with empty output/selection history and resolves the immediate predecessor normally. Generate, Regenerate and Revision/Repair share the narrative handoff resolver and the existing off-event-loop extraction/cancellation path.

### Mode and reference contracts

The existing compiler determines capabilities: T2VA consumes no media; I2VA consumes exactly one first-frame Picture; FL2VA consumes first/last Pictures; L2VA consumes a last-frame Picture only; Ref2VA accepts Pictures, Videos and Audio. Phase D endpoint strategies fit Ref2VA or an unoccupied I2VA/FL2VA first-frame slot. A manually supplied first anchor is never replaced. FL2VA's ordinary last anchor is still required. Short tails require Ref2VA. Auto incompatibility warns and omits; explicit incompatibility blocks. No automatic mode rewrite occurs. Automatic Story/Ref2VA selection remains a later UX option.

`HandoffProjection` owns a typed `ResolvedVisualHandoff`, a transient reference and optional cache path. Saved Workspace assets remain unchanged. Compiler projections append handoff after ordinary inputs, using reserved semantic IDs and existing per-kind numbering. Collisions fail. The writer receives explicit relationship, strategy, source identity, media kind, effective tail duration and requested cut timing in a separate policy layer. The Input Map and routing view label its continuity origin without filesystem paths. Metadata-only map/status requests perform no decoding. Endpoint pixels reach the prompt writer through its existing image protocol when vision is enabled; Video stays a typed manifest input for the H3 renderer, following the existing Video contract.

PreparedWorkflow, semantic requirements, reference maps and upload staging consume that same effective projection. Transient video routes always enable frames and exclude embedded audio. Send re-resolves the canonical source and verifies bytes before external transport, without creating a Workspace asset. Existing Workflow Profile bindings must provide the corresponding Picture/Video capacity. Graph authority, explicit Send and accepted-job recording remain unchanged; Phase F1 separately reconciles exact jobs and their outputs.

### Local extraction and disposable cache

Cache layout: `outputs/handoffs/<take-uuid>/<take-sha256>/<artifact-key>/{endpoint.jpg|tail.mp4,metadata.json}`. The semantic key hashes UUID, SHA, extraction contract, requested tail duration where relevant and encoding parameters. The endpoint contract reuses Phase C v1 evidence only after source, UUID/hash and cache-byte verification; otherwise it derives the actual final decoded frame locally. No observation is created or modified. Endpoints retain the Phase C maximum 960-pixel dimension and 1 MiB JPEG limit.

Tail extraction uses packaged PyAV/libx264, CRF 18, fast preset, two encoder threads, yuv420p, at most 960 pixels on the long side and **no audio stream**. A first decoding pass locates the actual endpoint; a second pass encodes only the final interval using decoded timestamps, without keyframe remux overrun or full-Take RAM buffering. Both passes are bounded to ten minutes and 36,000 source frames; source frames may not exceed 4096×4096 pixels. A tail has at most 480 decoded frames, at most two seconds and at most 32 MiB. Each extraction worker has a 120-second timeout; two local media workers can run concurrently, with at most 120 seconds waiting for a slot. All subprocesses use argument arrays, no shell, and hidden Windows windows. Temporary output is discarded on failure. Canonical Takes are never transcoded or altered.

Cache contents are hash/size verified and atomically published after ownership revalidation. Missing/corrupt cache regenerates. Full copy Save As preserves policy and raw Takes and intentionally omits this cache. Take/Generation deletion removes exclusively owned known cache leaves; busy files can defer reclamation. Reordering changes the immediate predecessor and semantic identity. Preview is ID-scoped at `GET /api/projects/{projectId}/generations/{generationId}/visual-handoff/preview`, loads one image/video only on request, and never autoplays. No client-supplied filesystem path is accepted.

### Provenance and validation

The authoring fingerprint adds source Generation UUID, selected Take UUID/SHA, requested/resolved strategy, applicable duration/cut intent and artifact semantic identity. Cache filenames, creation times and unrelated Take metadata do not participate. Identical cache regeneration does not stale prompts. Selecting Take B after A, changing tail/cut intent, or changing the immediate predecessor stales dependent selected prompts without rewriting historical output or the source Generation's own prompt. Independent Auto remains unaffected.

Provider-free tests cover deterministic colored videos (including source audio), final endpoint, exact bounded intervals, short sources, no-audio output, immutable bytes, cache reuse/regeneration, migration, canonical selection, modes, numbering, non-mutation, writer policy, fingerprints, Save As/delete/reorder, API/preview, mocked uploads/Send and mounted controls. The opt-in **`h3-visual-handoff`** suite has four generic synthetic cases: tail continuation reference, endpoint then early new shot, endpoint match and independent. Its case-local manual-render ZIP includes every final H3 reference and a safe manifest; there is no LLM judge. Running the prompt benchmark alone cannot establish rendered continuity. No live provider, real ComfyUI or live visual suite is part of implementation validation.


### Execution Settings navigation (2026-10-21)

The settings mount groups Connection & Workflow Profile, Workflow Controls,
Renderer Seed, References & Routing, Prepared Workflow and Native Continuation.
Workflow Controls has a prominent counted native disclosure, initially collapsed;
ordinary refresh keeps its open state, scope, presets and unsaved control drafts.
Selected execution Profile and applied continuity Profile are described separately;
applying remains explicit. Renderer Seed summarizes the saved effective preference
and marks unsaved edits without changing seed allocation or persistence.

References show one compact active channel count. Picture-only setups hide the
routing fieldset; Audio/Video retain their route checkboxes. Actual routing and
visual-handoff warnings stay visible. Prepared readiness shows H3 mode, duration,
native/seed state and a separate warning area. Supplementary diagnostics, full
native source identifiers, assets and graph remain in the expandable inspector.
Native handoff status, continuation settings and the labelled attempt selector
with one contextual action area remain separate from ordinary Workflow Controls.
Unsaved continuation inputs and relevant disclosures survive status rerenders.
Technical identifiers wrap within their containers at narrow widths. Execution
selection, fingerprints, Prepare/Send, acceptance and renderer history authority
are unchanged. No schema or Send performance change is included in that UX update.

### Local-directory verified staging (2026-10-21)

In ComfyUI Connections, optionally set **Local ComfyUI input root** to the actual
ComfyUI `input` directory as seen by the MiniConstruct backend. This is per
Connection, never inferred from localhost and never a global staging database.
For containers or separate hosts the root must be an explicitly accessible mount
of that same directory. Leave blank for existing verified HTTP staging.
`localInputDirectory` is an additive nullable Connection field; old records load
with no mapping. Saving checks access through a provider-free local-status API;
relative/control-character paths fail validation, unavailable roots warn.
An accessible directory is not by itself proof that the mapping is correct.

Initial uploads use `/upload/image`, `type=input`, `subfolder=miniconstruct`,
`overwrite=false`. The server's **returned** `name` and `subfolder` determine
the graph value `subfolder/name` and local path `configured-root/subfolder/name`.
This matches [ComfyUI's upstream upload implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py):
the input type selects `folder_paths.get_input_directory()`, and the response returns
the final collision-adjusted name plus subfolder and type.
The requested deterministic filename is never substituted for a collision-adjusted
response. Only `type=input` is accepted. Nested returned subfolders are supported.
No local directories or staged files are created or modified by MiniConstruct.

The bounded in-memory receipt index is still keyed by full serialized Connection
(including UUID, URL and directory setting), digest, exact size, deterministic
name and MIME. Receipts also remember whether that returned location was locally
verified. Reuse always requires freshly verified bytes; existence, timestamps and
cached mapping success cannot authorize reuse. A first upload's local mapping is
checked too. A mapping failure warns and verifies the upload via HTTP; its receipt
keeps subsequent Sends on verified HTTP until exact local verification establishes
the mapping, instead of repeatedly uploading to a wrong mapping. A once-valid local receipt whose file disappears or changes is evicted
and safely restaged through the upload API. Canonical Reference changes use a new
content key. ComfyUI cleanup is detected the same way; retained valid bytes remain
reusable across renderer restarts. Backend restart/eviction loses hints and uploads
again. Shared bytes across Generations still require each Generation's authority.

Returned path components reject absolute/drive paths, traversal, empty components,
Windows devices, trailing dots/spaces, alternate data streams and unsafe characters.
Windows path comparison uses platform case semantics. The configured root resolves
strictly; each child rejects symlinks/reparse points, including junctions. Before
reading, the opened Windows handle's final path must be below that root (Linux uses
the opened `/proc/self/fd` path); unexpected/unsupported locations fall back safely.
Only regular files are hashed, in bounded chunks, with exact length and SHA-256 plus
before/after file metadata checks. Missing/changed bytes never authorize reuse.
Unavailable roots and unsafe child mappings use verified HTTP with an explicit
Send warning. Unsafe server response components reject Send before local reads or
queueing. Failed HTTP verification follows normal restaging/failure rules.

Canonical payloads are still verified before and after staging; Profile, selected
prompt/provenance, controls, seeds, execution fingerprint and accepted native lineage
are revalidated under the existing canonical/configuration locks before `/prompt`.
No automatic retry follows ambiguous acceptance. New submissions/seeds remain
independent. Ordinary Prepare stays offline. Local hashes run outside those locks.
Neither local nor HTTP verification can prevent external filesystem mutations after
verification or guarantee immutable bytes at delayed renderer execution. The user
must configure the correct root for the selected Connection; matching bytes in an
unrelated copy cannot prove that copy is ComfyUI's real input directory. Standard
ComfyUI APIs do not offer an atomic file-hash-and-queue transaction.

Manual acceptance: save the intended Connection's actual input root and review
the local status; Prepare five saved Pictures offline, then explicitly Send three
times. Confirm three genuine queue IDs/submissions and appropriate seed provenance.
With `MINICONSTRUCT_SEND_TIMING=1`, first Send should upload five files, later Sends
should show five staged reuses, local verification bytes/time, zero uploads and zero
remote verification requests, and one prompt request each. After local cleanup or
same-size replacement, confirm restaging; with the setting cleared, confirm HTTP
verification returns. These live steps are left to the user; implementation/testing
made no real provider requests, live Sends or ComfyUI renders.


### H3 Aspect Ratio discovery and Custom ratios (2026-10-24)

H3 Setup now offers `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9`
and `21:9`, plus Automatic and Custom. Fresh Projects still start at `4:3`.
Project/Generation persistence and H3 authoring retain the selected string.

In Execution settings, edit a saved Profile, create or edit the **Aspect Ratio
(H3 Setup)** semantic binding, and explicitly select its node and scalar input.
Choose the **Enum capability connection** and click **Populate mappings from
ComfyUI**. For a newly imported graph, save the Profile first so discovery has an
exact saved graph to inspect. Discovery also supports a new binding on that graph;
no saved binding or exposed Workflow Control is required.

The semantic endpoint `POST /api/execution/profiles/{id}/aspect-ratio-capabilities`
accepts `connectionId`, `nodeId` and `inputName`. It reuses the legacy enum discovery
implementation: bounded, read-only `/object_info/{exact_class_type}` with an
eight-second timeout and a 1 MiB response limit. Only the exact required/optional
input declaration is inspected. Both legacy COMBO lists and V3 COMBO options are
supported. Non-string enums, duplicate choices, duplicate ratio tokens, ambiguous
input declarations, forceInput/multiselect widgets, missing targets and transport
failures are rejected. No class-name or input-name guesses establish support.

Review the proposed additions and detected choices. Existing values that are
still declared and agree with the requested numeric ratio survive unchanged.
A declared choice with a different strict leading numeric ratio token is a
semantic conflict, even though it belongs to the enum. Renderer-specific labels
without such a token remain manual translations, with an explicit warning that
numeric equivalence cannot be verified. A conflicting or undeclared existing
value displays its current and proposed replacement; the user
must explicitly approve replacement or removal before applying. **Apply detected
mappings** updates the binding in the Profile draft; **Save profile** persists it
through the existing save/GET verification mechanism. Discovery alone never
writes configuration. Complete mappings report that they are up to date. The
manual editor remains available, including preserved nonstandard mapping keys.
No Workflow Control is created or reinstated.

`AspectRatioSource.enumChoices` is an optional bounded list of verified string
choices, saved alongside `valueMap`. Older Profiles load with no declaration and
retain their prior Profile/native-lineage identity. A nonempty declaration
participates in Profile identity and execution fingerprints. Prepare and Send do
not query capability metadata. Saved declarations describe the last explicit
verification, not a promise that a renderer's installed nodes have not changed.

A verified enum is closed: a saved explicit mapping must select a declared value.
If both the H3 value and mapped renderer choice have strict numeric ratio tokens,
they must agree. For example, saved `3:2 → 4:3 (Standard)` fails Prepare with the
exact target and current/requested values, plus instructions to Populate, resolve
and Save profile. Send recompilation applies the same guard before queueing.
A declared renderer-specific label without a numeric token is preserved with an
unverified-equivalence warning; descriptive labels and node titles are not guessed.
Without an explicit mapping, the strict leading ratio token can resolve a unique
saved choice. Thus Custom `21:9` resolves to `21:9 (Ultrawide)` when declared;
Custom `5:4` fails before queueing if absent. Automatic also requires a declared
exact option or an explicit mapping to a declared option. No unsupported ratio
falls back to `4:3`. In older six-entry maps, newly selected standard ratios fail
with a missing-mapping explanation until configured. Profiles without enum
metadata preserve existing raw Custom behavior and warn that support is
unverified; free-form STRING inputs cannot be populated as enums.

For node `115 / aspect_ratio`, both prepared graphs and mocked `/prompt` bodies
contain exactly `2:3 (Portrait Photo)` or `21:9 (Ultrawide)` for the corresponding
H3 choice. A changed H3 ratio invalidates the execution fingerprint; Send recompiles
and rejects a stale expected fingerprint. Existing legacy authority, seed control,
other ResolutionSelector inputs and exact submission provenance remain intact.

Exact pixels such as 1536 × 1024 are a separate future capability. A selector with
`aspect_ratio`, `megapixels` and `multiple` does not provide direct width/height
inputs. General width/height graph rewriting is not implemented.

Manual UI acceptance (live renderer checks remain user-run):

1. Open an existing six-map Profile; select the intended connection and exact
   ResolutionSelector target. Populate, inspect additions for `2:3` and `21:9`,
   apply, save, reopen and verify all eight values. Confirm no new Workflow Control.
2. Choose each new ratio in H3 Setup, Prepare and inspect node 115. Reopen the
   Project, switch Generations, duplicate a Generation and confirm the ratio.
3. Enter Custom `21:9` and Prepare; verify Ultrawide. Enter Custom `5:4`; verify
   a blocking explanation and no queue request. Change a prepared ratio and check
   stale preparation is discarded.
4. With ComfyUI unavailable, verify discovery reports failure without changing
   mappings, while saved supported ratios still Prepare offline.
5. Introduce an invalid manual renderer mapping in a draft, populate and verify
   it requires an explicit conflict decision before Apply. Cancel/reload to retain
   the saved Profile. Ambiguous metadata must leave the draft unchanged.

Automated coverage uses real Profile routes, persistence, H3 instruction layers,
Prepare, Send recompilation and mocked queue payloads, plus mounted editor handlers
and Chromium/Edge browser clicks. It does not call an LLM, queue a real renderer
job or render video.


## Preparation diagnostic severity

Preparation used to put compiler notes, offline limitations, native state summaries,
Workflow Control inheritance messages, routing uncertainty and compatibility warnings
in the same `warnings` list. The browser interpreted any entry as “Prepared with
warnings”, so even an ordinary successful offline Prepare looked exceptional.

`PreparedWorkflow.diagnostics` now supplies `{code, severity, message}` entries.
Severity is `info`, `warning` or `error`; source conditions choose it, without browser
message matching. The existing `warnings` and `information` lists remain as
projections for API and sequence-readiness consumers. No saved Profile schema changes.
Entries keep source order, deduplicate by message and retain the highest severity.
Unknown legacy messages and unrecognized browser severity values remain warnings.

The sources and classifications are:

- The offline compiler supplies information about offline validation, imported seed
  defaults, legacy H3 aspect-ratio authority and inspection-only private omissions.
  Unbound imported media and unverified/manual aspect-ratio mappings remain warnings.
- Workflow Control resolution supplies information for safely ignored removed-control
  defaults. Invalid values for a current control still warn when inheritance is used.
  The controls API exposes the same structured entries and legacy projections.
- Native resolution supplies predecessor compatibility warnings. The preparation
  compiler adds informational save-only/source summaries and artifact lifecycle notes.
  Visual handoff fallback/omission warnings remain warnings at the preparation boundary.
- Reference history verification warns when a used label has unknown history, changes
  source or no longer resolves. Changes confined to unused labels are informational.
  Missing historical mapping only warns when the prompt actually uses Reference labels;
  current routing alone cannot establish what those labels originally meant.
- Profile validation, required References/media, semantic bindings, aspect-ratio
  authority, topology and native capability/lineage failures keep their existing
  exceptions and API failure responses. They never become successful diagnostics.
  Profile-list `preparationIssues` still comes from the existing readiness validators.

The mounted execution UI announces “Prepared — Ready to Send” for success with no
warnings, “Prepared — Review warnings” for actionable warnings and “Preparation failed”
for failure. Informational entries are available once in native keyboard-accessible
`details`/`summary`, collapsed on each new preparation, with a count. The graph inspector
remains separate. Warning text is not repeated in the inspector or routing/control
panels while the same preparation already presents it. Ordinary rerenders preserve an
opened disclosure; invalidation and failed Prepare clear the previous diagnostics.
Existing context/epoch checks discard late responses across Generation, Profile,
Reference, binding and continuation changes. Explicit Profile selection is unchanged.
Consumed/stale Send readiness never retains a Ready to Send announcement.

This changes diagnostic presentation only: graph construction, seed resolution,
fingerprints, native lineage, reference staging and Send recompilation/validation are
unchanged. Send still recompiles against saved authority, checks the expected execution
fingerprint and revalidates after staging before transport.
