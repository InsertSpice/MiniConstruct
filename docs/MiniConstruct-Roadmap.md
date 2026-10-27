# MiniConstruct Roadmap — Continuity 2.0

**Workflow Profile duplication (2026-10-26):** Duplicate opens an independent unsaved
copy in the existing editor, preserving the exact graph and all typed reusable
configuration. Save creates a fresh UUID through normal creation; Cancel/close
discards the draft. Replacement graphs keep bindings and diagnose missing targets.
Execution selection, native preferences, scoped overrides and existing submissions
remain unchanged. Provider-free route and mounted frontend regressions cover copies,
reload, independence, graph changes and mocked Prepare/Send. See
[profile setup](execution-orchestration.md#reusable-workflow-profiles-and-mode-switching).

**Workflow Profile onboarding (2026-10-21):** Saved profiles now explain management
versus execution, compatible-mode declarations and reusable Ref2VA/I2VA setup.
The single editor provides ordered setup guidance and contextual I2VA first-frame
mapping help. Explicit execution selection survives ordinary and continuity
refreshes, invalidates stale preparation and retains existing profile/scope
boundaries. Native-continuation preference remains a separate explicit action;
no persistence schema or execution pipeline changes. Mounted frontend and offline
backend regressions cover switching and required binding failures. Manual browser
usability and actual ComfyUI graph/render validation remain pending. See
[profile setup and switching](execution-orchestration.md#reusable-workflow-profiles-and-mode-switching).

**Local verified staging (2026-10-21):** Connections now optionally map ComfyUI's
actual input root to an accessible backend directory. Initial staging still uses
the upload API. Repeat Sends hash exact staged bytes locally, eliminating media
uploads and verification downloads when valid. Missing/changed files restage;
invalid/inaccessible mappings warn and retain verified HTTP staging. Safe returned
paths, child reparse/link rejection and opened-handle containment protect host
reads. Canonical authority and queue acceptance are unchanged. Five-Picture
provider-free comparisons and local/remote regressions are documented in
[Send verification performance](send-verification-performance.md#local-directory-verified-staging).
Real-world queue speed and manual setup acceptance remain to be tested.

**Execution UX update (2026-10-21):** Execution Settings now use distinct Connection
& Workflow Profile, prominent counted/collapsible Workflow Controls, Renderer Seed,
References & Routing, Prepared Workflow and Native Continuation groups. Picture-only
routing stays compact, warnings remain visible, verbose diagnostics live in the
inspector, and technical identifiers wrap. Continuation drafts and disclosure state
survive ordinary refreshes. Unknown historical submissions no longer automatically
open Live Preview or accumulate a running timer; exact Watch/Recover and background
discovery remain available. Queued jobs show waiting; only confirmed running work
has an observed-running timer, suspended on disconnect or discovery uncertainty.
Canonical terminal truth releases the observer, media and progress. Provider-free
mounted and backend regressions cover these semantics. Manual browser/layout review
remains pending. Near-instant queueing remains the separate performance item below.

**Send reliability update (2026-10-21):** payload verification now checks selected
execution authority rather than requiring the whole Project manifest to remain
unchanged. Provider-free barriers cover renderer reconciliation, submissions, imports
and metadata changes during verification, genuine execution conflicts, native lineage,
concurrent intentional Sends, renderer failures and immediate accepted receipts.
Send compiles canonical descriptors and verifies active bytes before/after uploads;
canonical Pictures avoid repeated base64 hydration and redundant hashing. Opt-in stage
timings and an offline before/after benchmark are available. Bounded staging receipts
avoid repeated uploads after full byte/SHA-256 verification on each Send.
Remote-only Connections use five verification downloads per repeat; explicitly
mapped local input roots now verify those bytes without HTTP media transfers.
No automatic prompt retries were added. The final authority
check and `/prompt` acceptance share the canonical locks; slow queue responses can
delay other mutations. **The reported 5+ second live delay still needs live stage
measurement.** See [verification and performance](send-verification-performance.md).

Continuation setup update: **2026-10-20**. **Continue from Selected Take** is
implemented for one-shot and externally imported video sources. A single guarded
Project mutation inserts and activates a fresh Generation immediately after the
exact source, with Continue previous shot, Auto handoff and the 1.5-second bounded
tail default. Compatible identity/style/environment Pictures share existing media;
observations remain predecessor evidence, with user corrections authoritative.
Accepted native execution lineage and Workflow Profile compatibility keep their
existing rules. Setup never analyzes, generates, sends, retries or accepts native
state. Provider-free regression coverage exercises creation, stale authority,
concurrent duplicate requests, reference carry and browser navigation ownership.

**Draft Continuation implemented (2026-10-20):** optional single-call drafting of
the next Creative Request uses canonical immediate-predecessor Selected Take
UUID/SHA-256, saved observations/corrections, historical Main Prompt/Story intent,
verified RenderSubmission H3 provenance and optional new user direction. It reuses
existing continuity/Story projections and provider/navigation lifecycle, adds an
editable ephemeral preview, and requires explicit local adoption and ordinary
saving. No automatic analysis, planning, H3 generation or rendering is introduced.
Provider-free tests establish assembly, authority, provenance and adoption
boundaries. **Actual LLM behavioral validation remains pending**, including
observed-versus-planned contradictions, absent observations, creative expansion,
Story and external-video cases. Live browser presentation, profile compatibility
and rendered continuity quality still require manual validation. See
[execution orchestration](execution-orchestration.md#continue-from-selected-take).

Earlier roadmap baseline: 2026-10-03, checkpoint `cd1f17f` and the saved-prompt/native-selector pass. Rendered-output continuity is the primary production priority; environment-reference research comes next, before richer Story Bible work.

## Product direction and mandatory principle

**Planned continuity is not observed truth.** Story Planner describes intended state. The selected rendered output is ultimately what happened visually. A requested outcome must never silently become proof that the render achieved it. This distinction is mandatory for long-form generation.

The complete target pipeline is:

`Story intent → Planned continuity → H3 authoring → rendered video → observed continuity → next Generation`

Phase A is complete. Phase B is complete for the manually validated production workflow. Phase C is implemented, its behavioral H3 handoff gate passed, and real video analysis/persistence was manually validated. Phase D is implemented; the native same-shot G1 → G2 live workflow/render gate passed manual validation on **2026-09-22**, while further validation of other visual strategies remains useful. Phase F's F1 exact tracking/import and F2a/F2b live observation, exact Cancel, Recover and explicit Retry are implemented with provider-free coverage; broader Sequence scheduling remains future work. Phase E Environment geometry remains open, and selective intra-shot temporal anchors are the next research priority; canonical Environment models and Phase G remain future work. MiniConstruct remains a usable standalone H3 prompt-authoring workbench, with optional Story and Execution layers around it. ComfyUI retains workflow graph authority.

## Current architecture / version snapshot

Package **0.4.0** (`pyproject.toml`, `miniconstruct.__version__`); Project schema **v17** (`project_store.PROJECT_SCHEMA_VERSION`); Workspace **v1** (`models.workspace.Workspace`); execution configuration **v7** (`execution.ExecutionConfig`); Project execution sidecar **v4** (`execution_state.ProjectExecutionState`). Older supported records adapt on read without rewriting and upgrade on their next canonical mutation. Historical version introductions below are milestones, not the current schema snapshot.

Project owns Sequence order, stable Subjects, Story authority, planning, Takes and observations. Workspace owns per-Generation H3 authoring inputs. The execution sidecar owns explicitly saved H3 prompt libraries, execution selections, routing, renderer preferences, Workflow Control values and native attempts. Machine-local configuration owns Connections and Workflow Profiles. Legacy History is preserved browser-local evidence, no longer populated or surfaced in the normal prompt UI; exact submission provenance and explicit Selected Take/native acceptance govern the rendered continuity loop.

## Completed foundation to preserve

- Filesystem Projects, atomic writes, stable Project/Generation UUIDs, ordered Sequences, legacy read-only adapters, content-addressed reference sharing. Project schema **v17**, Workspace schema **v1**.
- H3 T2VA, I2VA, FL2VA, L2VA and Ref2VA authoring, official guide assembly, exact resolved dialogue, structural validation, selective Revision and narrow Repair Format. Picture, Video and Audio labels are closed-world from the current numbered manifest. Concrete dialogue language is metadata, not spoken annotation. `continue_previous_shot` starts from the predecessor ending framing/state, with camera changes after that opening boundary. Story Creative Direction guides planning separately from per-Generation H3 Tone / Performance controls.
- H3 vision input optimization derives a disposable 768-pixel, quality-84 RGB JPEG for the prompt writer during Generate, Revision and Repair. Canonical saved Pictures retain their content identity and full execution payload for ComfyUI. The previous optimization was live validated by the user: three canonical 1536×1536 Pictures became three 768×768 prompt-writer derivatives, with 253.2 KiB aggregate encoded vision payload; first-content latency fell from roughly 450 seconds to 56 seconds, with no meaningful H3 prompt quality loss observed. These are user-reported local-backend measurements, not universal guarantees.
- Sequence Dialogue Language, source/resolved spoken text separation, cached inspectable translation and exact output validation. As Written remains the compatible default. The reviewed live behavioral gate passed in run `20260919T114957Z-fa5ecdef` (`story-dialogue-language`, `gemma4-thinking-16384`): Story-authored Japanese and manual English → Japanese resolution passed review, source and resolved text remained separate, resolver invocation/cache reuse were confirmed, resolved Japanese reached ordinary H3 authoring, and exact-dialogue validation passed.
- Story Initial Range (2–4 recommended, 5–12 Extended), terminal Plan Next (1–4), Insert Between, Replan Range, signed proposals and atomic Apply. Extended Initial Build remains one provider request. Local Story context estimates are advisory warnings; provider tokenization/failure is authoritative, with no hidden retry, reduction, or staging.
- Generation-owned Story Continuity Memory with explicit trusted / needs_review / legacy_unknown status. Planner limits remain 6,000 summary characters, 64 facts, 64 threads, 500 characters per entry and 32 Subject links; creation pools remain bounded per operation.
- Stable Sequence Cast, Narrative Identity and compatibility/recognition-only retained aliases, exact visual participation and deterministic Subject identity auto-carry. New Story/H3 authoring receives current names only; previous names are not generative vocabulary or normal UI controls, and MiniConstruct never silently rewrites historical prose to the current name. Dormant cast members do not automatically become local H3 Subjects.
- Completed Character Profiles: stable characterization, appearance defaults and speech/mannerisms, with relevance-bounded Story projection. Profiles remain separate from current state and are not a hidden ordinary H3 profile block.
- Story Creative Direction and the eight ordinary H3 Tone / Performance controls. Auto remains valid; Story Planner leaves control enums to the user and Workspace.
- Visual Style Auto now uses available Picture evidence when the prompt-writing model supports vision, preserves supported media and distinctive traits, respects Style / Appearance, Subject Identity, Environment and General Visual roles, and follows explicit user style changes. Without reliable inspectable or textual style evidence, it stays neutral. Provider-free tests cover prompt assembly across Generate, Story, Revision and Repair; an LLM behavior pass and rendered-output review remain outstanding. The policy guides the writer but cannot guarantee exact medium reproduction.
- Explicit saved prompt libraries, selected **generated H3 prompt** provenance, staleness checks, workflow preparation, reference routing and explicit ComfyUI Send. A selected generated prompt is not a Selected Take.
- Generation-owned **Saved H3 Prompts** replace the single saved draft and normal browser auto-History: multiple explicit immutable exact-text/hash snapshots in execution sidecar v4, newest first in an ordinary dropdown in the former History footprint beside Validation, with one wrapped detail preview and stable Save N numbers. Final-v4 `saveNumber`/`nextSaveNumber` never renumber/reuse deleted numbers; dedup and Save As retain them. Old v3 and provisional unnumbered v4 adapt in memory without read-time writes. Save appends/deduplicates; one matching normal-button selected-entry Load/Delete row avoids accidental replacement and confirms dirty loads. Save/Load set `activeSavedPromptId` for reopen/return restore unless a newer candidate takes precedence; active deletion clears the pointer without replacing visible text or execution selection. Restored text remains `manual_edit`, separate from execution adoption. A conservative 100-entry Generation limit rejects overflow with deletion guidance, never evicts user data. v3 single drafts migrate deterministically in memory without a read-time rewrite; actual mutation writes v4. Save As remaps libraries; new/duplicated/Story-created Generations start empty. Legacy browser History data is preserved, dormant, and not bulk-migrated.
- Compact native-attempt management replaces repeated cards with one labelled selector and one selected-attempt details/action area. Exact submission IDs join Send number, local timestamp, renderer/native status and known filename or multiple-output count. **✓ ACCEPTED** remains obvious; initial inspection chooses accepted, otherwise newest, while refresh preserves inspection. Available attempts offer Accept/Remove; accepted attempts offer Revoke (`available`, `accept=false`), retaining availability. Mark unavailable is removed from primary UX; renderer failure/cancellation still updates status. Native removal affects bookkeeping only and requires prior revocation/supersession for accepted attempts. Rendered Takes, renderer Watch/Reconnect, progress, Cancel, Recover, Retry/import and explicit native acceptance remain separate authority.
- Actual **Rendered Takes** use one inspection dropdown, one details/action area and at most one Take preview. **✓ SELECTED** remains explicit Project authority; inspection defaults to selected, otherwise newest, and survives refresh without changing observations, Story context, Visual Handoff or native state. Exact Take IDs drive actions, and submission IDs drive Send labels. Supporting **ComfyUI render history (N)** is the bottom outer disclosure, closed initially/on Generation switch with compact live/latest summary; polling preserves user disclosure choice and all existing job controls/import remain inside.
- Provider-free regression coverage and opt-in behavioral benchmarks with human review, no LLM judge.
- Story drafts remain editable ephemeral proposals: manual prose edits preserve signed authority and Apply/Revision eligibility, while real canonical Project changes still block stale actions.
- Story Planner Vision is a saved opt-in preference, off by default and effective only for image-capable models. Planner requests retain Picture metadata and reference recommendations without pixels when off; when on, relevant Pictures use disposable 768-pixel RGB JPEG model inputs. Ordinary H3 vision and Story Planner reasoning remain independent. The Sequence action **Prepare next ready Generation** runs ordinary Prepare only and never sends automatically.

## Story-aware H3 authoring

The previous richer-`creativeRequest` Story approach was superseded by product
testing. Story Planner now develops Project **Story Plan / Premise** into one
**Scene Plan** per Generation: a compact, directable dramatic bridge from Story
planning to H3 audiovisual execution. It favors meaningful observable progression
within Story scope while allowing intentional stillness; it remains planning prose,
not H3 execution syntax or a final H3 prompt. The one-shot H3 path uses Workspace
Main Prompt / Idea; the Story-aware H3 path uses saved Story authority directly.
Main Prompt / Idea is not the Story-to-H3 bridge. Build Initial Range remains one local model
request for 2–12 Generations. New slots can choose any 4–15 second duration for
their own pacing, with G1's duration as a fallback. Applied Scene Plans remain editable,
and explicit Story operations repair later continuity. No Narrative Brief or staged
Initial Build was introduced. Provider-free checks prove authority and plumbing;
creative quality is supported only by the limited live reviews recorded below, not
by provider-free checks alone.

H3 Prompt Detail offers **Concise / Standard / Detailed** for fresh **Generate from Main Prompt** only. It controls articulation of the chosen idea, independently of Creative Interpretation; it adds no plot authority, shots or duration. Standard adds no detail-policy text. **Generate from Story** reads saved Story authority and uses the upstream Scene Plan; it does not consume the one-shot detail policy. Revision and Repair retain their separate scopes.

Fresh Main Prompt and Story H3 authoring receive conservative **Natural Liveliness** exactly once: sparse biological cues preserve established gaze, expression, pose and attention, with deliberate stillness and stronger user/Story/reference authority taking priority. It is excluded from Revision and Repair. Matched fixed-frame anime render testing on **2026-10-01** supported production promotion of the refined state-preserving policy; this is limited evidence for the tested seed/settings, not a universal animation guarantee.

Story planning is a completed foundation: Project **Story Plan / Premise** feeds Generation-owned **Scene Plans**, Build Initial Range, terminal Plan Next, Insert Between, Replan Range and scoped Revision. Signed proposals remain ephemeral until atomic Apply; Story Memory, Character Profiles and Story Creative Direction retain their separate factual, characterization and presentation roles. Reasoning stays independently configurable, separate from Planning Style and vision.

Project **Planning Style** independently selects **Planning Creativity** (Faithful / Balanced / Exploratory) and **Scene Plan Detail** (Concise / Standard / Detailed). Balanced + Standard adds no style-policy text. Style affects Story planning; H3 Prompt Detail affects one-shot final H3 authoring. Style cannot broaden Revision scope, protected continuity, exact dialogue or media authority. Live prompt-writer comparisons on Gemma4 and Qwen3.8 found Scene Plan Detail generally increased concreteness/directability, though not simply output length; Exploratory added some local invention with model/seed-dependent strength. The two axes were useful enough to retain as designed. Reasoning materially changed decomposition and is not directly comparable to the matched reasoning-off quartet. This is limited prompt-writer quality evidence, not H3 render evidence or a universal model-quality claim.

Story authors per-Generation shot count and ordered Shot Plan / Camera Intent; Incoming State anchors the first shot and Intended Outgoing State the last. Apply synchronizes H3 Setup Shots, while later manual H3 overrides remain authoritative and mismatches are advisory. Operation-local Auto / Prefer N / Exact N shot guidance is signed proposal authority, not a persisted total-shot budget. Story does not set exact H3 timestamps.

For operations that propose `participatingSubjectIds`, the request-local response schema, parser and Apply checks constrain ordered unique IDs to the operation's **exact canonical Subject allowlist**. Names, labels, invented IDs and canonical IDs outside that operation's allowlist are invalid; an empty allowlist requires `[]`. Story Planner cannot create Subjects. Prose may mention unregistered characters without granting them canonical participation. Dormant cast relevance and actual visual participation remain distinct.

## Phase A — Continuity 2.0: Planned → H3

**Status: COMPLETE.**

Reviewed live behavioral gate: `20260919T143402Z-de1819fd`, `h3-continuity-bridge`, `gemma4-thinking-16384`. Preserve this completed behavior.

Saved Planned Continuity now enters ordinary H3 authoring as a separate backend-resolved context. The browser sends the Project ID and stable active Generation UUID; it does not concatenate continuity into Creative Request. Older callers without a Generation ID resolve the saved active UUID. Standalone generation has no invented Sequence context.

| Input | H3 authoring contract |
| --- | --- |
| Sequence Context | Stable world/sequence guidance; preserve relevant constraints without forcing every fact into each shot. |
| Incoming State | Hard start-state authority unless explicit current authoring requests a change/reset. |
| Intended Outgoing State | Desired end state; author a plausible transition, never assume it at frame zero. |
| Relationship to Previous | Same-shot momentum, same-scene/new-camera, evidence-dependent exact endpoint, or independent semantics. G1 has no predecessor. |
| Camera / Shot Intent | Shot-authoring authority, subordinate to exact H3 mode frame anchors. |
| Story Memory projection | Trusted immediate-predecessor background: at most 1,500 summary characters and eight durable facts; no Open Threads, current after-state or untrusted snapshots. Independent omits predecessor memory. |

Current visible-state precedence is:

`explicit current authoring / Incoming State > predecessor user correction > predecessor model observation > predecessor Intended Outgoing State > trusted Story Memory > profile/reference defaults`

Identity evidence says who the Subject is. Current continuity says what is true now. A removed jacket stays removed despite older identity Pictures or stable profile defaults; neither source is mutated.

Environment-role Pictures define location, architecture, layout, persistent furniture/fixtures, materials and relevant broad lighting/palette. They do not automatically define first-frame composition, camera position/height, lens/framing or Subject placement. Explicit composition/anchor roles may constrain those. These are prompt semantics, not a guarantee that reference-camera bias disappears.

Story Apply deterministically shares immediate-predecessor Environment-only Pictures into new same-scene slots for `continue_previous_shot`, `new_shot_preserve_continuity` and `match_previous_ending_frame`. Distinct views survive; equivalent payload/notes/options evidence is deduplicated. Explicit recommendations are materialized first. Independent receives no automatic environment carry. Composition/frame anchors are excluded from this rule. Initial Range, Plan Next, Insert and legacy Build use it; existing Replan targets keep their authority.

The existing fingerprint now includes the exact supplied planned-continuity projection; reference sets already participate through Workspace provenance. Selected generated prompts become stale when relevant inputs change. History remains immutable. Omitted Open Threads, unprojected facts and unrelated distant metadata do not create new H3 dependencies.

Compact nonblocking warnings identify missing endpoint evidence, omitted untrusted predecessor memory and same-scene authoring without usable visual references in the selected mode. Phase A itself did not watch or analyze output; explicit analysis is now provided by Phase C.

The **h3-continuity-bridge** live behavioral gate passed human review in the run recorded above. Offline fixture/model-input checks alone do not establish model compliance or rendered consistency. Phase B does not change H3 authoring and does not require another model-facing benchmark. See [Story Planner](story-planner.md#planned-continuity-to-h3) for precise projection and evidence rules.

## Phase B — Generation Output Association / Selected Take

**Status: COMPLETE for the user’s manually validated workflow.**

Phase B introduced Project schema v13 to store Generation-owned `renderSubmissions`, `takes` and one nullable `selectedTakeId`. Older Projects read without mutation; the next canonical write upgrades them. Workspace stays v1. A submission is an accepted job, a Take is an actual video, and selection is always explicit. Neither a newest job nor a newest video is authority.

Immutable prompt snapshots preserve exact prompt text/hash, authoring fingerprint, known H3 mode, real execution-selection identity/time and reference-map provenance. Provenance-preserving History uses normal exact-fingerprint selection when it matches the current saved Generation. Legacy or stale History can be explicitly adopted as exact text; adoption binds it to the current snapshot and does not reconstruct its original provenance. Manually edited H3 text ends model-output provenance but remains usable through explicit adoption or Prepare. The execution sidecar distinguishes `generated_prompt`, `adopted_history` and `manual_edit`; browser History IDs and stale reference maps are never execution authority. H3 Format Audit findings are advisory; stale provenance is reported without blocking Prepare. Prepare selects the current visible candidate when present. Workflow and transport impossibilities can still block Prepare or Send. Explicit imports use a recorded submission, the selected execution prompt, or unknown provenance. Successful Send captures its prepared prompt and actual accepted Comfy ID. No render seed is inferred from the opaque graph.

Raw video streams into temporary storage and is atomically published at `outputs/media/<sha256>.<safe-extension>`. Multiple Take records may share a blob. Generation UUIDs own outputs regardless of order; Duplicate/new/inserted/Story-created Generations start without outputs. Save preserves outputs; Save As preserves all output records and independently usable media through hard links or bounded copying. Active-Workspace portable exports exclude filesystem outputs. Unreferenced blobs are pruned on Take/Generation deletion. Missing files retain metadata and accept only matching-SHA-256 recovery.

Rendered Takes sits beside the Generation execution workflow, with explicit provenance, a compact list, one expanded preview, explicit Select/Clear/Delete and missing-media Reattach. Selection performs no analysis and does not rewrite Story Memory or Planned Continuity. Phase C now lets the selected observation change dependent later H3 fingerprints; the source Generation’s own prompt remains historical. Deterministic storage/API tests, mounted frontend state tests and mocked Comfy transport tests form the acceptance gate. User validation covered manual H3 MP4 imports, preview, multiple Takes, explicit Selected Take, Generation switching and persistence. The external_unknown workflow works without Send to ComfyUI; built-in Send was outside that Phase B manual gate and was subsequently validated by the dated native/F1 gates below.

## Phase C — Observed End State / Textual Continuity

**Status: implemented; behavioral H3 handoff gate passed; real video analysis/persistence manually validated.**

Project v14 persists separate per-Take hash-bound observations, preserving raw model and effective user-corrected state. An explicit vision action derives the actual final frame and bounded final-two-second contact sheet locally. All provenance modes, including external_unknown, are fully analyzable without a prompt or ComfyUI submission. No automatic selection-triggered inference occurs.

Current-start visual authority is `explicit current authoring / Incoming State > predecessor user correction > predecessor machine observation > predecessor Intended Outgoing State > trusted Story Memory > profile/reference defaults`. Only the selected Take's matching observation enters same-scene H3 context and bounded Story Planner projections. Effective changes stale dependent H3 prompts through existing fingerprints; independent shots and source-Generation historical prompts remain unaffected. Story Memory does not auto-mutate.

The UI supports evidence review, structured correction, original-model inspection, reset and confirmed re-analysis. Save/restore controls show local dirty, success and failure status and do not rewrite Planned Continuity or Story Memory. Recognition now considers a bounded historical Subject pool and up to four current-or-prior identity Pictures without materializing them into the analyzed Generation or treating merely offered candidates as dependencies. Cache regeneration, Save As, exact-byte recovery and deletion preserve ownership. Deterministic decoding/persistence tests, mocked vision tests and mounted UI tests accompany an opt-in `h3-observed-continuity` behavioral suite. The reviewed live `h3-observed-continuity` gate passed. Real browser validation also confirmed a new-shot successor with blank Incoming State can use predecessor observation without Story Planner. Phase C supplies structured textual evidence; Phase D independently adds actual media.

## Phase D — Relationship-driven Visual Handoff

**Status: implemented; initial rendered behavior reviewed, with further production validation useful.**

| Relationship (Auto) | Implemented handoff |
| --- | --- |
| continue_previous_shot | Compatible confirmed native H3 state when the active profile supports it; otherwise Video-only bounded tail continuation reference; default 1.5 seconds, configurable 0.5–2.0 seconds. |
| new_shot_preserve_continuity | Actual endpoint opening anchor, then an immediate new shot; default requested cut at 100 ms, range 0–500 ms. Initial rendered review found a very early cut, with broader validation still useful. |
| match_previous_ending_frame | Actual endpoint Picture as a strong opening anchor; no mandatory cut. |
| independent | No automatic predecessor visual evidence. |

Production constraint: normal continuation tails are **about 1–2 seconds**, default **1.5 seconds**, never automatically above two seconds. Full Selected Takes remain historical authority and are **not normally passed to H3**. Longer Video references materially increase H3 generation time. Shorter Takes use available duration. Tails contain no predecessor audio. A rendered Ref2VA test showed that the supplied tail is not reproduced as an exact target prefix; the strategy is therefore continuation-from-end reference semantics, not seamless overlap. Native same-shot continuation is now implemented and live validated through the optional execution capability below; this does not establish seamless overlap for generic tail references or validate every Phase D strategy.

Project v15 introduced per-Generation Auto/None/endpoint/endpoint-then-cut/tail-reference policy; Workspace stays v1. New/manual/Story Generations default to Auto; Duplicate preserves policy without outputs. Story Planner still chooses only Relationship to Previous. Take UUID/SHA and immediate Sequence predecessor are authority. Observation/vision analysis and prompt provenance are not prerequisites. Derived media is disposable Project cache and enters H3 as typed transient evidence, without adding normal Reference Assets or changing identity, observations, Planned Continuity or Story Memory.

The compiler, Input Map, H3 writer policy, fingerprints, Workflow Profile preparation and upload path use the same projection. Ref2VA accepts both artifacts; I2VA/FL2VA can accept endpoint evidence in a free first-frame slot. Auto incompatible/missing handoff warns and retains textual continuity; explicit visual choices block. Manually chosen modes and existing frame anchors remain authoritative. Automatic Story/Ref2VA mode selection is a possible later UX improvement. Explicit current resets outrank predecessor pixels.

Provider-free media/resolver/mode/provenance/lifecycle/UI and mocked transport tests accompany the opt-in **`h3-visual-handoff`** suite. Its generic tail-reference, early-cut, endpoint and independent cases export self-contained manual-render ZIPs, with no LLM judge. Initial rendered testing found endpoint → early new shot promising: a requested 100 ms cut occurred very early but not frame-exactly. It also disproved tail-prefix duplication, so the generic strategy is continuation-from-end only. Phase D itself adds no full-Take automatic reference, native audio handoff, final Sequence assembly, renderer polling or hidden submission. The optional native execution capability below extends the handoff hierarchy.

## Native H3 continuation — optional execution capability

**Implemented; real G1 → G2 native workflow/render gate passed manual validation on 2026-09-22.**

An accepted predecessor native state remains eligible across Workflow Profile graph
fingerprint drift when the current Profile provides usable native Load mappings.
MiniConstruct warns about the drift, uses the accepted execution's artifact namespace
and slot, and builds current wiring from the active Profile. A production-shaped
mocked Send regression verifies exact accepted-state consumption and preserves the
current Profile's Load mapping.

G1 used a native-capable Ref2VA profile, consumed no predecessor, correctly suppressed the native Load edge, completed its ComfyUI render and saved native state; the user explicitly accepted that execution. G2 used Continue previous shot, Auto handoff and the same profile, resolved that exact accepted G1 execution, prepared native continuation with 22 context frames, and rendered successfully through MiniConstruct Send. More than one live G2 continuation test succeeded. The concrete pure-latent integration is a tested example; core remains package-agnostic and does not depend on its custom-node implementation.

The active Workflow Profile may declare versioned native H3 AV continuation mappings. Auto same-shot continuation prefers compatible confirmed predecessor execution state, then the bounded Ref2VA tail, then Observed/Planned text. Endpoint handoff remains appropriate for a state-preserving new shot. Native state is never a Workspace asset, Picture, Story Memory, observation or planning record; Story Planner gains no new relationship.

Execution-side records preserve independent attempts, server-generated opaque keys, Project/Generation UUIDs, workflow compatibility and RenderSubmission provenance. Queue acceptance records pending state. F1 reconciles exact renderer completion to make only its associated pending native execution available; failure or cancellation makes it unavailable. Availability is lifecycle eligibility and does not independently verify external Save artifact storage. Acceptance remains explicit, and user-marked unavailable attempts stay unavailable. With a known Selected Take, only its submission’s accepted artifact qualifies; unknown/external Takes fall back. With no selected Take, explicit accepted execution is authority. Reorder resolves the immediate predecessor again, and pending rerolls never choose themselves or displace accepted state by chronology.

Generation-owned renderer seed preferences now provide Workflow default, Random each Send and Fixed. Controlled modes require one explicit `renderer_seed` Profile binding; the prompt-writing LLM Seed remains separate. The submitted controlled seed is retained on RenderSubmission. Native Render attempts can be removed individually, or unavailable non-accepted attempts cleared in bulk, without deleting renderer jobs, RenderSubmissions, Takes, video media or external Comfy latent files. Accepted attempts must be revoked or superseded before removal.

Profiles own frame grids, synchronized audio support, optional source out-points and permitted combined conditioning. G1 saves without consuming. Prepare displays context/audio/source and suppresses generic tail upload unless the profile expressly requires both. Execution fingerprints cover lineage, profile and ordering; ordinary prompt fingerprints exclude native keys/context settings. Current configuration v7 and execution sidecar v4 retain the native capability introduced in v4/v2; Project v17 and Workspace v1 are current. See [mapping and validation evidence](execution-orchestration.md#native-h3-continuation-capability).

No custom-node dependency, latent browser, automatic motion detector, destructive tail editing, timeline assembly, provider call or live render is part of this implementation.

## Phase E — Current Look / Environment Identity

**In progress. Viewpoint-neutral Environment prompt semantics are production-promoted after manual H3 validation on 2026-10-02.** The prompt-authoring subproblem has a production default; this does not complete Phase E or settle environment geometry. Canonical Current Look and Environment models remain future work, scoped by later rendered continuity evidence.

`Subject stable identity != Subject current appearance/look`

A Subject may later retain stable identity evidence alongside current wardrobe, appearance and condition evidence, with clear provenance and replacement rules. Existing continuity precedence already protects current state from older identity/profile defaults.

`Environment identity != camera composition != current rendered spatial state`

Story Apply's automatic Environment carry remains unchanged. Viewpoint-neutral Environment semantics treat ordinary Environment Pictures as stable identity/appearance evidence; explicitly authored topology and continuity authority remain authoritative. The new `partially_preserved` default for requested substantial viewpoint reconstruction reduced severe source-camera anchoring in the tested lateral-bedroom gate. Gemma's exact provider output was structurally valid and its render escaped the doorway composition, with loose furniture/architecture relationships. Qwen's raw writer output had Markdown fence errors, though its semantic treatment was compliant; after the user removed only that fence, its render also escaped the source view and retained the bedroom topology more plausibly. These two matched renders show that topology quality remains model/output dependent, not solved or universally reliable. The complete record and settings are in [the environment benchmark](h3-environment-consistency-benchmark.md).

**Production contract hardening (2026-10-03):** broader mixed-character/multi-shot Gemma4/Qwen3.8 smoke testing exposed visually inferred object inventories, source-screen left/right, Environment-as-close-up-target misuse and missing per-shot retention bindings despite the narrow gate's successful source-composition escape. Ordinary `environment` Pictures now require a deterministic minimal final Subject definition with explicit identity-only, anti-composition and requested-view reconstruction semantics. Visually inferred inventory is excluded; authored topology/colors/left-right remain authority in shot/state prose. Environment Subjects bind as settings without displacing explicit camera targets. Retention remains visual: every listed shot binds the Subject label, and an extreme close-up without identifiable room content omits it. New-view `partially_preserved`, shared Main Prompt/Story policy, composition/frame authority and the existing structural validator remain intact. Provider-free coverage exercises both paths; no heuristic camera-target audit, schema/version, persistence, carry, execution or temporal change was added. See [the environment benchmark](h3-environment-consistency-benchmark.md) for the regression record and audit rationale.

**Remaining Environment research:** Phase E stays open for actual rendered scene-state evidence and spatial-consistency strategies. Canonical Environment identity remains optional future architecture, justified only if simpler prompt semantics and observed rendered state prove insufficient. Panorama/multi-view approaches may be studied experimentally; no production panorama role, UI or requirement is introduced. This remaining geometry work does not block unrelated research.

## Phase F — Execution / ComfyUI Closed Loop

**F1 and F2a/F2b implemented; broader scheduling remains future work.**

`Story plan → H3 prompt → ComfyUI job → output/take → Selected Take → Observed End State → next Generation`

- **Exact tracking and output import (F1):** recorded Prompt UUID governs modern exact-job lookup and compatible exact-history fallback. Visible-Generation polling/refresh resumes persisted authority after restart. One unique video imports automatically into managed Takes; zero videos remain completed without a Take, and multiple candidates require explicit output choice. Safe output/temp descriptors and idempotent receipts prevent reference-file import or duplicate Takes. No automatic Take selection or native acceptance occurs.
- **Exact live observation and Cancel (F2a):** one Project-scoped foreground observer labels the exact Generation/send and retains recorded Generation/submission/prompt identity across same-Project editor navigation. Standard ComfyUI and optional Model Preview Override/KJ-style previews plus node/sampler progress remain transient and separate from Takes and persisted HTTP job truth. Canonical terminal reconciliation automatically transfers to the next active Project render; the editor need not move. Active disconnected submissions reconnect on normal foreground polling with bounded 15/30/60/120-second backoff. Explicit Watch / Reconnect pins an exact active attempt and can force a connection. Promoted Cancel and history Cancel share exact targeting, retain the preview until confirmed terminal truth and never use global interrupt. Confirmed cancellation preserves the associated pending native-attempt lifecycle.
- **Recover and Retry (F2b):** Recover reconciles an existing exact submission without queueing. Confirmed Retry is limited to failed/cancelled attempts, revalidates current prompt/Profile/workflow/media/execution authority and creates a new submission and native attempt through normal Send. Recorded controlled renderer seed and Workflow Control values are reused; lineage preserves the old attempt. Unknown jobs require Recover. Retry is explicit, never automatic.
- **Execution controls:** Generation-owned renderer seeds offer Workflow default / Random each Send / Fixed through an explicit Profile binding, separate from the prompt-writing LLM seed. Workflow Controls expose declared scalar inputs/dictionary leaves; sidecar Project defaults and sparse Generation overrides determine effective values. Named Profile presets copy complete values into those scopes. Immutable submission provenance records exact effective controls; presets are not graph variants or native compatibility authority. Prepare uses current H3 duration and bound aspect-ratio mappings.
- **Native-attempt lifecycle:** exact terminal completion makes only its associated pending native attempt available; failure/cancellation makes it unavailable, without displacing accepted predecessor authority. Availability does not verify external artifact storage. Acceptance remains explicit. Individual removal and unavailable-attempt cleanup preserve submissions, Takes and external latent files; accepted attempts require revocation or supersession first.
- **Compact execution UX:** Profile, renderer seed, Workflow Controls and Prepare/Send precede output history. Imported Takes are prominent; job history and manual fallback remain compact disclosures. Live Render Preview sits after Prepare / Send and above ComfyUI Execution Settings, outside diagnostic render history. A bounded read-only Project summary returns the best active candidate and exact watched record; one foreground polling cadence replaces any need for per-Generation discovery loops. Automatic priority is running → queued, then Sequence order across Generations and newest createdAt/descending stable ID within a Generation. Useful running/equal-priority watchers stay stable; automatic queued watchers may upgrade to better canonical status. Historical unknown jobs remain recoverable with optional exact Watch, without an automatic Live Preview or running timer. Explicit Watch remains pinned across Generation navigation until terminal; terminal resumes automatic Project selection. G1 → G2 → G3 can transfer with the editor still on G1. Project changes clean up socket/Blob/media/progress and choice. Observation implies no Take, native, observed-state, handoff, controls, authoring, execution or navigation authority; no Prepare/Send/Retry occurs. No watched pointer or schema change is introduced.

Prepare verifies References and bounded H3 Setup; Send submits the prepared execution without repeating those writes. Unsaved Main Prompt, Story and notes no longer invalidate preparation through general editor dirtiness or become saved by Send. Unrelated Project revisions remain sendable only while the existing execution fingerprint matches; relevant Reference, Workflow Control, renderer seed and native lineage changes retain their checks. Scoped edit digests and ordered mutation receipts still protect actual authoring writes. Foreground refresh augments the default nonmutating summary with bounded recorded-Connection queue discovery and exact job checks before ranking. This identifies older running work hidden by stale queued status; legacy history lookup also resolves exact active queue identity. Failed transports are retired even during reconciliation, and browser disconnect promptly closes an idle upstream observer. Provider-free regressions cover immediate repeated Sends with random/fixed/default seeds, unsaved drafts, delayed receipts, real execution conflicts, exact submission provenance, fresh status priority, handoff and socket recovery; live ComfyUI validation remains outstanding.

Foreground refresh selects the queue-established running job before waiting for sequential history reconciliation. The existing single 15-second cadence continues discovery, handoff and bounded reconnection while history downloads/status requests are pending; output import remains on the normal exact reconciliation path. Positive queue identity avoids redundant exact active-job confirmation, while queue absence still requires exact terminal truth. Controlled delayed-HTTP regressions cover this scheduling guarantee; live renderer validation remains outstanding.

The one-shot **Render next ready Generation** action inspects saved Sequence order and exact render/selection authority, opens one candidate and runs ordinary Prepare in inspection mode; Send remains explicit. It stops at earlier active/unknown jobs and unresolved output decisions. Mechanically usable accepted native state remains eligible across Profile fingerprint drift. Readiness is advisory, not a permanent scheduler.

**Validation scope:** the Project-scoped foreground panel, G1 → G2 → G3 terminal transfer without navigation, same-Generation priority/explicit choice, bounded exact auto-reconnect, promoted offscreen Cancel, stale event isolation and media cleanup are covered by provider-free regressions. The read-only Project summary is tested for bounded minimal data, deterministic ordering, exact identity, terminal reporting and nonmutating reads. No live ComfyUI validation is claimed for this change. Provider-free transport, storage, authority and mounted frontend regressions cover F1/F2 and execution controls. User-reported live validation on **2026-09-22** confirmed Send → exact completion → unique-video retrieval → automatic managed Take import → in-browser preview. The same date's native G1 → G2 render gate is separate. Those gates do not establish later live preview/progress/Cancel/Recover/Retry or every preset/profile combination; no additional live validation is claimed here.

Current Project **v17**, Workspace **v1**, execution configuration **v7**, execution sidecar **v4**. Additive job/cancellation/retry fields retain older-record compatibility. Remaining Phase F work is broader dependency-aware Sequence automation and optional resource/VRAM scheduling. Successful submission is not proof of a chosen output or successful continuity.

## Phase G — Optional Higher-level Story Bible / Episode Planning

**Deferred behind production continuity.**

Richer location/prop/relationship models, episode hierarchies and broad story planning may become useful after the rendered continuity loop works. Do not build a general Story Bible first. Preserve the completed Character Profiles and bounded Story operations; do not replace them with a monolithic planner or a single request for 100 detailed Generation plans.

## Delivery discipline

Keep provider-free tests separate from opt-in live behavioral validation. Validate saved authority, boundedness, reference sharing, mode/exact-dialogue contracts, frontend edit/save/switch behavior and selected-prompt staleness. Report structural success separately from human-reviewed behavior and actual rendered-output evidence. No hidden model retries, invented media observations or automatic publishing/releasing.

## Behavioral gates / active validation

Provider-free regression proves structure, ownership, authority and isolation. Live prompt-writer review measures model-facing behavior. Actual H3 render review measures visual outcomes. Evidence from one level must not be promoted to another; benchmarks use human review, not an LLM judge.

| Capability / research | Recorded evidence and current decision |
| --- | --- |
| Story dialogue-language | Reviewed live writer gate `20260919T114957Z-fa5ecdef`, Gemma 4: Story Japanese and English → Japanese resolution, source/output separation, resolver cache reuse and exact H3 dialogue passed. This is authoring evidence, not a rendered speech gate. |
| Planned continuity bridge | Reviewed live writer gate `20260919T143402Z-de1819fd`, Gemma 4: wardrobe/current-state and same-room/new-camera authoring passed. It does not guarantee rendered environment consistency. |
| Observed continuity | Reviewed live H3 handoff gate plus manual real-video analysis/persistence and successor use; offline synthetic-observation fixtures separately prove projection. |
| Native continuation / F1 | User manual workflow/render gates on 2026-09-22, scoped as above; generic tail/endpoint review does not establish universal seamless continuity. |
| Temporal-directing overlay | Initial live prompt review across Gemma 4 and Qwen3.8 was model-dependent, sometimes verbose/inventive, with a Qwen Ref2VA Picture-provenance failure. Overlay remains benchmark-only, not promoted. |
| Scene Plan directability | Limited Gemma 4 live review found clearer downstream action/attention and physical progression while preserving quiet stillness; this supported the compact production Scene Plan guidance. A Qwen3.8 Q5 reasoning-on run timed out before first token and supplied no behavioral result. |
| Character performance / H3 Prompt Detail | Four completed live character-performance writer runs exist; the stronger experimental overlay remains benchmark-only. Historical overlay-suffix and dialogue-tag check defects were corrected for future runs without rewriting archives. Prompt Detail is production articulation control, not proof of better acting. Matched fixed-frame comparisons found Detailed alone was not a reliable substitute for state-preserving liveliness in the tested renders. |
| Natural Liveliness | Initial render testing exposed lowered gaze/prolonged eye closure under stillness. Refined matched anime I2VA testing on 2026-10-01 showed brief blinking with restored gaze/expression and preserved held stillness in tested seed/settings, supporting conservative production promotion. Anime symbolic-expression rules were not established or promoted. |
| Planning Style | Live prompt-writer review on Gemma4 and Qwen3.8 supports retaining both axes, with model/seed-dependent effects; reasoning-on examples are not directly comparable to the matched reasoning-off quartet. This is writer-quality evidence only, not render evidence or a universal quality claim. |

See [benchmark suites](../benchmarks/README.md), [temporal directing](h3-temporal-directing-benchmark.md), [Scene Plan directability](story-scene-directability-benchmark.md), [character performance](h3-character-performance-benchmark.md) and [Natural Liveliness](h3-natural-liveliness-benchmark.md) for reproducible definitions and scoped findings. Historical runs retain their original policies/checks; new controls do not retroactively validate them.

## Future research / priority order

1. **Selective intra-shot temporal anchors:** compare ordinary chronological prose with sparse intra-shot temporal landmarks for a few meaningful multi-stage actions. Official H3 syntax clearly standardizes timestamps for shot cuts. One user-supplied dance render closely followed requested cut times; sparse intra-shot anchors appeared to influence coarse action timing/order, without exact pose-at-timestamp guarantees. This is supplied motivating evidence, not a repository-controlled passed gate. The existing temporal-directing suite now implements the provider-free `miku-intra-shot-actions` renderer gate with three isolated chronological/approximate/exact-form treatments and human timing-review packs; actual matched renders remain pending. The Environment prompt-authoring production gate is complete; this temporal renderer gate is implemented but unpromoted. Intra-shot timestamping is not promoted to production and does not promise frame-exact action scheduling.
2. **Environment geometry and observed state:** continue investigating model-dependent room-topology drift and useful current spatial state evidenced by rendered Takes. The viewpoint-neutral prompt-authoring subproblem is already production-promoted. Do not expand canonical Environment architecture until further evidence warrants it. Panorama/multi-view remains optional experimental research.
3. **Remaining validation:** broader visual-handoff/execution-control checks remain useful; keep prompt review separate from matched rendered comparisons.
4. **Video combine/export:** deferred, not abandoned. Sequence assembly is a lower-priority workflow convenience; no combine/export design or implementation is part of this reconciliation.
5. **Larger scheduling / Story Bible:** revisit broader Phase F automation and Phase G only after continuity evidence justifies the added architecture.


## H3 aspect-ratio configuration update — 2026-10-24

All eight standard H3 ratios are available, including portrait photo `2:3` and
ultrawide `21:9`; the fresh default remains `4:3`. Modern semantic Aspect Ratio
bindings now reuse explicit ComfyUI enum discovery, preview additions/conflicts,
preserve valid manual mappings, and save verified closed-enum choices through the
ordinary Profile save. Prepare remains offline. Supported Custom tokens resolve
exactly; undeclared values block for verified enums. Older free-form/raw contracts
and legacy Profiles retain compatibility. Provider-free mounted/browser tests and
backend regressions cover discovery, persistence, prepared graphs and mocked Send.
See execution-orchestration.md for configuration and manual acceptance steps.

Exact pixel dimensions remain a separate future enhancement requiring explicitly
verified width/height inputs; no general graph rewriting or inferred dimensions
are part of this change. Live renderer acceptance is not claimed by mock tests.
