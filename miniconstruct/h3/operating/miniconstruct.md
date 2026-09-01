# MiniConstruct H3 operating instructions

- Follow the official guide's exact H3 grammar. Use canonical `<Subject N>`, `<Picture N>`, `<Video N>`, and `<Audio N>` labels.
- The structured manifest is authoritative: Role defines an asset's function; Notes define its facts, constraints, and Subject mappings; Global Reference Relationships cover only cross-asset/timeline facts. The main prompt defines the target video.
- `<Subject N>` is a stable visible entity independent of asset numbering; one Subject may combine several assets.
- Inspect Pictures only when vision is enabled. Video and Audio are metadata-only: never invent their contents from filenames; use only explicit instructions, Notes, Reference Labels, and inspectable Pictures.
- Follow the applicable mode guidance for continuation behavior. Exact Dialogue / Lyrics is immutable: preserve wording and punctuation inside `<d>...</d>` in its source language; speaker IDs follow first vocal-event order, not Subject numbers.
- Keep labels stable: Video audio does not create an Audio label, and Audio reuse is direct signal retention while Audio reference is guidance only.
