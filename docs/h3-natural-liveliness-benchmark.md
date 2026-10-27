# H3 natural liveliness benchmark

This opt-in experiment tests what the prompt writer does when Main Prompt authority is sparse. It reuses the character-performance harness (generation, exact section isolation, authority capture, production validator, reference provenance and manual packaging). Fresh production H3 authoring now includes the conservative state-preserving Natural Liveliness policy. This suite tests its contribution through benchmark-only ablation. It is a separate suite, outside broad/default benchmark runs; existing character-performance cases and archives remain unchanged.

## Four-arm factorial design

| Variant | Real creativeControls.promptDetail | Canonical production liveliness section |
| --- | --- | --- |
| standard-control | standard | removed by ablation |
| detailed-control | detailed | removed by ablation |
| standard-experimental | standard | present once, unchanged production |
| detailed-experimental | detailed | present once, unchanged production |

Compare Standard Control with Detailed Control for Prompt Detail alone; each matching Control with Experimental for Natural Liveliness alone; and compare those treatment effects across detail levels for interaction. Every arm starts with ordinary production one-shot assembly. Control removes exactly one canonical Natural Liveliness system section inside benchmark code before the writer call; Experimental leaves production messages unchanged. No second overlay is added. Detailed is selected with the production field, not emulated with an overlay. Workspace authority, Main Prompt, canonical dialogue, references, images, duration, shots and generation settings otherwise match. Profile selection controls model, endpoint, temperature and reasoning (on or off); the existing fixed requested seed 1 and one-variation conventions apply to all arms. Effective seed and provider fallback evidence remain recorded, since seed support depends on the provider.

## Production policy and experiment revision 2

The executable policy has one source: `miniconstruct/h3/natural_liveliness.py`, title `Natural Liveliness policy`. The benchmark imports the production title/body/helper; it has no independent policy prose. It guides sparse biological cues that return immediately to the established gaze, expression, pose, and attention. Deliberate stillness takes priority; a blink closes briefly and reopens without lowered eyes, redirected attention, prolonged closure, or a new emotional beat. Closed eyes, sleep, obscured faces, and nonliving subjects remain protected. There is no fixed blink schedule or anime symbolic-expression policy.

New runs record `livelinessExperimentRevision: 2` at run/case/arm level and `livelinessTreatment: "ablation_control"` or `"production"` per arm and manual pack. Existing `livelinessOverlay` arm labels remain readable for compatibility. Historical archives are not rewritten: pre-promotion Control was production without a benchmark overlay, and Experimental used the historical benchmark overlay. Archives without revision metadata retain that historical meaning. No generic result schema change is required.

## Cases

All cases are Main Prompt / one-shot, eight seconds and one shot; no Story Planner cases.

| Case | Focus |
| --- | --- |
| neutral-visible-face | Adult in ordinary daylight, visible face, no explicit emotional direction. |
| exact-dialogue-listener | Two visible adults; Iris speaks exact canonical Workspace dialogue, Leo listens silently with reaction left unspecified. |
| implied-concern | Adult notices an empty key hook; mild uncertainty without a new major event. |
| deliberate-stillness | Held attention, restraint, minimal movement; no fidgeting or repeated gaze shifts. |
| closed-eyes-rest | Eyes deliberately closed throughout; no added blinking. |
| two-visible-characters | Quiet shared wait, no large emotional change; independent naturalness. Ref2VA with the existing neutral Reference Sheet fixture and Strict identity fidelity. |
| anime-awkwardness | Modern 2D digital anime adult waving to the wrong table; mild awkwardness, descriptive visual expression/body cues. Human prompt contains no ASCII emoticons. Symbolic marks require actual style/context support. |
| nonliving-mannequin | Optional cheap negative control: rigid painted face must not acquire biological animation. |
| anime-neutral-fixed-frame | Sparse neutral performance from the shared fixed first-frame anime portrait; no explicit liveliness cue in Main Prompt. |
| anime-deliberate-stillness-fixed-frame | Held attention and stillness from the same shared first frame, with no explicit blink instruction. |

The reference case requires a vision-enabled profile; otherwise it skips all four calls explicitly. Other cases work with vision off. The neutral reference image is a byte-identical copy of the existing benchmark asset; no new image generation was used.

## Fixed-frame anime I2VA cases

The first T2VA live-render round allowed large composition and casting variance, even with the same H3 renderer seed. The `anime-neutral-fixed-frame` and `anime-deliberate-stillness-fixed-frame` I2VA cases use the same exact first-frame anime portrait, holding the initial face and composition constant while testing whether state-preserving micro-performance guidance changes animation from that starting image. Identical first frames do not make H3 deterministic: outputs can still diverge. Manual comparisons require matched H3 renderer seeds and settings. The matched 2026-10-01 tests observed a rendering benefit for the refined candidate in the tested seed/settings, as recorded below; this does not establish universal reliability. They do not require a vision-capable prompt-writing profile: when vision is available, the ordinary production image input is sent; otherwise the normal text-only prompt-writing behavior and I2VA Workspace authority are retained.

## Machine isolation and regression checks

For each detail level, remove exactly one canonical liveliness section from Experimental's final effective system message and compare every remaining field of every provider message with Control. For each treatment level, do the same with the exact production Detailed policy section. Setup fails loudly before a provider call if zero or multiple Natural Liveliness system sections exist, or if its whole body differs from the canonical production text. Removal uses an exact section boundary and body match, never fuzzy deletion. Missing or duplicated effective sections fail isolation checks. Shared provider response-behavior instructions are retained. Comparisons use captured unsanitized final messages, including image URLs, before result sanitization. Validated Workspace dumps must match after removing only promptDetail. Arm metadata explicitly records both factors and the experiment revision/treatment.

Provider-free tests construct all 40 arms using the fake provider, verify production Experimental assembly independently, match settings/seeds/references, and exercise reasoning on and off. Deliberately changed user authority fails both overlay and Prompt Detail isolation. Direct adversarial checks also reject system changes, image changes, extra messages and duplicate/missing sections. Wrong dialogue fails the production validator in all four arms. Checks cover H3 validity, shot structure, reference legality/provenance and exact dialogue; they do not score creative quality. Extra invented speech is also a human-review concern. No diagnostic term extraction is implemented.

## Human review

Review all four exact prompts side by side. Every case includes these questions:

- Did Standard Control already feel sufficiently alive?
- Did Detailed Control improve articulation without adding unsupported plot?
- Did Experimental add useful natural micro-performance beyond each matching Control?
- Did Detailed + Experimental become redundant or over-directed?
- Were facial cues subtle and context-appropriate rather than theatrical?
- Was any character over-animated or fidgety?
- Were new emotions or dialogue invented (including speech for the silent listener)?
- Was deliberate stillness and exact performance direction preserved?
- Did a blink reopen immediately?
- Did gaze return to or remain on the same attention target?
- Did Experimental create an unsupported emotional change?
- Did deliberate stillness remain genuinely still?
- Were closed eyes and sleeping respected without added blinking?
- For multiple characters, did the prompt avoid synchronized mechanical behavior?
- For anime, were visual conventions described naturally rather than as ASCII text, with symbolic marks justified?
- After actual matched H3 renders, did H3 follow the direction usefully? Better wording alone is not proof.

A better-written prompt does not prove H3 will animate the face better. Prompt-writer tests and the matched fixed-frame render observations below support this conservative promotion, with limits specific to the tested seed/settings. Community experience suggests H3 can respond to subtle facial direction, but expression control is not perfectly reliable. This is anecdotal motivation, not a measured result: see the first-hand [micro-expression experiments](https://www.reddit.com/r/StableDiffusion/comments/1wap0rb/pushing_ai_emotions_is_possible_through/) and the [expression mismatch and prompt adjustment report](https://www.reddit.com/r/StableDiffusion/comments/1wpp6uq/please_help_preventing_minimax_h3_making_this/). Their prose and dialogue tags are not imported into this policy.

## Initial real-render observation (2026-10-01)

In the initial matched MiniMax H3 evaluation, including an anime fixed-first-frame comparison, the overlay demonstrated that explicit micro-performance guidance can induce a visible brief blink in a neutral anime character. The original policy could also over-steer deliberate stillness into lowered gaze or sustained eye closure. The experimental policy was therefore refined toward brief state-preserving biological cues. This records that evaluation only; it does not establish universal H3 behavior or production readiness.

## Refined fixed-frame observation and production promotion (2026-10-01)

Matched fixed-frame anime I2VA renders showed a brief visible blink in the refined production candidate. The neutral result returned to essentially the same gaze and expression; the deliberate-stillness result preserved the held gaze/expression without the earlier lowered-gaze or prolonged-closure failure in that tested seed/settings. Control could leave the visible face biologically frozen, and Detailed prompting alone was not a reliable substitute in these comparisons. This supports conservative conditional guidance, not universal H3 behavior. Anime symbolic-expression rules were not established by this experiment and were not promoted.

Fresh Main Prompt and Story H3 writing receive the policy exactly once. Prompt Detail remains independent. Story planning, scoped H3 Revision, Repair Format, structural validation, schemas/UI, execution fingerprints, and historical generated prompts are unchanged.

## Manual packs and opt-in execution

The shared writer creates a separate manual-render ZIP for each completed variant under ignored `benchmarks/runs/h3-natural-liveliness/<run-id>/`, plus results.json and review.md. Every pack contains the untouched generated prompt, effective reference bytes with labels/hashes, case/variant identity, explicit promptDetail, livelinessOverlay, and revision/treatment metadata when present, public model/profile identity, matched settings, mode, duration, shots, aspect ratio, creative controls and identity fidelity. The pack's promptDetail reflects its actual arm. No ComfyUI call or video rendering occurs.

For a later authorized live writer run, select an existing profile ID:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.benchmark.natural_liveliness --profile <configured-profile-id>
```

Use `--case <case-id>` to select cases and `--profiles <path>` for a different profile file. This command makes provider calls; implementation regression tests use only the fake provider. No live model run or rendering was used to implement this suite.
