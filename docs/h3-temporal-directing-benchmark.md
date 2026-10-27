# H3 Temporal Directing Benchmark

`h3-temporal-directing` is an opt-in, human-reviewed behavioral benchmark. It tests:

> Can stronger temporal directing improve action, spatial continuity and shot progression without causing over-direction, premature plot invention or unnecessary verbosity?

It also tests an abstraction question. Generate from Story already supplies Story Plan / Premise, the current Scene Plan, ordered Scene Plans and other saved Story authority to H3. The benchmark records what authority was available so reviewers can distinguish missing planning information from weak H3 directing translation. Story fixtures can declare generic `authorityMarkers`; reports show whether each marker appears in the supplied Story authority. The Kent fixture declares its quest object as one such marker.

The five historical writer fixtures cover:

- a 13-second Story-aware Ref2VA search scene, with four ordered Scene Plans, multiple moving Subjects and three synthetic identity Pictures; the image fixture requires a vision-enabled profile and is explicitly skipped for text-only profiles;
- quiet dialogue where pauses and stillness are intentional;
- a two-person cup handoff and interaction geometry;
- foreground/background crossing paths and screen direction;
- a physical street-dance performance with an original instrumental rhythm.

The synthetic identity Pictures are local illustrated PNGs under `benchmarks/temporal-directing/assets/`. They are loaded through the existing benchmark `FixtureAsset` path resolver into canonical Workspace `subject_identity` assets, then passed through the production H3 image preparation path. Model-facing review data records Picture order, asset identity, fixture path, exact-byte digest, transformed dimensions and encoded size without storing base64 in the run report. Cases declare `requiresVision`; unsupported profiles skip the required case with a clear reason while running the other fixtures.

Story and one-shot authoring are both represented. One-shot cases receive no Story authority. Each case produces a current-production control and an experimental arm by default. The control uses `generate_workspace_prompts`, the production H3 assembly, and the current production policy. The experiment deep-copies those messages and appends one benchmark-only temporal-directing section inside the existing system message. Message roles/count, user content, reference data, and image inputs remain identical. The overlay does not enter normal Generate from Story or Generate from Main Prompt. It does not replace or edit the official MiniMax H3 guide.

The overlay explicitly ranks official H3 structure and reference requirements above its directing goals. Run metadata records the selected profile's effective model ID, temperature, token limits, reasoning mode, and vision capability. The temporal suite fixes seed mode to `fixed` and effective seed to `1`, recording source profile seed settings separately to show the override. API keys are not persisted.

No LLM judge or creative score is used. Each pair includes the actual model-facing message structure with image payloads replaced by their provenance, exact output, structural validation, authority summary, and descriptive counts for the mode-appropriate H3 description body (`integrated_multimodal_description` for base modes and `detailed_description` for Ref2VA): characters and words, shot count, duration, H3 timestamps and textual times, and trivially repeated exact appearance phrases. The control uses the production `generate_workspace_prompts` path. Provider-free regression tests compare the complete production base messages with the experimental messages after removing exactly the appended section. Story identity and Character Profile diagnostics are derived from the `characterProfiles` projection actually included in model-facing Story authority. Human reviewers assess H3 validity, story fidelity, temporal information density, static-padding burden, action progression, spatial/orientation clarity, cut continuity, camera usefulness, appropriate stillness, invention discipline, verbosity efficiency, overall preference (Control / Experimental / Tie), and a free-form note.

## Run intentionally

Use a configured local profile from `benchmarks/models.local.json`. The behavioral suite makes provider calls, so these commands are intentionally separate from provider-free pytest:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.temporal_directing --profile <profile-id>
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.temporal_directing --profile <profile-id> --case kent-gold-key-search
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.temporal_directing --profile <profile-id> --variant control
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.temporal_directing --profile <profile-id> --variant experimental
```

Run files are written under `benchmarks/runs/h3-temporal-directing/`, with `review.md` and `results.json` per run. The report leaves reviewer choices blank until a person completes them.

## Initial live review finding

The experimental H3 overlay received initial live review across Gemma 4 and Qwen3.8 and was **not promoted to production**. Effects were model-dependent: Gemma 4 changed little in the reviewed cases; Qwen3.8 sometimes added action, blocking and orientation detail, but also became more verbose or inventive in other cases. One Qwen Ref2VA output lost required Picture provenance despite the overlay's explicit structural priority. This is limited evidence from reviewed outputs, not proof that temporal directing is harmful. Production H3 behavior and official guides remain unchanged. The separate [Story Scene Plan Directability Benchmark](story-scene-directability-benchmark.md) subsequently produced limited, encouraging Gemma 4 evidence for a small upstream Scene Plan guidance change.

## Sparse intra-shot renderer-isolation gate (2026-10-03)

The historical five-case experiment tested general directability prose, action progression, spatial continuity, camera behavior and prompt-writer discipline. Its overlay permitted approximate timings when useful but did not prescribe matched intra-shot timestamp treatments. Inspection of seven local historical results found numeric timestamps accompanying later Shot headers, rather than a controlled intra-shot treatment. The descriptive timestamp counter includes cut times and textual times; it is not a timing-adherence measure. These were writer-output reviews, not actual H3 render-timing trials. Gemma 4 and Qwen3.8 results were model-dependent, sometimes verbose/inventive; one Qwen Ref2VA result had Picture-provenance trouble. The overlay remains unpromoted. Historical fixtures, commands, results and ZIP archives remain unchanged.

The new experiment extends this same suite using `renderer-case.json` and the same module, run directory, `results.json`/`review.md` convention and manual-render ZIP/manifest infrastructure. It directly authors final H3 prompts, so no fake provider or live writer is necessary. No production authoring path is involved.

### Motivating supplied evidence

A user-supplied 15-second Tartarus dance render requested official shot cuts at 00:04.800 and 00:10.300; human/video review observed cuts approximately at 4.58 s and 10.25 s. Sparse intra-shot landmarks at 00:11.800, 00:12.800, 00:13.500 and 00:14.100 broadly placed corresponding actions in the intended later sequence, without clean frame-exact pose matches. This is motivating user-supplied render evidence, not a repository-controlled temporal-anchor gate. It is consistent with soft temporal-landmark behavior; no statistical significance is claimed.

Official H3 timestamps standardize shot cuts. Intra-shot landmarks here are experimental prose, including the exact-form arm; they are not asserted to be official scheduling grammar, animation keyframes or millisecond promises.

### Primary case and isolated treatments

`miku-intra-shot-actions`: T2VA, 8 seconds, exactly one Shot, 16:9, no Pictures/Video/Audio references, no dialogue, no cuts or transitions. Hatsune Miku is named without a character reference; identity quality is not evaluated. In an uncluttered indoor studio with a fixed full-body camera, she begins standing beside a simple chair facing camera, raises her right hand and performs one clear wave, lowers the hand and turns her whole body around to face the chair, sits down, then remains seated briefly. Ordinary physically achievable motion only; no dancing, jumping, extra characters or extra props.

The shared final-prompt template uses three substitutions only:

| Treatment | Event prefixes |
| --- | --- |
| `chronological-control` | First / Then / After that; no numeric intra-shot times |
| `approximate-landmarks` | Around 00:02.000 / Around 00:04.000 / Around 00:06.000 |
| `exact-landmarks` | At 00:02.000 / At 00:04.000 / At 00:06.000 |

Opening, ending, character/content, action sentences/order, scene, camera, soundscape, music and negative constraints are identical. Provider-free tests explicitly reverse the three prefix substitutions and compare the entire prompt with the control, verify exactly three timestamp landmarks per timestamped arm, and prove exact-form equals approximate-form with only `Around` replaced by `At`. All three pass the existing H3 validator without changing it. Every prompt says `one continuous shot; no cuts; no transitions`; no `[Shot 2]` appears. One clean case is sufficient for the first gate; no second case is added.

### Prepare packs without providers or rendering

```powershell
./.venv/Scripts/python.exe -m miniconstruct.benchmark.temporal_directing --renderer-gate
```

This needs no profile and prepares all three arms together. Packs live under `benchmarks/runs/h3-temporal-directing/<run-id>/`. Each ZIP contains exact `prompt.txt`, `manual-render-manifest.json` and `review.md`; no reference media. The manifest records case/treatment, T2VA, duration, shot count, aspect, no-cut contract, event targets and blank renderer seed, workflow name/digest, model, sampler, steps, guidance, dimensions, fps, other settings, video artifact and reviewer fields. The existing reference-manifest helper is reused; no generic harness extraction is needed.

### Human timing review and staged spending

No LLM judge. Record approximate first occurrence of wave onset / clear wave, turn onset / clearly facing chair, sit onset / clearly seated, with separate onset/completion seconds and notes. Missing or unobservable events remain blank with an explanation. Timestamped arms have targets 2.0/4.0/6.0 s and separate signed observed-minus-target deltas for onset and clear state. These two observations help expose ambiguity between starting an action and reaching its state; neither is declared the sole success criterion. The chronological control has no target-time score. No automatic tolerance, pass threshold or aggregate quality score is introduced.

Review separately: all events present; correct order; temporal separation; closer placement to 2/4/6; exact versus approximate benefit; unnatural waiting/freezing; accidental cuts/transitions; teleporting; abrupt pose snapping; rushed early events; final seated state reached. Cuts, discontinuities, freezing and snapping count against a treatment even if timing improves.

After implementation review, a separately authorized first render stage uses one fixed renderer seed, all three arms and identical H3 workflow/settings: only three videos. Stop if there is no meaningful difference. Only if landmarks show plausible benefit, add two matched seeds, for a maximum initial set of nine videos. Packs are prepared; actual matched renders and human findings are pending. Implementation performs no H3 rendering or real ComfyUI execution.

### Potential later production decision, still unpromoted

If rendered evidence supports sparse landmarks, a later candidate is: Concise invents no intra-shot timestamps; Standard normally uses chronological prose and preserves explicit user timing; Detailed may add a small number of meaningful landmarks when multi-stage action/choreography/effect/state transitions materially benefit. This is a candidate only. Explicit user-authored timing remains authority regardless of benchmark outcome. This research concerns invented helpful landmarks when users supply progression without times. H3 Prompt Detail, ordinary H3 authoring, exact user timing, Natural Liveliness, Environment, Story Planner, schemas, UI, validator semantics, execution and ComfyUI are unchanged.
