# MiniConstruct behavioural benchmarks

This harness records the current behaviour of MiniConstruct's real prompt-writing path before prompt-engineering changes. It assembles the normal Workspace prompt, uses the normal OpenAI-compatible client, and runs the existing structural validator. It never calls Repair Format or regenerates an invalid answer.

## Local profile configuration

Copy `models.example.json` to `models.local.json` and edit it for your local server. `models.local.json` is ignored by Git. Keep credentials out of the file: optionally set `apiKeyEnv` to the name of an environment variable holding the key.

Profiles are ordinary local OpenAI-compatible connections. Names such as `qwen38`, `muse-glimmer`, and `gemma4` are examples only; suites never hard-code model names. Declare `supportsVision: true` only where the selected model *and endpoint* accept OpenAI-style image inputs.

## Commands

From the repository root:

```powershell
python -m miniconstruct.benchmark list
python -m miniconstruct.benchmark run --suite smoke --profile qwen38
python -m miniconstruct.benchmark run --case exact-dialogue-basic --profile gemma4
python -m miniconstruct.benchmark run --suite planner --profile qwen38
python -m miniconstruct.benchmark run --suite all --profile qwen38
python -m miniconstruct.benchmark run --suite planner --profile gemma4 --source-root I:\AI-Models\MiniConstruct-Benchmark-Source
python -m miniconstruct.benchmark compare benchmarks/runs/<older-run> benchmarks/runs/<newer-run>
```

The baseline convention is temperature `0.4`, fixed seed `1`, and one variation. These are harness settings, not application defaults. Some backends reject seed; MiniConstruct's existing unsupported-seed fallback retries without it, and the result records `seed.status: dropped_unsupported`. An accepted seed means the provider accepted the request field, not a blanket claim of deterministic reproduction.

## Definitions and results

- `cases/<id>/workspace.json` is a serialized, real MiniConstruct `Workspace`.
- `cases/<id>/case.json` contains only benchmark metadata, requirements, deterministic checks, review questions, and optional relative `fixtureAssets` paths. Case-local fixtures resolve under the case directory; planner cases may mark a logical path as `source: external`, resolved from `--source-root` or `MINICONSTRUCT_BENCHMARK_SOURCE_ROOT`. Fixture bytes are never written to result JSON.
- A vision-required case skips explicitly when a profile does not declare vision support; it is not a model failure.
- `suites/*.json` lists case IDs.
- `runs/<run-id>/` contains `run.json`, one case JSON result per case, and `summary.md`. Runs are local and ignored by Git.

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

[Story Planner](../docs/story-planner.md) uses the same runner, results, summaries, profiles and external `FixtureAsset` resolver. `operation: build_story_plan` invokes production Build; `operation: revise_story_plan` invokes production scoped Revision. Each case creates an isolated temporary saved v4 Project using `ProjectStore` authoring helpers, then deletes that temporary store. No real user Project is used. Story Apply and stale-snapshot conflicts remain deterministic production tests, not benchmark operations.

`--suite story` contains eleven ordinary cases: four-beat decomposition, existing author authority, explicit shot transitions, a protagonist reference sheet, station references from two source Generations, six aviation references, metadata-only media honesty, source disambiguation, and three scoped revisions. `--suite story-stress` contains only `story-eight-generation-stress`. `all` includes the eleven ordinary Story cases and excludes the opt-in stress case. No separate vision suite is added; select individual visual cases with `--case`.

Story case JSON has a dedicated `story` section:

- `sequenceContext` and an ordered `generations` list. Each Generation has `workspacePath`, continuity (`incomingState`, `intendedOutgoingState`), `planning`, and optional `fixtureAssets`. Workspaces use production authoring-safe validation, so blanks and incomplete authoring states are allowed.
- Exactly one `build` (`idea`, `desiredGenerationCount`) or `revision` (`draft`, `selectedItems`, `scopes`, `instruction`). Revision has no Build count. Its complete draft contains `sequenceContext` and `items`, including every authoring field and `referenceRecommendations`.
- Draft `existingGeneration`, recommendation `sourceGeneration`, revision `selectedItems`, and check `item`/`relatedItem` use one-based ordinals; new draft items have `existingGeneration: null`. The runner binds these to the saved Project's genuine UUIDs and snapshot fields. Build plan-item IDs use the production factory seam with reproducible UUIDs; revision plan-item IDs are reproducible too. No UUID or snapshot safety is weakened.
- Generation-local `fixtureAssets` use the existing `{assetId, path, source}` shape and permit the same asset ID to map to different files in different source Generations. Case-level mappings are also supported for globally unambiguous assets. Duplicate mappings within one Generation are rejected.

Real Picture fixtures use these external logical paths:

- `assets/Anime_Girl_ReferenceSheet.png`: `subject_identity`, with existing `reference_sheet` layout metadata.
- `assets/alice_station_identity.png`: `subject_identity` on Generation 1; `assets/station_environment.png`: `environment` on Generation 2.
- `assets/glider1.png` through `assets/glider6.png`: `general_visual` on one Generation, for fictional aviation planning. Metadata does not identify real people or transcribe visual answers.

These cases require `requirements.vision: true` and a profile with `supportsVision: true`; otherwise they skip before resolving assets or creating a client. With vision they submit exactly 1, 2 and 6 `image_url` parts respectively. Lower-level production assembly with vision disabled still emits zero image parts. Audio/Video assets remain metadata-only. No new asset role, media decoding or analysis is added. External image bytes are read unchanged and externalized only into the temporary Project outside the repository; no image bytes or base64 are recorded in results. Runtime SHA-256 provenance is retained without adding hash pinning.

Story settings are temperature `0.4`, fixed seed `1`, one variation, reasoning off, streaming off, Repair never, automatic retry never, and no LLM judge. A benchmark-only single-attempt client stops on compatibility/seed errors instead of retrying or silently dropping the canonical seed. E4/H3 transport policy is unchanged.

Story fixture Projects use a benchmark-only `ProjectStore` subclass with UUID5 identities derived from the case ID and canonical Generation ordinal, plus the fixed UTC timestamp `2020-01-01T00:00:00.000Z`. This stabilizes the production snapshot's `baseProjectId`, `baseProjectUpdatedAt`, Generation IDs and `baseSequenceFingerprint` while leaving normal ProjectStore UUID/time behavior unchanged. Build plan-item IDs use the same case-derived deterministic seam; revision plan-item IDs are deterministic as well. The production Story Planner still reads the saved snapshot and assembles its ordinary request from it; no messages or snapshot fields are post-processed.

Hard checks evaluate counts, identity/order, authoritative exact fields, nonblank fields, closed relationships, selected revision scopes, warning endpoints/direction, valid sourceGenerationId + assetId pairs, duplicate pairs within each item, and image counts. `story_field_*` checks accept an optional `item` ordinal; omission means every item (changed/unchanged require a corresponding baseline). `story_selected_fields_only_changed` also preserves identity and Sequence Context; selected recommendations may be recomputed according to production policy. Separate `story_field_changed` checks require the requested edit to actually occur. Generic text expectations and conservative `forbidden_text_absent` checks are reused. Narrative coherence, feasible decomposition, camera variety, reference interpretation and unsupported claims belong in `humanReview`; there are no embeddings, aesthetic scores or LLM judges.

Results retain sanitized `rawModelOutput`, `modelInput`, `effectiveRequest`, canonical Generation IDs, reference hashes/logical paths, latency and usage. `structuredStoryPlannerOutput` is the final production `StoryPlanProposal`; revision also records `initialStoryDraft`. Hard revision checks use production's projected output, so harmless raw out-of-scope paraphrases discarded by production do not fail them. Human review separately inspects raw scope obedience. There is no separate parsed pre-projection proposal capture; raw output plus the initial and final drafts provide the diagnostics without modifying production parsing/projection. Raw JSON and exactly one complete JSON/unlabelled Markdown fence follow production acceptance; prose, multiple fences, malformed or non-object JSON follow production rejection. Provider and validation errors retain available raw/input diagnostics with source roots, secrets and data URLs removed.

Canonical live commands (run manually after reviewing the fixtures):

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story --profile gemma4 --source-root I:\AI-Models\MiniConstruct-Benchmark-Source
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story --profile qwen38 --source-root I:\AI-Models\MiniConstruct-Benchmark-Source

$env:MINICONSTRUCT_BENCHMARK_SOURCE_ROOT = 'I:\AI-Models\MiniConstruct-Benchmark-Source'
.\.venv\Scripts\python.exe -m miniconstruct.benchmark run --suite story --profile gemma4
```

Automated tests use temporary source roots and tiny synthetic PNGs through the real resolver and production services, with offline clients or mock transports. They never require the real external root or call a model. Runtime SHA-256 provenance is recorded in each benchmark result; no expected-hash pinning is used.

The real Ref2VA height/scale fixture is in the `core` suite and requires a profile with `supportsVision: true`.
