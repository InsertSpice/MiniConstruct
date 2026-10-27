# Generation-owned Story Memory rebuild report

Implemented on current master, with all changes left uncommitted. Story Continuity Memory now belongs to Generations: each snapshot is the state true after its Generation.

1. **Initial branch / HEAD / status:** `master`, `ab42f6b2b0a7dd281a4f278f732a32ccad4a253a` ("Add long-range Story identity recall"), clean working tree.
2. **Architecture found:** Project v8, Workspace v1; one writable `sequence.storyMemory`; Generation manifests had workspace, continuity, planning, asset mappings and stable UUIDs. Save wrote the global memory, and Plan Next returned one batch-level patch. Existing cast/narrative identity, historical identity recall, reference materialization, bounded planning, Revision and benchmark paths were inspected before implementation.
3. **Current canonical schema:** Project v10. Workspace remains v1. The two unrelated v9 layouts are historical compatibility inputs only.
4. **Generation fields:** `storyMemoryAfter: StoryContinuityMemory` and `storyMemoryStatus: trusted | needs_review | legacy_unknown`. Canonical v10 Generations require both fields. Historical v9 files are classified structurally: Generation-owned v9 remains readable, while the abandoned temporal v9 shape (`sequence.storyMemory` / `sequence.storyMemoryTemporal`) is adapted only for migration.
5. **Terminal authority:** `_story_memory(manifest)` resolves the actual terminal Generation snapshot. The invariant test checks object identity: there is one canonical object, so separate current and terminal authorities cannot diverge.
6. **Sequence-global memory:** removed from canonical persistence; retained only in the frozen v8 adapter. HTTP `storyMemory` remains a write-payload name, with an explicit selected UUID; responses now expose selected `storyMemoryAfter`.
7. **v1–v8 migration:** strict, non-mutating reads; v8 global memory becomes terminal trusted memory. Nonterminal snapshots are empty and `legacy_unknown`. v1–v7 terminal memory adapts to empty trusted state. Next canonical write emits v10 without a competing Sequence field.
8. **Legacy unknown:** explicit warning that empty fields do not establish an empty historical story. Fields remain editable; Save or explicit confirmation accepts the selected snapshot. Unknown downstream history stays `legacy_unknown` until independently accepted.
9. **Selected editor:** selecting G3 restores G3's snapshot; G6 restores G6's. The heading uses its current ordinal, while persistence and selection use UUIDs.
10. **Normal Save:** edited memory is sent with `storyMemoryGenerationId` and `expectedProjectFingerprint`. Wrong/missing authority or stale state is rejected. Memory and active workspace are committed together. Unrelated saves omit memory and preserve its trust status. Save holds the editor steady while awaiting its response.
11. **Manual append:** deep-copies predecessor memory. Empty G1 is trusted. An untrusted predecessor yields `needs_review` on the new Generation.
12. **Middle insertion:** deep-copies the preceding snapshot; insertion before G1 starts empty and trusted. Existing successors retain content and require review.
13. **Duplicate:** deep-copies source memory, retains stable Subject semantics, allocates a new Generation UUID, and invalidates existing successors. Save As clones every snapshot and maps an explicit active-memory edit to its new UUID.
14. **Delete / reorder:** deletion invalidates the surviving suffix from the deleted position; reorder starts at the first changed position. Unaffected prefixes and all surviving UUID-owned values remain intact.
15. **Deep copy:** nested facts, threads and Subject links are independent across inherited, duplicated, projected and persisted snapshots. Direct object-identity and mutation regressions pass.
16. **Trust model:** only `trusted`, `needs_review`, and `legacy_unknown`; no exact/incomplete/unavailable replay states.
17. **Historical edits:** changed G3 becomes trusted; later known snapshots become `needs_review` without overwritten values. Canonically equal saves do not invalidate successors. Unknown history retains its more specific unknown status.
18. **Review / confirmation:** Confirm Story Memory uses normal Save and the selected UUID. It accepts only that Generation; it never trusts every successor.
19. **Timeline:** compact scrollable list of all Generations, readable status and selected marker; row clicks use ordinary selection and existing unsaved-change handling.
20. **Plan Next input:** only terminal `storyMemoryAfter`, requiring terminal trust. The UI still requires terminal selection. Earlier untrusted states do not independently block Plan Next after the terminal is confirmed.
21. **Per-slot contract:** every returned slot requires `memoryPatchAfter`; an empty patch explicitly preserves state. Patches apply sequentially, producing one independent trusted snapshot per new Generation. Existing patch machinery was extended, rather than replaced with complete model-authored snapshots.
22. **Temporary IDs:** 16 issued IDs per collection per operation, signed in the operation authority. Any slot can first use an unused issued ID; later slots can update or resolve it. One operation-wide UUID mapping preserves identity across persisted snapshots. Unknown, cross-collection and conflicting IDs are rejected.
23. **Atomicity:** all slots, references and intermediate memory states validate before the single manifest replacement. Overflow cannot be hidden by a later resolution. Invalid Apply leaves the Project unchanged.
24. **Exact G3/G6 summary regression:** passed through production HTTP Save and backend reload, plus mounted JavaScript editor/timeline/Save and simulated browser reload using the exact railway-underpass summaries.
25. **Durable facts:** independent open-underpass and sealed-underpass facts pass; removing the later fact preserves the earlier fact.
26. **Open threads:** G3's medicine-delivery obligation survives while G6 has no unresolved obligation; both reload independently.
27. **Three-slot evolution:** G4 keeps the promise open, G5 retains it and adds a fact, G6 resolves it and can update the fact. Final-slot state does not leak backward. Production-path tests pass.
28. **Long-range identity:** existing stable Subject relevance, historical `subject_identity` and `character_comparison_scale`, semantic deduplication, pixel deduplication, local precedence, exclusion and safe materialization regressions pass. Relevance uses terminal memory.
29. **Dialogue / Revision:** exact dialogue, intentional blank dialogue, scoped patch Revision, frozen recommendations, Narrative Identity and H3 regressions pass. Narrative renames now refresh the loaded Project fingerprint so the next normal Save has current authority.
30. **Storage:** see the table below. Realistic growth is 2,750 compact memory bytes and approximately 5,988 actual manifest bytes per Generation. The separate maximum-length escaped-JSON memory ceiling is 3,588,053 bytes per Generation; at 12/30/60/100 Generations it is 43,056,636 / 107,641,590 / 215,283,180 / 358,805,300 bytes. No compression or secondary memory store was added.
31. **Files changed:** complete grouped inventory below; 29 modified files and three new files, including this report.
32. **Python:** full monolithic suite completed: **1,089 passed, 1 skipped**, 92.01 seconds. The skip is the existing directory-symlink test because this environment cannot create directory symlinks. Three warnings concern existing FastAPI test-client deprecation and enum serialization test inputs. Cache writing was disabled and isolated test directories were used.
33. **JavaScript:** `node --test tests/js/*.test.mjs`: **224 passed, 0 failed**. `node --check web/js/app.js` also passed.
34. **Diff check:** `git diff --check` passed. The complete diff was reviewed for duplicated authority, wrong Save targets, shared state, trust invalidation, temporal leakage, migration and scope regressions.
35. **Benchmark validation:** all offline benchmark tests passed in the full suite. A final focused Story / benchmark / Generation-memory run passed **142 tests**. Benchmark definition listing and the standalone offline storage measurement succeeded. Existing 4/12/30/60-source N=3 bounded-scaling tests now also assert required slot patches and one persisted snapshot per new Generation.
36. **Story Planner documentation:** `docs/story-planner.md` now prominently documents ownership, selection, trust, Save, migration, per-slot patches, temporary authority, previews, limitations and storage measurements.
37. **Roadmap:** `docs/MiniConstruct-Roadmap.md` records this v10 milestone and the future order: prove the foundation, Insert Between, Replan Range, Story Long-Form Behavioral Benchmark Suite, then Story Creative Direction. Build Initial Range remains future work.
38. **Compatibility / limitations:** older canonical Projects remain readable, but older binaries cannot read newly saved v10 Projects. Reload the frontend and repropose old ephemeral Plan Next drafts; old batch-level replies are rejected. Memory writes now require selected UUID and fingerprint; the response field is `storyMemoryAfter`. Both historical v9 shapes are readable; old temporal checkpoint/event data is migration-only and is never canonical v10 authority. Draft memory previews are review-only; canonical Generation memory remains editable. Legacy Build inherits snapshots and does not propose per-slot memory evolution.
39. **Archive:** no archival source or tests were inspected or reused. The archival branch was not checked out, modified, merged or cherry-picked.
40. **Final Git state:** still `master` at the original HEAD; 29 tracked files modified, three new files, no staged changes. Temporary test outputs were removed from the repository.
41. **Insert Between:** not implemented. Existing manual Before/After insertion remains available with the new inheritance/trust rules.
42. **Replan Range:** not implemented.
43. **Long-form behavioral benchmark suite:** not implemented; only existing offline structural/scaling support and the requested storage measurement changed.
44. **Story Creative Direction:** not implemented. Build Initial Range was not implemented either.
45. **External / Git actions:** no live provider, LLM or ComfyUI calls; no commit, push, tag or release.

## Measured storage

| Generations | Compact Story Memory JSON bytes | Actual Project manifest bytes |
| ---: | ---: | ---: |
| 12 | 33,000 | 72,288 |
| 30 | 82,500 | 180,072 |
| 60 | 165,000 | 359,712 |
| 100 | 275,000 | 599,232 |

Fixture: roughly 500-character summary, 12 realistic durable facts, four open threads, and ordinary blank Generation Workspaces. Media payloads are outside the manifest. Run `python -m miniconstruct.benchmark.memory_storage` to reproduce. The theoretical ceiling above uses existing maximum lengths and six-byte JSON escapes, separately from realistic text; it is not a whole-Project or media bound.

## Changed-file inventory

| Area | Files |
| --- | --- |
| Persistence / transport | `miniconstruct/project_store.py`, `miniconstruct/models/api.py`, `miniconstruct/api/routes.py` |
| Memory / Plan Next | `miniconstruct/models/story_memory.py`, `miniconstruct/models/story_operations.py`, `miniconstruct/story_operations.py` |
| Benchmark support | `miniconstruct/benchmark/story.py`, new `miniconstruct/benchmark/memory_storage.py`, `benchmarks/README.md` |
| Frontend | `web/js/api.js`, `web/js/app.js`, `web/js/project-workflow.js`, `web/js/story-planning.js`, `web/js/subject-identity.js`, `web/css/app.css`, `web/index.html` |
| JavaScript tests | `tests/js/story-planning.test.mjs`, `tests/js/project-api.test.mjs`, `tests/js/narrative-identity.test.mjs` |
| Python tests | new `tests/test_generation_memory.py`; `tests/test_story_next.py`, `tests/test_project_store.py`, `tests/test_media_persistence.py`, `tests/test_narrative_identity.py`, `tests/test_planning.py`, `tests/test_sequence_subjects.py`, `tests/test_story_apply_persistence.py`, `tests/test_story_planning.py`, `tests/test_workflow_preparation.py` |
| Documentation | `docs/story-planner.md`, `docs/MiniConstruct-Roadmap.md`, new `docs/story-memory-rebuild-report.md` |
