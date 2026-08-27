# Official MiniMax H3 guide locations

This public-release repository does not redistribute the complete official
MiniMax H3 guides. MiniConstruct expects the following official files in this
directory:

- `SKILL.md`
- `base-en.txt`
- `ref-en.txt`

After creating the Python environment, obtain the recorded official snapshot
with:

```powershell
.\.venv\Scripts\python.exe -m miniconstruct.h3.guide_acquisition
```

The command downloads only files that are missing, directly from the official
[`MiniMax-AI/MiniMax-H3`](https://github.com/MiniMax-AI/MiniMax-H3) repository.
It preserves response bytes and verifies the SHA-256 values in
`provenance.json`. You may instead manually put the corresponding official
files at these paths. Existing files are left alone.

The exact upstream paths, revision, retrieval date, and hashes are recorded in
`provenance.json`. Do not edit the official guide files to implement
MiniConstruct behavior; application-specific rules belong in
`../operating/miniconstruct.md`.

