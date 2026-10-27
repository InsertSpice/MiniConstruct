# H3 character performance benchmark

This opt-in, human-reviewed suite asks whether stronger character-performance direction makes H3 prompts more alive and scene appropriate while retaining identity likeness, exact dialogue, continuity, and restraint. It produces evidence for a later decision; normal production H3 instructions are unchanged.

## Matched design

Each case runs two calls through `generate_workspace_prompts`. **Control** uses the unmodified production assembly and generation path. **Experimental** uses that same path with one benchmark-only section appended to the system message after assembly. The two calls receive one validated Workspace and the same Story authority, references, Pictures, dialogue, mode, duration, shots, Creative Controls, profile settings, and requested seed `1`. The result records the exact effective textual messages, sanitized Picture provenance, raw model output, validator findings, effective seed, usage, and latency. It does not clean or rewrite either output.

The suite is outside `all` and every broad/default suite. A case requiring vision skips before any provider call when the profile has `supportsVision: false`.

## Cases

| Case ID | Human review focus |
| --- | --- |
| `strict-neutral-reference-reaction` | Ref2VA, one neutral/expressionless multi-view Subject Identity Picture designated as a Reference Sheet, Strict Subject Identity Fidelity; stable likeness versus transient startle and recovery. |
| `story-reserved-profile-news` | Story Scene Plan plus a composed, observant, reserved Character Profile; a small unsettling discovery should shape the character's own response style. |
| `story-exact-dialogue-listener` | Two Story Subjects, one exact spoken line saved in the current Workspace and matching the current Story Generation, and a silent listener who attends to the speaker and open door without added speech. |
| `quiet-news-restraint` | Quiet negative control: silence, avoided eye contact, no outburst or camera agitation. |
| `active-alarm-movement` | Alarm reaction integrated with two moving Subjects and readable aisle geometry. |
| `one-shot-hope-to-uncertainty` | Ordinary Generate from Main Prompt, with an implied emotional transition and no Story context. |

The neutral reference sheet was generated solely as a benchmark Picture fixture. It depicts one Subject in several neutral views; its expression is not an identity requirement. The Strict case leaves production fidelity semantics intact.

## Checks and review

Machine checks cover both arms completing, the single system-section difference, Control isolation, matched seed, validator capture, shot count, legal reference labels, the normal dedicated exact Spoken Output instruction, exact dialogue under the production validator, Picture transmission with vision, fidelity evidence, and Character Profile presence evidence. The isolation check removes exactly one canonical benchmark section from Experimental's final system message and compares every remaining message field with Control. Provider-ready messages may append the ordinary response-behavior instruction after the overlay; this is shared production behavior, not a benchmark difference. The Story dialogue fixture must match its Workspace dialogue; the suite rejects a separate benchmark-only dialogue override. Invented additional dialogue remains a human-review concern. Machine checks do **not** score acting, subtlety, likeness, emotion, realism, or prompt quality. A failed structural check remains visible beside the untouched raw output.

Four earlier completed live runs are preserved unchanged as historical evidence. Their `experimental_has_overlay` failures came from checking that the overlay was the final system-message suffix even though provider preparation appends the shared response-behavior instruction afterward. Their dialogue findings also used the superseded comparison contract that counted the official leading `[Language]` metadata as spoken text. Future runs use the corrected checks; old result JSON is not rewritten.

Review each pair for concrete performance, progression through time, causal reactions, non-speaker response, Character Profile influence, Strict likeness with changing expression, unnecessary identity repetition, verbosity, unsupported emotion, the quiet case's restraint, and action readability. The review file leaves these as questions rather than numeric scores or an LLM judgment.

Each completed arm also gets an existing-style `*.manual-render.zip` with `prompt.txt`, the same effective Picture bytes and reference manifest, plus duration, shots, aspect ratio, Creative Controls, and fidelity. These are for a separately authorized manual render. The suite does not call ComfyUI or render video.

## Run manually

Configure `benchmarks/models.local.json` using the existing benchmark profile mechanism, then run one local model at a time:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.character_performance --profile qwen38
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.character_performance --profile gemma4
```

Use the actual configured profile IDs for Qwen3.8 27B and the Gemma-based writer. `--case <case-id>` selects a scenario. The Strict reference case requires a vision-enabled profile. Results are written under ignored `benchmarks/runs/h3-character-performance/<run-id>/`, with `results.json`, `review.md`, and manual-render packs. Running the command makes real local provider calls; no provider is called by the fixture tests.

The experimental section is defined in `miniconstruct/benchmark/character_performance.py` and copied verbatim into the run evidence. It has no production authority and has not been promoted into `STORY_DIRECTING_POLICY`, fidelity controls, the UI, or any schema.
