# Releasing MiniConstruct

Run the release tool from the authoritative development checkout after this
tool itself has been reviewed and committed:

```powershell
.\release.ps1 -Version 0.4.0
```

It derives `MiniConstruct-public` and `MiniConstruct-GitHub` as sibling
directories.  For temporary or non-standard layouts, supply `-DevRepo`,
`-PublicRepo`, and `-GitHubRepo` explicitly.  Check the non-mutating path and
branch validation first with:

```powershell
.\release.ps1 -Version 0.4.0 -DryRun
```

The script refuses wrong branches, existing target tags, and prohibited guide
files. It permits only one dirty development state by default: the four known
version fields already changed to the requested version. This lets a failed
test or declined `DEV <version>` confirmation resume safely. A genuine late
source correction requires the explicit `-RefreshDevelopmentSource` switch and
typing `SOURCE <version>`; it is fully retested and committed with an explicit
release-source trailer before promotion.

The exact established development SHA is merged into `public-release` with
`--no-commit`. A conflicted staging merge is left in progress; resolve and
stage every conflict, then rerun the same command. The script verifies that
`MERGE_HEAD` is that exact source SHA, tests the staged merge, removes the
temporary guides, verifies the index did not change, and only then creates the
staging merge commit.

Public staging temporarily downloads the pinned official guides only to run
tests. A `finally` cleanup deletes them and rechecks that none are present or
staged. The committed staging tree is then passed to its existing audited
archive exporter, which changes the clean GitHub clone's files but never its
history. The prepared GitHub index tree must exactly match the staging tree.
If `RELEASE <version>` is declined, that exact prepared index is an allowed
resume state; any unstaged or untracked GitHub change still stops the tool.

Before that export, the tool fetches `origin/master` and compares every file
changed publicly since the latest public version tag with the staging commit.
Any different README, documentation, screenshot, or other remote-only file
stops the release for an intentional reconciliation.

The public commit and annotated tag require typing `RELEASE <version>` after a
summary that includes the public Git identity and remote. They are sent in one
atomic push. No staging repository is ever pushed. With `-ReleaseNotesPath
notes.md`, an installed and authenticated `gh` client creates the corresponding
GitHub Release; a failure there is reported as an incomplete Release object,
not a failed Git release.
