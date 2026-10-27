# Automatic aspect-ratio mapping — implementation and validation

Date: 2026-10-24 (Australia/Sydney).

## Result and root cause

H3's shared frontend vocabulary, HTML selector and backend missing-map guard
listed six ratios. Modern bindings already supported explicit `valueMap`
translations, but the existing ComfyUI enum discovery action served only legacy
exposed Workflow Controls. This caused six manual entries and omitted native
portrait-photo and ultrawide choices.

H3 now supports `1:1`, `2:3`, `3:2`, `3:4`, `4:3`, `9:16`, `16:9` and `21:9`,
plus Automatic and Custom. The fresh Project default stays `4:3`. Shared
vocabulary governs reopening into the standard selector and the mapping editor;
backend missing-map checks include all eight. Ratios retain their canonical
Project/Generation string through prompt authoring and execution.

## Discovery and safe editing

Create or edit an Aspect Ratio semantic binding on a saved workflow, explicitly
select its node/input and choose the Enum capability connection. Click
**Populate mappings from ComfyUI**, review, **Apply detected mappings**, then
**Save profile**. A newly imported graph must first be saved; a new binding does
not need to be saved before discovery.

The semantic endpoint uses the existing bounded enum-discovery transport and
exact saved class/input declaration. It checks a scalar string enum and strict
leading ratio tokens, using the same token boundaries as legacy compatibility.
No parallel metadata API, class guessing or automatic Prepare discovery exists.

Discovery previews additions without changing inputs or saved configuration.
Apply updates only the editable binding/Profile draft; the ordinary save and
canonical GET persist and verify it. Existing six valid entries survive exactly.
Correct numeric translations are preserved. A declared numeric renderer choice
that contradicts the H3 ratio is a semantic conflict. Renderer-specific labels
without a recognizable numeric ratio token remain manual mappings, with an
explicit equivalence-review warning. Contradictory or undeclared manual values
show their current and replacement values, with an
unchecked explicit decision for replacement/removal. No binding is erased or
legacy Workflow Control recreated. Complete mappings report that they are up to
date. Nonstandard mapping keys survive ordinary editing.

Unreachable ComfyUI, missing targets, wrong input declarations, non-string enums,
duplicate choices, ambiguous ratio tokens and ambiguous required/optional inputs
produce useful errors without changing the editable binding or saved Profile.
A target, connection or draft change invalidates a pending discovery proposal.

## Custom ratios, metadata and authority

The optional `AspectRatioSource.enumChoices` list persists verified choices with
the exact binding. It is bounded to 100 unique strings (256 characters each) and
rejects ambiguous ratio tokens. Absence serializes exactly like older Profiles;
old Profile/native-lineage identity remains stable. A saved declaration enters
Profile identity and execution fingerprints. No configuration version bump or
Project migration is required.

A verified enum is closed. Explicit mappings must select declared values; otherwise
a unique strict ratio token resolves a saved choice. Custom `21:9` thus resolves to
`21:9 (Ultrawide)`, even without an explicit `21:9` map entry. Custom `5:4` blocks
if absent. Automatic likewise needs a declared exact option or an explicit valid
mapping. No unsupported value becomes raw text or falls back to `4:3` for a
verified enum. A conflicting explicit manual map blocks rather than being
silently replaced at execution time.

Older Profiles without enum metadata preserve raw Custom behavior with an
explicit warning that raw-value support is unverified. Genuine free-form string
inputs retain that behavior; discovery rejects them as non-enums. Existing
six-map Profiles block newly selected standard ratios until mappings are supplied.
Offline Prepare uses only saved configuration, including when ComfyUI is down.
Saved enum declarations reflect explicit verification, not live node monitoring.

Execution still uses canonical H3 Setup, modern semantic precedence, legacy
compatibility and Workflow Control conflict checks. Profile identity includes the
mapping/declaration. Changed ratios invalidate preparation; Send recompiles and
rejects stale expected fingerprints. Seed authority, native continuation, other
ResolutionSelector inputs and exact submission provenance remain on their existing
production paths.

## Exact verified graph and mocked queue values

| H3 selection | Prepared graph | Mocked `/prompt` request |
| --- | --- | --- |
| `2:3` | `graph["115"]["inputs"]["aspect_ratio"] = "2:3 (Portrait Photo)"` | `body["prompt"]["115"]["inputs"]["aspect_ratio"] = "2:3 (Portrait Photo)"` |
| `21:9` | `graph["115"]["inputs"]["aspect_ratio"] = "21:9 (Ultrawide)"` | `body["prompt"]["115"]["inputs"]["aspect_ratio"] = "21:9 (Ultrawide)"` |
| `3:2` | `graph["115"]["inputs"]["aspect_ratio"] = "3:2 (Photo)"` | `body["prompt"]["115"]["inputs"]["aspect_ratio"] = "3:2 (Photo)"` |

These assertions exercise real discovery/Profile routes, real persistence and
Prepare/Send production paths. Browser discovery begins with six mappings,
previews mocked eight-choice ResolutionSelector metadata, saves and reloads eight,
and checks the exact prepared and queued values. Mounted Generation handlers
cover reopening, switching and duplication for both new ratios. H3 instruction
layers retain the selected ratio. No tests rely solely on parser fixtures.

## Pixel dimensions

Exact 1536 × 1024 output is a separate future enhancement. The current selector's
`aspect_ratio`, `megapixels` and `multiple` inputs do not establish direct pixel
control. No width/height graph rewriting is implemented.

## Validation results

- Focused backend: **226 passed** (`test_workflow_controls.py`,
  `test_workflow_preparation.py`, `test_semantic_aspect_discovery.py`,
  `test_enum_capabilities.py`, `test_legacy_aspect_ratio.py`).
- Focused editor/native-browser: **48 passed**; all four browser scenarios ran,
  with no skips. Additional mounted persistence suite: **42 passed**, including
  both new ratios through switching and duplication.
- Final full Python command: `.\.venv\Scripts\python.exe -m pytest` —
  **2631 passed, 3 skipped, 2 warnings**, 303.57 seconds.
- Final full JavaScript command: `node --test tests/js/*.test.mjs` —
  **668 passed, 0 failed, 0 skipped**, 41.73 seconds. This includes the final
  mounted persistence cases and preservation of valid manual translations.
- `git diff --check`: passed.
- Python warnings: existing Starlette/httpx deprecation and inability to write
  the local pytest cache. Neither prevented test execution.
- Initial full validation exposed old-Profile serialization and error-context
  compatibility issues; those were corrected before the final successful run.
- Final Git status: **16 modified tracked files, 3 new files, no staged changes**.
  The three new files are this report, `miniconstruct/aspect_ratios.py`, and
  `tests/test_semantic_aspect_discovery.py`. Temporary test logs were removed.
  No commit was created.

## Files changed

- `web/index.html`: complete H3 selector.
- `web/js/aspect-ratios.js`: shared eight-ratio vocabulary.
- `web/js/api.js`: semantic discovery API client.
- `web/js/execution-config.js`: preview, conflict decisions, draft apply and metadata preservation.
- `miniconstruct/aspect_ratios.py`: strict mapping and closed-enum offline resolution.
- `miniconstruct/enum_capabilities.py`: shared transport and semantic discovery adapter.
- `miniconstruct/api/execution_routes.py`: read-only semantic discovery route.
- `miniconstruct/execution.py`: optional bounded enum declaration model.
- `miniconstruct/native_continuation.py`: preserve older identities when metadata is absent.
- `miniconstruct/workflow_preparation.py`: resolution, warnings and exact fingerprint values.
- `tests/test_semantic_aspect_discovery.py`: real routes, persistence, authoring, compilation, Send and failures.
- `tests/test_workflow_preparation.py`: eight-ratio missing-map checks and accurate raw warning.
- `tests/js/execution-config.test.mjs`: mounted discovery, manual conflicts and safe persistence.
- `tests/js/aspect-ratio-browser.test.mjs`: actual browser discovery and exact mocked submissions.
- `tests/js/project-fingerprint.test.mjs`: mounted new-ratio Generation isolation/duplication.
- `tests/js/support/project-fingerprint.py`: provider-free exact metadata and route bridge.
- `docs/execution-orchestration.md`: usage, metadata contract, limits and manual acceptance.
- `docs/MiniConstruct-Roadmap.md`: delivered scope and separate pixel-dimension enhancement.
- `docs/aspect-ratio-mapping-validation.md`: this report.

## Manual UI acceptance

1. Open the six-map Profile in Execution settings, edit its Aspect Ratio binding,
   choose the intended connection and exact renderer target, and Populate.
2. Review the new `2:3`/`21:9` entries, apply, save, reopen and verify all eight.
   Confirm no exposed Aspect Ratio Workflow Control has appeared.
3. Select each new ratio in H3, Prepare and inspect node 115. Reopen the Project,
   switch Generations and duplicate; confirm the saved ratio and prepared value.
4. Try Custom `21:9`, then `5:4`: expect declared Ultrawide, then a blocking error.
   Change a prepared ratio and verify stale preparation is discarded.
5. Disconnect ComfyUI: discovery must fail without altering mappings; saved
   supported choices must still Prepare offline.
6. In a draft, enter a renderer value absent from the enum; Populate and confirm
   the conflict requires a decision. Cancel/reload to preserve the saved Profile.

Native browser validation is automated and provider-free. Live renderer acceptance
remains user-run; no real Send, LLM request or video rendering was performed.
All changes remain uncommitted; the starting repository was clean.


## Pass 279 conflict correction — 2026-10-25

The original Populate action and Prepare resolver accepted any mapped value found
in the verified enum. This incorrectly preserved `3:2 → 4:3 (Standard)`, despite
ComfyUI also declaring the correct `3:2 (Photo)` choice. A previous JavaScript
regression incorrectly asserted that behavior; it has been replaced.

Both paths now use the established strict leading numeric ratio token rule
(`digits:digits` followed by end, whitespace or an opening parenthesis). Python
exposes the same parser used by enum discovery, and the shared frontend ratio
module provides compatible parsing. Two recognizable, unequal tokens establish
a semantic conflict without guessing from node titles or descriptive labels.

Populate shows the current contradictory value and recommended replacement.
An unchecked explicit decision is required before Apply. Without that decision,
the editable value and saved Profile remain intact. Cancelling the review or a
discovery failure preserves saved configuration. Correct six-entry mappings still
survive while the two new ratios are populated.

Prepare rejects an already-saved contradictory mapping with the exact target,
requested H3 value, mapped renderer value and actionable correction steps. The
guard applies after reload, independent of how the mapping was created. Send
recompiles through the same resolver and cannot queue the contradictory graph.

Declared renderer-specific labels without numeric tokens remain supported, with
an equivalence-review warning in Populate and Prepare. For example, `Photo preset`
is not guessed to be either `3:2` or `4:3`; nor are embedded tokens such as
`Portrait 4:3` or malformed tokens such as `3:20preset` treated as leading ratios.
Correct verified mappings, closed-enum rejection, offline Prepare, raw legacy
contracts, all eight standard choices, native continuation and provenance remain
unchanged. No schema or stored configuration migration is introduced.

Correction validation:

- Focused backend: **232 passed**, including saved numeric-conflict rejection in
  Prepare and Send, correct graph/queue values, strict token boundaries,
  renderer-specific labels, legacy compatibility and Profile persistence.
- Focused frontend/browser: **93 passed**, including the actual browser conflict
  preview, blocked Apply until an explicit decision, cancellation and failures.
- Full JavaScript: `node --test tests/js/*.test.mjs` — **670 passed, 0 skipped,
  0 failed**, 45.09 seconds.
- Final standalone Python: `.\.venv\Scripts\python.exe -m pytest` —
  **2637 passed, 3 skipped, 2 warnings**, 296.57 seconds.
- The first Python full run had **2636 passed, 3 skipped and 1 setup error**:
  Windows reported socket buffer exhaustion (`WinError 10055`) while pytest-asyncio
  created an event loop in an unrelated benchmark test. The affected benchmark
  module passed all **84 tests** on rerun, and the subsequent complete Python run
  passed. No benchmark or operating-system configuration was changed.
- Remaining Python warnings are the existing Starlette/httpx deprecation and
  inability to write the local pytest cache.
- `git diff --check` and `git diff --cached --check`: passed.
- Final Git status: the original **19 staged paths** are retained; **9 of those
  paths also contain unstaged correction edits**. There are no new untracked
  files after removing temporary logs, and no commit was created.

Files changed by this correction (relative to the staged pass 279 snapshot):

- `miniconstruct/aspect_ratios.py`
- `miniconstruct/workflow_preparation.py`
- `web/js/aspect-ratios.js`
- `web/js/execution-config.js`
- `tests/test_semantic_aspect_discovery.py`
- `tests/js/execution-config.test.mjs`
- `tests/js/aspect-ratio-browser.test.mjs`
- `docs/execution-orchestration.md`
- `docs/aspect-ratio-mapping-validation.md`

The existing pass 279 staging is preserved; correction edits remain unstaged.
No commit, real provider request or live ComfyUI submission is made.
