# Pass 293 imported-media correction

Date: 2026-10-27. This correction continues the existing uncommitted implementation.
Its 25 already-staged files were preserved exactly. No commits, delegation, provider
calls, live ComfyUI Sends, model rendering or saved user configuration edits occurred.

## Root cause

`picture_free_errors()` excluded a few input names, including `image`, but did not
classify the nodes that introduced external media. Stock `LoadVideo/file` and
`LoadAudio/audio` therefore passed the explicit picture-free Profile contract.
In the supplied reproduction, `LoadVideo` fed `GetVideoComponents`, whose audio
output replaced the generated audio at `CreateVideo/audio`. Model/codec identity
correctly stayed unchanged because this was an output-mux change, outside model
identity. That identity was never an adequate substitute for graph source validation.

The existing generic Send preflight already blocked unbound stock media loaders;
this correction moves the explicit capability's rejection into Profile save/load
and shared native validation as well, and covers classified custom media loaders.
It does not claim a previously passing contract proved a successful live render.

## Corrected detection and boundary

`comfy_media.py` centralizes the existing stock widget map, reused unchanged by
ordinary preparation diagnostics and transport preflight. The new capability-only
source check classifies exact stock Picture/Video/Audio loader classes and known
Video Helper Suite registrations. Every node is checked, including disconnected
leftovers, regardless of whether its filename is scalar, linked, empty or an
attached descriptor. No reachability inference is needed to reject an imported
source under this narrow contract.

The additional exact custom registrations are the VHS video upload/path/FFmpeg,
image path/directory and audio upload/path loaders. Registration names were checked
against the [upstream Video Helper Suite source](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite/blob/main/videohelpersuite/nodes.py).
This is a finite registry, not a wildcard claim about all custom media nodes.

Nested image/video/audio data URIs, Project media payload descriptors and inline
waveform payloads are rejected too. Known model/CLIP/VAE/LoRA/native AV loaders remain
eligible. Unrecognized loading/importing/reading class names fail as unsupported
semantics rather than being falsely classified as safe model loaders. Arbitrarily
named opaque custom code can still conceal filesystem access: the API graph is not
an exhaustive installed-node audit. Such custom implementations require review and
a separately verified contract; this correction does not infer arbitrary H3 topology.

Generated-media graph links are not imports. `VAEDecodeAudio/samples`,
`CreateVideo/audio`, `GetVideoComponents/video` and `SaveVideo/video` remain valid.
Only the explicit `pictureFreeI2va` capability invokes the additional checks.
Ordinary I2VA and Ref2VA validation and imported-file diagnostics retain their
existing behavior. The model/codec identity algorithm and all native lineage,
namespace, slot, always_extend, prompt and authoring mechanisms are unchanged.

## Exact production regressions

`tests/test_i2va_imported_media.py` adds 31 parameterized cases. Negative cases use
both `ExecutionStore.save()` and the persisted `Profile` constructor, so the tests
cover actual production validation rather than only a leaf helper.

| Requirement | Coverage |
| --- | --- |
| Valid neutral successor | Production Profile save succeeds with normal generated output wiring. |
| Imported Picture | Disconnected stock LoadImage and LoadImageMask reject. |
| Imported Video | LoadVideo/file rejects, including empty, graph-linked and attached-descriptor values. |
| Imported Audio | LoadAudio/audio rejects. |
| Supplied mux reproduction | Exact 70 LoadVideo → 71 GetVideoComponents → 15 CreateVideo/audio rejects. |
| Disconnected loaders | All 13 classified stock/custom source types reject as leftovers. |
| Generated audio/video | VAEDecodeAudio → CreateVideo → SaveVideo remains valid; a generated-video component/mux round trip also validates. |
| No new Picture | Completed/accepted G1 authorizes G2 with no assets; production Prepare and mocked Send succeed. |
| Initial required Picture | Ordinary I2VA Required-Picture Profile remains valid; ordinary Ref2VA also remains valid. |
| Accepted source/Send guards | Existing 35 native-only cases remain in the focused run; the new test also tampers with a source Profile and verifies Send stops before transport. |
| Identity independent of mux | Imported Video/Audio mux changes leave model identity unchanged but fail Profile validation; generated mux changes also leave identity unchanged and remain valid. |
| Custom classification | All nine recognized VHS source registrations reject. Unknown OpaqueLoader, AcmeVideoImporter and ReadAudio fail with an unsupported-semantics error. |

Attached-media cases additionally cover all three media data-URI types, a nested
Project descriptor, a raw descriptor and an inline waveform payload, without relying
on an input named image, audio, video or file.

The broader focused run includes initial I2VA, G1/G2/G3, exact accepted predecessor
selection, source revocation, Connection matching, fingerprint invalidation,
namespace/slot authority and final Send recompilation. Live HTTP transport is
forbidden; the only queue submission exercised uses mocked `/prompt` transport.

## Files changed for this correction

- `miniconstruct/comfy_media.py` (new): shared standard widget map, finite media
  source classification, attached constants and unsupported-loader boundary.
- `miniconstruct/i2va_contract.py`: invoke source checking only inside the explicit
  picture-free capability's existing whole-graph scan.
- `miniconstruct/workflow_preparation.py`, `miniconstruct/execution_send.py`: reuse
  the identical standard map for their existing imported-file behavior.
- `tests/test_i2va_imported_media.py` (new): 31 production validation regressions.
- `docs/i2va-native-continuation.md`: document source rejection and custom-node limits.
- `docs/i2va-imported-media-correction.md` (new): this report.

## Validation

Focused Python: **288 passed**, 2 existing warnings, in 20.99 seconds.
- `.\.venv\Scripts\python.exe -m pytest`: **2,764 passed, 3 skipped,
  2 warnings** in 311.78 seconds. The warnings are the existing Starlette/httpx
  deprecation and denied pytest cache directory writes.
- `node --test tests/js/*.test.mjs`: **682 passed**, no failures or skips, in
  42.15 seconds.
- `git diff --check`: clean (exit 0).
- No live provider/ComfyUI requests or rendering ran. Temporary test logs were removed.

## Git status and preservation

The earlier pass was already staged when this request began. Its staged diff SHA256
is `6dddca82cdf52f8e4a61dd039787021988cd44e644a349c4b842b00a3e57fbb7` and remains
unchanged. This correction is unstaged. No staged/unstaged work was reset, discarded
or committed. HEAD remains `f335737`. All 25 prior staged entries remain intact;
this correction has four existing files changed only in the working tree and three
new untracked files. Whitespace verification passes. Final status follows.

```text
A  docs/examples/i2va-native-only-api-workflow.json
A  docs/examples/i2va-native-only-profile.json
MM docs/i2va-native-continuation.md
A  docs/i2va-native-only-implementation-report.md
M  miniconstruct/api/routes.py
M  miniconstruct/execution.py
 M miniconstruct/execution_send.py
M  miniconstruct/generation.py
M  miniconstruct/h3/builder.py
M  miniconstruct/h3/validator.py
AM miniconstruct/i2va_contract.py
M  miniconstruct/models/api.py
M  miniconstruct/models/visual_handoff.py
M  miniconstruct/models/workspace.py
M  miniconstruct/native_continuation.py
M  miniconstruct/native_continuation_models.py
M  miniconstruct/project_store.py
M  miniconstruct/visual_handoff.py
MM miniconstruct/workflow_preparation.py
A  tests/fixtures/i2va-native-only-workflow.json
M  tests/js/visual-handoff.test.mjs
A  tests/test_picture_free_i2va.py
M  web/js/app.js
M  web/js/execution-config.js
M  web/js/visual-handoff.js
M  web/js/workflow-preparation.js
?? docs/i2va-imported-media-correction.md
?? miniconstruct/comfy_media.py
?? tests/test_i2va_imported_media.py
```
