# Setup, troubleshooting, and development

## Startup and guides

Use the [quick start](../README.md#installation). Windows setup reuses the local
Python environment. The batch launcher requires that environment; run.ps1 uses it
when present and otherwise tries Python 3.12 via py. Run the Unix launcher from
the repository folder.

Official guides are acquired at upstream commit
`d21241f0a4b3acbb34c97dae47fa417b7065e438`. Paths and SHA-256 hashes are recorded in
[provenance](../miniconstruct/h3/guides/provenance.json) and the guide snapshot module.
Existing files are verified too. Altered files are reported and preserved; no
substitute guides are used. If upstream is unavailable, retry later:

```sh
python -m miniconstruct.h3.guide_acquisition
```

## Troubleshooting

- Connection refused: start your prompt-writing server and check host and port.
- Discovery fails: check the required API prefix, or enter a model ID manually.
- Images ignored: check both model and server support multimodal Chat Completions.
- Format errors: inspect validation findings, then use Repair Format where appropriate.
- Missing Video/Audio: reattach the original file to its existing reference card.
- Slow Story planning: review timeout, output-token limits, and reasoning settings.

The health endpoint is `/api/health`; local API documentation is at `/api/docs`.

## HTTP logging

Successful and cached requests are quiet by default. Handled 4xx responses produce
warnings; handled 5xx responses produce errors. Unhandled exceptions retain
traceback logging. Startup and shutdown diagnostics remain visible.

To log every request, use `python -m miniconstruct --verbose-http`,
`.\run.ps1 -VerboseHttp`, or `./run.sh --verbose-http`. These combine with host,
port, and reload options. Quiet diagnostics omit query strings, bodies, and headers;
verbose access logs include query strings.

## Privacy and storage

Keep loopback binding unless you intend to expose the app. On Windows,
`.\run.ps1 -BindHost 0.0.0.0 -Port 8743` permits LAN access and can expose the
application and configured credentials to other devices.

Projects live under `%LOCALAPPDATA%/MiniConstruct/Projects` on Windows or
`$XDG_DATA_HOME/miniconstruct/projects` (otherwise `~/.local/share/miniconstruct/projects`)
on Unix. Set `MINICONSTRUCT_PROJECTS_ROOT` before startup to choose another root.
Back up the complete Project directory.

LLM settings live in browser-local storage. Keys are not persisted unless you
explicitly enable persistence and are excluded from Project exports and inspectors.
ComfyUI Connections and Workflow Profiles use separate machine-local storage.
Remote endpoints receive request text and vision-enabled Pictures. Ordinary raw
Audio/Video is not sent to the prompt writer. Explicit ComfyUI Sends may upload
bound media. Choose endpoints you trust.

## Development

The frontend uses HTML, CSS, and browser ES modules without a Node build step.
The backend uses FastAPI and Pydantic. Editable installs serve repository web assets;
distributions bundle them in the package.

After installing `.[dev]` and acquiring guides, run:

```sh
python -m pytest
node --test tests/js/*.test.mjs
git diff --check
```

Tests use mocked providers and disposable local data, without live LLM or ComfyUI.
See [RELEASING](../RELEASING.md) for public export checks.
