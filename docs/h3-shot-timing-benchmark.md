# H3 shot timing benchmark

`h3-shot-timing` is opt-in writer research: does the current writer default to
duration / shot count even when meaningful scene beats warrant unequal holds?
The user observed 10 seconds / four shots producing starts at 0, 2.5, 5 and 7.5
seconds. This motivates the experiment; it is not an established benchmark finding.
Official H3 omits a timestamp on Shot 1, so evidence infers its start as zero.

## Matched design and exact experimental section

Control uses the exact current `generate_workspace_prompts` production path.
Experimental uses that same service with just this benchmark-only system section
appended through its existing research seam:

```text
## Benchmark-only shot timing policy

BENCHMARK-ONLY SHOT TIMING POLICY
Choose shot boundaries from meaningful scene, action, dialogue and reaction beats
and the scene's pacing. Do not default to equal mathematical subdivision of total
duration by shot count. Unequal shot lengths are valid. Equal or near-equal spacing
remains valid when the scene genuinely calls for it. Never add arbitrary timing
jitter merely to appear irregular. Explicit user timing remains authoritative.
Preserve all existing official H3 structure, reference rules and other writer
policy; this section changes only the choice of shot boundaries.
```

Each pair shares the validated Workspace, T2VA mode, duration, shots, creative
request, canonical dialogue, empty references, Creative Controls, writer seed,
profile/model, token budget and reasoning mode. The normal response-behavior
instruction is shared. Sanitized provider-ready messages, effective settings,
profile metadata and canonical Workspace are saved. Isolation removes exactly one
canonical overlay and compares the remaining complete captured provider request.
Production compatibility fallback may make a pair fail isolation; inspect its
seed provenance before treating it as matched. An accepted seed means acceptance
of the field, not guaranteed determinism. Keys, headers and raw error bodies are
not persisted. No production template or other writer policy changes.

## Cases

| Case | Seconds / shots | Pacing question |
| --- | --- | --- |
| dialogue-reaction | 10 / 4 | Brief setup, spoken line, silent recognition, kitten reveal |
| fast-escalation | 8 / 3 | Brief trolley setup, longer recovery action, short relief |
| slow-atmosphere | 12 / 4 | Sustained mist and ripple holds around a brief dew insert |
| regular-rhythm | 8 / 4 | Comparable cup placements on a steady pulse; equal is plausible |
| explicit-timing | 10 / 3 | Opening hold until 4 seconds; door slam and cut at 6 seconds |

Simple text-only scenes avoid reference/vision confounds. The three implicit-pacing
requests specify shot count and ordered events, without directing relative shot
lengths. Their dialogue, chained physical actions and atmospheric events are the
signals from which the writer may infer pacing. No fixture supplies preferred
numerical cut times except explicit user authority. Fixture revision 2 makes this
change; schemaVersion remains 1 because the data shape is unchanged.

## First live run and fixture revision

The initial live run exercised Qwen3.8 Q5 and Gemma4 31B QAT. Qwen3.8 Control was
already non-equal for dialogue, escalation and atmosphere; Experimental modestly
shifted two cases and left one unchanged. Its regular-rhythm case remained equal,
and explicit timing was preserved. Gemma4 Control was non-equal for dialogue and
escalation, while atmosphere Control was exactly equal and Experimental became
unequal. Gemma4 Experimental produced malformed timestamp syntax in regular rhythm;
both explicit-timing arms also produced malformed timestamp syntax.

These first three fixtures contained direct relative-pacing cues, so they did not
cleanly test whether a writer infers timing from scene content without such cues.
Fixture revision 2 removes those cues while retaining the semantic beats. The
malformed Gemma4 outputs are recorded as format failures in those cases; they do
not establish that the timing policy causes format failures generally. No winner
is claimed. Existing run artifacts remain historical evidence and are untouched;
the revised fixtures apply to future runs.

## Evidence and eligibility

Provider/request success, untouched output, normal H3 validator findings,
structural eligibility, exact requested shot-count compliance and timing evidence
are separate. Failed provider arms remain recorded and the other arm is attempted.
Invalid outputs remain archived without Repair Format or regeneration. Ordinary
production compatibility fallback can retry unsupported parameters; it does not
repair or regenerate an invalid response.

Extraction reuses production `SHOT_RE`, `FIELD_RE` and literal masking, limited
to the T2VA description body. Dialogue, quoted text and other sections do not
contribute fake shots. It records emitted timestamps in integer milliseconds
(Shot 1 normally null), inferred start zero, numbered starts and completeness.
Structurally eligible complete sequences get all derived shot durations including
the final hold. Invalid/incomplete outputs retain observed timestamps but leave
duration/equal-spacing conclusions null.

Exactly equal means each duration times requested shot count equals total duration
in integer milliseconds. Approximately equal means **every duration is within
100 ms of total duration / shot count**, including the last shot. This allows
millisecond rounding of 8 / 3. The fixed tolerance is descriptive, not a quality
threshold. Equal can be artistically right; irregularity is never rewarded.
Explicit cut starts are compared exactly with no tolerance. Whether the door slam
or other action actually coincides with that cut remains a blank human-review
field, separate from numerical authority compliance.

## Human review

Every result and pack includes unanswered questions; no LLM judge is used:

1. Do cuts correspond to meaningful scene/action/dialogue beats?
2. Does Control appear to subdivide mechanically?
3. Does Experimental better fit the pacing?
4. Does Experimental introduce arbitrary or gratuitous irregularity?
5. Does each shot have enough time for its action?
6. Are shots implausibly short or unnecessarily long?
7. Does Experimental allow sensible near-equal timing in the regular case?
8. Is exact user timing preserved, including the event at the cut?
9. Is scene content otherwise materially comparable between arms?

Review writer output first. Blank reviewer, notes and preference fields are provided.
There is no automatic behavioral quality score.

## Run, repeat and optional render process

From the repository root with an existing configured profile:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.shot_timing --profile <configured-profile-id>
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.shot_timing --profile <configured-profile-id> --case fast-escalation
```

The standalone suite follows character-performance/temporal-directing convention:
its own named fixture catalog and module entry point. It is excluded from `all`,
smoke, broad/default suites, normal implementation validation and provider-free
CI. Only fixture/schema/mock tests run in pytest. These manual commands make live
writer calls; implementation does not run them.

Default: one matched seed (1) per case, ten calls for all five. Assess whether
differences are meaningful first. For stochastic ambiguity, explicitly use
`--repeats 2` or `--repeats 3`: matched seeds 1..N, at most 30 calls. These runs
include seed 1 again; keep the profile matched and inspect provenance. No automatic
repeat escalation occurs.

Results: ignored `benchmarks/runs/h3-shot-timing/<run-id>/results.json`, `review.md`
and one ZIP per case/seed/completed arm. ZIPs use the existing `prompt.txt` /
`manual-render-manifest.json` convention plus `review.md`, with exact prompt,
duration/shots/aspect, writer seed, model/profile/settings, structural findings,
timing evidence, empty references, review questions and blank renderer seed,
settings and video fields. Invalid prompts are evidence archives, not render
candidates. Rendering is neither required nor invoked. Later authorized rendering
should use one matched renderer seed and identical workflow/settings per pair;
record settings and human findings. Writer evidence establishes no renderer quality.

## Conservative promotion criterion

A separate production proposal requires matched human review across cases (and
small repeats if needed) showing more consistent meaningful cuts, reduced
inappropriate mechanical subdivision, no systematic arbitrary jitter, no degradation
of regular scenes, exact user authority preserved, and no degraded structural
compliance. One attractive example is insufficient. Numeric irregularity never
triggers promotion. No winner is declared. Main Prompt, Story → H3, Revision,
Repair, validator, Workspace/Project schemas, execution, Saved H3 Prompts and
production Roadmap remain unchanged.
