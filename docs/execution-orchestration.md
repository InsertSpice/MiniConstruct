# Execution orchestration

MiniConstruct can prepare a saved Generation for a reusable ComfyUI Workflow
Profile, then explicitly send it to a saved ComfyUI Connection.
**Prepare is offline. NOT SENT TO COMFYUI.** It does not need a Connection and changes nothing in ComfyUI.
Open **Execution / ComfyUI** in the toolbar to manage profiles and connections.
Use **ComfyUI preparation** below the complete core prompt-authoring workflow to
select and prepare a prompt.
Saving a profile does not contact ComfyUI.

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
The execution file has its own schema version **3**. Version 1 files load through
an in-memory migration and are written as v3 on the next configuration mutation.
Version 2 direct `controlledConsumers` mappings migrate losslessly to exact
`deactivationTargets`; no transitive target is invented. Legacy mappings and
imported graphs are preserved. Reference presence defaults to
Optional with no invented deactivation destinations; Validate draft and preparation
summaries identify incomplete optional-slot configuration. A present legacy slot
can still resolve. A missing slot cannot prepare until its contract is configured
or it is made Required. Non-reference bindings keep their previous meaning.
Project schema **v5**, Workspace and Story Planner schemas remain unchanged.
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
duration bindings always resolve from saved state; no render seed is inferred.

Replacing a workflow keeps the draft bindings visible. Missing or non-scalar
targets are marked in the UI, and a save with invalid bindings is rejected
transactionally: the previous graph, fingerprint and mappings remain intact.
Users can fix/remove bindings and save the replacement and mappings together.
There is no retargeting by class/title. The dedicated workflow replacement API
retains saved bindings and rejects a replacement that makes them invalid.

## Select a generated prompt for this saved Generation

Generate prompts normally, choose the completed valid variation you want, and
click **Use for execution**. This is the finished H3 prompt, not Creative Request
or a Story Planner request. There is one selection per filesystem Generation.
Selecting another replaces it; **Clear selection** removes it. Nothing runs.

Generate (including streaming), Repair and completed Revision responses include a
`sourceFingerprint` for the request Workspace. Applying a Revision carries its
candidate fingerprint to the output. Editing raw output clears that provenance.
History-loaded text has no trusted snapshot provenance and cannot be selected
merely because it is in History. The backend validates selected text independently.
This proves valid text against the saved authoring snapshot, not that a particular
model emitted particular bytes; there is no signing or response archive.

You can generate before saving, then save the matching state and select that
result. A source fingerprint different from the current saved Generation is
rejected with: “This prompt was generated for a different Generation state.
Save/regenerate or select a prompt produced for the current saved Generation.”
Mode readiness uses normal `Workspace` validation. Prompt validation uses the
existing `validate_workspace_prompt`; F3 does not create a second H3 validator.
The existing FL2VA validator had contradictory bare-label and required-alignment
checks. The shared validator now exempts only its required alignment Picture tokens;
body labels, duration and final-shot checks remain enforced, as do the guides.

## Snapshot identity and Project-local persistence

ProjectStore owns `execution-state.json` at the Project root. Its independent
schema version 1 contains `selections`, keyed by stable Generation UUID. Each
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
shows **Selected prompt is stale for the current saved Generation** and blocks
Prepare. Reverting to the exact matching semantic snapshot makes it current again.

| Operation | Selection behavior |
| --- | --- |
| New Generation | No selection |
| Duplicate one Generation | Authoring copied; no selection copied |
| Delete active Generation | Its selection is pruned |
| Save As / duplicate whole filesystem Project | Selections preserved with remapped Generation IDs; changed authoring overrides can make them stale |
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

Browser IndexedDB History remains a convenience store capped at 50 prompts.
Generate replaces in-memory variations; Repair replaces the active output;
Revision updates its originating output. Workspace/Generation/Project restoration
clears outputs; refresh starts with no outputs. History is loaded on demand with
**Load History**, so reopening and preparing a saved Project needs no History read.
F3 has no IndexedDB dependency.

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
Neither Project schema v5, Workspace, nor Story Planner fingerprints contain routing.

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
not mark a selected prompt stale. Prepare checks each reference tag against its
recorded source and blocks if its meaning changed or its route was disabled;
generate and select a prompt with the new routing in that case. It never rewrites
or renumbers completed text. A prompt whose referenced sources still match can
be reused. Normal summaries show semantic route status; exact targets remain in
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
unchanged. Project schema remains **v5**. Workspace snapshots in `project.json`
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
the file and saves. Saved files show **File payload saved**. Missing or corrupted
files that a manifest claims to own fail content validation rather than fabricating
payload authority.

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
generated prompt with **Use for execution**, and click **Prepare**. Inspect the
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
another copy. Receipts are transient and are not saved execution jobs.

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

F4 stops at queue acceptance. MiniConstruct knows ComfyUI accepted prompt ID X;
it does not track progress, retrieve outputs, open a WebSocket, poll history/queue,
cancel, render Sequences automatically or manage VRAM handoff. Tracking and output
retrieval belong to F5.

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
404; source mismatches, invalid prompts, missing payloads and preflight failures
return 422. No endpoint accepts a path or browser Workspace as execution authority.

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
queue acceptance return 502 with a safe explanatory message. Project schema v5
and the saved execution sidecar format are unchanged.
