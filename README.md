# MiniConstruct

MiniConstruct is a local prompt-writing workbench for **MiniMax H3**. Describe a
video, add references, and use a separate **OpenAI-compatible LLM** to turn your
ideas into a structured H3 prompt.

MiniConstruct does not install H3 models or run H3 inference. Use the prompt in
your existing H3 workflow; connecting MiniConstruct to ComfyUI is optional.

## Installation

### Requirements

- Python **3.12 or newer**
- A modern Chromium, Firefox, or Safari browser
- An OpenAI Chat Completions-compatible prompt-writing server and model

Download or clone this repository and open its folder. No frontend build step,
database server, or account is required.

### Windows

Double-click **`setup-miniconstruct.bat`**. It creates or reuses the repository-local
`.venv`, installs dependencies, and acquires the official H3 guides. Existing
Python environments are not automatically replaced. Setup offers to launch the app.

For later launches, double-click **`start-miniconstruct.bat`**, or run:

```powershell
.\run.ps1
```

### Unix-like systems (MacOS, Linux, etc)

Run these commands from the repository folder:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
python -m miniconstruct.h3.guide_acquisition
chmod +x run.sh
./run.sh
```

With your environment active, you can also launch directly:

```sh
python -m miniconstruct --host 127.0.0.1 --port 8743
```

### Open the app

Visit **http://127.0.0.1:8743**. Keep the terminal open while working; press
**Ctrl+C** there to stop the server.

Official guides are downloaded from a pinned upstream revision and verified by
hash. If acquisition fails, retry when upstream is reachable. See
[setup and troubleshooting](docs/setup-and-troubleshooting.md).

## Connect your prompt-writing LLM

1. Start your compatible server, such as LM Studio, Ollama, or Unsloth Studio.
2. Open **Settings** in MiniConstruct.
3. Enter the **Base URL**, including the API prefix expected by your server
   (commonly `/v1`), and an API key if required.
4. Select or enter the **model ID**. Manual IDs work when discovery is unavailable.
5. Enable **Model accepts images** only when both model and server support images.

Backend context capacity describes the model's already loaded context size;
leave it blank if unknown. MiniConstruct does not resize or reload your server.

## Write your first prompt

1. Create or open a **Project**, then save it.
2. Select an H3 mode and describe your video in **Main Prompt / Idea**.
3. Add the references required by that mode. Add optional Picture, Video, or Audio
   references, assigning Subjects and roles where appropriate.
4. Set duration, dialogue, and any creative controls you want to use.
5. Choose **Generate from Main Prompt** and review the result.
6. Edit manually or use **Revise Selection**, then Validate. Use **Repair Format**
   when the creative content is right but the H3 formatting needs work.
7. Save the prompt or use **Save .txt** to export it for your H3 workflow.

When vision is enabled, Pictures can be sent to an image-capable prompt writer.
Ordinary Video and Audio references contribute metadata and your notes; the
prompt writer does not watch or hear those files.

For sequences, the optional **Story Planner** proposes Scene Plans for review.
Apply the draft, then use **Generate from Story**. See the [Story Planner guide](docs/story-planner.md).

## Optional ComfyUI integration

Use an existing ComfyUI installation with an H3 workflow and its required models.
MiniConstruct does not supply the rendering environment.

1. Open **Execution / ComfyUI**, add a Connection, and import an API-format workflow
   as a Workflow Profile. Map its prompt, duration, and reference inputs.
2. Save the Project and choose **Use for execution** on the desired prompt.
3. Click **Prepare** and inspect the prepared inputs and diagnostics.
4. Click **Send to ComfyUI** to upload the required inputs and queue the render.

Prepare does not send a job. Reference or execution-setting changes can require
selecting the prompt and preparing again. Queue acceptance is not render completion.
See [execution orchestration](docs/execution-orchestration.md) for mapping, Takes,
render recovery, and continuation.

## Features at a glance

- **T2VA, I2VA, FL2VA, L2VA, and Ref2VA** prompt authoring
- Picture, Video, and Audio references with semantic Subjects
- Exact dialogue, dialogue-language controls, and creative direction
- Prompt variations, selective revision, validation, and format repair
- Editable Projects, ordered Generations, and Saved H3 Prompts
- Optional Story Planner, continuity tools, and ComfyUI execution

## Further documentation

- [Advanced authoring and references](docs/authoring.md)
- [Story Planner and planned continuity](docs/story-planner.md)
- [Execution, rendered Takes, and Visual Handoff](docs/execution-orchestration.md)
- [Native continuation](docs/i2va-native-continuation.md)
- [Visual style controls](docs/h3-explicit-visual-style.md)
- [Setup, troubleshooting, privacy, and development](docs/setup-and-troubleshooting.md)
- [Maintainer release workflow](RELEASING.md)

MiniConstruct binds to your computer by default. Network exposure can expose
configured credentials. Remote prompt writers receive the text and permitted
images you send; ComfyUI receives media required by explicit Sends.

## License and acknowledgments

See the license declaration in [pyproject.toml](pyproject.toml) for this checkout.
MiniMax's documentation and models are separate third-party works. MiniConstruct
uses the official [MiniMax H3 prompting material](https://github.com/MiniMax-AI/MiniMax-H3).
Upstream paths and hashes are recorded in
[guide provenance](miniconstruct/h3/guides/provenance.json).

MiniConstruct's original source in this public distribution is **GPL-3.0-or-later**;
see [LICENSE](LICENSE) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
The three complete official H3 guides are excluded and acquired using the setup
commands above. MiniMax materials retain their separate terms.
