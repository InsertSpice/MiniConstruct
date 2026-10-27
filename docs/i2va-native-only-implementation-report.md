# Native-only I2VA implementation report

Date: 2026-10-27 (Australia/Sydney). Worktree started clean at the committed
checkpoint `f335737` containing pass 292. No commits, delegation, live provider calls,
ComfyUI Sends, model rendering, saved user Profile edits or saved ComfyUI workflow
changes were made.

## Result and architectural blockers

Implemented an explicit native-only I2VA successor operation. Ordinary I2VA keeps
its Picture and official first-frame grammar. The former blockers were spread
across strict Workspace validation, H3 mode guidance/validation, Picture numbering,
Visual Handoff, Continue Selected Take, Profile graph validation and preparation.
The saved graph's 50→51→52→6 geometry dependencies made deleting its first-frame
route insufficient. Existing cross-Profile native acceptance also lacked an
immutable, independently comparable model/codec declaration for this new path.

The solution uses two explicit Profiles and a narrow renderer contract. It does
not claim that arbitrary H3 graphs, installed node versions or native tensor
contents can be verified offline.

## Capability and operation selection

`NativeCapability.pictureFreeI2va` is a versioned, validated optional declaration
with a renderer contract name and conditioning node ID. It requires exact stock
H3/extender/guider/sampler/AV Save/Load wiring, independent scalar width/height,
no new media bindings or image-anchor inputs, head overlap, and enabled value
`always_extend`. Only I2VA compatibility is allowed. It cannot coexist with
allowEndpointAnchor or a required tail reference.

The separate saved Visual Handoff strategy `native_i2va_continuation` is user
intent. It requires Continue previous shot, I2VA, no new references and a verified
accepted immediate predecessor. Selecting only the Profile, Auto, an Optional
Picture, a native loader or an old video never activates the operation. Both
missing-source requests and attempts to start G1 with this Profile block.

The ordinary source Profile is retained independently. The successor Profile is
selected/applied explicitly in existing ComfyUI Execution controls. No Project JSON
editing or additional configuration page is necessary. The existing native Profile
capability editor remains the workflow author's place to declare renderer support.

## Authoring, references and dialogue

Canonical source resolution grants a private, transient Workspace projection.
JSON and persisted drafts cannot assert it. Generation resolves the source before
its mode validator permits a missing I2VA Picture. This preserves ordinary I2VA
validation instead of globally removing its requirement.

Native-only prompts use base three-section grammar with no new first-frame
instruction or media labels. The compiler includes the exact accepted submission's
preceding scene authoring, with its old first-frame preamble removed, as historical
context. Native state owns the physical opening. Existing identity, current action,
Story Scene Plan, camera, motion, sound and exact dialogue policies remain active.
The validator checks the native grammar and rejects invented media. Prepare checks
that grammar even for manually selected prompts. Ordinary selected-prompt authority
and advisory validation behavior remain unchanged outside the new operation.

Production and streaming authoring revalidate native acceptance before the provider
boundary and after output. A mid-generation revocation cannot produce a completed
usable result. The separate translated-Spoken-Output refresh request can carry the
filesystem source identity for this operation and validates it before translation.
Native-only Prepare compares the selected prompt to resolved target-language speech,
including supported H3 language tags, rather than the untranslated source text.

## Native lineage and Send

Existing ownership and exact source selection remain: Project UUID, Generation UUID,
immediate canonical predecessor, explicit accepted pointer or exact Selected Take
submission, confirmed artifact status, RenderSubmission membership, Connection,
accepted attempt, storage namespace and directional slots. New native-only sources
must additionally have completed renderer job status on the exact accepted execution.

New I2VA preparations snapshot `i2vaStateIdentity`, a bounded hash of the configured
model/CLIP/VAE dependency graph and native format key. Shared adapter graphs are
hashed without exponential expansion; node renaming does not change semantic model
identity. The successor must match the immutable source snapshot and its effective
prepared graph is checked after controls. Different model/codec/format declarations
and all cross-mode sources block. Historical records without a snapshot require a
new initial execution; current editable Profiles cannot retroactively prove them.
This hashes declarations, not actual model files or native tensors.

Each attempt gets its own Save namespace. G2 loads G1's exact accepted namespace and
recorded slot 1, then saves a new namespace at slot 2. G3 repeats from accepted G2
slot 2 and saves slot 3. Newer unaccepted rerolls and filesystem ordering never
select a MiniConstruct source. The positive loader slot prevents its zero-index
newest-file fallback.

Send checks the source Connection, reconstructs the execution, compares the reviewed
fingerprint, and recompiles immediately before `/prompt` under the existing locks.
Source revocation or replacement, Profile edits, prompt changes, Sequence changes
and effective native/control changes invalidate preparation or block queueing.
The renderer's declared `always_extend` behavior is an additional failure boundary.

## Creation and switching behavior

New Generation and Continue Selected Take retain existing behavior for ordinary
sources. After an initial image generation, explicitly select I2VA, Continue previous
shot, the native-only handoff choice, and the separate successor Profile. Remove new
references explicitly. Continuing an already eligible native-only successor preserves
that explicit choice for G3 and later. Existing source settings and Profiles are
not overwritten. UI text states the source, Profile, availability, blocked reason,
and absence of a new first-frame Picture. No latent-state media preview is invented.

Tests exercise Profile switching, Generation switching, reloading saved Projects,
Profile duplication, accepted-source replacement and the resulting fingerprints.

## Backward compatibility

The full suites cover normal I2VA, initial Save-only operation, Ref2VA/T2VA native
continuation, FL2VA/L2VA, reference routing/deactivation, image Visual Handoff, Profile
duplication, aspect ratio, renderer seeds, Visual Style, diagnostic severity and
RenderSubmission provenance. New capability defaults are omitted from historical
Profile/compatibility fingerprints, and absent snapshot values do not alter the
existing Ref2VA/T2VA execution fingerprint shape. Existing Projects and Profiles
never become native-only through migration or inference.

## Implemented files

- `miniconstruct/i2va_contract.py`: stock graph validation and immutable model/codec
  dependency identity.
- `native_continuation_models.py`, `native_continuation.py`: typed capability,
  snapshot, strict explicit source resolution and no-fallback execution gate.
- `models/workspace.py`, `models/visual_handoff.py`, `models/api.py`,
  `visual_handoff.py`: saved intent, private authoring projection, historical accepted
  scene context and dialogue refresh identity.
- `h3/builder.py`, `h3/validator.py`, `generation.py`, `api/routes.py`: continuation
  grammar, provider-boundary revalidation, streaming errors and dialogue handling.
- `execution.py`, `workflow_preparation.py`, `project_store.py`: Profile validation,
  strict native-only preparation, fingerprint preservation and Continue Selected Take.
- `web/js/visual-handoff.js`, `app.js`, `workflow-preparation.js`,
  `execution-config.js`: explicit selection, Profile/source explanations and native-only
  dialogue refresh. Existing execution_send recompilation/Connection checks are reused.
- `tests/test_picture_free_i2va.py`, `tests/js/visual-handoff.test.mjs`,
  `tests/fixtures/i2va-native-only-workflow.json`: new regressions and neutral fixture.
- `docs/i2va-native-continuation.md`, `docs/examples/i2va-native-only-profile.json`,
  `docs/examples/i2va-native-only-api-workflow.json`: user setup and neutral examples.

## Regression and mocked transport coverage

The production-shaped source fixture retains 50→51→52→6, 55/56/59, sampler, codec,
and trim wiring. A separate successor fixture supplies independent dimensions and
omits the image branch. Tests reject the exact Picture-dependent source topology
as a successor, linked loader-fed dimensions, imported Picture constants, wrong
Load edges/slots, unsupported capability versions, Auto extender fallback and
anchor/tail conflicts.

Positive tests create completed/accepted G1, author and execute G2 without a Picture,
persist/reload, continue to G3, and compare the entire mocked `/prompt` graph to the
prepared graph with only the fresh Save attempt namespace differing. They assert
exact source namespace, Load slot, Save slot, AV edges, independent dimensions,
selected H3 text and RenderSubmission provenance. No media upload is emitted for G2.

Negative tests cover no predecessor, missing/unaccepted/pending/revoked/legacy state,
model/VAE/format/mode mismatch, Selected Take mismatch/external provenance, a newer
unaccepted reroll, first-frame conflicts, incorrect strategy/relationship, another
Connection, profile/prompt switching, stale fingerprints and revocation during the
final Send recompilation. Mock provider generation and translation exercise canonical
authoring and mid-provider revocation. Live HTTP transports are forbidden in these
tests. Broader existing regressions cover normal I2VA and Ref2VA behavior.

## Validation results

- Focused production-contract, authoring, native, routing, execution, Take and dialogue
  set: **551 passed**, 2 existing warnings, in 63.33 seconds.
- Focused JavaScript continuation/configuration/preparation set: **198 passed**.
- Final `.\.venv\Scripts\python.exe -m pytest`: **2,733 passed, 3 skipped,
  2 warnings**, in 321.60 seconds. The warnings are the existing Starlette/httpx
  deprecation and denied pytest cache directory writes.
- Final `node --test tests/js/*.test.mjs`: **682 passed**, no failures or skips,
  in 43.91 seconds.
- `git diff --check`: clean (exit 0).
- Both neutral JSON examples validate through the production Profile model; the
  API example graph exactly matches the Profile example's imported graph.
- New Python regression file: **35 cases**. The two added mounted UI cases bring
  the JavaScript total from 680 to 682. No live inference/renderer transport ran.

## Required ComfyUI configuration and real-render work

Use the separate successor export described in the setup guide. Keep the initial
Picture binding Required; do not apply the successor declaration to the saved
Picture-dependent graph. Choose independent dimensions consistent with the real
source canvas, preserve actual model/codec/adapter wiring, native Load/Save, sampler,
decode, duration and trim, and declare only installed behavior that has been tested.
The neutral examples contain fictional model names and are not render-verified.

Remaining manual validation is the installed node implementation, missing/corrupt
slot failures, exact AV artifact Save/Load naming, native tensor/canvas compatibility,
head overlap/trim duration, visual identity/action/camera continuity, and any desired
audio continuity across G1→G2→G3 and rerolls. Acceptance confirms the user-verified
artifact; offline code does not read remote tensors or hash renderer model files.
Passing mocked graph/transport tests is not proof of seamless rendered continuation.

## Final Git status

The initial tree had no staged or unstaged changes. Final HEAD remains `f335737`.
All changes are uncommitted and unstaged: **19 modified tracked files and 6 new
files**. `git diff --cached` is empty and whitespace verification passes. Temporary
test logs were removed. No saved user configuration is included in the diff.

```text
 M docs/i2va-native-continuation.md
 M miniconstruct/api/routes.py
 M miniconstruct/execution.py
 M miniconstruct/generation.py
 M miniconstruct/h3/builder.py
 M miniconstruct/h3/validator.py
 M miniconstruct/models/api.py
 M miniconstruct/models/visual_handoff.py
 M miniconstruct/models/workspace.py
 M miniconstruct/native_continuation.py
 M miniconstruct/native_continuation_models.py
 M miniconstruct/project_store.py
 M miniconstruct/visual_handoff.py
 M miniconstruct/workflow_preparation.py
 M tests/js/visual-handoff.test.mjs
 M web/js/app.js
 M web/js/execution-config.js
 M web/js/visual-handoff.js
 M web/js/workflow-preparation.js
?? docs/examples/i2va-native-only-api-workflow.json
?? docs/examples/i2va-native-only-profile.json
?? docs/i2va-native-only-implementation-report.md
?? miniconstruct/i2va_contract.py
?? tests/fixtures/i2va-native-only-workflow.json
?? tests/test_picture_free_i2va.py
```
