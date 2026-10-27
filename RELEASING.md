# Releasing MiniConstruct

Review and commit the source, release tooling and overlay before an ordinary
release. The tool never commits development or staging. It requires clean
repositories with no merge, rebase, cherry-pick or other interrupted operation.

From the development checkout, prepare with one command:

```powershell
.\release.ps1 -Operation Prepare -Version 0.5.0
```

Omit `-Version` to propose the next minor version after the latest public tag.
The current verified public baseline is v0.4.0. `-Source` accepts an exact commit
or reachable revision; HEAD is the default. All release code and overlay files
must match that commit. No source version bump or source commit is needed:
version fields change only in the exported snapshot.

Prepare prints its output directory, normally a new ignored directory beneath
`MiniConstruct-public/.release-artifacts/`. Inspect `preview.txt`, `preview.diff`,
`inventory.json`, test logs and `manifest.json`. Then publish that manifest:

```powershell
.\release.ps1 -Operation Publish -Manifest '<preparation-directory>\manifest.json'
```

Publish displays the remote, public author, source SHA and exact public tree.
It requires typing `PUBLISH v0.5.0` before changing any live Git state or pushing.
There is no unattended confirmation flag. Neither operation creates a GitHub
Release web object; publication creates the public commit and annotated Git tag.

## Architecture and overlay maintenance

- **MiniConstruct** owns application source, tests and `release/overlay.json`.
- **MiniConstruct-public** is the staging location for immutable preparations.
  Its historical `public-release` worktree stays intact; its application files
  and branch are no longer merged, updated, or used as source.
- **MiniConstruct-GitHub** supplies public history, origin and public author
  identity. Only Publish imports the tested public objects and pushes to origin.

The snapshot is built from raw blobs at the recorded source commit, the
version-controlled overlay, and release version. It never copies development
history. The overlay carries GPL license/notices, a public README supplement,
guide setup documentation and small exact-anchor transformations of modern
source. A missing/duplicate anchor stops with its filename and expected text.
Update that transformation and its tests once when upstream code changes.
Never copy an old public app.py over development.

`tools/release_policy.py` applies an allow/deny policy to every tracked source
file and audits the resulting tree again. New top-level categories, new binary
assets and changed asset bytes require explicit review. Required reusable image
fixtures are content-addressed in `binary_sha256`. File-level audit exceptions
must identify the exact SHA-256, rule and reason; there are no blanket test or
fixture exemptions. The inventory explains every exclusion. Gitignore is not
used as the export security boundary.

The source SHA, source tree, overlay revision/content hash, configuration hash,
public baseline, public tree, guide hashes, file hashes/modes, version, author
and check results are recorded. The manifest checksum and test-log hashes detect
accidental edits. This is a local trusted-maintainer record, not a cryptographic
attestation against someone who can rewrite both the tool and its records.

## Checks and dry runs

```powershell
.\release.ps1 -Operation Prepare -Version 0.5.0 -DryRun
```

DryRun runs the same assembly, remote comparison, audits and checks in a new
temporary directory. It does not change input indexes, branches, tags, worktree
files or remotes. The resulting manifest is deliberately not publishable.
It is not a preflight-only command. A network or test failure blocks verification
and leaves diagnostic logs in the reported preparation directory.

Before committing changes, maintainers may exercise a review-only preview:

```powershell
.\release.ps1 -Operation Prepare -Version 0.5.0 -DryRun -ReviewWorkingTree
```

This exports current tracked and non-ignored untracked development files,
records HEAD and the resulting file hashes, and marks the result non-publishable.
It permits dirty development only; staging and GitHub must still be clean.
It is not reproducible from HEAD alone. Commit the reviewed changes yourself,
then run ordinary Prepare for a publishable, commit-reproducible artifact.

Checks run against a disposable copy in a new virtual environment: install
`.[dev]`, acquire the pinned official guides, verify startup through FastAPI's
lifespan and an HTTP root request, run all Python and JavaScript tests, and check
Git whitespace. The temporary install is removed on success and failure.
Official guides never enter the prepared snapshot or candidate Git index.
No model/provider request, ComfyUI Send, benchmark run or render is initiated.
Network access is needed for public refs, Python dependencies and official guides.

## Publication, divergence and recovery

Prepare requires the GitHub checkout HEAD to equal the actual remote branch.
It fetches public history into the disposable repository only. Public-only
changes since the latest release tag must match the new snapshot or preparation
stops with their paths. Integrate those changes into development or the overlay,
review them, and prepare again. A dirty staging tree is never a source of patches.

Publish verifies source/staging state, tool/overlay hash, public author and remote
again, plus every snapshot byte, path, mode, index tree and check log. Source
changes after Prepare require another preparation. An unexpected remote commit
or tag is never overwritten. Publication is a non-force atomic push of exactly
the new public commit and annotated tag; its single parent is the public baseline.
The tested and published tree IDs must be identical.

If the push fails or the acknowledgement is lost, rerun **Publish with the same
manifest**. Commit and tag objects are deterministic, including identity and
time; retries do not duplicate them. The tool accepts only the recorded baseline
or exact release commit on the remote, and an absent or exact intended tag. This
also recovers an exact branch-only/tag-only partial state. It verifies both remote
refs, writes `publication.json`, and fast-forwards the local GitHub checkout.
A checkout collision after remote success leaves publication recorded and can be
retried after preserving and moving the colliding local file. Atomic push support
is required; there is no sequential push fallback.

An interrupted *old* merge is a separate blocker. Preserve its worktree, index,
Git operation files and untracked files to a backup before deciding whether to
finish or abort it. Inspect `git status`, `git ls-files -u`, `MERGE_HEAD`, and
`ORIG_HEAD`. Reproduce the recovery in a disposable clone first. No automatic
abort, reset, clean, history rewrite or conflict-side selection is performed.
There was no interrupted operation in the audited three checkouts.

## Configuration and tests

`-DevRepo`, `-PublicRepo`, `-GitHubRepo`, `-Output` and `-PythonExe` support other
locations. `release/overlay.json` fixes the allowed origin URL and target branch;
fetch and push URLs must both match. The public checkout's configured Git author
is recorded and checked again before publication.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_release_script.py tests/test_guide_acquisition.py
.\.venv\Scripts\python.exe -m pytest
node --test tests/js/*.test.mjs
git diff --check
```

Integration tests create disposable repositories and a local bare remote. They
never publish to GitHub. Fresh-install validation additionally depends on current
package availability: application bytes are reproducible, but dependencies use
the project's existing version ranges rather than a fully pinned lockfile.
Keep preparation artifacts private: source identity and local maintainer paths
belong in the manifest, not in the public Git tree.

Public export also strips embedded PNG prompt/workflow/EXIF metadata using a
validated chunk transformation, preserving pixel bytes and color information.
Both the reviewed input hash and expected output hash are recorded in the overlay.
The original development asset is left untouched.
