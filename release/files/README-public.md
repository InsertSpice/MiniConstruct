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
