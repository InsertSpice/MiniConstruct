# MiniConstruct

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
- Associate references with stable semantic **Subjects**
- Use image references directly with a vision-capable prompt-writing model
- Use **Reference Sheets** as multi-view identity evidence
- Use **Character Comparison / Scale** references for relative height and body proportions
- Preserve **exact dialogue**
- Build **video continuation** and **seamless overlap continuation** prompts
- Control camera preferences, tone, performance, music, visual style, identity fidelity, and prompt-generation seed
- Generate multiple prompt variations
- Validate generated H3 structure and reference usage
- Repair structural formatting problems
- Highlight part of a generated prompt and **revise only that selection**
- Save editable Projects and keep recent generated prompts in local History

MiniConstruct handles the H3-specific instructions behind the scenes while keeping the creative request itself editable.

## Example workflows

### Reference-driven character performance

Attach one or more images of a character, mark them as Subject / Identity references, describe the scene and performance you want, then use Creative Controls to steer things such as camera movement, performance energy, tone, and identity fidelity.

MiniConstruct turns the workspace into a Ref2VA prompt that explains how H3 should use the supplied references.

### Reference Sheets

A Subject / Identity Picture can use **Auto**, **Single view**, or **Reference sheet** layout handling.

Reference Sheets are treated as complementary identity evidence for one Subject rather than as multiple copies of that character. Multi-view sheets can provide evidence for front, three-quarter, profile, rear, full-body, and facial appearance where those views are visibly present.

Expression examples are treated as evidence of how the same face deforms rather than instructions to reproduce an expression sequence. MiniConstruct also tells the prompt-writing model not to recreate the sheet's grid, panels, annotations, or repeated character copies as target-video composition.

### Character Comparison / Scale

A Picture can be assigned the **Character Comparison / Scale** role and linked to multiple Subjects.

This allows a shared reference to establish relative relationships such as:

- height difference
- body-size contrast
- broad body proportions

The comparison reference is relationship guidance rather than target-shot composition authority. Its poses, staging, camera angle, background, and side-by-side layout do not need to be copied into the target video.

### Image-to-video

Choose **I2VA**, attach the required first-frame Picture, describe what should happen after that frame, and generate the finished H3 prompt.

For first-and-last-frame generation, use **FL2VA**. For last-frame-only generation, use **L2VA**.

### Video continuation

Attach a previous video as the continuation source and describe what should happen next.

For transitions between separately generated clips, **Seamless overlap continuation** can use the ending portion of the previous clip as overlap material so the new generation continues through the boundary instead of resetting the action, pose, camera, or momentum.

### Exact dialogue

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

Music can be left on **Auto**, explicitly encouraged, or disabled when you do not want the generated H3 prompt inventing musical content.

### Subject Identity Fidelity

Identity Fidelity controls how strongly MiniConstruct tells the prompt writer to preserve identity evidence supplied by Subject references.

Identity references can also be described by focus, such as facial identity, full-body appearance, outfit/clothing, or detail.

Identity preservation targets stable appearance and morphology rather than transient expression, gaze, pose, or other momentary performance state.

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

Revision works from the current generated prompt rather than regenerating the entire output.

**Repair Format** is deliberately narrower. MiniConstruct can make deterministic structural corrections where possible and otherwise uses a focused repair request rather than reinterpreting the whole scene.

## Reference-driven prompting

Pictures, Videos, and Audio are numbered independently.

A reference can be assigned a role, Notes, and semantic relationships describing how it should be used.

A `<Subject N>` represents a visible semantic entity in the target video rather than being another name for `<Picture N>` or `<Video N>`.

Several assets may contribute to the same Subject, while one reference may also describe a relationship between multiple Subjects.

### Image vision

When **Model accepts images** is enabled, Pictures are sent to the prompt-writing LLM using OpenAI-compatible `image_url` content.

Pictures are resized in the browser to a maximum longest side of 1600 pixels before being stored and used.

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

Shot count may be automatic or explicitly chosen. MiniConstruct validates shot timestamps, required sections, reference resolution, dialogue, mode-specific alignment, retention relationships, and other structural rules.

## Dialogue and continuation

Exact dialogue is entered separately from the main creative request so wording can be preserved verbatim while the creative field remains free to describe timing, reactions, interruptions, pauses, camera behavior, and performance.

MiniConstruct also supports the official `[video continuation]` relationship for continuing from a source video's ending state.

**Seamless overlap continuation** is a stronger continuation strategy that uses the ending portion of the previous clip as overlap material so the next generation can continue through the boundary with less risk of replay, reset, camera discontinuity, or lost momentum.

## Installation

### Requirements

- Python 3.12 or newer
- A modern Chromium, Firefox, or Safari browser
- An OpenAI Chat Completions-compatible prompt-writing server

Typical local options include LM Studio, Ollama, Unsloth Studio, and other compatible servers.

No Node.js frontend toolchain, database server, account, or cloud service is required.

### Official H3 guide setup

MiniConstruct uses the official MiniMax H3 prompting material, but the complete official guide files are **not redistributed in this repository**.

After installing MiniConstruct, run the guide-acquisition command once:

```text
python -m miniconstruct.h3.guide_acquisition
```

This explicitly downloads the pinned official MiniMax H3 guide snapshot and verifies the expected files.

Guide acquisition does not happen silently during generation. If the required guides are missing, MiniConstruct stops during startup and prints the acquisition command and expected paths.

### Windows

For first-time setup, double-click `setup-miniconstruct.bat` (or run it from any directory). It creates or reuses the repository-local `.venv`, installs MiniConstruct, and acquires the pinned official MiniMax H3 guides. Existing environments are never deleted or replaced automatically.

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

### Linux and macOS

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m miniconstruct.h3.guide_acquisition
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

The Base URL should include the API prefix expected by the server, commonly `/v1`.

MiniConstruct can discover models when the server exposes a compatible model-list endpoint, but manual model IDs are always supported.

Enable **Model accepts images** only when both the selected model and server support OpenAI-compatible multimodal Chat Completions.

Generate and Regenerate use streaming output, so the prompt appears while the model is writing it. **Stop** closes the active browser request and upstream HTTP stream.

Reasoning defaults to **Off** because MiniConstruct primarily wants direct structured output.

## Projects and History

**Projects** are complete editable workspaces stored by the local filesystem backend. The visible Projects list and normal New/Open/Save/Rename workflow use the filesystem Project API.

**New** creates a fresh unsaved Workspace. The first Save creates the filesystem Project and assigns its backend UUID; clicking New alone does not create a persisted Project.

Existing browser/IndexedDB Project records remain discoverable under **Legacy browser Projects** and can be explicitly imported into a new filesystem Project. The legacy source is retained after import. Filesystem Projects can be permanently deleted after an explicit confirmation; legacy browser records may also be deleted explicitly.

Portable project files can still be exported and imported. An imported project becomes a new filesystem Project after it is saved.

Exported `.miniconstruct-project.json` files can contain processed Pictures but never API keys or raw Video/Audio.

Because raw Video and Audio are not stored inside a Project, imported or reloaded projects may show **media not attached** until the original local file is reattached. Its semantic settings are preserved.

**History** stores the 50 most recent completed generated prompts and their validation metadata in browser IndexedDB.

Projects and History are separate.

### Filesystem Project foundation

MiniConstruct’s server-side filesystem-backed Project storage provides durable, portable project storage, while History remains browser/IndexedDB-backed.

By default, filesystem Projects are stored in the platform application-data directory: `%LOCALAPPDATA%\MiniConstruct\Projects` on Windows, or `$XDG_DATA_HOME/miniconstruct/projects` (falling back to `~/.local/share/miniconstruct/projects`) on Unix-like systems. Set `MINICONSTRUCT_PROJECTS_ROOT` to choose another root before starting the server.

Each filesystem Project has a UUID-based directory and a versioned `project.json` manifest, with immutable content-addressed image payloads kept under `assets/pictures/`. Projects persist one canonical linear ordered Sequence of stable-identity Generations. The UI can add, insert, duplicate, activate, delete, and move Generations; displayed numbers come from current order, while Generation UUIDs remain internal and stable. A new Generation is blank/default, while Duplicate copies the committed authoring state and asset mappings. Normal Save updates only the active Generation. The manifest stores structured Workspace metadata and references to owned asset files rather than embedding image data URLs. `outputs/`, `workflows/`, and `provenance/` are reserved for later phases.

Existing schema v1, v2, v3, and v4 filesystem Projects continue to load safely. They are adapted in memory to the canonical schema-v5 representation, with blank planning metadata where it did not previously exist and an empty Project-level `storyIdea`, and are upgraded to schema v5 on the next successful write without being rewritten merely by listing or opening them. Sequences remain linear. Portable export remains active-Generation/Workspace-oriented for now, and legacy browser migration remains a one-Generation import.

### Continuity authoring

Each filesystem Project Sequence has one manually authored **Sequence Context** for facts or rules that should remain broadly consistent. Each Generation also has manually authored **Incoming State** and **Intended Outgoing State** describing the relevant start state and the intended successful end state. These are plain authoring notes: MiniConstruct does not propagate state automatically, analyze generated output, or feed these notes into H3 prompt generation yet. Save requests that omit continuity preserve the stored values; an explicit blank clears a value.

### Assisted continuation and shot planning

Each Generation may also have **Relationship to Previous** and a free-text **Camera / Shot Intent**. The relationship can be **Continue previous shot**, **New shot — preserve continuity**, **Match previous ending frame**, or **Independent / new scene**. The first Generation is shown as **Sequence start**. These are planning metadata, separate from general Creative Controls and Workspace state.

Manual authoring remains fully supported. **Infer from Previous** is an optional, explicit one-Generation action that fills missing prompt, continuity, relationship, and camera-intent fields from the committed predecessor; it never silently runs on Save or Generation changes. **Revise Plan** is also optional and scoped to the fields selected by the user. Both actions produce a draft proposal that must be reviewed and saved normally.

### Optional Story Planner

Open **Story Planner** in the Sequence panel of a saved filesystem Project. Save canonical edits first, enter a high-level Sequence Idea, save it, and choose **2–12 total Generations**, at least the existing count. **Build Sequence** proposes an editable draft containing existing Generations in order followed by new slots. Existing non-empty authored fields and Sequence Context remain authoritative during Build; blank areas may be filled.

Nothing in the Project changes until **Apply Draft** succeeds. Edit draft Sequence Context and per-Generation fields directly, or select Generations, fields, and an instruction for **Revise Selected**. Revision preserves every unselected field exactly and cannot change Sequence Context. Unselected neighbors are never silently rewritten; upstream/downstream warnings may require manual reconciliation.

Reference recommendations remain source-qualified. Valid recommendations on new Story items are planned references: Apply attaches the committed source references and required Subjects. Recommendations on existing Story items remain advisory, and their rationale is not persisted. Pictures may supply actual vision input when **Model accepts images** is enabled; Audio/Video remain metadata-only. No media analysis occurs.

**Apply Draft** saves the complete Sequence in one atomic operation, preserving existing identities, assets, and the active Generation. New Generations are created only then, inheriting the immediately preceding Generation’s H3 mode and attaching only planned source references with their required Subjects. **Discard Draft** clears only the proposal. Navigation or conflicting operations ask before discarding a draft; a changed underlying Project blocks revision/Apply until the draft is rebuilt. The High-level Sequence Idea is saved as Project-level `storyIdea` in schema v5; editing it marks the Project dirty. Save As, copies, and portable export/import retain it. There is no special Project mode.

See the [Story Planner guide](docs/story-planner.md).

Picture references may be supplied as visual planner context when the configured model supports the existing image-input protocol. Audio and Video references contribute continuity-aware metadata, roles, ordering, and user-authored notes, but are not content analysed. MiniConstruct does not yet perform audio/video analysis or automatic last-frame handoff.

## Validation and diagnostics

MiniConstruct validates structural H3 requirements including:

- required sections and ordering
- mode-specific alignment
- shot syntax and timestamps
- reference resolution
- Ref2VA retention structures
- Subject/reference provenance where applicable
- dialogue consistency and verbatim dialogue where practical

Findings are reported as **ERROR**, **WARNING**, or **INFO**.

**Show LLM Instructions** displays the textual context MiniConstruct assembled for the prompt writer, excluding API keys and binary image data.

## Official H3 specification

MiniConstruct is built around the official MiniMax H3 prompting material from:

[MiniMax-AI/MiniMax-H3](https://github.com/MiniMax-AI/MiniMax-H3)

The public release currently uses the pinned upstream commit:

```text
d21241f0a4b3acbb34c97dae47fa417b7065e438
```

The complete official H3 guide files are not redistributed in this repository. Instead, `python -m miniconstruct.h3.guide_acquisition` explicitly retrieves the pinned files from upstream during setup.

Exact upstream paths, provenance information, and SHA-256 hashes are recorded under `miniconstruct/h3/guides/`.

MiniConstruct's own operating instructions are kept separately from the official guide material so application-specific behavior does not silently modify the underlying H3 specification.

## Licensing and third-party materials

MiniConstruct's original source code is licensed under **GPL-3.0-or-later**. The full license is in [LICENSE](LICENSE).

MiniMax H3, MiniMax model weights, and official MiniMax documentation are separate third-party materials. Acquiring the official H3 guides does not place those files under MiniConstruct's GPL license.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for the relevant MiniMax sources and licensing information.

## Troubleshooting

**Model discovery fails**

Check that the Base URL includes the server's required API prefix, commonly `/v1`. You can always enter the model ID manually.

**Connection refused**

Make sure the prompt-writing server is running and the configured host/port are correct.

**Required H3 guides are missing**

Activate the virtual environment and run:

```text
python -m miniconstruct.h3.guide_acquisition
```

Then start MiniConstruct again.

**Images are ignored or rejected**

Enable **Model accepts images** only when the selected model and backend support multimodal OpenAI Chat Completions.

**The generated prompt has validation errors**

Inspect the findings first. If the creative content is correct and only the H3 structure is wrong, use **Repair Format**.

**Imported Video or Audio says `media not attached`**

Reattach the original local file to the existing reference card. Its saved semantic configuration is retained.

## Development

Run the Python tests with:

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

The frontend is plain HTML, CSS, and browser ES modules with no Node.js build step.

The backend uses FastAPI and Pydantic, with H3 assembly, validation, API routing, and the OpenAI-compatible client kept in separate modules.

## Privacy and security

MiniConstruct binds to loopback by default.

Projects, settings, and History remain in the current browser profile.

API keys are excluded from Project exports, History, inspectors, and server logs. API keys are not persisted unless you explicitly enable key persistence.

Image data is sent only to the configured prompt-writing endpoint when vision is enabled.

Raw Video and Audio bytes are never sent to the prompt-writing LLM.

Using a remote endpoint or binding MiniConstruct outside loopback expands the trust boundary accordingly.
