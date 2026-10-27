# MiniConstruct behavioural benchmarks

## Scene-driven H3 shot timing (opt-in research)

The [H3 shot timing benchmark](../docs/h3-shot-timing-benchmark.md) compares exact
production Control with one benchmark-only timing-policy section in Experimental.
Five text-only cases cover dialogue/reaction, escalation, atmospheric holds,
regular rhythm and explicit user cut times across several duration/shot combinations.
Run intentionally with
`.\.venv\Scripts\python.exe -m miniconstruct.benchmark.shot_timing --profile <configured-profile-id>`.
It records sanitized matched requests, raw outputs, validator findings, descriptive
timestamp/equal-spacing evidence and human-review questions, with manual-render ZIPs.
Default is one matched seed; optional `--repeats 2` or `3` follows initial review.
Excluded from broad/default suites; fixture tests are provider-free. No production
timing policy is promoted.

## Native H3 continuation manual execution gate

Automated native-capability tests are provider-free: they validate a fictional
profile mapping, canonical lineage, frame grids, preparation and mocked Send.
They cannot measure native continuity without rendering. No LLM judge is used.

For a separately authorized manual rendering check:

1. Configure a real continuation-capable Workflow Profile and its verified model,
   mode and synchronized AV frame grids. Apply it before writing the initial prompt.
2. Send an initial generic Subject clip. G1 must consume no predecessor, have any
   Profile-declared native source edge absent from the prepared graph, and save
   its native state. Check actual workflow completion/storage, then explicitly
   confirm and accept that execution in MiniConstruct.
3. Create G2 with Continue previous shot + Auto. Verify Native in the inspector,
   the exact G1 execution identity, effective context and audio setting. Confirm
   there is no tail Video upload unless this profile expressly requires both.
4. Render the extension. Inspect the seam, body/object/camera momentum, repeated
   or missing motion, frozen starts, and sound/dialogue continuity. Verify that
   the workflow removes hidden repeated context according to its own contract.
5. Import/select the G1 rendered Take with its exact submission provenance. Switch
   G2 to Tail continuation reference, regenerate its Video-aware prompt, and
   render a comparison with the same intended action. A tail is continuation
   evidence, not a guaranteed exact overlap. Record differences by human review.
6. Reroll G1, confirm the new artifact, and select its matching Take. Re-prepare G2
   and verify the source changes. Also check an external Take safely falls back.

Retain full canonical Takes. If testing an explicit source out-point, record the
decision and compare it; never silently trim the original output. A successful
queue receipt or provider-free fixture is not a passed rendered continuity gate.

This harness records the current behaviour of MiniConstruct's real prompt-writing path before prompt-engineering changes. It assembles the normal Workspace prompt, uses the normal OpenAI-compatible client, and runs the existing structural validator. It never calls Repair Format or regenerates an invalid answer.

## Single-reference environment consistency (opt-in)

The [H3 environment consistency benchmark](../docs/h3-environment-consistency-benchmark.md) compares four treatments of one canonical anime-bedroom Picture: ordinary production, reusable Environment Subject, Subject plus explicit no-composition transfer, and the existing `storyboard_composition` control. Run `./.venv/Scripts/python.exe -m miniconstruct.benchmark.environment_consistency --profile qwen38-q5 --case bedroom-new-camera`. It retains ordinary optional vision behavior, fixed writer seed, exact request isolation, structural validation and existing self-contained manual-render ZIPs. It is excluded from broad suites. Historical matched H3 renders established strong opening single-view composition bias; later user-supplied panorama/community examples showed occasional viewpoint reconstruction. Revision 4 adds the separate `bedroom-side-view-miku` case: viewpoint-neutral partial/full original, screen-space partial original, and viewpoint-neutral partial panorama. Run `./.venv/Scripts/python.exe -m miniconstruct.benchmark.environment_consistency --profile gemma4 --case bedroom-side-view-miku`. Miku is named scene content without a character reference; panorama remains optional experimental metadata. Use one writer, then one matched renderer seed across four arms, and more seeds only if meaningful differences emerge. Structural eligibility, exact treatment compliance and matched eligibility remain separate; old case/treatments and archives are retained. No production semantic change or winning strategy is claimed. See the linked document for actual findings, exact contracts, asset provenance and review dimensions.

## Sparse intra-shot temporal anchors (provider-free packs)

The existing [H3 temporal-directing suite](../docs/h3-temporal-directing-benchmark.md) now includes the renderer-isolation case `miku-intra-shot-actions`: three deterministic final T2VA prompts, 8 seconds, one continuous Shot, 16:9, no references or dialogue. Prepare chronological-control, approximate-landmarks and exact-landmarks together with `./.venv/Scripts/python.exe -m miniconstruct.benchmark.temporal_directing --renderer-gate`. No profile/provider/render is used. Existing run paths and manual-render ZIP/manifest conventions retain exact prompts, renderer recording fields and human onset/clear-state review at 2/4/6 seconds for timestamped arms. Render three videos with one matched seed first; stop without meaningful benefit, otherwise optionally add two matched seeds (maximum nine videos). Historical writer-overlay fixtures/archives remain readable; no production promotion. Actual matched renders are pending.

The opt-in [H3 character performance benchmark](../docs/h3-character-performance-benchmark.md) uses matched Control/Experimental H3 calls through the production generation service, with only one benchmark-only system section added to Experimental. It is excluded from broad suites. Run explicitly with `.\.venv\Scripts\python.exe -m miniconstruct.benchmark.character_performance --profile <configured-profile-id>`; a vision-enabled profile is needed for the Strict Reference Sheet case. Results include sanitized model instructions, raw prompts, structural checks, human-review questions, and manual-render packs. No live provider is used during implementation tests.

## Local profile configuration

Copy `models.example.json` to `models.local.json` and edit it for your local server. `models.local.json` is ignored by Git. Keep credentials out of the file: optionally set `apiKeyEnv` to the name of an environment variable holding the key.

Profiles are ordinary local OpenAI-compatible connections. Names such as `qwen38`, `muse-glimmer`, and `gemma4` are examples only; suites never hard-code model names. Declare `supportsVision: true` only where the selected model *and endpoint* accept OpenAI-style image inputs.

`contextWindow` is optional. When set (for example `16384`), Story results calculate `promptContextUtilization` only from provider-reported `prompt_tokens`, and `totalContextUtilization` only from provider-reported `total_tokens`; either remains unavailable when the required provider measurement is absent. The production Story request path also uses a known value for conservative preflight capacity checks; unknown capacity proceeds. Character and UTF-8 byte counts are recorded separately and are never presented as token estimates.

## Commands

From the repository root:

```powershell
python -m miniconstruct.benchmark list
python -m miniconstruct.benchmark run --suite smoke --profile qwen38
python -m miniconstruct.benchmark run --case exact-dialogue-basic --profile gemma4
python -m miniconstruct.benchmark run --suite planner --profile qwen38
python -m miniconstruct.benchmark run --suite all --profile qwen38
python -m miniconstruct.benchmark run --suite planner --profile gemma4 --source-root ..\MiniConstruct-Benchmark-Source
python -m miniconstruct.benchmark compare benchmarks/runs/<older-run> benchmarks/runs/<newer-run>
```

The baseline convention is temperature `0.4`, fixed seed `1`, and one variation. These are harness settings, not application defaults. Some backends reject seed; MiniConstruct's existing unsupported-seed fallback retries without it, and the result records the shared seed contract (`accepted_by_provider`, `dropped_unsupported`, or `not_requested`) plus `unsupportedFallback`, `knownUnsupported`, and `finalRequestIncludedSeed`. An accepted seed means the provider accepted the request field, not a blanket claim of deterministic reproduction. Story results use this same contract and derive requested seed/mode from effective benchmark settings.

## Definitions and results

- `cases/<id>/workspace.json` is a serialized, real MiniConstruct `Workspace`.
- `cases/<id>/case.json` contains only benchmark metadata, requirements, deterministic checks, review questions, and optional relative `fixtureAssets` paths. Case-local fixtures resolve under the case directory; planner cases may mark a logical path as `source: external`, resolved from `--source-root` or `MINICONSTRUCT_BENCHMARK_SOURCE_ROOT`. Fixture bytes are never written to result JSON.
- A vision-required case skips explicitly when a profile does not declare vision support; it is not a model failure.
- `suites/*.json` lists case IDs.
- `runs/<run-id>/` contains `run.json`, one case JSON result per case, and `summary.md`. A visual-handoff case additionally writes a case-local `*.manual-render.zip` containing `prompt.txt`, every final effective H3 Picture/Video/Audio reference, and `manual-render-manifest.json`. The manifest maps each `<Picture N>`, `<Video N>`, or `<Audio N>` to a deterministic safe filename and identifies ordinary versus continuity-handoff evidence; it contains no source filesystem paths. Runs are local and ignored by Git.

Result schema version 1 captures Git/dirty status, public endpoint and model identity, the sanitized effective request parameters, effective textual system/user inputs, and the ordered transmitted image inputs. Each transmitted image records its decoded-byte SHA-256 and MIME type; an asset ID, filename, and fixture path are included only when that hash maps to exactly one Workspace asset. Base64/data URLs are never persisted. It also records raw model output, validator findings, seed/fallback provenance, latency, returned token usage where available, deterministic check results, skips/errors, and human-review questions.

The interactive UI uses the streaming `/api/generate-stream` path. The harness deliberately uses the shared non-streaming production generation service so it can collect one completed raw response without a web server; each run and case records `generationTransport: non_streaming`. Its prompt assembly and generation settings are covered against the streaming payload preparation. The expected transport-only differences are `stream: false` versus `true` and the streaming-only usage option.

Each case result also includes an empty `reviewNotes` field as a local template for a maintainer's observations; ratings are never required for a run to complete.

Deterministic checks intentionally stay small: `contains`, `notContains`, `regex`, `notRegex`, `requiredFindingCodes`, and `forbiddenFindingCodes`. Subjective assessment belongs in `humanReview` and is surfaced in the generated summary. No LLM judge or quality score is used.

## Planner suite

The `planner` suite measures E4 one-Generation planning through this same harness. Cases use `operation: infer_plan` or `operation: revise_plan`; `workspace.json` is the current draft and inference cases may include a committed `previous-workspace.json`. Planner metadata records Sequence Context, continuity, planning fields, revision instructions, and editable scopes.

Planner cases use canonical benchmark settings: temperature `0.4`, fixed seed `1`, one variation, reasoning off, and non-streaming. Hard checks cover exact authority, populated fields, relationship values, scoped changes, source-qualified recommendation pairs, duplicate pairs, and conservative forbidden-text checks. Subjective continuity, camera, audiovisual/reference, and unsupported-media review stays in `humanReview`. There is no LLM judge, semantic score, embedding comparison, or automatic cinematography score.

Planner Picture cases can use real external visual fixtures. The three canonical planner images live under `MiniConstruct-Benchmark-Source\assets`: `alice_station_identity.png`, `alice_station_endpoint.png`, and `station_environment.png`. No image bytes are committed to this repository. A vision-enabled profile sends resolved Picture fixtures as actual image inputs; without `supportsVision: true`, planner assembly sends no image parts. Audio and Video remain metadata-only until Media Analysis exists. Recommendation preservation across revisions remains owned by deterministic JavaScript `mergeRecommendations` tests; planner cases measure returned recommendation validity and source semantics.

Run only planner cases with `--suite planner`; run ordinary regression cases with `--suite all`. The harness does not call Repair, add automatic retries, or change planner prompts. E4 retains the production client's compatibility fallback described above.

## Story Planner suites

[Story Planner](../docs/story-planner.md) uses the same runner, results, summaries, profiles and external `FixtureAsset` resolver. `operation: build_story_plan` invokes production Build; `operation: revise_story_plan` invokes production scoped Revision; `plan_story_next`, `plan_story_insert`, and `plan_story_replan` invoke their bounded production proposal paths. Each case creates an isolated temporary saved filesystem Project using `ProjectStore` authoring helpers, then deletes that temporary store. No real user Project is used. Ordinary proposal cases do not Apply; explicit stateful long-form scenarios Apply and reload through production helpers. Stale-snapshot conflicts remain deterministic production tests.

`--suite story` retains the existing eleven normal regressions and `--suite story-stress` retains the opt-in eight-generation stress case. The focused suites are `story-smoke` (three inexpensive checks), `story-dialogue`, `story-continuity`, `story-vision`, `story-scaling`, and `story-initial-range-extended`. `story-scaling` uses the controlled 2/4/8/12 Generation counts for both legacy Build and a one-item, dialogue-only Revision; its nonblank Scene Plans are intentionally authoritative and repeated, so it measures transport/context growth rather than creative decomposition quality. `story-initial-range-extended` instead exercises the modern one-call Initial Build path at matched 4/8/12 totals. Its cases share Story Idea, Sequence Context, Creative Direction, cast/Profiles, G1 authoring/state, and settings; only `requestedTotalCount` varies. The workspaces are text-only with no assets or Pictures, even for a vision-capable profile. The intentionally blank G1 Scene Plan lets the model demonstrate decomposition. It records the normal token, context, latency, item-count, reference, and projected-memory evidence plus ordered-slot/memory-availability checks. Human review assesses coherent arc, meaningful use of extra beats, filler/rushing/duplication, later consistency, and ending quality. There is no LLM judge, repair, hidden retry, or real-model quality claim until a successful run is manually reviewed.

The former `story-creative-request-quality` fixture and its 2026-09-22 result
examined richer Workspace `creativeRequest` prose. Product testing superseded that
architecture. The retained historical fixture is not a current Story quality gate.
Future live review should follow Story Plan / Premise → ordered Scene Plans →
Generate from Story H3 and assess pacing, dialogue, arc, Character Profiles and
Generation boundaries directly. Provider-free checks establish plumbing only;
there is no LLM judge or creative-quality claim.

`story-tone-performance-controls` is a separate, opt-in two-case production-path suite for the real per-Generation Workspace `tonePerformance` controls. The scene-arc case asks for calm setup, immediate danger, and aftermath through narrative rather than slider instructions; the authority case begins with a saved non-Auto Drama value and otherwise Auto controls. Machine checks cover returned count, valid closed-domain controls, and protected non-Auto preservation. Result artifacts include `effectiveTonePerformanceControls` per returned Generation so review never has to infer settings from raw prose. Human review covers meaningful scene-driven variation, restrained Auto use, excluded-control absence, and non-duplication in Scene Plans. It adds no LLM judge, automatic retry, numerical quality score, or live-model claim. Run it manually after reviewing fixtures:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-tone-performance-controls --profile gemma4-thinking-16384
```

The dedicated `story-character-profiles` suite is a small, opt-in behavioral gate before richer Story Bible work. It remains excluded from `all` and broad/default suites and contains five cases: a matched blank-vs-guided Plan Next A/B pair; current Story Memory overriding a conflicting stable Profile fact; a long-absent returning Alice with Profile plus historical identity Picture evidence; and exclusion of an irrelevant dormant Subject Profile. Fixtures reuse `narrativeSubjects[].characterProfile`, the normal saved-Project Story paths, deterministic IDs, the external Picture resolver, and recorded request/result evidence. The control and guided cases share Story Idea, Sequence Context, starting Generation, Story Memory, Narrative Name, operation, count, settings, references, instructions, and deterministic structure; only Alice's Profile differs.

Machine checks cover operation/count, stable Subject relevance, Profile presence or exclusion in supplied context, Profile policy presence or absence, Story Memory transport, valid sourceGenerationId plus assetId pairs, historical identity selection, and transmitted image count. Human review covers natural characterization, background expertise, restrained reactions, speech/appearance use, non-parroting, current-state authority, continuity, and absence of invented dormant Subjects. No LLM judge, semantic score, embedding similarity, automatic retry, or prose-quality assertion is used. The external Picture fixture is `assets/alice_station_identity.png`; its source directory remains read-only and no image bytes or base64 are committed.

Run it manually with the reasoning-enabled local Gemma profile:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-character-profiles --profile gemma4-thinking --source-root ..\MiniConstruct-Benchmark-Source
```

Run `20260915T232035Z-a0152707` was completed and manually reviewed with `gemma4-thinking` (`HauhauCS/Gemma4-31B-QAT-Uncensored-HauhauCS-Balanced-MTP`). All five cases completed structurally and passed their deterministic machine expectations. The matched A/B showed a useful but restrained effect: control was comparatively generic cautious curiosity, while the guided plan was more deliberate and observational, including closer envelope inspection and concise dialogue such as `No name... no date.` The guided output did not mechanically recite Profile fields or turn them into exposition; the Profile influenced characterization without dominating Story progression.

The current-state authority case was a strong pass. With neutral Generation and Plan Next wording, Gemma retained Alice's current fractured-ankle/crutches state across both beats while using stable methodical, focused, observant characterization. It did not revert to unaided walking or rewrite the Profile. The returning-Subject case passed: Alice's Profile returned to the bounded request, the historical G1 identity Picture was transmitted, the correct `sourceGenerationId` + `assetId` pair was offered, and Gemma recommended the legitimate reference. The irrelevant-Subject case also passed: Bob's dormant Profile and identity evidence were excluded, Profile policy was not added for Bob, and Bob was not introduced into the beat.

One non-blocking raw-output observation remains: the guided case invented an unavailable `box_01` reference recommendation. Production sanitization discarded it, leaving `referenceRecommendations: []`; no invalid reference survived or could be applied. This is useful model-behavior evidence, not a Character Profile architecture failure.

In this single run, control used 3,002 prompt tokens and guided used 3,194, an approximately 192-token Profile overhead. Maximum total context utilization across the five cases was 8.91%; all requests stayed within the configured 600-second Story Planner timeout. Latency is not interpreted from this one run.

### Stateful long-form stress suite

`story-longform-stress` is an **expensive, opt-in, stateful** benchmark. It is deliberately excluded from `all` and every broad/default suite. It materializes one three-Generation Project, applies six sequential Plan Next batches, then applies one historical Replan and one historical Insert: approximately **8 model calls** and **22 final Generations**.

Run it manually with the reasoning-enabled Gemma profile:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-longform-stress --profile gemma4-thinking
```

The staged scenario moves through early journey, bridge approach, collapse/detour, alternate route, delivery, and Carol's return. It records per-phase raw proposal/evidence, actual apply/reload state, canonical/local context size, requested/returned slots, Picture transport, provider usage when available, context utilization when reported, latency, memory hygiene, historical reference candidates/recommendations, and a compact summary table. Missing provider token usage stays unavailable; the harness never estimates it. It makes no live-quality claim until the resulting run is reviewed. The offline regression uses a deterministic fake through the same production planning and ProjectStore Apply methods, including its partial-failure artifact path; it never calls a configured provider.

Story case JSON has a dedicated `story` section:

- `sequenceContext` and an ordered `generations` list. Each Generation has `workspacePath`, continuity (`incomingState`, `intendedOutgoingState`), `planning`, and optional `fixtureAssets`. Workspaces use production authoring-safe validation, so blanks and incomplete authoring states are allowed.
- Exactly one `build` (`idea`, `desiredGenerationCount`), modern `initialRange` (`requestedTotalCount: 2..12`), or `revision` (`draft`, `selectedItems`, `scopes`, `instruction`). Initial Range requires one saved G1 and records one provider request; Revision has no Build count. Its complete draft contains `sequenceContext` and `items`, including every authoring field and `referenceRecommendations`.
- Draft `existingGeneration`, recommendation `sourceGeneration`, revision `selectedItems`, and check `item`/`relatedItem` use one-based ordinals; new draft items have `existingGeneration: null`. The runner binds these to the saved Project's genuine UUIDs and snapshot fields. Build plan-item IDs use the production factory seam with reproducible UUIDs; revision plan-item IDs are reproducible too. No UUID or snapshot safety is weakened.
- Generation-local `fixtureAssets` use the existing `{assetId, path, source}` shape and permit the same asset ID to map to different files in different source Generations. Case-level mappings are also supported for globally unambiguous assets. Duplicate mappings within one Generation are rejected.

Real Picture fixtures use these external logical paths:

- `assets/Anime_Girl_ReferenceSheet.png`: `subject_identity`, with existing `reference_sheet` layout metadata.
- `assets/alice_station_identity.png`: `subject_identity` on Generation 1; `assets/station_environment.png`: `environment` on Generation 2.
- `assets/glider1.png` through `assets/glider6.png`: `general_visual` on one Generation, for fictional aviation planning. Metadata does not identify real people or transcribe visual answers.

These cases require `requirements.vision: true` and a profile with `supportsVision: true`; otherwise they skip before resolving assets or creating a client. With vision they submit exactly 1, 2 and 6 `image_url` parts respectively. Lower-level production assembly with vision disabled still emits zero image parts. Audio/Video assets remain metadata-only. The rainy station/platform identity cases use the retained `assets/alice_station_identity.png`; the reference-sheet case intentionally retains `assets/Anime_Girl_ReferenceSheet.png` for identity/layout evidence rather than raincoat continuity. External image bytes are read unchanged and externalized only into the temporary Project outside the repository; no image bytes or base64 are recorded in results. Runtime SHA-256 provenance is retained without adding hash pinning. The source directory is read-only and is never normalized or rewritten.

Story settings keep temperature `0.4`, fixed seed `1`, one variation, streaming off, Repair never, automatic retry never, and no LLM judge. Reasoning mode comes from the selected benchmark profile: ordinary canonical baseline profiles may use `off`, while dedicated thinking profiles may use `on` for controlled A/B runs. The recorded `effectiveRequest` is the final authority for what was sent. A benchmark-only single-attempt client stops on compatibility/seed errors instead of retrying or silently dropping the canonical seed. E4/H3 transport policy is unchanged.

Story fixture Projects use a benchmark-only `ProjectStore` subclass with UUID5 identities derived from the case ID and canonical Generation ordinal, plus the fixed UTC timestamp `2020-01-01T00:00:00.000Z`. This stabilizes the production snapshot's `baseProjectId`, `baseProjectUpdatedAt`, Generation IDs and `baseSequenceFingerprint` while leaving normal ProjectStore UUID/time behavior unchanged. Build plan-item IDs use the same case-derived deterministic seam; revision plan-item IDs are deterministic as well. The production Story Planner still reads the saved snapshot and assembles its ordinary request from it; no messages or snapshot fields are post-processed.

Hard checks evaluate counts, identity/order, authoritative exact fields, nonblank fields, closed relationships, selected revision scopes, warning endpoints/direction, valid sourceGenerationId + assetId pairs, duplicate pairs within each item, and image counts. `story_field_*` checks accept an optional `item` ordinal; omission means every item (changed/unchanged require a corresponding baseline). `story_selected_fields_only_changed` also preserves identity and Sequence Context; all recommendations remain exact because no current Story Revision scope authorizes their mutation. Separate `story_field_changed` checks require the requested edit to actually occur. Generic text expectations and conservative `forbidden_text_absent` checks are reused. Narrative coherence, feasible decomposition, camera variety, reference interpretation and unsupported claims belong in `humanReview`; there are no embeddings, aesthetic scores or LLM judges.

Results retain sanitized `rawModelOutput`, `modelInput`, `effectiveRequest`, canonical Generation IDs, reference hashes/logical paths, latency and usage. `structuredStoryPlannerOutput` is the final production `StoryPlanProposal`; revision also records `initialStoryDraft`. Revision records the raw patch response and the projected complete draft. Production rejects out-of-scope patch fields before machine checks; there is no separate parsed pre-projection capture. Raw output plus initial/final drafts provide diagnostics without modifying production parsing/projection. Raw JSON and exactly one complete JSON/unlabelled Markdown fence follow production acceptance; prose, multiple fences, malformed or non-object JSON follow production rejection. Provider and validation errors retain available raw/input diagnostics with source roots, secrets and data URLs removed.

Each completed Story case also has `storyMetrics`: operation; legacy `generationCount` (historically the canonical source-Generation count); explicit `sourceGenerationCount`; Build `desiredGenerationCount`; Initial Range `requestedTotalRange`, returned-item count, new-Generation count, ordered slots, and projected Story Memory; Revision `requestDraftItemCount`; selected Revision item/scope counts; vision mode; actual transmitted Picture count; source asset-kind counts; exact system/user/total text characters and UTF-8 bytes; full raw response characters/bytes; final API item count (`responseItemCount`, retaining its historical meaning); raw Revision patch count (`responsePatchCount`, null for Build); provider token fields (or `null` when unavailable); request duration; and optional prompt/total context utilization. A dialogue-only Revision exposes the selected change (`requestedChangedItemCount: 1`), raw model patch count (`responsePatchCount: 1`), and full projected API draft count (`responseItemCount`). Response size and completion tokens measure the raw model response, now patches for Revision. Controlled 2/4/8/12 fixtures should each return one patch. Dialogue-only Revision transmits zero Pictures with either vision capability; camera and continuity scopes use relevant visual inputs. `compare` keeps raw values visible, accepts older results with only the legacy count, no patch count, or no total utilization field, and reports unavailable values as `None` rather than inventing zeros.

Canonical live commands (run manually after reviewing the fixtures):

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story --profile gemma4 --source-root ..\MiniConstruct-Benchmark-Source
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story --profile qwen38 --source-root ..\MiniConstruct-Benchmark-Source

.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-smoke --profile qwen38 --source-root ..\MiniConstruct-Benchmark-Source
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-dialogue --profile qwen38 --source-root ..\MiniConstruct-Benchmark-Source
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-scaling --profile qwen38
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-initial-range-extended --profile gemma4-thinking
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --case story-dialogue-blank-revision --profile qwen38
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story-vision --profile gemma4 --source-root ..\MiniConstruct-Benchmark-Source
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --case story-vision-dialogue-revision --profile qwen38 --source-root ..\MiniConstruct-Benchmark-Source
.\.venv\Scripts\python.exe -m miniconstruct.benchmark compare benchmarks/runs/<older-run> benchmarks/runs/<newer-run>

$env:MINICONSTRUCT_BENCHMARK_SOURCE_ROOT = '..\MiniConstruct-Benchmark-Source'
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story --profile gemma4
```

Automated tests use temporary source roots and tiny synthetic PNGs through the real resolver and production services, with offline clients or mock transports. They never require the real external root or call a model. Runtime SHA-256 provenance is recorded in each benchmark result; no expected-hash pinning is used.

The real Ref2VA height/scale fixture is in the `core` suite and requires a profile with `supportsVision: true`.

### Generation-owned memory contract (Project v9)

Plan Next responses require `memoryPatchAfter` on every slot; the previous batch-level `memoryPatch` is rejected. The existing offline fake-provider tests exercise production validation and atomic Apply, including intermediate snapshots, one operation-wide temporary creation-ID pool per collection with sequential consumption/remapping, bounded 4/12/30/60-source context, and preserved historical Subject recall. Stored older benchmark reports remain readable; a saved ephemeral old Plan Next draft must be proposed again.

Run `python -m miniconstruct.benchmark.memory_storage` for a provider-free storage measurement at 12/30/60/100 Generations, including a separate maximum-length JSON byte ceiling. This is storage/structural validation only; it is distinct from the targeted `story-longform` behavioral suite and the opt-in stateful `story-longform-stress` scenario.

`contextWindow` describes backend capacity for preflight/utilization; it does not configure or reload the model server. Configure the server separately to the same capacity. `maxTokens` is the completion/output budget, not prompt plus completion total. A client HTTP timeout stops waiting, but a local server may continue generating when cancellation is unsupported or not carried through.

## H3 continuity bridge (opt-in)

`h3-continuity-bridge` contains `h3-continuity-wardrobe` and `h3-continuity-bedroom`. Both use small synthetic illustrated PNGs, not real observed outputs. Each fixture creates a temporary saved Project and resolves the same backend-authoritative Generation context used by production. Machine checks inspect model input plus the existing structural validation; human review checks jacket absence/identity and same-room/new-camera behavior. No LLM judge, hidden repair or automatic retry is added.

Run only when deliberately requested: `.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite h3-continuity-bridge --profile <profile>`. No live run was performed for implementation. Provider-free tests load fixtures and inspect assembly without invoking the runner/provider.


`h3-observed-continuity` is an opt-in H3 authoring suite with synthetic persisted Selected Take observations: failed planned jacket removal (keep jacket on) and explicit current Incoming State override (jacket absent). It uses ordinary saved-Project context, exact input checks and human review, with no LLM judge. It does not run video analysis or establish rendered-output consistency.
