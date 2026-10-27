# Hyperflow native-only I2VA example: setup

These public examples are synthetic, production-shaped regression material.
Their model filenames, Profile identities, imported asset name and native paths
are neutral placeholders. They are not an export of a user's renderer installation.
Keep real workflow exports, Profile bodies and inspection reports in a private,
Git-ignored directory; do not publish them with regression fixtures.

## Public bundle

The JSON files in [examples/i2v-hyperflow-native-only](examples/i2v-hyperflow-native-only/)
are self-contained:

| File | Purpose |
| --- | --- |
| `initial-profile-fixture.json` | Typed synthetic initial Profile demonstrating the Optional-Picture readiness failure |
| `initial-profile-create-draft.json` | Required-Picture metadata repair with seed authority; same initial graph |
| `successor-api-workflow.json` | Native-only successor ComfyUI API graph |
| `successor-profile-create.json` | Existing `ProfileCreate` body containing graph, bindings, controls and native declaration |
| `successor-profile-draft.json` | Full typed preview with a distinct synthetic identity |
| `fixture-contract.json` | Recalculated example model identity, synthetic Picture dimensions and scalar canvas |

These extend the established neutral examples without changing them. Use the
[compatibility report](i2v-hyperflow-native-only-compatibility.md) to inspect wiring
and validation coverage. No installed model or custom-node version is certified.

## Prepare your own independent Profiles

1. Keep the initial visual ComfyUI workflow and saved MiniConstruct Profile intact.
   Export a separate API copy and keep that real configuration private.
2. For G1, use an image-conditioned Profile with exactly one Required first-frame
   Picture. The synthetic Optional-Picture fixture intentionally fails readiness
   because `50 → 51 → 52` supplies the canvas to `6`; its Required-Picture repair
   retains this graph and passes normal production validation.
3. Create a new successor Profile, compatible with **I2VA only**. Import its separate
   API graph. Remove `50/LoadImage`, `51/ImageScaleToTotalPixels`, `52/GetImageSize`,
   and `6/first_frame`. Remove every other imported media loader, including unused ones.
4. Set independent integer width and height at `6`, both divisible by 32. Match the
   exact accepted G1 tensor geometry. The public example uses a synthetic 1200×800
   Picture, 0.80 MP scaling and 32-pixel rounding to obtain **1120×736**. These values
   are examples, not a claim about any real Picture. H3 Setup aspect ratio has no
   authority over dimensions in this topology.
5. Copy your actual model, CLIP, video/audio VAEs, LoRA/attention/HyperFlow settings
   consistently into both private Profiles. Do not mix these example filenames
   with a real accepted native state. Preserve the existing sampler and native
   Save/Load relationships. Keep effective controls equal to accepted G1.
6. Bind Generated H3 prompt to `6/prompt`, Duration to `35/value`, and Renderer seed
   to `8/noise_seed`. The successor must have no Picture, Video or Audio bindings.
   Retain only controls whose targets still exist and are valid.
7. Paste your native capability into the advanced Profile editor. Map Save to `56`,
   Load to `59`, the predecessor source link to `59:0 → 55/prev_latent`, and enabled
   mode to `always_extend`. Add version-1 `pictureFreeI2va` with conditioning node
   `6`. Preserve your actual native compatibility key across initial/successor;
   the public key is only an example.
8. Inspect decoder/trim/mux wiring. Both `57:0` video and `57:1` audio should feed
   `15/CreateVideo`. Click **Validate draft**, review the result, and save as a new
   Profile when ready. Applying a Profile to a Generation is a separate selection.

The UI imports API workflow JSON, not full typed Profile previews. The existing
`POST /api/execution/profiles/validate` API accepts a `ProfileCreate` body without
saving; `POST /api/execution/profiles` creates a new saved Profile with a fresh ID.
The synthetic creation body illustrates that schema. No live API request is made
by the regression tests or this documentation.

## G1 → G2 → G3

Render and inspect G1 using the real first-frame Picture Profile. After the renderer
reports completion, verify the native AV artifact and explicitly accept that exact
execution. Existing historical records without a model/codec identity snapshot do
not gain compatibility from today's editable Profile.

For G2, choose I2VA, **Continue previous shot**, and **Native-only I2VA continuation
(no new Picture)**. Remove new media references and apply the independent successor
Profile before authoring its native-only prompt. Prepare must load accepted G1's
namespace/slot 1 and save a fresh attempt at slot 2. After deliberately rendering,
inspecting and accepting G2, G3 must load that exact namespace/slot 2 and save slot 3.
A newer unaccepted reroll cannot select the predecessor. Do not queue example
placeholder paths directly; MiniConstruct resolves real accepted lineage.

Native AV artifacts avoid manual frame extraction. The installed extender may
internally decode context; `always_extend` prevents a fresh-clip fallback but does
not certify direct tensor reuse, seamless audio or output duration. Verify your
installed implementation and exact accepted tensors in a deliberate renderer test.

## Reproduce offline tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_i2v_hyperflow_successor_bundle.py tests/test_picture_free_i2va.py tests/test_i2va_native_continuation.py tests/test_i2va_imported_media.py -q
.\.venv\Scripts\python.exe -m pytest
node --test tests/js/*.test.mjs
git diff --check
```

All inputs are committed synthetic fixtures and in-memory neutral Picture pixels.
Configuration and Project writes use disposable temporary directories; HTTP is
mocked and live transports raise. No private bundle, installed renderer, user home
directory, real model weight or local media file is needed.
