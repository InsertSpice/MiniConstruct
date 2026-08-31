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
- Associate references with semantic **Subjects**
- Use image references directly with a vision-capable prompt-writing model
- Preserve **exact dialogue**
- Build **video continuation** and **seamless overlap continuation** prompts
- Control camera preferences, tone, performance, music, visual style, identity fidelity, and prompt-generation seed
- Generate multiple prompt variations
- Validate generated H3 structure and reference usage
- Repair formatting problems without rewriting the creative intent
- Highlight part of a generated prompt and **revise only that selection**
- Save editable Projects and keep recent generated prompts in local History

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

For transitions between separately generated clips, **Seamless overlap continuation** can use the ending portion of the previous clip as an overlap reference so the new generation continues through the boundary instead of resetting the action, pose, camera, or momentum.

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

**Repair Format** is deliberately narrower: it receives the current prompt, the relevant H3 guide, and structural findings, and is instructed to repair formatting rather than reinterpret the scene.

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

Shot count may be automatic or explicitly chosen. MiniConstruct validates shot timestamps, required sections, reference resolution, dialogue, mode-specific alignment, and other structural rules.

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

### Windows

From the repository directory in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
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

The Base URL should include the API prefix expected by the server, commonly `/v1`.

MiniConstruct can discover models when the server exposes a compatible model-list endpoint, but manual model IDs are always supported.

Enable **Model accepts images** only when both the selected model and server support OpenAI-compatible multimodal Chat Completions.

Generate and Regenerate use streaming output, so the prompt appears while the model is writing it. **Stop** closes the active browser request and upstream HTTP stream.

Reasoning defaults to **Off** because MiniConstruct primarily wants direct structured output.

## Projects and History

**Projects** are complete editable workspaces stored locally in IndexedDB.

They can be saved, loaded, deleted, exported, and imported.

Exported `.miniconstruct-project.json` files can contain processed Pictures but never API keys or raw Video/Audio.

Because raw Video and Audio are not stored inside a Project, imported or reloaded projects may show **media not attached** until the original local file is reattached. Its semantic settings are preserved.

**History** stores the 50 most recent completed generated prompts and their validation metadata.

Projects and History are separate.

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

The development snapshot currently corresponds to upstream commit:

```text
d21241f0a4b3acbb34c97dae47fa417b7065e438
```

Exact upstream paths and SHA-256 hashes are recorded in `provenance.json`.

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

Projects, settings, and History remain in the current browser profile.

API keys are excluded from Project exports, History, inspectors, and server logs. API keys are not persisted unless you explicitly enable key persistence.

Image data is sent only to the configured prompt-writing endpoint when vision is enabled.

Raw Video and Audio bytes are never sent to the prompt-writing LLM.

Using a remote endpoint or binding MiniConstruct outside loopback expands the trust boundary accordingly.
