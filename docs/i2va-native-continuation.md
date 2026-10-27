# I2VA and Picture-free native continuation

Updated 2026-10-27. Changes are opt-in and remain uncommitted. The saved
`I2V-Hyperflow` Profile was inspected read-only; no saved Profile, Project,
Connection, active selection or ComfyUI workflow was changed.

## Initial and successor operations

Ordinary I2VA still requires exactly one real first-frame Picture and the official
first-frame alignment line. An initial native Save-only execution retains Picture
conditioning and geometry, disables the extender, disconnects the declared Load
edge, and saves a fresh attempt namespace. Accept the exact completed execution
only after verifying its native artifact.

A **native-only I2VA successor** is a separate explicit operation. Choose I2VA,
Continue previous shot, and **Native-only I2VA continuation (no new Picture)** in
Visual handoff. Apply a separate successor Workflow Profile in ComfyUI Execution
before generating the prompt. The operation requires no new Picture, Video or Audio
references, a completed and explicitly accepted immediate predecessor execution,
and the versioned Profile capability below. Missing media, Optional slots, a loader,
a previous video, Auto, or selecting a compatible-looking Profile never enable it.
A missing, revoked, pending, incompatible or cross-mode source blocks authoring and
Prepare/Send. There is no textual, endpoint, generic Reference, or fresh-clip fallback.

The deliberately narrow v1 path uses the stock `MiniMaxH3ImageToVideo`,
`MiniMaxH3VideoExtender`, `SamplerCustomAdvanced`, `BasicGuider`, and AV Save/Load
shape. Other custom topologies require a separately verified contract.

### Imported-media source boundary

The picture-free contract rejects known imported-media **node types**, including
`LoadImage`, `LoadImageMask`, `LoadVideo` and `LoadAudio`, wherever they appear in
the graph. Empty inputs, linked filename inputs and disconnected leftover loaders
are also rejected. This is independent of filename widget names: `LoadVideo/file`
and `LoadAudio/audio` cannot bypass the contract. Exact registered Video Helper
Suite image-directory, image-path, video-upload/path/FFmpeg and audio-upload/path
loaders are classified as imported sources too. The standard loader widget map is
shared with the existing imported-file diagnostics and Send checks; ordinary
I2VA/Ref2VA behavior is unchanged.

Attached image/video/audio data URIs, Project media payload descriptors and inline
audio waveform payloads are rejected, including nested constants. Normal generated
`VAEDecodeAudio/samples`, `CreateVideo/audio`, `GetVideoComponents/video` and
`SaveVideo/video` connections remain valid. Merely using an audio or video input
name is not evidence that a node imports media. Model/codec identity intentionally
does not cover irrelevant output muxing; this separate source validation is required.

Unrecognized loader/importer/reader class names fail conservatively as **unsupported
loading semantics**, rather than being reported as verified media or harmless model
loaders. Known model/codec/LoRA loaders and the declared native AV loader remain
eligible. This is bounded offline classification, not exhaustive inference about
arbitrarily named custom code. Opaque custom nodes can conceal filesystem access;
their installed implementation still requires review and a separately verified
contract. Passing the static validator does not certify all possible custom nodes.

See the [imported-media correction report](i2va-imported-media-correction.md) for
the reproduction, production Profile-validation regressions and full test results.

## Why two Profiles

The current saved graph has:

```text
50 LoadImage -> 51 ImageScaleToTotalPixels (0.80 MP, 32-pixel steps)
               |-> 6 / first_frame
               `-> 52 GetImageSize -> 6 / width,height
6 conditioning -> 55 VideoExtender -> 11 BasicGuider -> 12 sampler
6 empty AV latent ---------------------------------> 12 sampler
59 LoadLatent -> 55 / prev_latent
12 sampler -> 56 SaveLatent -> 13/14 decode -> 57 trim -> video output
55 / trim_frames ---------------------------> 57 trim
```

Its Picture binding remains Optional in the saved user configuration. For an
initial-image Profile, the user should explicitly change that binding to Required.
A Required Picture may supply geometry; no safe-deactivation requirement is imposed
on that always-active Picture. An Optional binding cannot discard only `first_frame`
while leaving the `50 -> 51 -> 52 -> 6 / width,height` dependencies executable.
Tests explicitly reject this topology as a picture-free successor.

Create a separate ComfyUI workflow variant yourself:

1. Duplicate/export the initial workflow in ComfyUI. Keep the original separately.
2. Disconnect `6 / first_frame`; omit it from the API export entirely. Remove unused
   image loaders/scalers/size nodes and all other imported endpoint media constants.
3. Supply independent integer `6 / width` and `6 / height` values divisible by 32,
   matching the intended canvas. V1 intentionally requires direct scalar dimensions;
   linked dimensions, including native-loader outputs, are rejected. H3 Setup aspect
   ratio does not calculate these dimensions. Existing aspect-ratio mappings and
   renderer seed authority retain their normal behavior.
4. Keep the sampler, guider, native Save/Load, decode, duration, and head-overlap trim
   wiring intact. Set the native enable mapping's enabled value to `always_extend`.
   This renderer mode must fail if native context is absent; `auto` is not sufficient
   for the successor Profile.
5. Export API format and create a NEW Workflow Profile compatible only with I2VA.
   Bind Generated H3 prompt to `6 / prompt` and duration to the existing duration
   control. Do not declare any media reference bindings in this successor Profile.
6. Validate the installed renderer behavior described below, then explicitly declare
   `pictureFreeI2va` in the Profile's existing native capability editor.

No application code transforms arbitrary image-dependent graphs, prunes their
loaders, overwrites a saved workflow, or switches Profiles automatically.

Neutral examples with fictional model names are provided in:

- [API graph](examples/i2va-native-only-api-workflow.json)
- [MiniConstruct Profile declaration](examples/i2va-native-only-profile.json)

They illustrate wiring and pass offline validation. Substitute your own tested
model/codec declarations and canvas; they are not rendered or installation-verified
replacement workflows.

## Capability declaration

Keep the initial Profile's existing native Save/Load declaration. For the separate
successor, retain the same native format `compatibilityKey`, plus this explicit
addition to `nativeContinuation`:

```json
"pictureFreeI2va": {
  "version": 1,
  "rendererContract": "minimax_h3_extender_av_v1",
  "conditioningNodeId": "6"
}
```

This is an author declaration of a renderer contract, not automatic discovery or
proof of installed support. `allowEndpointAnchor` stays false; it is a different
coexistence capability and cannot authorize picture-free operation.

Exact mappings for the illustrated graph:

| Semantic | Node/input | Authority |
| --- | --- | --- |
| enable | 55/mode | enabled `always_extend`, disabled `disabled` |
| context_frames | 55/context_length | default 22; grid 5 + 17n, maximum 39 |
| save_key | 56/filename_prefix | fresh attempt namespace plus `/clip` |
| save_segment_index | 56/clip_index | current canonical Sequence position |
| load_key | 59/latent_path | exact accepted predecessor namespace |
| load_segment_index | 59/clip_index | predecessor's recorded Save slot |
| sourceLinks | 55/prev_latent | exact 59 output 0 |

The initial Profile may keep enabledValue `auto`, since its first execution is
Save-only and still has a required image. The successor must declare
`always_extend`. `requiredInputs` retains context and both directional slots.
Both `requiresTailReference` and `allowEndpointAnchor` must be false. Audio
continuity is not newly guaranteed: this graph has no audio_enabled control.

## Authoring and prompt format

Only canonical server resolution of the explicit saved handoff choice, accepted
source, same I2VA mode and declared successor capability grants a transient
native-only Workspace projection. It is a private attribute; JSON cannot set it,
and it is not persisted as a replacement I2VA mode. Draft persistence continues to
allow incomplete authoring without granting permission to generate or execute it.

Ordinary I2VA keeps the exact official Picture 1 opening line. The native-only
projection uses the three base sections, beginning directly with
`integrated_multimodal_description:`. It has no first-frame alignment line, no
Picture 1, and no invented Video continuation reference. Its validator still checks
base structure, shot timing, supplied-media labels and exact dialogue; it does not
merely skip ordinary validation. Prepare also enforces this grammar on explicitly
selected prompts, including manually selected historical text.

The writer receives the exact accepted predecessor submission's historical scene
prompt, with its old first-frame alignment preamble removed. Relevant identity,
scene, motion, camera and sound context is preserved as historical authoring;
planned endpoints are not promoted to observations. Current Main Prompt / Idea or
Story Scene Plan, camera direction, time progression, dialogue and existing creative
policies retain their normal authority. Native state governs the physical opening.
No execution IDs, latent keys, storage paths or Profile IDs are prompt inputs.

Before a provider call, source resolution must succeed. Production generation
rechecks acceptance/source identity after the mocked or real provider returns;
Prepare and Send resolve again. A stale generated prompt never grants execution
permission after source revocation.

## Exact authority and compatibility

Existing Project UUID, Generation UUID, immediate canonical predecessor, Selected
Take submission where applicable, accepted execution pointer, status, confirmation,
RenderSubmission ownership, Connection and execution fingerprints remain authority.
No directory scan, newest file, default loader index or rendered-video filename
chooses the source. New native-only execution additionally requires renderer job
status `completed` on that exact submission.

New I2VA native preparations record an immutable `i2vaStateIdentity` derived from
the native compatibility key and the actual prepared model/CLIP/video-VAE/audio-VAE
dependency declarations. Prompt, seed, geometry and node IDs do not define model
compatibility. This allows two independently identified Profiles to share state
without permitting different configured model/codec families or native formats.
The effective prepared graph is checked again after controls are applied.

Historical records lacking this snapshot are not silently upgraded from the current
editable Profile. Generate and explicitly accept a new initial execution after this
change to establish the snapshot. A changed/replaced model file with the same name
is not detectable by this offline declaration identity; the renderer contract and
artifact confirmation remain necessary. This is not tensor inspection or a model
file content hash.

The source namespace is the exact accepted attempt, for example
`mc/<project>/<g1>/<accepted-attempt>`, and its stored Save slot is loaded. G2 saves
to a different attempt namespace at slot 2; G3 loads that accepted namespace/slot 2
and saves its own slot 3. Rejected or newer unaccepted rerolls never replace it.

At Send the source Connection must match. The normal compiler reconstructs the
execution, compares the reviewed fingerprint, then recompiles under the Project and
configuration locks immediately before mocked or real `/prompt`. Changes to source
acceptance, Profile, capability, prompt, Sequence or effective controls cannot reuse
a stale Prepare result.

## User interaction and compatibility

For G1, use the initial Required-Picture Profile, ordinary I2VA prompt, and normal
Save/Send/accept lifecycle. For G2, create a new Generation or Continue Selected Take,
explicitly choose I2VA, Continue previous shot, Native-only I2VA continuation, remove
any new references yourself, and apply the separate successor Profile. These choices
do not modify G1's Profile, generation settings, selected Take or native acceptance.
Profile browsing alone does not apply continuity settings.

Continue Selected Take from an eligible native-only successor preserves that explicit
I2VA choice for G3 and beyond. Continuing an initial image generation keeps the
existing creation behavior until the user chooses the new successor operation.
The UI names the successor Profile, source Generation, availability, blocked reason,
and absence of a new Picture. It does not offer a fake media preview for latent state.

Normal I2VA, Ref2VA/T2VA native continuation, FL2VA/L2VA, routing, image Visual Handoff,
Profile duplication, aspect ratio, renderer seeds, Visual Style, diagnostics and
RenderSubmission provenance retain their existing paths. Existing Projects and
Profiles are not reinterpreted as picture-free.

## Required renderer validation

Upstream source inspection confirms that the stock H3 node permits an omitted
first_frame, and the extender offers always_extend and AV Save/Load. This does not
verify installed versions, tensor compatibility, seamless motion, exact output
length, trim behavior or audio continuity:

- [ComfyUI H3 node implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_h3.py)
- [H3 extender implementation](https://github.com/pmhaidn/ComfyUI-Minimax-H3-Extender/blob/main/nodes.py)

In a separate deliberate manual renderer check, verify optional first_frame support,
stock head-context behavior, failure when the exact slot is missing/corrupt, correct
positive directional slot naming, the actual native format and model/VAE pair,
independent canvas dimensions, Save passthrough, sampler conditioning, decode and
trim outputs. Verify G1 -> G2 -> G3, rerolls, revocation handling in MiniConstruct,
visual identity/action/camera continuation and any desired audio continuity.
Queue acceptance and mocked tests do not establish seamless rendered continuation.
No live provider, ComfyUI Send, model inference or user workflow modification was
performed during implementation.

## Verification and implementation report

See [implementation report](i2va-native-only-implementation-report.md) for changed
files, regression coverage, final suite results, limitations and Git status.
