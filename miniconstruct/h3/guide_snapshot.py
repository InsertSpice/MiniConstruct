"""Pinned metadata for the official MiniMax H3 guide snapshot.

This module intentionally contains metadata only; it never includes guide
contents and has no runtime dependencies, so maintainer tools can load it
without importing the application package.
"""

from __future__ import annotations


UPSTREAM_COMMIT = "d21241f0a4b3acbb34c97dae47fa417b7065e438"
UPSTREAM_REPOSITORY = "https://github.com/MiniMax-AI/MiniMax-H3"
GUIDE_SNAPSHOT = (
    (
        "SKILL.md",
        "skills/h3-prompt-writing/SKILL.md",
        "a7000443588ca3f145e3b3fd8900f14e0325dc460bd811268fac89a9dc8e56d0",
    ),
    (
        "base-en.txt",
        "skills/h3-prompt-writing/references/base-en.txt",
        "2cfebc096a6e08370f288d468d90b60f7f9bcb938f94bf090816e910e48e75fc",
    ),
    (
        "ref-en.txt",
        "skills/h3-prompt-writing/references/ref-en.txt",
        "1e574f356716ad55612247ffb7bbccbcdb484ad96599d63c7dca1af186b1fab7",
    ),
)
