# MiniConstruct

MiniConstruct is a local, dark-themed web workbench for turning a creative idea
and semantic reference manifest into a structured **MiniMax H3** prompt. It uses
a separate OpenAI-compatible prompt-writing LLM, validates the returned H3
grammar, and keeps editable Projects and generated-output History in your
browser.

MiniConstruct generates prompts. It does **not** run MiniMax H3, perform ComfyUI
inference, inspect video, listen to audio, extract frames, or transcribe media.

MiniConstruct's original source code is licensed under GPL-3.0-or-later.
MiniMax H3 and its official documentation are separate third-party materials;
see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Requirements

- Python 3.12 or newer
- A modern Chromium, Firefox, or Safari browser
- An OpenAI Chat Completions-compatible local server such as LM Studio, Ollama,
  Unsloth Studio, or another compatible implementation

No Node.js, frontend build, database server, account, or cloud service is
required.

## Cross-platform setup

MiniConstruct runs on Windows, Linux, and macOS. A new installation is:
clone the repository → create and activate a virtual environment → install
MiniConstruct → explicitly acquire the official guides once → start the local
server.

After activating the virtual environment, use these portable commands on every
platform:

```sh
python -m pip install -e ".[dev]"
python -m miniconstruct.h3.guide_acquisition
python -m miniconstruct
```

Guide acquisition is a one-time, explicit download of the official MiniMax
files. It never occurs silently during generation. If you start before
acquiring them, MiniConstruct prints the same acquisition command and expected
paths.

### Windows

In PowerShell, from the repository directory:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m miniconstruct.h3.guide_acquisition
python -m miniconstruct
```

`start-miniconstruct.bat` is an optional double-click launcher after setup. It
uses `.venv\Scripts\python.exe`, binds to `127.0.0.1:8743`, keeps its terminal
visible, and can be stopped with Ctrl+C. `run.ps1` is also optional; it is not
required to run MiniConstruct.

To allow LAN access deliberately with the PowerShell wrapper:

```powershell
.\run.ps1 -BindHost 0.0.0.0 -Port 8743
```

### Linux and macOS

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m miniconstruct.h3.guide_acquisition
python -m miniconstruct
```

`run.sh` is an optional convenience wrapper after setup.

Binding to all interfaces can expose the UI and its configured API key to other
devices on your network. The default remains `127.0.0.1`.

The health endpoint is <http://127.0.0.1:8743/api/health>, and local API docs
are available at <http://127.0.0.1:8743/api/docs>.

## Connect a prompt-writing model

The Base URL must include the compatible API prefix when the server uses one.
MiniConstruct calls only `GET /models` and `POST /chat/completions` beneath that
URL. Model discovery is optional; you can always type a model ID manually.

Generate and Regenerate use standard OpenAI-compatible SSE token streaming.
The output grows while the model is working, the authoring workspace remains
editable, and **Stop** closes the browser request and upstream HTTP stream.
Stopped or failed partial output remains available to copy, but is not validated
or saved to History as a completed prompt. Each in-flight request uses an
immutable snapshot of the Workspace and selected model settings captured when
generation begins.

### LM Studio

1. Load a text or multimodal instruction model and start LM Studio's local
   server.
2. Use `http://127.0.0.1:1234/v1` as the Base URL.
3. Choose **Discover**, select a model, and use **Test connection**.
4. Enable **Model accepts images** only when that loaded model and server route
   really accept OpenAI `image_url` content parts.

### Ollama

1. Start Ollama and make sure the selected model is installed.
2. Use `http://127.0.0.1:11434/v1`.
3. Discover or enter the exact Ollama model ID, then test the connection.

### Other compatible servers

Enter the server's OpenAI-compatible base URL, optional API key, model ID,
temperature, and output-token budget. MiniConstruct does not infer vision or any
other capability from the model name. If model listing is unsupported, manual
model entry still works.

## H3 modes

- **T2VA** — text-driven audiovisual generation using the official three-field
  base format.
- **I2VA** — one exact first-frame Picture at 0.00 seconds.
- **FL2VA** — exact first and last Pictures, with official alignment syntax and
  effective-duration ending.
- **L2VA** — one exact last-frame Picture at the effective-duration ending.
- **Ref2VA** — semantic full-reference generation using the official six-section
  format.

Duration is hard-limited to the official 4–15 second range. Shots may be auto or
any positive integer; MiniConstruct does not invent a four-shot cap. Later cuts
must have increasing `MM:SS.mmm` timestamps within the duration. Variations are
independent prompt rewrites and are never treated as shots.

## Reference semantics

Pictures, Videos, and Audio are numbered independently within their categories.
Stable internal IDs survive reordering. A `<Subject N>` is a semantic visible
entity, not an asset alias: it may draw from several assets, and one asset may
contribute to several Subjects.

- **Role** says what an asset does.
- **Notes** provide asset-specific facts or constraints.
- **Reference Labels** describe relationships among Subjects and reference
  labels.
- **Main prompt / idea** says what happens in the target video.

Images are downscaled in the browser to at most 1600 pixels on their longest
side and saved in Project data. When vision is explicitly enabled, they are sent
to the prompt-writing LLM as standard OpenAI-compatible `image_url` content
parts. MiniConstruct shows a warning when image vision is disabled.

Video and Audio are always metadata-only to the prompt-writing LLM. Only
filename, MIME type, browser-derived duration, role, Notes, options, and semantic
relationships are sent. Raw video/audio is never base64-encoded into generation
requests. A Video with sound never creates an Audio reference automatically.

Audio roles distinguish direct full/partial signal reuse from guidance such as
voice timbre, beat, dialogue content, or audio style. For voice timbre, connect
the explicit Audio and Subject in Reference Labels; the LLM is reminded that it
cannot hear the Audio and must not invent its properties.

## Continuation modes

**Continuation source** uses the official `[video continuation]` relationship:
the target proceeds from the source video's ending state and need not replay the
whole source at its opening.

**Seamless overlap continuation** is a stronger strategy, not a new H3 task
type. Supply the final roughly 1–2 seconds of the preceding video (a recommendation,
not a hard limit). Shot 1 reproduces that complete clip as the same physical
moments, then advances beyond its final frame without replay, rewind, reset,
pose/state snap, camera discontinuity, or lost momentum. When a duration is
known, the assembled instructions use its real `00:00.000–MM:SS.mmm` range;
otherwise they refer to the complete Video duration without fabricating a time.
The outgoing camera crosses the boundary before any later editorial cut.

## Dialogue

Enter exact lines separately, for example:

```text
Subject 1: First exact line.
Subject 2: Second exact line.
Subject 1: Third exact line.
```

The main creative field remains the place for timing, reactions, interruptions,
pauses, camera behavior, and performance. MiniConstruct tells the writer to keep
dialogue verbatim inside `<d>...</d>` in its source language. `(S1)`, `(S2)`,
and later IDs follow actual vocal-event order; they do not equal Subject numbers.

## Validation and repair

MiniConstruct validates exact required section names/order, mode alignment lines,
Shot 1 and later cut grammar, timestamp order and bounds, reference resolution,
Ref2VA retention, speaker consistency where practical, and verbatim dialogue.
Findings are classified as ERROR, WARNING, or INFO. The validator checks explicit
grammar and invariants rather than stylistic prose.

**Repair Format** performs one user-requested LLM pass with the official guide
and current findings. It is never an uncontrolled retry loop. **Show LLM
Instructions** displays the same layered textual context used by generation,
excluding API keys and binary image data.

Generation uses a short connection timeout but no short read timeout after the
stream is established, accommodating long first-token latency and slow local
inference. Model discovery and connection testing retain short timeouts. The
portable cancellation mechanism is closing the active stream; a backend may
continue computing after disconnect if it does not stop inference when its
client connection closes.

### Reasoning and performance diagnostics

Reasoning defaults to **Off** for structured H3 rewriting. MiniConstruct adds a
direct-output instruction and, for identified Unsloth Studio endpoints, sends
the Gemma/Qwen-compatible `thinking` and `enable_thinking` chat-template flags.
**Backend default** sends no reasoning preference; **On** explicitly requests it
where that compatibility is known. If a strict backend rejects optional fields,
MiniConstruct retries once without them. Reasoning deltas and inline reasoning
tags are measured separately and never enter the canonical H3 prompt.

The output area reports request assembly, first upstream event, first reasoning
delta, first final-content delta, final-output duration, safe token usage when
provided, and a SHA-256 cache-input fingerprint. The fingerprint includes the
model-visible messages, image-content hashes, and relevant generation/template
parameters, but excludes API keys and raw image data. Matching fingerprints on
unchanged requests confirm equivalent model input; they do not guarantee that a
backend will reuse its KV cache.

## Projects and History

Projects are complete editable workspaces stored in IndexedDB. Save, Save As,
load, delete, export, and import are available from the header. Exported
`.miniconstruct-project.json` files contain processed Pictures but never API
keys or raw Video/Audio. After reload or import, Video/Audio metadata remains and
the card says **media not attached** until you reattach a local file without
losing its semantic settings.

History is a separate local list of the 50 most recent generated outputs. It
stores prompt text and validation metadata, not editable Projects or secrets.

## Official H3 specification and architecture

MiniConstruct keeps its own operating instructions separate from the normative
MiniMax material. For this public release, the complete official guides are not
redistributed. After normal Python setup, run:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.h3.guide_acquisition
```

The command fetches only missing guides from the official
[`MiniMax-AI/MiniMax-H3`](https://github.com/MiniMax-AI/MiniMax-H3) repository
at commit `d21241f0a4b3acbb34c97dae47fa417b7065e438`; it verifies the recorded
SHA-256 hashes and never downloads guides during generation. Network access
therefore occurs only when you explicitly run this setup command. If preferred,
manually obtain the official `SKILL.md`, `base-en.txt`, and `ref-en.txt` files
and place them under `miniconstruct/h3/guides/` as shown in that directory's
README. Existing files are used and are not overwritten.

If the guides are missing, MiniConstruct stops before serving requests and
prints the setup command and expected paths. Exact upstream paths, retrieval
date, source URLs, revision, and SHA-256 hashes are in
`miniconstruct/h3/guides/provenance.json`.

MiniConstruct additions live separately in
`miniconstruct/h3/operating/miniconstruct.md`. Request construction layers the
operating rules, only the relevant official guide, mode/reference guidance, the
canonical manifest, and user material. FastAPI routing, Pydantic workspace
models, H3 assembly/validation, and the generic OpenAI-compatible client remain
separate modules. The frontend is plain HTML/CSS and browser ES modules with no
build step.

## Licensing and third-party materials

MiniConstruct's original source code is licensed under GPL-3.0-or-later; the
full license is in [LICENSE](LICENSE). MiniMax H3, model weights, and official
MiniMax documentation are separately licensed third-party materials. Obtaining
the official H3 guides from upstream does not place them under MiniConstruct's
GPL license. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for links to
the applicable MiniMax sources and licensing information.

## Development and tests

```powershell
.\.venv\Scripts\Activate.ps1
pytest -q
```

Tests use mocked HTTP transports and do not require a live LLM server.

## Troubleshooting

- **Model discovery fails:** verify the Base URL includes `/v1` if required,
  then enter the model ID manually if the implementation lacks `GET /models`.
- **Connection refused:** start the local LLM server and confirm its port.
- **Images ignored or rejected:** enable the vision switch only for a model and
  backend that accept OpenAI multimodal Chat Completions. MiniConstruct warns
  when vision is off.
- **Validation errors:** inspect each finding and the assembled instructions;
  use one Repair Format pass if the content is correct but the grammar is not.
- **Imported media says not attached:** this is intentional privacy behavior;
  reattach the local Video/Audio file on its existing card.
- **API key persistence:** keys are not remembered unless you explicitly enable
  it. Browser storage is still readable by software with access to that local
  browser profile, so prefer keyless local endpoints where possible.

## Privacy and security

The server binds to loopback by default. Projects, settings, and History stay in
the current browser profile. Project exports, History, inspectors, and server
logs exclude API keys. Image data is sent only to the configured endpoint when
vision is enabled; Video/Audio bytes are never sent. Treat any non-loopback bind
or remote endpoint as an explicit expansion of your trust boundary.
