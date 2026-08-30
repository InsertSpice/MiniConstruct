# Maintainer public-release workflow

MiniConstruct uses three deliberately separate directories:

- `MiniConstruct` is the authoritative development source. It contains the
  locally obtained official MiniMax H3 guides needed for development.
- `MiniConstruct-public` is a private/local public-release staging worktree.
  It may retain development ancestry, but its history **MUST NEVER be pushed
  to GitHub** because older commits can contain third-party guide material.
- `MiniConstruct-GitHub` is the clean-history public repository. It receives
  tree snapshots only; it does not receive `MiniConstruct-public` history.

## Normal release flow

1. Ensure the development repository is clean and committed.
2. Merge the development HEAD into `MiniConstruct-public` with Git, preserving
   the public guide-acquisition, licensing, and guide-deletion adaptations.
3. Keep `SKILL.md`, `base-en.txt`, and `ref-en.txt` deleted from public staging.
4. Run the public tests and `python tools/audit_public_release.py`.
5. Commit the resolved public staging changes **locally** after review.
6. From `MiniConstruct-public`, export its committed tree into the clean public
   repository:

   ```powershell
   python tools/export_public_snapshot.py ..\MiniConstruct-GitHub
   ```

7. Inspect `git status` and `git diff` in `MiniConstruct-GitHub`, then run its
   tests and the audit tool there.
8. Commit and push only from `MiniConstruct-GitHub`.

The export tool refuses dirty source or destination repositories, exports only
the committed public staging tree, mirrors tracked-file deletions, leaves
`destination/.git` alone, and never commits or pushes.
