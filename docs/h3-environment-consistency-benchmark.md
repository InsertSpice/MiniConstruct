# H3 single-reference environment consistency benchmark

Updated 2026-10-02. Manual matched community-versus-topology and production-gate H3
evidence now support promotion of the sparse viewpoint-neutral Environment policy
for avoiding source-camera anchoring. Exact room geometry remains model/output
dependent; Phase E stays open for that broader problem. Historical revision-3 and
revision-4 definitions and archives are retained.

## Question and boundary

Can one existing Environment Picture preserve recognizable room identity and layout
while allowing a substantially different requested camera composition? Production
use can lose room details after close-ups or bounded continuation context, while
reference Pictures can pull H3 back toward their source view. This pilot tests
stable environment identity first. It does not implement current scene state,
Take-derived spatial evidence, frame selection, multiple room views, an Environment
registry, Story Bible, new schemas or user controls.

**Prompt-writer compliance does NOT establish environment consistency.** The
benchmark is only successful when matched actual H3 renders are reviewed. This
implementation supplies plumbing and human review material, with no success claim.

## First live writer round (revision 1)

The retained local archives are:

- Qwen3.8, profile `qwen38-q5`: `20261001T164803Z-ec4c1e45`.
- Gemma4, profile `gemma4`: `20261001T165658Z-e5ae7b85`.

Both writers already defined the production room as an Environment Subject sourced
from Picture 1, described its identity/layout/furniture, and requested the lower,
mattress-height viewpoint. B and C were not strongly separated from production;
Environment-as-Subject alone is not established as fundamentally new behavior.
The original D changed only the role, but both writers still chose the requested
new viewpoint. It failed to provide useful source-composition bias as a negative
control. Qwen D even marked the room partially_preserved because the camera changed;
camera change alone must not alter the evidenced Environment Subject's retention.

All four Gemma outputs failed with `retention_subject_not_in_shot`: retention
claimed Subject 1 appeared in Shot 1, whose text omitted its literal label.
All four Qwen outputs were wrapped in a Markdown text fence and failed with
`markdown_fence` and `extraneous_preamble`. Both D outputs also retained the
`missing_task_relationship` WARNING. These failures are preserved honestly.
No first-round output was used as matched render evidence. These are prompt-writer
observations, not findings about actual H3 environment consistency.
The old archives and raw outputs are not rewritten, repaired or deleted.

## Second live Qwen writer round (revision 2)

Run `20261001T172028Z-fbd701bf`, profile `qwen38-q5`, was reviewed conservatively:

| Arm | Observed writer result |
| --- | --- |
| A | Otherwise plausible H3 body was again wrapped in a Markdown text fence; structurally ineligible. Raw output remains unchanged. |
| B | Structurally valid and materially similar in environment semantics to A, further evidence that ordinary production commonly abstracts the room as a Subject. B remains diagnostic, not automatically production. |
| C | Structurally valid, but treatment incomplete: it said Picture 1 supplies identity/layout evidence for Subject 1 while omitting the explicit denial of target-composition authority and the camera/framing/lens/perspective/composition exclusion. It is unsuitable evidence for the intended anti-composition treatment. |
| D | Structurally valid; final H3 explicitly made Picture 1 composition/framing authority and included the canonical keyframe-completion relationship. |

At that writer-round checkpoint there was no actual H3 render conclusion; later
matched render findings are recorded below. The archived revision-2
flags mean structural eligibility only; old results/manifests are not rewritten
as revision-3 compliance results. Exact revision-3 sentences differ from the old
flexible wording contract; the historical plan required a revision-3 rerun before rendering.
B appears suitable as a production-like fallback for this Qwen run after human
review, but semantic equivalence and any substitution must be recorded manually.

## Fixture and case

Suite: `h3-environment-consistency`. Case: `bedroom-new-camera`.
Canonical fixture: `benchmarks/environment-consistency/assets/anime-bedroom.png`.
One PNG, 1184 × 888 pixels, 1,424,945 bytes. SHA-256 derived from the supplied bytes:
`1d926e21e663c1b3de53ba216c16d2175b1e5735aa134d894d1f34aeb257ecbe`.
This documented observation is not a hardcoded runtime hash. Loading and running
read and fully decode the actual file, derive its hash, and compare the resolved
Workspace bytes. Packaging rechecks it against the recorded run fingerprint and
refuses a changed fixture. The source file is never edited or copied per arm.
No additional view, floor plan, hidden map or derivative fixture is created.

The source view is elevated from the doorway. Its visible geometry supports a
lower viewpoint in the aisle beside the desk on the bed's right, facing the
headboard/window rather than an unseen reverse wall. Exact shared target request:

> Show the same anime bedroom from a lower, closer viewpoint in the narrow aisle beside the right-hand desk and the right side of the bed, rather than from the elevated doorway-wide viewpoint. Place the camera near mattress height, looking diagonally across the bed toward its wooden headboard and the window with tied green curtains. Retain part of the tall bookshelves on the left and the adjacent desk, chair and lamp on the right in the view so their relationships remain judgeable. Use one continuous shot with a slow, restrained lateral camera move along the aisle, without crossing furniture. No people enter. Preserve the same bedroom identity, architecture, furniture, relative spatial relationships, materials and visible colors. Do not reveal or invent the unseen wall behind the source camera.

All arms use Ref2VA, 8 seconds, one shot, 16:9 and one variation. The same physical
fixture maps through the real numbered Reference Manifest to `<Picture 1>`.
Ref2VA does not force Picture 1 at 0.00 as an opening frame; I2VA is never used.

## Four treatments

| Treatment | Implementation and question |
| --- | --- |
| `production-control` | Ordinary production `environment` Picture role. No benchmark layer; existing Environment-only identity/layout and no-composition guidance remains intact. What does MiniConstruct already do today? |
| `environment-subject` | Same production Workspace plus one benchmark system section requesting one reusable Environment Subject in H3 `subject_definitions`, sourced from the real Picture. Preserve architecture, furniture inventory, bed/desk/bookshelf/window relationships, evidenced proportions, materials, colors and layout. No added anti-composition paragraph. This is diagnostic: does deliberate abstraction change anything beyond production? A and B being essentially identical is a valid finding. |
| `environment-subject-no-composition` | Exactly the same Subject section plus one explicit no-composition-transfer section. Candidate stronger H3-facing treatment: the FINAL `subject_definitions` must state that the numbered Picture supplies environment identity/layout evidence for the numbered Subject, not target composition authority. Preserve evidenced room content as `fully_preserved` despite the new camera. Does explicit H3-facing separation improve actual rendering? |
| `composition-anchor` | Same bytes and shared creative request; existing `storyboard_composition` role PLUS a request-local instruction requiring a FINAL standalone Picture composition anchor for Shot 1, source camera/framing/perspective authority, explicit shot/retention reference and canonical `keyframe completion` summary task. This deliberately introduces an authority conflict with the requested new camera. No first-frame role or I2VA is introduced. |

The H3 Environment Subject is generated reusable content, not a new canonical
Environment record. The experiment is motivated by Picture as concrete visual
reference evidence (potentially composition authority), versus Subject as reusable
visible content including an environment/location/background. It does not assume
Subject abstraction is the winning treatment. A/B/C already inherit production's
no-composition policy; C measures an additional explicit reinforcement alongside
Subject abstraction, not the introduction of a previously absent prohibition.
D retains the same target request, so its intentional composition authority may
conflict with the requested camera change. Human review must establish whether
the corrected concrete relationship actually causes visible source-view reuse; writer compliance alone cannot.

## Final-H3 contracts in revision 3

C requires this exact sentence verbatim, exactly once in `subject_definitions`,
using the dynamically resolved labels (shown here for the pilot):

> <Picture 1> supplies environment identity and layout evidence for <Subject 1>; it is not target composition authority, so do not preserve <Picture 1>'s camera position, framing, lens, perspective, or composition unless independently requested.

Labels come from the real numbered manifest and ordinary Subject numbering.
Surrounding prose may vary; the canonical sentence cannot be omitted, paraphrased,
duplicated, or placed only in another section. Identity-only Picture evidence stays
within the Environment Subject definition instead of becoming a concrete anchor.
The writer is instructed to include the entire sentence verbatim, including its
negative composition clause. No output post-processing inserts or normalizes it.

C retains the Environment Subject as `fully_preserved` for visibly evidenced
architecture, furniture inventory, relative layout, materials, colors and fixtures.
Camera change alone is not partial preservation; unseen geometry is not required.

D requires this exact standalone Picture relationship verbatim, exactly once in
`subject_definitions` (shown here for the pilot):

> <Picture 1> is the concrete composition and framing anchor for [Shot 1]; preserve its camera position, framing, perspective, and broad spatial presentation as target-shot authority.

Surrounding prose remains flexible. The supplied local official Ref2VA guide §2.2
permits standalone concrete composition anchors; §3 defines `keyframe completion`
for concrete frame anchors and combination using ` + ` for multiple relationships.
The current validator also expects `keyframe completion` for `storyboard_composition`.
D therefore requires a summary task prefix containing that canonical type without
duplicate types, explicit Picture usage in Shot 1, and `fully_preserved` Picture
retention whose role qualifier contains Shot 1 plus composition or framing.
For example: `<Picture 1> ([Shot 1] composition anchor): fully_preserved - ...`.
That qualifier is a deterministic role/shot bookkeeping requirement, not a score
for surrounding prose. This is composition anchoring, not a literal first frame.

B/C instruct the writer that any retained Environment Subject claimed to appear
in Shot 1 must literally appear as the same `<Subject N>` label in that shot's
`detailed_description`. D applies the same rule to any Subject it defines.
B/C/D end with a compact instruction to return H3 directly without Markdown fences,
Markdown headings or explanatory preamble, while preserving canonical H3 labels.
A receives none of these benchmark reminders: it remains untouched production.
No production validator or assembly policy is weakened to accommodate old failures.

Matched render priority after revision 3:

Prefer A (production-control) / C / D when A is a matched render candidate. If A is structurally ineligible solely for a provider-formatting defect and human review finds B materially equivalent for the environment semantics under test, B / C / D may be used instead. Record that substitution and its reason in human review; B remains diagnostic, not automatically production. Do not automate semantic equivalence or render all four.

Do not substitute B automatically, rewrite A, or render all four automatically.
B may also be rendered diagnostically when human review finds it materially distinct.
The suite records this policy in `renderPriorityNote`; the shared report writer
only displays supplied metadata and has no environment-specific knowledge.

## Matching, capture and isolation

The selected ordinary profile controls model, endpoint, reasoning and temperature.
The benchmark fixes the writer seed at 1 across all four arms. No model is hardcoded.
Normal compatibility and unsupported-seed fallback are retained and recorded with
the shared seed provenance contract; accepted seed is not a determinism guarantee.
Results also record exact effective provider parameters and compare them across
arms. Divergent fallback settings fail the matched-parameters check for review.

Every arm owns a deep copy of the same ordinary Workspace and a new GenerationRequest.
B, C and D use the existing request-local `benchmark_system_section` seam, after
normal assembly. A passes no section. No global prompt policy is patched.
An existing RecordingClient captures the final effective payload, including
production reasoning response behavior and compatibility fallbacks. Full raw
provider messages, including image data URLs, are compared in memory against
independently built ordinary production payloads. Only one exact rendered B, C or D treatment section
(including its final output reminder and resolved labels) may be removed; all remaining message fields must equal ordinary authority.
A must equal its ordinary payload exactly; D must reduce to ordinary assembly
with only its existing composition-role change after removing its exact layer. A/B/C are also compared directly.
Workspace comparison permits only D's deliberate role change and separately
compares decoded renderer-reference bytes with the actual canonical bytes.

Persisted messages sanitize image URLs into existing image provenance records;
no base64 or source absolute paths are written. Canonical fixture hashes and
writer-input hashes are distinct. Provider-free tests exercise overlapping runs
and reject deliberate image identity, unrelated user-message and treatment-layer
mutations. These isolation checks are not generated-word quality scores. Revision 3 separately
checks exact experiment-imposed C/D relationships and structural role/shot/retention
bookkeeping. It does not score creative wording, detail, beauty, furniture-noun
counts, semantic equivalence, or rendered room quality; no LLM judge is used.
Ordinary structural H3 validation still runs for all outputs, including invalid
ones; it is not changed to favor a treatment. No Repair call is made.

## Prompt-writer vision

With `supportsVision: true`, normal production assembly creates its disposable
bounded JPEG vision derivative. That derivative stays in memory and is never
saved as a repository fixture or substituted for the renderer's original PNG.
With vision disabled, the suite still runs through ordinary metadata/notes-only
assembly, sends zero images and makes no fake vision observations. The shared
request is authored camera/scene direction based on inspection of the fixture.
Each arm records vision enablement and actual transmitted image hashes.
The next live writer round should again use an ordinary vision-capable profile.

## Human generated-prompt review

- Did the prompt preserve a coherent bedroom identity?
- Did it treat the room as reusable environment content rather than a frozen backdrop?
- Did it preserve bed/desk/bookshelf/window/furniture relationships?
- Did it accidentally turn the Environment Picture into a first-frame anchor?
- Did it reproduce the source doorway composition despite the requested camera change?
- Did it obey the requested lower/closer new camera viewpoint?
- Did it invent major unsupported room architecture?
- Did the Environment Subject treatment omit important concrete room details?
- Did explicit no-composition treatment add anything useful beyond Subject abstraction alone?
- Does the composition-anchor control clearly encourage source-view reuse?
- Are A and diagnostic B materially different, or does production already provide the same room abstraction?
- Does C's final subject_definitions explicitly establish Picture identity/layout evidence without target composition authority?
- Does C retain evidenced room content as fully_preserved despite the new camera, without requiring unseen geometry?
- Does D's final prompt explicitly preserve concrete Picture composition authority, including the canonical keyframe completion task relationship?

## Human actual-render review

Prioritize matched-eligible A/C/D prompts with matched renderer settings and the
same image upload. If A fails solely on provider formatting, B/C/D is permissible
only after recording human-reviewed environmental equivalence and substitution.
Record those settings and comparative notes with the actual renders; separate
writer seed from renderer seed. Review each dimension independently:

- ROOM IDENTITY: Does this still look recognizably like the same bedroom?
- BED: Same broad size, style, location and orientation?
- DESK: Same side of room and relationship to bed/window?
- BOOKSHELVES: Retained and spatially plausible?
- WINDOW / CURTAINS: Retained in coherent relation to headboard/desk?
- MATERIAL / COLOR IDENTITY: Floor, furniture, bedding and curtains broadly preserved?
- CAMERA FREEDOM: Is the target genuinely a different composition?
- SOURCE-COMPOSITION BIAS: Did H3 simply reconstruct the doorway-wide source view?
- GEOMETRIC DRIFT: Major furniture relocation, room resizing, bed scaling or missing fixtures?

No automatic score collapses these dimensions. Review all prompts before spending
H3 render time. A single image cannot prove hidden geometry, and one scene/seed/
profile cannot establish generalization. Missing evidence must not become invented
layout authority. Multiple complementary views are optional later research.

## Results and manual render packs

The existing character-performance harness capture helper and ZIP writer are
reused. The small generic extraction exposes completed-variation evidence;
optional arm-local metadata and role projections extend the existing manifest.
No independent archive format or production policy is introduced.

`benchmarks/runs/h3-environment-consistency/<run-id>/` contains `results.json`,
`review.md` and four `<case>.<treatment>.manual-render.zip` files. Each ZIP contains:

- `prompt.txt`: exact generated H3 prompt, without repair or reformatting.
- `references/picture-1.png`: exact canonical PNG bytes, including for vision writers.
- `manual-render-manifest.json`: existing schemaVersion 1 reference mapping and
  hashes, treatment identity/description, negative-control flag, public profile/
  model identity, reasoning state, temperature, requested/effective seed and
  fallback provenance, effective request settings, Ref2VA, duration, shot count,
  aspect, ordinary creative controls, actual fixture hash and safe relative
  provenance, vision status and effective reference map/role.

Results capture Git commit/dirty provenance, timing/usage, sanitized full effective
messages, image hashes, generated prompt, structural findings, isolation checks,
prompt/render review questions, empty review-note fields, experiment revision,
render priority and pending render status. Each arm and its ZIP manifest records
`renderEligible`: true exactly when ordinary H3 validation has no ERROR findings.
Warnings remain visible and do not by themselves block structural eligibility.
Revision 3 adds independent `treatmentCompliant` and `matchedRenderEligible` flags
both to each arm and to its manual-render manifest:

- A: treatment compliant by definition, because it has no benchmark contract.
- B: a Subject definition sourced from the actual Picture must have corresponding
  retention and literal Shot 1 usage. The writer's Subject number is permitted;
  no creative wording or environmental equivalence is scored.
- C: the exact dynamically labelled sentence must occur once globally and within
  `subject_definitions`; its expected Environment Subject must be defined/sourced,
  used in Shot 1 and retained as `fully_preserved`. Downgrading retention for camera
  change fails the contract; no automated inference of downgrade reasons occurs.
- D: the exact sentence must occur once globally as a standalone Picture definition;
  the summary must include canonical `keyframe completion`, the Picture must be
  used in Shot 1, and its fully-preserved composition/framing role must be retained.
- `matchedRenderEligible = renderEligible AND treatmentCompliant`.

These are deterministic experiment contracts, not a creative-quality score or a
replacement for H3 structural validation. Treatment failure never alters output.
Request/result-local checks record named requirements and booleans; no shared
mutable checker state is used. Exact text is checked as returned without rewriting
whitespace or labels. A structurally valid incomplete C is not a matched candidate;
a fenced A remains treatment compliant but structurally/matched ineligible.
A D missing its task relationship retains the ordinary structural warning while
also failing the independent treatment contract.

`review.md` displays **Structurally render eligible**, **Treatment compliant** and
**Matched render candidate** independently, plus ordinary findings and named
experimental requirement results. Manifests also preserve the required canonical
relationship and requirement checks for clear provenance.
Packs retain completed writer arms even if structurally invalid, marked ineligible
for matched rendering. The ZIP always contains the exact provider output. Fences
are not stripped, labels are not injected, retention is not rewritten and Repair
Format is not called. Display fences in the review document safely enclose raw
fenced responses without changing them. Old archives remain unchanged. No renderer is invoked by this suite.

## CLI and validation

From the repository root, following the existing focused benchmark CLI convention:

```powershell
./.venv/Scripts/python.exe -m miniconstruct.benchmark.environment_consistency --profile qwen38-q5 --case bedroom-new-camera
```

`--profiles`, `--root` and repeated `--case` follow the other focused suites.
The profile must exist in `benchmarks/models.local.json` (or `--profiles` file).
The suite is deliberately opt-in and is not added to `all` or default suites.
Implementation/refinement tests use fake clients only, including valid A/B/C/D
outputs, the observed fenced/Subject-in-shot failures, missing D task warnings,
structural/treatment/matched eligibility, exact and missing/altered/duplicate/misplaced
contracts, camera-only retention downgrade, exact archival, dynamic labels,
request-local isolation, and generic safe-review-fence/priority-note coverage.
No live provider, H3 renderer or real ComfyUI is called during refinement.
The revision-3 plan above records the historical procedure. Actual render findings
are recorded below; revision 4 is the next controlled gate. Changes remain uncommitted.


## Actual H3 evidence from the historical case

The user reports completed matched bedroom-new-camera H3 renders. Production/current
Environment semantics already caused the writer to abstract the room as an Environment
Subject. Production control, Environment Subject and explicit no-composition treatments
still began very close to the source Picture's elevated doorway composition. The explicit
no-composition treatment changed camera trajectory somewhat but did not reliably establish
the genuinely new mattress-height side viewpoint. The deliberate composition-anchor
control visibly pulled toward / converged on the source composition. An older
character-on-bed test also strongly reused that room composition before later cutting
or transitioning away; it is supporting historical observation, not another arm here.

The scoped conclusion: H3 can distinguish stronger versus weaker composition authority,
but ordinary single-view native Picture conditioning exerted strong opening composition
bias in these renders. This does not show that wording can never overcome it, or that
production semantics are wrong. No repository-generated measurements, seed identifiers,
video files or per-render scores are invented for these user-reported findings.

## Subsequent user-supplied exploratory evidence

These are manual exploratory observations, not repository-controlled benchmark results:

- Reducing reference resolution did not meaningfully remove source-camera bias.
- Placing the ordinary reference on a large grey canvas did not meaningfully remove it.
- Drawing a desired camera position on the image did not yield a useful result.
- A generated panorama of the same bedroom gave mixed but notable success: H3 sometimes
  started from substantially different viewpoints, no single obvious reference-camera
  composition dominated every render, and results remained inconsistent.
- A community example using the original single-view bedroom opened laterally across
  the bed instead of reproducing the doorway view. Its sparse Environment definition
  explicitly allowed new viewpoints and used `partially_preserved`.

These observations show that native Ref2VA can sometimes reconstruct another viewpoint.
They do not isolate whether sparse wording, retention, panorama, seed or another factor
caused success. Panorama is optional experimental evidence, not a required user workflow.

## Revision 4: bedroom-side-view-miku

This NEW case preserves bedroom-new-camera and its historical four treatments. The CLI
retains the old case as the default and selects the new experiment explicitly with
`--case bedroom-side-view-miku`. Case-specific treatment metadata and per-arm revision
metadata retain revision 3 for the old case and revision 4 for the new case. Old one-case
registries remain loadable without a panorama; stored archives are never rewritten.
The existing writer, structural validator, capture and render-pack framework are reused.
The shared benchmark pack writer accepts an optional arm-case resolver so each ZIP
contains the appropriate canonical reference, including panorama, without a new framework.

The hypothesis is that persistent Environment identity should be viewpoint-neutral;
shot-local camera/framing belongs in `[Shot N]`, and useful topology should be expressed
through camera-independent relationships. Source-frame left/right descriptions may anchor
the source camera and must not automatically become canonical identity. There is no
established H3-specific evidence that it reliably converts screen coordinates into a
stable 3D world coordinate system. This candidate direction awaits rendered comparison.

### Exact shared target scene

> Inside the same anime bedroom, Hatsune Miku lies lengthwise on top of the bed. Start from a completely new camera position beside the LONG SIDE of the bed, approximately at mattress height. The optical axis looks nearly perpendicular across the mattress in a true lateral side view rather than from the doorway. Miku's body lies lengthwise along the bed and is visible approximately head-to-toe across the horizontal frame. The bed's long edge therefore runs broadly across the image rather than receding away from the doorway camera. Use a restrained slow push or lateral move while maintaining the side-of-bed spatial relationship. Preserve the identity of the same bedroom. Do not begin from the elevated doorway-wide composition.

All arms: native Ref2VA, 8 seconds, one shot, 16:9, one variation, no additional
image/video/audio references. Hatsune Miku is recognized sufficiently by name in the
user's H3 setup and is ordinary scene content only. Her body lying lengthwise creates
a geometric camera constraint. There is no character Subject, Picture or identity
sheet; character/facial fidelity is not an outcome or treatment variable.

### Canonical references

Both files exist and decode as PNG. Runtime metadata is derived from actual untouched
bytes (not pinned constants); the current observed values are:

| Path under benchmarks/environment-consistency | SHA-256 | Dimensions | Bytes |
| --- | --- | --- | --- |
| assets/anime-bedroom.png | `1d926e21e663c1b3de53ba216c16d2175b1e5735aa134d894d1f34aeb257ecbe` | 1184 × 888 | 1424945 |
| assets/anime-bedroom-panorama.png | `03586b630a7d8dbd8809a2b358c34a5125c41c9d528a2524bd628d4f6ddf9a4d` | 2172 × 724 | 1918647 |

Arms 1/2/3 use byte-identical original Pictures; arm 4 uses only the panorama.
Both assets are validated when loading the new case. Hash, dimensions, format and byte
size are recorded, and selected references are rechecked before packaging. Renderer
references are never resized; prompt-writer vision uses the ordinary disposable derivative.
No additional views are synthesized. `referenceRepresentation` (`perspective` or `panorama`)
is benchmark metadata only; no production panorama role, enum or UI is added.

### Exact final-H3 relationships and retention

Each relationship below must occur verbatim once within the sole Environment Subject
definition in `subject_definitions`. Labels are resolved from the real manifest and
ordinary Subject numbering (the fixture has `<Picture 1>` and `<Subject 1>`).

1. `minimal-partial-original`, original Picture, `partially_preserved`:

> <Subject 1> is the bedroom from <Picture 1>. Use <Picture 1> to establish the room's design, architecture, furniture identity, materials, colors, lighting character, anime style, and overall spatial identity. Reconstruct the same room from the camera viewpoint requested in the target shot. Source-frame screen placement is viewpoint-dependent rather than canonical target composition.

2. `minimal-full-original`, identical original and exact relationship above,
   `fully_preserved`. The request-local treatment section differs from arm 1 ONLY
   in the retention marker. Camera change remains explicitly allowed.

3. `screen-space-partial-original`, original Picture, `partially_preserved`:

> <Subject 1> is the anime bedroom environment in <Picture 1>, featuring a single bed with a white duvet and wooden headboard, tall bookshelves on the left, a desk with a chair and lamp on the right, a window with tied green curtains, and warm sunlight falling across a wooden floor.

4. `minimal-partial-panorama`, panorama Picture, `partially_preserved`, exactly
   arm 1's relationship plus:

> The panoramic source represents one coherent bedroom and supplies spatial/environment evidence, not a target-video projection. Do not reproduce its panoramic distortion, stitched layout, or any arbitrary source crop as required framing.

Minimal definitions exclude source-screen left/right, frame-left/frame-right,
center-of-image and foreground/background placement. The deterministic check conservatively
rejects these terms throughout subject_definitions; shot-local camera geometry remains
allowed. No floor plan or unsupported topology is added. The reference supplies most evidence.
Partial/full retention and reference representation are experimental authority, never
silently corrected. Arm 3 is a plausible concise previous-style definition.

### Deterministic compliance and separate structural eligibility

The new contracts require exactly one sourced Environment Subject with the exact
relationship once in its definition, the exact expected retention marker, literal
Subject usage in Shot 1, no invented Subject/Picture labels, one supplied asset,
no canonical reference Subjects, the correct reference representation, and direct
H3 output without fences/preamble. Minimal arms also require neutral definitions.
Effective payload isolation compares each request against ordinary production assembly
plus its exact local overlay; exact canonical renderer bytes are checked independently.
No global/client prompt requirements are mutated and raw provider output is never repaired.

`renderEligible` still means ordinary validation has no ERROR; `treatmentCompliant`
means every experimental contract passes; `matchedRenderEligible` is their conjunction.
A structurally valid wrong marker or missing relationship is treatment/matched ineligible.
Packaging preserves even ineligible output exactly with its flags. Human review must
also inspect failed isolation checks before rendering. No aggregate quality score is used.

### Human prompt review

- Did the prompt preserve a viewpoint-neutral room identity?
- Did it accidentally reintroduce source-image left/right placement?
- Did the target shot strongly establish a true lateral mattress-height bed view?
- Is Miku's body placement used to constrain camera geometry rather than dominate the prompt?
- Did partially_preserved vs fully_preserved materially change the writer's reconstruction freedom?
- Did panorama describe one coherent room rather than a target frame?
- Did the panorama prompt accidentally ask H3 to reproduce panoramic distortion?

### Human render review

Record comparative human notes separately for each dimension; do not evaluate character fidelity:

- OPENING VIEWPOINT: Does frame zero / opening ~0.5 s originate beside the long side of the bed?
- DOORWAY ANCHORING: Does it start near the original elevated doorway composition?
- BED GEOMETRY: Is the long edge broadly across the image in a lateral view?
- CHARACTER GEOMETRIC CONSTRAINT: Is Miku lying lengthwise consistently with the side view?
- ROOM IDENTITY: Is this recognizably the same bedroom?
- FURNITURE / ARCHITECTURE DRIFT: Does escaping the source camera destroy or relocate major features?
- REFERENCE-PROJECTION ARTIFACT: Does panorama produce obvious distortion or stitching?
- VIEWPOINT FREEDOM: Is camera geometry reconstructed rather than crop/zoom/pan around the source?

### Staged live plan and command

1. One reliable Gemma4 writer profile; all four treatments; inspect structural and
   treatment compliance and payload isolation. Qwen3.8 has a known tendency in this
   environment to fence otherwise valid output.
2. Render ONE identical fixed renderer seed across all four matched-eligible arms.
3. Only if that seed suggests meaningful differences, use additional matched seeds
   to test consistency. Writer seed and renderer seed are distinct.

Exact command from the repository root (Gemma4 profile already used in this research):

```powershell
./.venv/Scripts/python.exe -m miniconstruct.benchmark.environment_consistency --profile gemma4 --case bedroom-side-view-miku
```

This implementation runs no live provider, H3 render or real ComfyUI. Production builder,
Subject semantics, Story carry, ReferenceAsset roles, validator, Prompt Detail,
continuity, Project/Workspace schemas and UI are unchanged. Semantic-only conditioning
without VAE latent and custom Comfy transport remain future research. No winning
production strategy is selected; revision-4 rendered comparison is the next Phase E gate.

### Implementation validation report (2026-10-02)

- Focused `tests/test_benchmark_environment_consistency.py`: **75 passed**. Coverage
  includes legacy compatibility; four new arms; derived hashes and asset decode;
  perspective/panorama isolation; neutral definitions and deliberate screen-space
  wording; partial/full isolation; Miku without a reference; exact compliance;
  malformed/missing/misplaced/duplicate relationships; wrong retention; invented
  Subjects/Pictures/assets; request-local payload mutation; packaging and report output.
- Full `./.venv/Scripts/python.exe -m pytest`: **2079 passed, 1 skipped**, 222.34 s.
  Two warnings: Starlette test-client deprecation and inability to write pytest cache.
- Full `node --test tests/js/*.test.mjs`: **458 passed, 0 failed**.
- `git diff --check`: passed.
- Independent comparison against HEAD confirms the historical case object and all
  historical treatment definitions are identical. Legacy one-case fixture registries
  remain loadable without panorama; stored run archives were not changed.

Changed tracked files: `benchmarks/README.md`, `benchmarks/environment-consistency/cases.json`,
`docs/MiniConstruct-Roadmap.md`, this document, `miniconstruct/benchmark/environment_consistency.py`,
`miniconstruct/benchmark/character_performance.py`, and `tests/test_benchmark_environment_consistency.py`.
The supplied `anime-bedroom-panorama.png` was already untracked at entry and remains
untracked and unmodified. The original PNG is also unmodified.

The Roadmap records strong historical single-view composition bias, subsequent occasional
viewpoint reconstruction, and revision 4 as the next controlled Phase E gate.
Production Environment guidance/Subject semantics, Story carry, reference roles,
validator, Prompt Detail, continuity, schemas and UI are unchanged. No live provider,
H3 render, real ComfyUI, subagent or delegation was used. No commit, push or tag occurred;
all changes remain uncommitted at the original checkpoint `16a2cc0`.


## Manual matched community-versus-topology evidence (2026-10-02)

The user compared two manually authored prompts using the same original bedroom
Picture and Ref2VA workflow, 4:3, 8 seconds, renderer seed 1, 20 steps,
`res_multistep`, `simple` scheduler, video shift 12 and audio shift 3.
This is **MANUAL H3 evidence**, not an automated repository benchmark result.

The sparse community-style prompt immediately established the requested true
lateral bedside camera and reconstructed the same bedroom from a substantially
new viewpoint. Its concise Environment Subject treated Picture 1 as room design,
furniture, colors, lighting and anime-style evidence while explicitly freeing
camera angle, framing, perspective and composition. It used `partially_preserved`
for stable room identity with viewpoint reconstruction freedom, and direct Shot 1
camera language.

The topology-heavy prompt remained close to the original Picture projection and
mostly pushed inward. It specified fixed physical layout, bed axes and exhaustive
object rearrangement prohibitions. Additional attempted seeds showed the same
anchoring tendency **in previews** and were stopped by the user; those aborted
attempts are not reported as completed renders.

With the matched renderer/reference/settings this is stronger evidence than
writer-only compliance, and supports the sparse viewpoint-neutral production
candidate. It does not isolate every wording factor or establish universal success
across environments, models or seeds. Earlier exploratory panorama results remain
mixed: some new views, inconsistent outcomes and panorama-specific geometry.
Panorama remains optional experimental research; no role, UI, automatic panorama
or multi-view requirement is promoted.

## Completed production gate: viewpoint-neutral Environment promotion (2026-10-02)

The production policy in `miniconstruct/h3/builder.py` is promoted for ordinary
`environment` Pictures. Sparse viewpoint-neutral Environment Subjects treat the
Picture as stable identity/appearance evidence, avoid inferred source-screen
coordinates and speculative floor plans, and put camera geometry in the shot.
Explicit user topology, spatial relationships, blocking, Story continuity, Incoming
State and current camera intent keep their established precedence. For an explicit
substantial viewpoint reconstruction, `partially_preserved` preserves stable room
and object identity while allowing projection changes; it does not authorize
intentional room redesign. This addresses severe source-camera anchoring in the
tested gate. It does not certify exact geometry.

Both `gemma4` and `qwen38-q5` ran `bedroom-production-environment-gate` with ordinary
production assembly, no benchmark Environment overlay, original
`assets/anime-bedroom.png`, Ref2VA, 8 seconds, one shot, 4:3. Both generated a
Picture-sourced Environment Subject with `partially_preserved`, avoided the old
source-screen left/right inventory style, and directly requested a new lateral
mattress-height camera in Shot 1.

**Gemma exact writer and render:** its raw provider output was structurally valid.
The concise Picture-sourced room definition named visible furniture such as bed,
desk, bookshelf and window without assigning source-screen left/right positions.
Retention preserved room identity/materials/lighting while noting that the viewpoint
was shifted to a lateral side view. Shot 1 directly called for a true lateral view
beside the bed's long side at mattress height, horizontally across the mattress,
with Miku lengthwise and full body head-to-toe horizontally. Its rendered frame
zero began from that genuine lateral bedside viewpoint, successfully escaping the
original elevated doorway composition. The render retained the general bedroom
design but reconstructed window/desk/bookshelf relationships loosely. This passes
the source-camera anchoring objective; it is **not** a room-geometry consistency
pass.

**Qwen exact writer:** its semantic H3 body followed the same Environment policy
and requested the new lateral camera. The raw provider output was fenced in
Markdown `text`, so ordinary validation correctly reported `markdown_fence` and
`extraneous_preamble`; its exact raw result remains `renderEligible = false`.
No automatic fence stripping occurred. The user manually removed only the fence
for rendering. That edited prompt is manual visual evidence, not an exact-provider
structural pass. Its raw output conveyed the Environment treatment semantics and,
under the corrected checker described below, is conceptually
`treatmentCompliant = true` while `matchedRenderEligible = false` due to structural
failure.

The manually fence-stripped Qwen render also began from the requested true lateral
bedside view and preserved the original room topology more plausibly than Gemma:
Miku's head remained near the window/headboard end, the bookshelf toward the
opposite end, and desk/window/headboard relationships remained more recognizable.
This is materially more coherent in the reviewed render, not an exact 3D
reconstruction. These two render outcomes show that room geometry remains
model/output dependent.

The reviewed Gemma render and manually fence-stripped Qwen render shared embedded
ComfyUI settings: 928 x 672, 4:3, 8.0 seconds, 24 fps, renderer seed **4**, 20
steps, `res_multistep`, `simple`, denoise 1.0, `shift_video` 12, `shift_audio` 3,
`ref_image_size` `match`, and Picture 1 SHA-256
`1d926e21e663c1b3de53ba216c16d2175b1e5735aa134d894d1f34aeb257ecbe`. Renderer
seed 4 is separate from prompt-writer fixed seed 1.

**Benchmark diagnostic correction:** the earlier `explicit_viewpoint_freedom` check
searched only `subject_definitions` for anti-composition or camera-freedom wording.
That caused a false negative when a writer naturally communicated changed viewpoint
in retention and Shot 1. It is now named `viewpoint_reconstruction_freedom`. It
requires preserved Environment identity together with either an explicit viewpoint
shift/change/reconstruction in the Subject/retention text, or a novel viewpoint in
the current request paired with the requested lateral Shot 1 camera. Partial
retention alone, a generic mention of camera, or a camera request without preserved
room identity does not pass. No exact anti-composition sentence is required.
Ordinary H3 structural validation remains independent: Gemma is conceptually
structurally eligible, treatment compliant and matched eligible; Qwen's exact raw
output is structurally ineligible, treatment compliant and therefore not matched
eligible. Historical archive JSON remains unchanged; reruns use the corrected
checker.

The reusable opt-in production case remains profile-neutral and accepts any
configured writer profile:

```powershell
./.venv/Scripts/python.exe -m miniconstruct.benchmark.environment_consistency --profile gemma4 --case bedroom-production-environment-gate
```

The profile above reproduces Gemma; use `--profile qwen38-q5` for Qwen where
configured. The case remains a writer regression gate and does not automatically
render. Its purpose is to detect production prompt semantics. Human H3 review is
needed to assess room geometry.

This promotion validates the current prompt-authoring default for reducing severe
source-camera anchoring in the tested lateral-bedroom gate. It does not establish
exact topology, a persistent 3D Environment model, universal behavior across
writers/seeds/rooms, panorama preference, or that canonical Environment entities
will never be useful. Phase E remains in progress for rendered scene-state evidence
and model-dependent spatial consistency. Panorama and multi-view geometry remain
optional future research. No schema, role, UI, execution, Story carry or saved H3
Draft change accompanied this promotion.

## Production contract hardening after mixed-subject smoke tests (2026-10-03)

The promoted policy escaped source composition in the narrow lateral-bedroom gate.
Broader ordinary Gemma4/Qwen3.8 multi-shot authoring then exposed over-description
(bed/desk/bookshelf inventories, including inferred source-screen left/right),
Environment framing-target misuse, and retention membership without a matching
Subject label in every listed shot. The previous sparse-definition guidance and
permission to communicate viewpoint freedom elsewhere were too permissive.

Production now requires the canonical minimal definition in final
`subject_definitions`, with the actual dynamically allocated labels:

```text
<Subject N> is the environment from <Picture M>. Use <Picture M> only as a reference for <Subject N>'s environment identity, design, furniture, materials, colors, lighting, and visual style. Do NOT preserve <Picture M>'s camera angle, framing, perspective, or composition. Reconstruct <Subject N> from the camera viewpoint requested in each shot.
```

Ordinary `environment` Pictures supply visual detail directly; writers must not
append visually inferred object inventories or source-screen coordinates to
persistent identity. Explicit user-authored facts, including left/right, topology
and colors, remain authoritative in appropriate shot/state prose. OCR and vision
observations do not become user-authored facts.

Environment Subjects bind locatively as settings while explicit Main Prompt,
Camera / Shot Intent and Scene Plan camera targets retain authority. Retention
membership requires visible, identifiable environment content and an explicit
canonical Subject label in each listed shot. An extreme close-up without visible
room identity does not automatically retain the Environment in that shot.
`partially_preserved` remains the substantial-new-view default, preserving design
without locking projection or authorizing room redesign. Main Prompt and Story
share the same policy; composition/frame roles and mode anchors remain isolated.

Provider-free fake writers cover both authoring paths, vision on/off, character
close-ups, two visible shots, extreme-close-up omission, authored topology and
negative expected-output contracts for the smoke regressions. These tests verify
production instructions and fixture behavior, not real model compliance. Existing
`retention_subject_not_in_shot` validation is unchanged. No camera-target audit was
added: Picture roles are known, but Environment-to-Subject provenance and explicit
camera targets are free prose, not structured mappings; parsing them would require
heuristics. No new schema, persistence, Story carry, execution or temporal policy
was introduced. No live provider or render was used for this hardening pass.

The earlier gate outputs/checker remain historical evidence under their original
wording contract, not evidence that those outputs satisfy the new exact definition.
Phase E remains open; this change does not claim universal room consistency.

Public releases remove embedded PNG prompt/workflow/EXIF metadata during export
without changing image pixels. For the reusable `anime-bedroom.png` fixture,
historical hashes in this document describe the original development asset.
The public sanitized image has SHA-256
`933362ba96d62de38ca5c13991af95deb83fbb5d745beb08e0f98182114acc75`;
its dimensions and decoded pixels are unchanged.
