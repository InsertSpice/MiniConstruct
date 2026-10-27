# MiniConstruct

Primary purple actions are always attemptable. The frontend may explain why an action is likely to fail, but a click must show local or authoritative server validation feedback. In-flight actions report that work is already in progress and prevent duplicate requests without disabling the button.

**MiniConstruct is a local prompt-writing workbench for MiniMax H3.**

Describe the video you want, attach your references, choose the relevant H3 mode and creative controls, and MiniConstruct uses a separate OpenAI-compatible LLM to turn that workspace into a structured H3 prompt.

It is designed for people who want the flexibility of writing H3 prompts with an LLM without repeatedly explaining H3 syntax, reference semantics, dialogue rules, continuation behavior, camera preferences, and formatting requirements by hand.

> **MiniConstruct writes H3 prompts. It does not run MiniMax H3 itself.**
>
> Use the resulting prompt with your existing H3 workflow, such as ComfyUI or another compatible frontend.

## What can MiniConstruct do?

MiniConstruct can:

- Build prompts for **T2VA, I2VA, FL2VA, L2VA, and Ref2VA**
- Organize **Picture, Video, and Audio references**
- Associate references with semantic **Subjects**
- Use image references directly with a vision-capable prompt-writing model
- Preserve **exact dialogue**
- Build **video continuation** prompts and bounded tail continuation references
- Control camera preferences, tone, performance, music, visual style, identity fidelity, and prompt-generation seed
- Generate multiple prompt variations
- Validate generated H3 structure and reference usage
- Repair formatting problems without rewriting the creative intent
- Highlight part of a generated prompt and **revise only that selection**
- Save editable Projects and manage generated prompts with Generation-owned Saved H3 Prompts

MiniConstruct handles the H3-specific instructions behind the scenes while keeping the creative request itself editable.

## Example workflows

### Reference-driven character performance

Attach one or more images of a character, mark them as Subject / Identity references, describe the scene and performance you want, then use Creative Controls to steer things such as camera movement, performance energy, tone, and identity fidelity.

MiniConstruct turns the workspace into a Ref2VA prompt that explains how H3 should use the supplied references.

### Image-to-video

Choose **I2VA**, attach the required first-frame Picture, describe what should happen after that frame, and generate the finished H3 prompt.

For first-and-last-frame generation, use **FL2VA**. For last-frame-only generation, use **L2VA**.

### Video continuation

Attach a previous video as the continuation source and describe what should happen next.

For transitions between separately generated clips, a **tail continuation reference** can use the ending portion of the previous clip as Ref2VA motion/state evidence. The target should begin from that source ending state, but generic Video reference input does not duplicate source frames or guarantee a seamless boundary.

### Exact dialogue

### Sequence dialogue language

Each filesystem Project persists one canonical `sequence.dialogueLanguage` setting: **As Written** (the compatible default), Arabic, Chinese, English, French, German, Italian, Japanese, Korean, Portuguese, Russian, or Spanish—the documented stable MiniMax H3 dialogue languages. Each Generation Workspace carries only the effective transport projection plus its own source dialogue and cached Spoken Output resolution. As Written keeps the former verbatim workflow and never calls a translator. With another selection, the existing Dialogue / Lyrics editor remains the authoring source and MiniConstruct stores a separate inspectable Spoken Output resolution. It is translated lazily, cached by source + target + contract version, and may be edited as an explicit user override. H3 compilation and exact-dialogue validation consume only that resolved target text; a failed or stale resolution blocks generation rather than falling back to source text. Story Planner is instructed to author new dialogue directly in the selected language. This is one Sequence setting only: there are no per-Subject languages, subtitles, dubbing, or multilingual-scene controls.

Enter dialogue separately from the creative request:

```text
Subject 1: Where are we?
Subject 2: Somewhere we shouldn't be.
```

MiniConstruct instructs the prompt-writing model to preserve those lines verbatim in H3 dialogue syntax while the main creative request controls timing, acting, reactions, camera behavior, and surrounding action.

### Fix one bad part of a prompt

Generate a prompt, highlight the section you want changed, enter a revision instruction, and use **Revise Selection**.

MiniConstruct replaces only that selected section instead of regenerating the entire prompt.

## How it works

### Execution orchestration

ComfyUI integration is optional. Open **Execution / ComfyUI** to save machine-local
self-hosted ComfyUI Connections and reusable API-format Workflow Profiles. Import a
graph, choose compatible H3 modes, and map completed prompt, duration and logical
Picture, Audio and Video reference inputs. For a saved Generation, **Prepare**
builds the executable job offline from persisted executable Picture, Audio and
Video references, and routes
master-workflow reference capacity for that Generation. **Send to ComfyUI** is a
separate, explicit action that uploads the prepared inputs and submits the job to
the selected Connection. Test Connection remains a read-only reachability check.

Workflow Profiles can also declare **Exposed Workflow Controls** for selected
scalar inputs or dictionary leaves in the imported API graph. The Project can
save sparse defaults, and each Generation can override them. Prepare resolves
Profile default → Project default → Generation override; Send submits those exact
values and records them with the Render Submission. Power Lora enabled/strength,
Spectrum enabled, steps, sampler and shift are examples of generic mappings,
not built-in node integrations. Profiles may also save complete named Workflow
Control presets such as Turbo 8-step or Quality + Spectrum. Choose a preset and
click Apply to copy its values into Project defaults or This Generation. Choosing
one alone changes nothing; Clear restores inheritance. Applied values remain
independent when a preset is later edited or removed. Presets are templates,
not graph variants, node bypass, or execution and native compatibility authority.

See [Execution orchestration](docs/execution-orchestration.md) for persistence,
mapping rules, sending boundaries and API routes.

### Prompt-writing flow

MiniConstruct sits between you and a prompt-writing LLM:

```text
Your workspace
    ↓
MiniConstruct H3 instructions + reference manifest
    ↓
OpenAI-compatible prompt-writing model
    ↓
Structured MiniMax H3 prompt
    ↓
Your H3 generation workflow
```

The prompt-writing model does not need to memorize MiniMax H3 prompting conventions itself. MiniConstruct supplies the relevant H3 guide, mode rules, reference relationships, dialogue constraints, Creative Controls, and workspace information with each request.

## Creative Controls

Creative Controls let you influence the generated prompt without stuffing every preference into the main creative request.

### Camera

Individual camera behaviors can be marked **Avoid**, **Auto**, or **Prefer**.

This is useful when you want, for example, an energetic moving camera without encouraging particular motions that do not suit the scene.

### Tone & Performance

Tone controls can emphasize qualities such as sensuality, drama, horror, tension, romance, and whimsy.

Performance controls separately influence how restrained, expressive, exaggerated, calm, or energetic the subject should be.

These controls affect presentation and performance rather than silently changing the requested character, scene, or wardrobe.

### Visual style and music

Visual Style can provide an additional stylistic direction to the prompt writer.

**Auto** infers the medium and supported visual traits from the request and applicable references. An **explicit preset** sets the target medium or treatment while relevant Pictures and user-authored Notes refine its appearance: Style / Appearance guides overall rendering, Subject / Identity guides character and costume design, and Environment guides background art and lighting. First-frame Pictures can inform appearance while retaining their existing anchor authority. References in another medium are translated into the selected presentation; their incompatible rendering does not override it. **Custom** keeps the user's authored style requirements.

The writer is guided to integrate a few supported traits into Shot 1, without a mandatory checklist, invented production details, or repeated style inventories. With vision disabled or unknown, it uses written evidence only; with no useful evidence, it keeps concise preset guidance. This changes authoring instructions, not rendering or workflow execution. See the [optional controlled style comparison](docs/h3-explicit-visual-style.md).

Non-diegetic music defaults to **Off** for new Generations, since sequence music is commonly handled in post. You can still explicitly set it to **On** (with optional direction) or **Auto**; existing authored values are preserved.

### Subject Identity Fidelity

Identity Fidelity controls how strongly MiniConstruct tells the prompt writer to preserve identity evidence supplied by Subject references.

Identity references can also be described by focus, such as facial identity, full-body appearance, outfit/clothing, or detail.

### Seed

MiniConstruct supports backend-default, random, and fixed seed behavior for compatible prompt-writing backends.

A fixed seed can help reproduce the prompt-writing model's output where the backend supports deterministic seeded sampling.

## Iterative prompt editing

Generated prompts remain editable.

You can:

- edit the raw prompt manually
- highlight a contiguous section and use **Revise Selection**
- validate the current prompt
- use **Repair Format** when the creative content is correct but the H3 structure is not

The iterative workflow is **Generate → manually edit and/or Revise Selection → Validate / optional Repair Format → Use for execution → Prepare → Send**. Editing raw output ends model-output provenance and invalidates its old validation and any in-flight Revision. The exact edited prompt remains the current candidate: select text to start a new Revision, validate it, or use it for execution without regenerating. Execution selection remains an explicit choice: editing never changes the selected prompt while typing, and **Update execution selection** adopts the exact visible candidate. Format Audit findings do not prevent selection.

For a saved filesystem Project, **Save H3 Prompt** updates the currently loaded Saved H3 Prompt in place, or creates its first save when the visible candidate has no saved edit target. The adjacent **Save As New** always creates another instance, even with identical text, and makes that entry the current edit target. Updates preserve the stable UUID and **Save N**, refresh exact text, SHA-256, source fingerprint, authoring path and last-saved timestamp, and do not consume a number. First Save without a target may reuse an exact duplicate. The compact **Saved H3 Prompts** panel beside Validation uses an ordinary dropdown in descending Save N order, with local timestamps and current/stale source indicators. A detail area shows a short wrapped preview and active restore state. Select an entry, then **Load** or **Delete**; dropdown inspection alone never replaces text or retargets Save. Load confirms before discarding unsaved edits and restores a `manual_edit` candidate, separate from execution selection, Prepare and Send. Manual editing, Revise Selection, Repair Format and Validate retain the loaded edit target; Generate and Regenerate start independent candidates without it. Successful Save and Load set the active restore pointer; Project reopen or Generation return restores that entry unless a newer in-memory candidate takes precedence. Edit targets are scoped to the actual visible candidate and Project/Generation, independently of the restore pointer. Delete clears the deleted edit target and active restore pointer when applicable, leaving visible text and execution selection intact. Deletion never renumbers survivors or reuses a number. Each Generation supports 100 entries with no silent eviction: updates remain available at capacity, while creating another entry requires deletion. Full Project Save As preserves IDs/numbers and remaps Generation ownership; fresh copies and Clear generated state retain their explicit keep/clear policy. None of these authoring operations saves automatically. **Save .txt** still exports a separate file.

Only one prompt-writing provider operation runs per page at a time. A global activity indicator shows its name, elapsed time, and Cancel or Stop. Primary action buttons remain clickable; clicking another action reports the active operation without sending another request. Cancel or Stop aborts MiniConstruct's browser request or stream and ignores late results. Story Cancel and model-backed Repair Format Stop also cancel the exact server operation and close its upstream provider request. A third-party model backend may take time to stop GPU computation after the connection closes.

For execution, an edited prompt is explicitly sent to the backend for adoption against the current saved Generation. The backend computes the current fingerprint and reference authority; it does not trust provenance or references from the browser. The execution record identifies this as `manual_edit`, not model-generated text. Format Audit reports H3 structure, dialogue, and reference problems, but explicit execution may continue despite ERROR findings. Missing prompts, incompatible Workflow Profiles, invalid bindings, missing required physical inputs, or transport failures can prevent Prepare or Send. Repair Format remains optional.

Revision works from the current prompt rather than regenerating the entire output.

**Repair Format** first applies conservative deterministic corrections when the current Workspace uniquely determines the answer, then runs the canonical validator again. For example, it can add a missing canonical identity Picture citation inside an existing Subject definition. A designated Reference Sheet may be named from its metadata; this step does not inspect its pixels or invent a list of visible views. If structural errors remain, Repair Format sends the partially corrected prompt, relevant H3 guide, and remaining findings to the prompt-writing model for a narrow format repair. It is not a universal auto-fixer.

Picture, Video, and Audio labels form a closed world from the current numbered Reference Manifest. H3 writing, Revision, and Repair may use only those labels; bad existing H3 text does not authorize invented labels. With no Audio reference, dialogue, voice, effects, and music do not justify `<Audio 1>`. A real numbered Audio reference remains usable for its configured role. Format Audit reports invalid labels while explicit execution remains available.

Resolved Spoken Output is literal audible text only; it never stores an H3 language tag. Each H3 utterance uses `<d>[Language] exact audible text</d>`. The leading supported language tag is required H3 syntax metadata, not speech, and is excluded from exact spoken-payload comparison. With a concrete Sequence language, the tag must match that language. In As Written mode, the writer selects a supported tag from the supplied dialogue and validation checks the tag name without language detection. Story Planner authors new dialogue directly in a concrete selected language and uses `Name:` or `Subject N:` lines for distinct vocal events. A pause within one utterance does not imply another speaker.

## Reference-driven prompting

Pictures, Videos, and Audio are numbered independently.

A reference can be assigned a role, Notes, and semantic relationships describing how it should be used.

For example, a Picture may define:

- Subject identity
- environment
- composition
- outfit
- a visual detail
- another reference-specific purpose

A `<Subject N>` represents a visible entity in the target video rather than being another name for `<Picture N>` or `<Video N>`.

Several assets may contribute to the same Subject, and reference relationships can explain how those assets relate to one another.

In Ref2VA, an identity-only Picture is cited in the same `<Subject N>` definition sentence that it informs (for example, `<Subject 1> is Alice, the woman shown in <Picture 1>, with ...`). It does not get a separate Picture definition or retention entry. A Picture is tracked independently when its configured role makes it a frame, composition, or storyboard reference; a Reference Sheet used only for identity does not make its panel layout part of the target composition.

### Image vision

When **Model accepts images** is enabled, the H3 prompt writer receives a transient RGB JPEG derivative (maximum longest side 768 pixels, quality 84) through OpenAI-compatible `image_url` content. The browser processes new and replacement Pictures into canonical JPEGs with a maximum longest side of 1600 pixels and quality 0.88. Existing saved Pictures retain their canonical payloads. The canonical Picture remains reference and execution authority; prompt-writing preprocessing does not change its ComfyUI upload.

This optimization has been live validated by the user: three canonical 1536×1536 Pictures were transmitted as three 768×768 prompt-writer derivatives, with 253.2 KiB aggregate encoded vision payload. First-content latency fell from roughly 450 seconds to 56 seconds, with no meaningful H3 prompt quality loss observed. These are user-reported local-backend measurements, not universal guarantees.

### Video and Audio

Raw Video and Audio are **not** sent to the prompt-writing LLM.

MiniConstruct sends their metadata, duration, role, Notes, options, and semantic relationships instead.

This means the prompt writer cannot actually watch a supplied Video or hear a supplied Audio file, so MiniConstruct avoids asking it to invent properties it cannot inspect.

## H3 modes

MiniConstruct supports the main MiniMax H3 audiovisual generation modes:

- **T2VA** — text-driven audiovisual generation
- **I2VA** — one exact first-frame Picture at `0.00`
- **FL2VA** — exact first and last Pictures
- **L2VA** — one exact last-frame Picture
- **Ref2VA** — full semantic reference-driven generation

Duration is limited to H3's supported **4–15 second** range.

Shot count may be automatic or explicitly chosen. MiniConstruct validates shot timestamps, required sections, reference resolution, dialogue, mode-specific alignment, and other structural rules.

## Dialogue and continuation

Exact dialogue is entered separately from the main creative request so wording can be preserved verbatim while the creative field remains free to describe timing, reactions, interruptions, pauses, camera behavior, and performance.

MiniConstruct also supports the official `[video continuation]` relationship for continuing from a source video's ending state.

**Tail continuation reference** uses the ending portion of a previous clip as bounded Ref2VA evidence so the next generation can continue from its ending state with less risk of reset, camera discontinuity, or lost momentum. For native overlap, an optional continuation-capable Workflow Profile can carry the predecessor’s accepted H3 AV state through execution; the workflow owns protected context, suffix generation and removal of repeated context.

## Installation

### Requirements

- Python 3.12 or newer
- A modern Chromium, Firefox, or Safari browser
- An OpenAI Chat Completions-compatible prompt-writing server

Typical local options include LM Studio, Ollama, Unsloth Studio, and other compatible servers.

No Node.js frontend toolchain, database server, account, or cloud service is required.

### Windows

For first-time setup, double-click `setup-miniconstruct.bat` (or run it from any directory). It creates or reuses the repository-local `.venv`, installs MiniConstruct, and acquires the pinned official MiniMax H3 guides. Existing environments are never deleted or replaced automatically.

When launched from a source checkout, the app serves the repository's top-level `web/` assets directly, including with the documented editable install. Built distributions bundle those same assets inside `miniconstruct/web/`.

Prepare and Send appear directly beneath H3 execution selection. Before Prepare, MiniConstruct synchronizes only the active Generation's current H3 Setup duration into its saved Workspace; unrelated unsaved Project edits remain unsaved. Workflow duration bindings use that current value, and changing duration or aspect ratio after Prepare invalidates the prepared result.

The setup helper offers to launch MiniConstruct when it finishes. To launch it later from the repository directory in PowerShell:

```powershell
.\run.ps1
```

Open:

```text
http://127.0.0.1:8743
```

To deliberately allow LAN access:

```powershell
.\run.ps1 -BindHost 0.0.0.0 -Port 8743
```

The default loopback binding is safer. Binding to all interfaces can expose MiniConstruct and its configured API credentials to other devices on the network.

### Unix-like systems

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
chmod +x run.sh
./run.sh
```

You can also start MiniConstruct directly:

```sh
python -m miniconstruct --host 127.0.0.1 --port 8743
```

The health endpoint is:

```text
http://127.0.0.1:8743/api/health
```

Local API documentation is available at:

```text
http://127.0.0.1:8743/api/docs
```

## Connect a prompt-writing model

Open **Settings** and enter the OpenAI-compatible Base URL, API key if required, and model ID.

**Backend context capacity** describes the context size the backend is already loaded with; MiniConstruct uses it for estimates, diagnostics, and warnings, not to resize or reload the model server. Story estimates are advisory because provider tokenization is authoritative. **Max output tokens** is the requested completion budget in addition to input tokens. Leave capacity blank if unknown.

The Base URL should include the API prefix expected by the server, commonly `/v1`.

MiniConstruct can discover models when the server exposes a compatible model-list endpoint, but manual model IDs are always supported.

Enable **Model accepts images** only when both the selected model and server support OpenAI-compatible multimodal Chat Completions.

Generate and Regenerate use streaming output, so the prompt appears while the model is writing it. **Stop** closes the active browser request and upstream HTTP stream.

Ordinary H3 prompt-writing Reasoning defaults to **Off**. Story Planner has its own saved reasoning control, which defaults to **On**, and a separate request timeout for slower planning calls.

**Creative Interpretation** controls how much latitude MiniConstruct has to fill open space. **H3 Prompt Detail** controls how fully the H3 writer articulates the chosen idea when generating from Main Prompt; it does not grant more creative license. Standard preserves the existing one-shot prompt-writing behavior. Detailed with conservative interpretation stays faithful while describing the requested scene more concretely; Concise with permissive interpretation allows the permitted creative choices but describes the result compactly. Story generation does not use the one-shot H3 Prompt Detail policy: Story Planner's **Scene Plan Detail** controls upstream planning articulation instead.

Fresh H3 generation from Main Prompt (including regenerate) and Story now includes conservative **Natural Liveliness** guidance. Clearly visible open faces of living characters should avoid an unnaturally frozen performance when subtle biological movement fits stronger scene authority. Cues are sparse and preserve the established gaze, expression, pose, and attention; deliberate stillness remains valid. There is no fixed blink schedule, required blink, or user-facing intensity control. Tone / Performance, Creative Interpretation, exact dialogue, Story authority, continuity, and identity/reference evidence retain precedence. All Main Prompt Detail levels receive the same policy; Revision and Repair Format do not. Existing generated prompts remain historical and selectable/executable under existing rules; the application policy adds no Workspace field or staleness fingerprint change.

## Projects and History

**Projects** are complete editable workspaces stored by the local filesystem backend. The visible Projects list and normal New/Open/Save/Rename workflow use the filesystem Project API.

**New** creates a fresh unsaved Workspace. The first Save creates the filesystem Project and assigns its backend UUID; clicking New alone does not create a persisted Project.

**Save As** offers **Full copy** (the default) and **Fresh working copy**. Full copy retains the existing whole-Project fork behavior: Takes, selected Takes, Render Submissions/Send history, model observations and user corrections, managed Take videos, Saved H3 Prompts and execution selections survive. Disposable observation/handoff caches regenerate on demand. Native attempt/acceptance history is omitted because the new Project owns a distinct execution chain.

Fresh working copy preserves every Generation and its order/active position, canonical Workspaces, Story/Scene Plans, Main Prompt, dialogue and creative controls, planned continuity and Story Memory, Subjects/narrative names/aliases/Character Profiles and allocation watermark, Reference Asset IDs/settings and canonical Picture/Video/Audio payloads, Visual Handoff policy, workflow/routing/control/seed preferences and native capability settings. It assigns normal new Project/Generation identities. It starts with no Takes, observations/corrections, Sends/jobs/import receipts, selected execution prompt/reference map, preparation or native lineage/source-out decisions. **Keep Saved H3 Prompts** is off by default; enabling it retains text libraries, active saved pointers and Save N numbering using Full copy remapping, without restoring execution authority or automatically showing historical text on adoption. Generate → Use for execution → Prepare → Send to start again.

Fresh is a **filtered copy**: generated metadata is projected out before staging and only required canonical Reference Asset files transfer. Historical Take media is never first copied or hard-linked and then deleted. Reference files remain independently usable if the source Project is deleted; renderer-owned external files are never touched.

**Clear generated state** acts on the current saved filesystem Project after explicit confirmation. Save or discard authoring edits first. It applies the same metadata policy as Fresh while preserving Project/Generation identities and active Generation. **Also clear Saved H3 Prompts** defaults off; enabling it empties libraries, clears active pointers and resets fresh-library numbering to 1. Clear checks the loaded Project fingerprint under the transaction lock, commits canonical metadata, then prunes removed Takes' managed media and derived observation/endpoint/tail evidence. Shared blobs remain while another canonical record references them. An OS-locked file may remain as an unreferenced orphan; metadata stays cleared. Pre-existing orphan/reserved files are not a general disk sweep. Browser watchers, sockets, previews, inspections, preparation and visible prompt/validation state reset, and late pre-clear responses are ignored. This leaves a usable Project and is separate from Delete Project.

Both operations leave the **external ComfyUI output directory and external native/custom-node artifacts unchanged**. Clear reclaims only MiniConstruct-owned files beneath the Project root. See the [reset contract and API](docs/execution-orchestration.md#fresh-working-copy-and-clear-generated-state). No persisted schema changes: Project v17, Workspace v1, execution configuration v7, sidecar v4.

For a saved filesystem Project, Reference Asset Add, Remove, Replace, role, Subject bindings, identity focus/view/layout, comparison bindings and order save through a dedicated reference boundary. They do not save unrelated Main Prompt or Workspace edits. Asset Notes join that collection on field completion and before Generate / Use / Prepare / Send. Saved reference Notes do not mark an otherwise clean Project dirty or clear unrelated authoring dirtiness. Failed Notes saves retain the local text and report an unsaved error. A new unsaved Project keeps local references and Notes dirty until first Save.

**Replace Picture** opens the normal image picker and preserves the semantic slot: asset ID, role/order, Notes/options, Subject binding, focus, view/layout and comparison bindings. It replaces filename, MIME type, dimensions and processed canonical image bytes. **Remove** prevents new execution from resolving the removed asset after canonical success; failed edits retain the old card and show an error. Neither action generates or sends automatically. Reference changes clear execution selection and preparation, invalidate validation and stale saved prompts/drafts without rewriting their text. Explicitly **Use for execution** again before Prepare. Changed Picture numbering cannot silently substitute another asset into an old prompt's reference map.

Historical renders, Takes and saved prompts retain truthful provenance. Their references cannot restore current asset authority. Independent copies in other Generations remain intact; shared immutable blobs are retained safely on disk. See the [reference authority contract](docs/execution-orchestration.md#reference-asset-authority).

Existing browser/IndexedDB Project records remain discoverable under **Legacy browser Projects** and can be explicitly imported into a new filesystem Project. The legacy source is retained after import. Filesystem Projects can be permanently deleted after an explicit confirmation; legacy browser records may also be deleted explicitly.

Portable project files can still be exported and imported. An imported project becomes a new filesystem Project after it is saved.

Exported `.miniconstruct-project.json` files can contain processed Pictures but never API keys or raw Video/Audio.

Because raw Video and Audio are not stored inside a Project, imported or reloaded projects may show **media not attached** until the original local file is reattached. Its semantic settings are preserved.

Explicit Generation-owned **Saved H3 Prompts** supersede automatic browser History in the normal prompt workflow. Existing IndexedDB History records remain intact for compatibility; the UI no longer surfaces them or automatically adds model-generation/repair results. Legacy History adoption support remains dormant and is not execution authority.

Saved prompt libraries and selected execution prompts are separate records. Filesystem Full copy Save As copies each library and active pointer onto the corresponding newly identified Generation. Duplicate Generation and new or Story-created Generations start with empty libraries; deleting a Generation removes its library. Portable active-Workspace export excludes saved prompts and the other execution sidecar data.

### Filesystem Project foundation

MiniConstruct’s server-side filesystem-backed Project storage provides durable, portable project storage, while legacy History remains preserved in browser IndexedDB.

By default, filesystem Projects are stored in the platform application-data directory: `%LOCALAPPDATA%\MiniConstruct\Projects` on Windows, or `$XDG_DATA_HOME/miniconstruct/projects` (falling back to `~/.local/share/miniconstruct/projects`) on Unix-like systems. Set `MINICONSTRUCT_PROJECTS_ROOT` to choose another root before starting the server.

Each filesystem Project has a UUID-based directory and a versioned `project.json` manifest, with immutable content-addressed image payloads kept under `assets/pictures/`. Projects persist one canonical linear ordered Sequence of stable-identity Generations. The UI can add, insert, duplicate, activate, delete, and move Generations; displayed numbers come from current order, while Generation UUIDs remain internal and stable. A new Generation is blank/default, while Duplicate copies the committed authoring state and asset mappings. Normal Save updates only the active Generation. The manifest stores structured Workspace metadata and references to owned asset files rather than embedding image data URLs. `outputs/media/` holds managed rendered Take videos; `workflows/` and `provenance/` remain reserved. Duplicate Generation copies authoring only, without render submissions or Takes.

**Rendered Takes (Continuity 2.0 Phase B)** appears below ComfyUI preparation for each saved Generation. A **Generated H3 Prompt** is textual render instruction; a **Render Submission** is an accepted renderer job; a **Take** is an actual imported video; a **Selected Take** is the actual output explicitly chosen by the user. **Selected generated prompt != Selected Take. Selected Take != Observed End State.** Selecting a video does not analyze it. Its existing effective observation can change dependent later H3 context; Story Memory and Planned Continuity are not rewritten.

Import an MP4, WebM, MOV, MKV, AVI, M4V or MPEG video (up to 8 GiB) and explicitly choose its provenance: a recorded ComfyUI submission, the exact generated prompt previously chosen with **Use for execution**, or **External / unknown source**. Choose a submission explicitly; filenames, dates and newest jobs are never used to infer it. Multiple Takes can share one prompt or submission. No Take is selected automatically. One expandable browser preview keeps the list compact; playback depends on browser codec support.

Take operations save immediately. Exact prompt text, its SHA-256, source fingerprint, known H3 mode, existing execution-selection identity/time and reference map are captured immutably. Browser History is not canonical output provenance. Unknown sources remain honestly unknown. ComfyUI Send records provenance only after acceptance; queue acceptance is not completion.

Rendered Takes uses one labelled inspection dropdown, one inspected Take details/action area and at most one Take video preview. Options show **✓ SELECTED** for the Project Selected Take, filename, local time, exact Send provenance or a truthful external/unknown label, and missing-media status. Initial inspection chooses the Project Selected Take, otherwise the newest; refresh preserves inspection. Inspecting another Take never changes Project selection, Observed End State, Story context, Visual Handoff or native authority. Preview, Select Take, Delete Take, View prompt provenance and exact-byte Reattach operate on the inspected UUID. Clear Selected Take remains explicit. ComfyUI render history follows the actual Take and Manual import areas, closed by default; expand it for the existing Watch/Cancel/Recover/Retry/import controls.

**Exact ComfyUI tracking and automatic Take import (Phase F1):** Rendered Takes shows Queued, Running, Completed, Failed or Unknown for each recorded send. While this area is mounted and the page is visible, pending jobs are checked every 15 seconds. **Refresh render status** also reconciles older sends after reopening the Project. The backend uses the recorded Connection and exact Prompt ID, preferring `/api/jobs/{id}` with an exact `/history/{id}` compatibility fallback. Older servers may show Unknown until terminal history exists. No newest-job or output-directory search occurs.

**Live observation and exact Cancel (Phase F2a):** **Live Render Preview** sits between Prepare / Send to ComfyUI and ComfyUI Execution Settings, independently of the bottom, collapsed ComfyUI render history. One transient **Project-scoped foreground observer** identifies its exact render as **Generation N · ComfyUI send N**. Editing or navigating to another Generation in the same Project preserves an active watcher; automatic following never changes the Generation selector or authoring edits. Project switching closes the socket, revokes its Blob URL and clears media, progress and the transient observation choice.

Automatic selection prefers canonical running → queued → unknown status, then canonical Sequence order across Generations, then newest `createdAt` and descending stable ID within a Generation. A running A remains watched when newer queued B arrives; equal-priority attempts do not churn. An automatic queued/unknown watcher may upgrade to a strictly better canonical status. After exact terminal HTTP reconciliation, its socket/media and any explicit choice clear, and the next active Project submission is watched automatically: G1 → G2 → G3 can proceed while the editor stays on G1. When no Project render is active, the panel hides. A bounded read-only Project summary supplies at most the best active candidate and the exact watched record; one foreground polling cadence discovers renders without polling every Generation independently. **Watch / Reconnect** in history explicitly pins that exact active attempt across same-Project Generation navigation; terminal state resumes automatic selection. No watched pointer is persisted.

A genuinely disconnected socket with canonical running/queued/unknown truth reconnects to the same exact submission on normal foreground polling. Repeated failures back off 15, 30, 60, then 120 seconds (capped), without an immediate reconnect loop; useful progress/media resets this delay. Merely connecting does not reset repeated failures. Manual **Watch / Reconnect** can force a fresh connection. Hiding a browser tab pauses HTTP polling while preserving the socket; foreground polling resumes on return. **Cancel render** in the promoted panel and history share the exact recorded Project/Generation/submission cancellation path, ultimately targeting only `/api/jobs/{exactPromptId}/cancel`. **Cancel requested…** retains the preview until canonical terminal confirmation. Cancelling another queued attempt preserves the foreground render. Unsupported cancellation remains unavailable; there is no global interrupt fallback.

The panel shows connection status, node/sampler progress, elapsed time and transient preview media. Standard JPEG/PNG, animated WebP and MP4 plus optional Model Preview Override/KJ-style normalization remain supported, with stable media-node reuse and Blob cleanup. Preview bytes never become Project media or Takes. Socket events are isolated to the exact prompt/client; disconnect is not renderer failure. Canonical HTTP reconciliation and existing confirmed-cancellation/native-attempt behavior remain authoritative. Observation implies no Take selection, native acceptance/revocation, observed-state, Visual Handoff, Workflow Controls, execution selection, Prepare, Send, Retry, or Story/H3 mutation. No schema/version bump or live ComfyUI validation is claimed for this change.

**Exact Recover and explicit Retry (Phase F2b):** Each RenderSubmission card can **Recover exact status** using its recorded ComfyUI prompt ID and Connection. Recover only reconciles that existing attempt; it never queues a prompt. It is safe for unknown or disconnected jobs and preserves idempotent output import. Failed and cancelled attempts offer **Retry exact attempt** with confirmation. Retry requires the current selected prompt, Workflow Profile, imported workflow and execution setup to match the old attempt, then runs the normal preparation and Send path. It creates a new ComfyUI job, RenderSubmission and native continuation attempt when applicable, and reuses the recorded controlled renderer seed. The old attempt stays in history. Neither action selects a Take or accepts native state. Unknown jobs must be recovered; there is no automatic resend. Exact Cancel 404 is diagnosed using job and route evidence instead of always being called unsupported.

**Render next ready Generation:** The Sequence panel has a one-shot action that inspects saved Generations in canonical order, opens the first unresolved render candidate by its stable UUID, and fills the existing Prepare result. It requires an explicit saved H3 execution selection and saved Workflow Profile preference. A sole configured Connection follows the normal selector default; with multiple or no Connections, choose one in the existing execution panel. Active or unknown exact jobs stop automatic selection to avoid duplicate work; completed output decisions stop it until the user selects/imports the intended output or accepts available native state. Failed or cancelled attempts may use a fresh ordinary Prepare; exact Retry stays separate. Prepare remains the graph compiler, including current Workflow Controls, visual handoff and accepted native state even across Profile fingerprint drift. The result is advisory for this convenience action: manual Use for execution, Prepare and Send remain available. No Send, scheduler, worker, automatic Retry, Take choice or native acceptance is performed.

One safe supported video from that exact completed job is downloaded directly into managed storage and appears as **Completed · Take imported**, with the same **ComfyUI send N · local time** provenance label and existing preview. Multiple videos expose explicit **Import output** choices under that send; zero videos show **Completed — no retrievable video output**. The completed Take is the primary result; renderer history is supporting diagnostic/job-control detail at the bottom, collapsed initially and on Generation switch. Its summary shows the total count and concise active/latest status; ordinary refresh preserves the user’s open/closed choice. **Manual import / fallback** remains available for external videos, older workflows and outputs MiniConstruct cannot retrieve. Automatic import never selects a Take, accepts native state, or retries the render. A failed job marks its associated pending native execution unavailable; successful completion still requires explicit native artifact confirmation because the generic profile contract cannot prove the Save node ran.

Videos stream to temporary files, are hashed incrementally, and are published atomically under `outputs/media/<sha256>.<extension>` without decoding, transcoding or recompression. Shared bytes can have separate Take records. Deleting a Take or Generation prunes blobs only when no Take in that Project references them; deleting the selected Take clears its selection. Missing media keeps its record and can be explicitly reattached only with identical SHA-256 bytes; different bytes require a new Take. Full copy Save As preserves submissions, Takes, selections and videos with hard links where available and bounded file-copy fallback, independently of the original Project. Portable `.miniconstruct-project.json` remains an active-Workspace format and excludes filesystem Take metadata and video payloads. See [output storage and API details](docs/execution-orchestration.md#rendered-takes-continuity-20-phase-b).

**Observed End State (Continuity 2.0 Phase C)** appears beside Rendered Takes. Save authoring, select a Take, then choose **Analyze Selected Take** using the normal vision-capable LLM settings. `external_unknown`, `selected_prompt` and `comfyui_submission` are equally analyzable: prompt provenance, an execution selection and ComfyUI Send are not prerequisites. Selection alone never makes a provider call.

**Intended / Planned Outgoing State** is the intended result; **Selected Take** is the chosen actual video; **Observed End State** is its evidence-derived endpoint; **Effective Observed End State** includes explicit user corrections. Inspect the endpoint and tail contact sheet, edit the structured fields, and **Save Corrections**. The original model result remains inspectable. **Reset Effective State to Model Observation** restores it. Re-analyzing edited state requires confirmation before replacing corrections.

Introduced in Project v14, separate per-Take observations are stored bound to UUID + SHA-256. The packaged PyAV decoder reads locally in a bounded worker; it does not change the video. At most four samples from the final two seconds form a compact contact sheet plus the actual last decoded frame. Only these two resized JPEGs and up to four local identity Pictures reach the analyzer; no video, whole Story history or future Generations are sent. Malformed responses fail visibly without hidden retries. Derived images live under Project outputs, never in `project.json`.

Same-scene H3 continuity uses: current explicit authoring / Incoming State > predecessor user correction > predecessor model observation > predecessor intended outcome > trusted Story Memory > profile/reference defaults. An observed jacket still on beats a planned removal; an explicit current reset can override it. Selecting another analyzed Take or editing effective state stales dependent H3 prompts, while the source Generation's own historical prompt and independent successors remain unaffected. Unanalyzed selections fall back to planned state with a warning. Plan Next and bounded Insert/Replan context receive selected effective observations as read-only evidence. Analysis never automatically mutates Story Memory.

Missing raw media retains existing hash-bound observations but prevents new analysis. Reattach exact bytes to recover. Missing derived evidence regenerates on review from available raw media. Full copy Save As preserves both model and effective observations and copies raw Takes; disposable evidence is regenerated on demand. Deleting a Take/Generation removes its observations and derived evidence without affecting other Takes. Phase C contributes **textual/structured state**. Phase D independently supplies lightweight actual predecessor media as described below. See [observation architecture](docs/execution-orchestration.md#observed-end-state-continuity-20-phase-c).

**Visual Handoff (Continuity 2.0 Phase D)** sits beside Relationship to Previous and Camera / Shot Intent. Textual continuity says what MiniConstruct observed; Visual Handoff supplies lightweight actual predecessor media to H3; identity references say who Subjects are. They complement one another. Endpoint/tail handoff needs only the immediately previous Generation's explicitly Selected Take and its raw media, even if it has never been analyzed. Native execution state has separate lineage and can exist before any Take is imported.

For Continue previous shot, Auto first uses **native H3 continuation** when the active Workflow Profile supports it and compatible predecessor execution state is confirmed; otherwise it uses a **1.5-second video-only tail continuation reference** when supported, then textual Observed/Planned continuity. Auto uses an **endpoint frame → immediate new shot** for New shot — preserve continuity, an **endpoint frame** for Match previous ending frame, and no media for Independent. Override with None, Endpoint frame, Endpoint → immediate new shot, or Tail continuation reference. Native H3 continuation is also offered when the active profile supports it; an unavailable explicit Native choice blocks Prepare/Send with guidance. Tail duration is **0.5–2.0 seconds**, using available duration for short Takes; opening-cut intent defaults to **100 ms**, bounded to **0–500 ms**. The tail gives Ref2VA recent motion and state: author from its ending state, without claiming that target opening frames repeat it. Cut timing is requested authoring intent, not a millisecond guarantee.

Ref2VA accepts either handoff. I2VA/FL2VA can use an endpoint in a free first-frame slot; an existing user anchor is preserved, and FL2VA still needs its last-frame Picture. T2VA and L2VA cannot represent this opening evidence. Auto warns and omits unavailable/incompatible handoff while retaining textual continuity; explicit visual choices block with an actionable error. The H3 mode never changes automatically. Current explicit authoring and Incoming State resets remain higher semantic authority than predecessor pixels.

The H3 Input Map identifies the transient handoff and its deterministic Picture/Video number. **Preview handoff** loads only on request and never autoplays. No normal Reference Asset is saved or imported. ComfyUI preparation routes/uploads the derived input through existing Workflow Profile bindings; the graph remains user-owned. Switching the predecessor's Selected Take stales dependent prompts. Full copy Save As preserves policy and Takes and regenerates disposable cache; delete/reorder follows immediate-predecessor authority. New/manual/Story Generations default to Auto; Duplicate preserves policy without outputs. Story Planner still chooses only the relationship.

Project **v15** introduced `planning.visualHandoff` (`strategy`, `tailSeconds`, `immediateCutMilliseconds`); the current Project schema is **v17**; legacy `short_tail_prefix` values read as the corrected tail-reference strategy without rewriting. Workspace remains v1. The opt-in `h3-visual-handoff` suite covers tail continuation, early cut, endpoint and independent cases with generic synthetic media, saved model inputs and human review. Its manual-render bundle includes every final H3 reference with a case-local manifest. Full Takes remain historical authority and are never automatically supplied in full. See [visual handoff architecture](docs/execution-orchestration.md#visual-handoff-continuity-20-phase-d).

**Native H3 continuation (optional)** is the strongest supported same-shot execution handoff: native AV context → a fresh sampling window with protected/guided overlap → a new suffix. The concrete workflow owns latent storage and hidden-context removal. MiniConstruct maps semantic controls through a Workflow Profile without depending on any custom-node package. Apply the profile in ComfyUI preparation before authoring. After an accepted Send, the execution is **pending**; exact successful render completion makes its associated attempt **available**. Use **Accept native state** only after the workflow saved its state. Availability does not independently verify external native Save storage; acceptance stays explicit. G1 saves only; later Generations consume only their immediate predecessor’s compatible accepted state while saving their own. The real native G1 → G2 workflow/render gate passed manual validation on 2026-09-22, including repeated pure-latent G2 renders with 22 context frames; core remains package-agnostic.

Known Selected Take provenance requires the same RenderSubmission’s artifact. External/unknown Takes use existing fallbacks. Without a Selected Take, an explicitly accepted continuation execution supplies authority; a pending reroll never silently replaces it. Context frames, alignment and audio support are profile-defined. Optional source out-points are recorded execution decisions and never modify the canonical Take. Project remains v17 and Workspace v1; current workflow configuration is v7 and the execution sidecar is v4. See [native capability, mapping and lineage contract](docs/execution-orchestration.md#native-h3-continuation-capability).

Native attempts use one labelled dropdown and one selected-attempt details/action area. Options show native status (including **✓ ACCEPTED**), canonical Send number/local timestamp, and a known output filename or truthful multiple-output count. The accepted attempt is inspected initially, otherwise the newest; refresh preserves inspection selection. Selecting an option changes no Take, Visual Handoff or acceptance authority. Available unaccepted attempts offer **Accept native state** and **Remove native attempt**; accepted attempts offer **Revoke acceptance**, which leaves native state available; unavailable/pending attempts retain existing removal eligibility. Primary **Mark unavailable** controls are gone, while renderer failure/cancellation still marks pending attempts unavailable. Removal deletes only native bookkeeping, preserving renderer jobs/submissions, Takes, media and external native artifacts.

Filesystem Projects use Project schema v17 with canonical Sequence `subjects` and `nextSubjectNumber`; Workspace schema remains v1. Each Subject has an opaque persistent internal ID, an optional preferred `narrativeName`, retained `narrativeAliases`, and a stable technical `Subject N` number. The Identity set selector offers the Sequence cast, including Subjects absent from the current Generation. Selecting one materializes only its ID and number locally; **+ New Subject** allocates from the Sequence counter and may remain unnamed. Fresh and Full copies preserve that canonical cast and allocation watermark; removing a Picture does not delete its Subject. New allocations become canonical with the same atomic save as the Generation.

In Ref2VA, **New Subject Picture** creates a distinct Subject at the next canonical number. **Add identity Picture** inside a Subject section explicitly attaches another Picture to that Subject, including a dormant Subject. **Replace Picture** preserves the selected Picture slot and its existing identity binding. Removing a Picture does not delete a canonical Subject or authorize an unrelated character to take its number.

Generation Workspaces contain only structural `{id, number}` Subject records and attached references; narrative metadata belongs to the Sequence. New modern Story Planner items use `participatingSubjectIds` for the exact visual cast. Apply materializes those Subjects and deterministically carries legal committed `subject_identity` Pictures; optional reference recommendations attach additional assets. Canonical cast availability alone does not add H3 prompt context, vision inputs, or execution references. Duplicate preserves the local subset; deleting a Generation does not delete cast members. Execution routing remains separate.

Supported older Project schemas are read without rewriting their files and upgraded to v17 on the next successful canonical write. Earlier cast migration merges exact repeated Subject ID/number mappings, retaining the highest Generation allocation counter or maximum Subject number plus one. An ID with different numbers, or a number with different IDs, still produces a Sequence cast conflict error and leaves source files untouched. No character renumbering, text rewriting, or identity inference from ID spelling is attempted; conflict-resolution UI is deferred. Earlier formats retain their existing defaults for missing planning metadata and `storyIdea`.

In grouped Ref2VA Reference Assets, set or rename a Subject after saving the Project. Headings and Identity/Comparison selectors show **Alice (Subject 1)**, with **Subject 1** as the unnamed fallback; bindings always use the opaque ID. Renaming retains old names as internal compatibility/recognition metadata; previous names are not normal UI controls or generative vocabulary. MiniConstruct does not silently rewrite historical authored prose to the current name. Saved names can be renamed but cannot be cleared in this foundation. The collapsed **Character Profile** disclosure offers one free-form field for stable character guidance on that same canonical Subject; older structured profile fields remain saved and available to Story Planner context. The Profile is Story Planner context, not direct H3 prompt content or current Story Memory. Story Planner and ordinary H3 authoring expose only the current Narrative Name plus stable ID/`<Subject N>` identity. Relevant name/profile changes stale the applicable Story drafts without rewriting historical outputs. Explicit Subject deletion frees its names and deletes the Profile, but its technical number is never reused. H3 writer requests explicitly list the current stored Subject labels and must never compact a sparse cast by list position; identity examples use an actually assigned label. A richer Story Bible remains future work. See the [narrative identity contract](docs/story-planner.md#narrative-identity).

For H3 output, **Narrative Name is the preferred human/Story identity**. Names help interpret Main Prompt, Story and continuity prose. In **Ref2VA**, `<Subject N>` is the canonical technical full-reference identity: a current name may appear once in its valid definition, then structural references use the token. Exact spoken dialogue and lyrics remain literal inside `<d>[Language] ...</d>`. **T2VA, I2VA, FL2VA and L2VA** follow their base Video grammar and do not require Ref2VA Subject labels or sections. Saved authoring stays human-readable, retained aliases remain recognition metadata, and Character Profiles remain Story Planner context. Format Audit still requires literal Subject tokens in Ref2VA retained shots; Repair does not perform blind name substitution. See the [H3 identity and validation policy](docs/story-planner.md#narrative-identity).

Portable export remains centered on the active Generation's Workspace: it carries that Generation's local Subjects, references, and authoring state, alongside the existing exported Story idea. It additionally carries canonical Sequence metadata, `sequenceSubjects` and `sequenceNextSubjectNumber`, so canonical Subject identity, dormant Subjects, Character Profiles, and the monotonic allocation watermark survive export/import; an explicitly empty cast with a watermark remains meaningful (for example, `sequenceSubjects: []` and `sequenceNextSubjectNumber: 9` means the next Subject is still Subject 9). This does not make portable export a complete Sequence archive: it contains neither every Generation Workspace nor every Sequence asset. Older Workspace-only portable envelopes remain supported through the legacy-compatible import path. Saving an imported Workspace into an existing Project through the API requires exact canonical mappings for existing IDs. New IDs must use unused, consecutive numbers starting at the Sequence counter; conflicting or stale allocations are rejected. Save As copies the entire Sequence cast. Sequences remain linear and legacy browser migration remains a one-Generation import.

### Continuity authoring

Each filesystem Project Sequence has one manually authored **Sequence Context** for facts or rules that should remain broadly consistent. Each Generation also has manually authored **Incoming State** and **Intended Outgoing State** describing the relevant start state and the intended successful end state. These fields form **Planned Continuity** and now influence ordinary H3 prompt authoring after Save. The backend resolves the saved active Generation by UUID and supplies a separate authoring context. Sequence Context guides relevant world constraints; Incoming State defines the start; Intended Outgoing State is a transition target for the end. Planned continuity is not observed truth: explicit Selected Take analysis supplies separate visual evidence. Save requests that omit continuity preserve the stored values; an explicit blank clears a value.

### Assisted continuation and shot planning

Each Generation may also have **Relationship to Previous** and a free-text **Camera / Shot Intent**. The relationship can be **Continue previous shot**, **New shot — preserve continuity**, **Match previous ending frame**, or **Independent / new scene**. The first Generation is shown as **Sequence start**. These saved fields now guide H3 relationship and camera authoring, separately from general Creative Controls and Workspace state; exact H3 frame anchors retain priority.

Story Memory remains a separate planner field and contributes a bounded trusted predecessor projection to H3 (up to 1,500 summary characters and eight durable facts, no Open Threads). Untrusted snapshots are omitted with a warning; independent shots omit predecessor memory. Current visible-state authority is explicit current authoring / Incoming State, then predecessor effective observation, predecessor intended outcome, trusted relevant memory, and profile/reference defaults. Relevant saved changes stale selected generated prompts without rewriting history.

Environment references mean location/layout identity, not automatic camera or first-frame composition authority. Story Apply shares predecessor Environment-only Pictures into new same-scene Generations without duplicating payloads; independent shots require an explicit recommendation. A new bedside angle may use the same room reference without reproducing its wide source view. This is prompt/reference semantics, not a guarantee against model bias. See the [H3 continuity contract](docs/story-planner.md#planned-continuity-to-h3). Selected Takes and explicit observed-output analysis are implemented. Relationship-driven visual handoff is implemented in Phase D; native G1 → G2 passed live manual validation on 2026-09-22, while further production review of other strategies remains useful.

Manual authoring remains fully supported. **Infer from Previous** is an optional, explicit one-Generation action that fills missing prompt, continuity, relationship, and camera-intent fields from the committed predecessor; it never silently runs on Save or Generation changes. **Revise Plan** is also optional and scoped to the fields selected by the user, including **Exact Dialogue / Lyrics**. Dialogue is exact free-form spoken or sung text: multiline text is preserved and an explicit blank means silence. Story Planner can author it initially; after Apply Draft it is the same canonical Generation Workspace field that Revise Plan and normal H3 authoring use. Both actions produce a draft proposal that must be reviewed and saved normally.

### Optional Story Planner

**Story Planner Vision** is a saved preference beside Planner request timeout and Story Planner reasoning. It defaults off. With vision off, Story Planner still receives Picture roles, notes, Subject mappings, reference recommendations, Character Profiles, Story Memory, and other text metadata; it receives no Picture pixels and must not claim to have inspected them. Enable it for visual or spatial planning, such as an environment layout, when **Model accepts images** is also enabled. Planner vision uses disposable RGB JPEG model inputs bounded to 768 pixels on the longest side; canonical Pictures and ComfyUI execution bytes are unchanged. Story Planner reasoning is independent. Ordinary H3 prompt writing keeps its own vision behavior.

**Prepare next ready Generation** finds the earliest Sequence Generation with saved execution authority and runs ordinary Prepare only. It never generates H3 or sends to ComfyUI. Prepare resolves native continuation when applicable; native state is not required for independent or new-shot Generations.

Open **Story Planner** in the Sequence panel of a saved filesystem Project. Save canonical edits first, enter a Story Plan / Premise, save it, and choose **2–12 total Generations** while Generation 1 is the sole saved Generation. **2–4 is recommended** for tighter planning; **5–12 is an explicit Extended Initial Build** that proposes G1 plus up to 11 new Generations in one model request, with sequential Story Memory previews. Extended builds use a larger single model request and may have weaker long-range consistency; larger builds may need a higher **Max output tokens** setting and longer Story Planner timeout, especially with reasoning enabled. Those settings remain user-controlled. For tighter planning, use 2–4 and continue with **Plan Next**. Twelve is a single-operation limit, not a Sequence-length limit. Existing non-empty authored fields and Sequence Context remain authoritative during Build; blank areas may be filled.

MiniConstruct has two explicit H3 authoring actions. **Generate from Main Prompt** uses only the
Workspace's one-shot Main Prompt / Idea as its creative request. **Generate from Story** uses the
saved Project **Story Plan / Premise** (`storyIdea`), the current Generation's **Scene Plan**
(`planning.scenePlan`), ordered Scene Plans, relevant Character Profiles, Story Memory,
continuity, dialogue, references and H3 controls. Main Prompt / Idea is not the Story-to-H3
bridge. Story Planner writes Scene Plans, and Apply leaves Workspace `creativeRequest` untouched.
The prior richer-`creativeRequest` Story approach was superseded after product testing.

The Scene Plan is the only new user-facing Story prose field. The planner can propose dialogue
in the existing Exact Dialogue / Lyrics field when it helps; silence remains valid. For new
Generations it may propose any 4–15 second duration according to scene pacing, using saved
G1 duration only if a proposal is missing or invalid. Replan preserves existing durations. Story
Revision edits Scene Plan under an explicit scope; Plan Next, Insert Between and Replan Range
read current saved Scene Plans, including manual edits. Applied Scene Plans remain editable
in Story Planner and require a normal Project save. No Narrative Brief or staged Initial Build
was introduced.

The permanent **Scene Plan — Current Generation** editor appears after Replan Range near the bottom of Story Planner, before Planned Continuity. Save edits with normal Project Save to use them in Story-aware H3 authoring. Generated Sequence Context is durable framing as the Sequence grows; temporary Initial Range or other operation counts must not become final Sequence length unless explicitly authored by the user. See [Story Planner](docs/story-planner.md) for authority and cancellation details.

Story Planner does not author H3 Tone / Performance enums. New Generations keep the shared Workspace controls at `Auto`; existing user choices remain saved. To override `Auto`, set **H3 Creative Controls** in the Generation Workspace. Those choices affect both Story and Main Prompt H3 generation. Story Creative Direction remains sequence-level planning guidance and is not copied into the controls or Main Prompt.

Nothing in the Project changes until **Apply Draft** succeeds. Edit the proposed per-Generation fields directly; these edits remain ephemeral, retain signed authority, and do not disable Apply or scoped Revision. The backend validates the complete draft on action. Bounded operation drafts retain signed boundaries and read-only projected Story Memory. Ordinary scoped Story Revision consumes the latest manually edited draft and preserves every unselected field exactly. Creative Direction uses structured controls by default, with Custom guidance for prose; it remains soft Story guidance, separate from Story Memory and ordinary H3 controls.

`participatingSubjectIds` is the exact per-Generation visual cast, never a prose name match: discussed or remembered Subjects are not participants. For new Initial Range, Plan Next, and Insert Between items, participating canonical Subjects receive legal committed `subject_identity` Pictures automatically. Reference recommendations remain source-qualified optional additional evidence; duplicate automatic/recommended source pairs attach once. Recommendations on existing Story items remain advisory, and their rationale is not persisted. Pictures may supply actual vision input when **Model accepts images** is enabled; Audio/Video remain metadata-only.

**Apply Draft** saves the complete Sequence in one atomic operation, preserving existing identities, assets, and the active Generation. New Generations are created only then, inheriting the immediately preceding Generation’s H3 mode and materializing participating Subjects, identity evidence and explicit recommendations, plus deterministic same-scene Environment carry-forward. **Discard Draft** clears only the proposal. Navigation or conflicting operations ask before discarding a draft; a changed underlying Project blocks revision/Apply until the draft is rebuilt. The Story Plan / Premise is saved as Project-level `storyIdea`; editing it marks the Project dirty. Save As, copies, and portable export/import retain it. There is no special Project mode.

See the [Story Planner guide](docs/story-planner.md).

Picture references may be supplied as visual planner context when the configured model supports the existing image-input protocol. Audio and Video references contribute continuity-aware metadata, roles, ordering, and user-authored notes, but are not content analysed. Ordinary planner references are not audio/video-analyzed. Explicit Selected Take analysis and automatic bounded visual handoff are separate Generation features.

## Validation and diagnostics

MiniConstruct validates structural H3 requirements including:

- required sections and ordering
- mode-specific alignment
- shot syntax and timestamps
- reference resolution
- Ref2VA retention structures
- dialogue consistency and verbatim dialogue where practical

Findings are reported as **ERROR**, **WARNING**, or **INFO**.

The output diagnostics also expose useful timing and request information such as first upstream event, first final-content token, total output duration, reported token usage where available, and a cache-input fingerprint.

**Show LLM Instructions** displays the textual context MiniConstruct assembled for the prompt writer, excluding API keys and binary image data.

## Official H3 specification

MiniConstruct is built around the official MiniMax H3 prompting material from:

[MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3)

The public release acquires official guides at upstream commit:

```text
d21241f0a4b3acbb34c97dae47fa417b7065e438
```

Exact upstream paths and SHA-256 hashes are recorded in `miniconstruct/h3/guides/provenance.json`. Complete official guides are acquired during setup and are not redistributed.

MiniConstruct's own operating instructions are kept separately from the official guide material so application-specific behavior does not silently modify the underlying H3 specification.

## Troubleshooting

**Model discovery fails**

Check that the Base URL includes the server's required API prefix, commonly `/v1`. You can always enter the model ID manually.

**Connection refused**

Make sure the prompt-writing server is running and the configured host/port are correct.

**Images are ignored or rejected**

Enable **Model accepts images** only when the selected model and backend support multimodal OpenAI Chat Completions.

**The generated prompt has validation errors**

Inspect the findings first. If the creative content is correct and only the H3 formatting is wrong, use **Repair Format**.

**Imported Video or Audio says `media not attached`**

Reattach the original local file to the existing reference card. Its saved semantic configuration is retained.

## Development

Run the Python tests with:

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

Tests use mocked HTTP transports and do not require a live prompt-writing model.

The frontend is plain HTML, CSS, and browser ES modules with no Node.js build step.

The backend uses FastAPI and Pydantic, with H3 assembly, validation, API routing, and the OpenAI-compatible client kept in separate modules.

## Privacy and security

MiniConstruct binds to loopback by default.

Filesystem Projects remain under the configured local Project data root. Prompt-writing LLM settings remain in browser-local settings/profile storage, while History remains in browser IndexedDB. Execution / ComfyUI Connections and Workflow Profiles remain in a separate machine-local ExecutionStore.

API keys are excluded from Project exports, History, inspectors, and server logs. API keys are not persisted unless you explicitly enable key persistence.

Picture/image payloads are sent to the configured prompt-writing endpoint only when vision is enabled; raw Video and Audio are never sent to the prompt-writing LLM. When the user explicitly chooses **Send to ComfyUI**, the bound prepared media required by that workflow may be uploaded to the selected ComfyUI Connection; this is separate from prompt-writer vision transmission.

Raw Video and Audio bytes are never sent to the prompt-writing LLM.

Using a remote endpoint or binding MiniConstruct outside loopback expands the trust boundary accordingly.

### Story Planner Planning Style

The collapsed **Planning Style** section beside Story Creative Direction offers **Planning Creativity** (Faithful, Balanced, Exploratory) and **Scene Plan Detail** (Concise, Standard, Detailed). Balanced + Standard is the compatibility default and adds no model-facing guidance. Creativity controls plausible local invention within saved story authority; detail controls how concretely the chosen development is described. Creative Direction separately controls tone, performance, pacing and beat density. Sparse + Detailed describes one development richly, while Exploratory + Concise can summarize a more inventive local beat compactly. Detail does not add plot, dialogue, shots, duration or H3 camera syntax. Values are Project authoring, mark the Project dirty, persist on Save and Save As, and survive portable export/import. Project schema is v17; v1–v16 read without mutation and adapt to defaults. Workspace stays v1. See [Story Planner](docs/story-planner.md#project-planning-style).

### Story Planner shot plans

Story Planner chooses a positive shot count for each Generation and writes an ordered **Shot Plan / Camera Intent**. In Build Initial Range, Plan Next, Insert Between, and Replan Range, operation-local **Shot guidance** offers Auto (the existing discretion to choose the fewest purposeful shots), Prefer N (a soft target of about N shots per Generation), and Exact N (a required count for every target Generation). N is **per Generation**, not a total-shot budget for the operation. The guidance is not saved in the Project; Apply saves the resulting ordinary `planning.shotCount`. A plan can hold one sustained shot or several purposeful cuts; it does not assign exact timestamps. Incoming State anchors the first shot, and Intended Outgoing State anchors the end of the final shot. Applying a Story draft copies its count into H3 Setup > Shots. A later manual H3 Setup edit is authoritative for H3 generation; a difference from the saved Story count is advisory, and H3 adapts the plan to the requested count. The optional `planning.shotCount` is an additive Project v16 field; older Generations derive it from their saved Workspace.shots where available on read and persist it on the next canonical write. Workspace remains v1.

## Public distribution and official guide setup

MiniConstruct's original source is licensed under **GPL-3.0-or-later**; see
[LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
MiniMax's documentation and model materials remain separate third-party works.

This repository excludes the three complete official H3 guides. Windows setup
(`setup-miniconstruct.bat`) installs dependencies and acquires the pinned guides
automatically. For a manual installation on any platform, activate your Python
environment and run:

```sh
python -m pip install -e '.[dev]'
python -m miniconstruct.h3.guide_acquisition
python -m miniconstruct
```

Acquisition uses upstream commit `d21241f0a4b3acbb34c97dae47fa417b7065e438` and
verifies raw SHA-256 hashes recorded in `miniconstruct/h3/guide_snapshot.py` and
`miniconstruct/h3/guides/provenance.json`. Existing files are verified too; an
altered file is reported and preserved. Startup requires the authentic guides.
No substitute guides are used. If upstream is unavailable, setup reports the
missing dependency and URL; retry acquisition when it is available.

The Python tests require guide acquisition first. Tests use disposable local
data and mocked providers; they do not require a running model or ComfyUI.
