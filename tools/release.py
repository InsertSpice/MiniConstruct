"""Prepare immutable public trees; publish only a verified preparation.

All assembly, indexing, fetching and testing during prepare happens in disposable
repositories. The three input repositories are inspected read-only.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

try:
    from tools.release_policy import GUIDES, ReleaseError, audit, digest, exclusion, safe_path, strip_png_metadata
except ModuleNotFoundError:
    from release_policy import GUIDES, ReleaseError, audit, digest, exclusion, safe_path, strip_png_metadata


ROOT = Path(__file__).resolve().parents[1]
CHECKS = ("fresh_install", "guide_acquisition", "startup", "python", "javascript", "whitespace", "tree_audit")


def run(args, cwd=None, env=None, input=None) -> bytes:
    result = subprocess.run([str(a) for a in args], cwd=cwd, env=env, input=input, capture_output=True)
    if result.returncode:
        error = ReleaseError(f"Command failed ({result.returncode}): {' '.join(map(str, args))}\n"
                             + (result.stdout + result.stderr).decode("utf-8", errors="replace")[-12000:])
        error.output = result.stdout + result.stderr
        raise error
    return result.stdout


def git(repo: Path, *args, env=None, input=None) -> str:
    return git_bytes(repo, *args, env=env, input=input).decode("utf-8").strip()


def git_bytes(repo: Path, *args, env=None, input=None) -> bytes:
    settings = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    if env:
        settings.update(env)
    return run(["git", "-c", f"safe.directory={repo.resolve().as_posix()}", "-C", repo, *args], env=settings, input=input)


def read_tree(repo: Path, revision: str) -> tuple[dict[str, bytes], dict[str, str]]:
    entries = git_bytes(repo, "ls-tree", "-rz", revision).split(b"\0")
    records = []
    for entry in filter(None, entries):
        meta, raw_name = entry.split(b"\t", 1)
        mode, kind, oid = meta.decode().split()
        name = raw_name.decode("utf-8")
        safe_path(name)
        if mode not in {"100644", "100755"} or kind != "blob":
            raise ReleaseError(f"Unsupported Git entry (symlink/submodule): {name} ({mode})")
        records.append((name, mode, oid))
    # One Git process preserves raw blob bytes without archive attributes/filters.
    raw = git_bytes(repo, "cat-file", "--batch", input="".join(f"{oid}\n" for _, _, oid in records).encode())
    files, modes, offset = {}, {}, 0
    for name, mode, _ in records:
        end = raw.index(b"\n", offset)
        size = int(raw[offset:end].split()[-1])
        files[name] = raw[end + 1:end + 1 + size]
        modes[name] = mode
        offset = end + size + 2
    return files, modes


def status(repo: Path) -> dict:
    operations = []
    for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer", "BISECT_LOG"):
        if Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", name)).exists():
            operations.append(name)
    return {"path": str(repo.resolve()), "head": git(repo, "rev-parse", "HEAD"),
            "branch": git(repo, "symbolic-ref", "--short", "HEAD"),
            "status": git(repo, "status", "--porcelain=v1", "--untracked-files=all"), "operations": operations,
            "index": digest(Path(git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")).read_bytes())}


def clean(repo: Path, allow_dirty=False) -> dict:
    state = status(repo)
    if state["operations"]:
        raise ReleaseError(f"Interrupted Git operation in {repo}: {', '.join(state['operations'])}; left intact")
    if state["status"] and not allow_dirty:
        raise ReleaseError(f"Repository has uncommitted changes: {repo}\n{state['status']}")
    return state


def remote_refs(repo: Path) -> dict[str, str]:
    return {ref: oid for oid, ref in (line.split() for line in git(repo, "ls-remote", "origin").splitlines())}


def identity(repo: Path) -> dict:
    result = {key: git(repo, "config", "--get", f"user.{key}") for key in ("name", "email")}
    if not all(result.values()) or any(c in result["name"] + result["email"] for c in "\n\r<>"):
        raise ReleaseError("Configure the intended public user.name and user.email in the GitHub checkout")
    return result


def remote_url(repo: Path, expected: str) -> str:
    fetch = git(repo, "remote", "get-url", "--all", "origin").splitlines()
    push = git(repo, "remote", "get-url", "--push", "--all", "origin").splitlines()
    if fetch != [expected] or push != [expected]:
        raise ReleaseError(f"Unexpected origin fetch/push URL; expected exactly {expected}")
    return expected


def overlay(root: Path) -> tuple[dict, dict[str, bytes], str]:
    config = json.loads((root / "release/overlay.json").read_text(encoding="utf-8"))
    content = {p.relative_to(root).as_posix(): p.read_bytes().replace(b"\r\n", b"\n") for directory in (root / "release", root / "tools")
               for p in sorted(directory.rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
    content["release.ps1"] = (root / "release.ps1").read_bytes().replace(b"\r\n", b"\n")
    fingerprint = digest(json.dumps({p: digest(b) for p, b in sorted(content.items())}, sort_keys=True).encode())
    return config, content, fingerprint


def replace_once(files: dict[str, bytes], path: str, before: str, after: str) -> None:
    text = files[path].decode("utf-8")
    if text.count(before) != 1:
        raise ReleaseError(f"Overlay conflict: {path}: expected one occurrence of {before[:100]!r}; found {text.count(before)}. Inputs untouched.")
    files[path] = text.replace(before, after).encode()


def assemble(source: dict[str, bytes], config: dict, content: dict[str, bytes], version: str) -> tuple[dict[str, bytes], dict]:
    files, excluded = {}, {}
    for path, data in source.items():
        reason = exclusion(path, config)
        if reason:
            excluded[path] = reason
        else:
            files[path] = data
    for target, payload in config.get("copy", {}).items():
        safe_path(target)
        files[target] = content["release/" + payload]
    for change in config.get("replace", []):
        replace_once(files, change["path"], change["before"], change["after"])
    for target, payload in config.get("append", {}).items():
        files[target] = files[target].rstrip() + b"\n\n" + content["release/" + payload]
    for target, expected_input in config.get("strip_png_metadata", {}).items():
        if target not in files:
            continue
        if digest(files[target]) != expected_input:
            raise ReleaseError(f"PNG sanitization source changed; review new asset: {target}")
        files[target] = strip_png_metadata(files[target])
    for path, pattern in config["version_fields"].items():
        text = files[path].decode()
        text, count = re.subn(pattern, lambda m: m[1] + version + m[2], text, flags=re.MULTILINE)
        if count != 1:
            raise ReleaseError(f"Expected one version field in {path}; found {count}")
        files[path] = text.encode()
    audit(files, config)
    return files, {"included": sorted(files), "excluded": excluded,
                   "overlay_changed": sorted(p for p in files if source.get(p) != files[p])}


def write_files(root: Path, files: dict[str, bytes]) -> None:
    for path, data in files.items():
        safe_path(path)
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def index_tree(repo: Path, files: dict[str, bytes], modes: dict[str, str]) -> str:
    git(repo, "read-tree", "--empty")
    # Hash bytes directly, bypassing autocrlf, attributes and global clean filters.
    records = []
    for name, data in sorted(files.items()):
        oid = git(repo, "hash-object", "-w", "--stdin", input=data)
        records.append(f"{modes.get(name, '100644')} {oid}\t{name}\0")
    git(repo, "update-index", "-z", "--index-info", input="".join(records).encode())
    return git(repo, "write-tree")


def guide_metadata(files: dict[str, bytes]) -> dict:
    tree = ast.parse(files["miniconstruct/h3/guide_snapshot.py"])
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)}
    specs = values["GUIDE_SNAPSHOT"]
    targets = tuple(f"miniconstruct/h3/guides/{s[0]}" for s in specs)
    if set(targets) != set(GUIDES):
        raise ReleaseError("Guide target set changed; update the explicit export policy before release")
    provenance = json.loads(files["miniconstruct/h3/guides/provenance.json"])
    expected = {name: (path, sha) for name, path, sha in specs}
    actual = {p["local"]: (p["upstreamPath"], p["sha256"]) for p in provenance["files"]}
    if actual != expected or provenance["upstreamCommit"] != values["UPSTREAM_COMMIT"]:
        raise ReleaseError("Guide provenance and pinned integrity metadata disagree")
    for item in provenance["files"]:
        url = f"https://raw.githubusercontent.com/MiniMax-AI/MiniMax-H3/{values['UPSTREAM_COMMIT']}/{item['upstreamPath']}"
        if item["sourceUrl"] != url:
            raise ReleaseError("Guide provenance URL disagrees with pinned upstream")
    return {"commit": values["UPSTREAM_COMMIT"], "sha256": {name: sha for name, _, sha in specs}}


def validate_install(snapshot: Path, package: Path) -> dict:
    """Test a copy: downloaded guides and Python build products never enter the candidate."""
    checks = {}
    with tempfile.TemporaryDirectory(prefix="miniconstruct-check-") as temp:
        work = Path(temp) / "source"
        shutil.copytree(snapshot, work, ignore=shutil.ignore_patterns(".git"))
        git(work, "init", "-q")
        git(work, "config", "core.autocrlf", "false")
        git(work, "add", "-f", ".")
        venv = Path(temp) / "venv"
        run([sys.executable, "-m", "venv", venv])
        python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONNOUSERSITE="1")
        env.pop("PYTHONPATH", None)
        env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = str(venv)
        env["PYTHON"] = str(python)

        def check(name, args):
            print(f"Checking {name}...", flush=True)
            try:
                output = run(args, cwd=work, env=env)
            except ReleaseError as exc:
                (package / f"{name}.log").write_bytes(getattr(exc, "output", str(exc).encode()))
                raise
            (package / f"{name}.log").write_bytes(output)
            checks[name] = {"passed": True, "log_sha256": digest(output)}

        check("fresh_install", [python, "-m", "pip", "install", "-e", ".[dev]"])
        check("guide_acquisition", [python, "-m", "miniconstruct.h3.guide_acquisition"])
        check("startup", [python, "-c", "from miniconstruct.h3.guide_acquisition import require_guides; require_guides(); "
              "from fastapi.testclient import TestClient; from miniconstruct.app import app; "
              "exec(\"with TestClient(app) as client:\\n assert client.get('/').status_code == 200\")"])
        check("python", [python, "-m", "pytest", "-q", "--basetemp", Path(temp) / "pytest"])
        js = sorted((work / "tests/js").glob("*.test.mjs"))
        if not js:
            raise ReleaseError("No JavaScript test files found")
        check("javascript", ["node", "--test", *js])
        check("whitespace", ["git", "diff", "--cached", "--check"])
        # TemporaryDirectory owns every acquired guide, even on failed checks.
    return checks


def save_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    temporary.replace(path)


def validate_public_delta(repo: Path, base: str, refs: dict, files: dict[str, bytes]) -> str:
    versions = sorted((tuple(map(int, ref[11:].split("."))), ref) for ref in refs
                      if re.fullmatch(r"refs/tags/v\d+\.\d+\.\d+", ref))
    if not versions:
        raise ReleaseError("No public semantic-version baseline tag; bootstrap requires an explicit reviewed baseline")
    _, tag = versions[-1]
    tagged = refs.get(tag + "^{}", refs[tag])
    git(repo, "merge-base", "--is-ancestor", tagged, base)
    old, _ = read_tree(repo, tagged)
    public, _ = read_tree(repo, base)
    conflicts = [p for p in set(old) | set(public) if old.get(p) != public.get(p) and public.get(p) != files.get(p)]
    if conflicts:
        raise ReleaseError("Public-only changes since " + tag + " must be integrated into source/overlay: " + ", ".join(sorted(conflicts)))
    return tag.removeprefix("refs/tags/v")


def prepare(dev: Path, staging: Path, github: Path, output: Path, *, version=None,
            source="HEAD", dry_run=False, review=False, validator=validate_install) -> dict:
    dev, staging, github, output = (p.resolve() for p in (dev, staging, github, output))
    if len({dev, staging, github}) != 3:
        raise ReleaseError("Development, staging and GitHub paths must be distinct")
    if review and not dry_run:
        raise ReleaseError("ReviewWorkingTree is available only with DryRun; never publishable")
    states = [clean(dev, allow_dirty=review), clean(staging), clean(github)]
    config, content, overlay_id = overlay(dev)
    for path in ("tools/release.py", "tools/release_policy.py", "release.ps1"):
        if content[path] != (ROOT / path).read_bytes().replace(b"\r\n", b"\n"):
            raise ReleaseError(f"Executing release tool differs from selected development checkout: {path}")
    remote_url(github, config["remote"])
    if states[2]["branch"] != config["branch"]:
        raise ReleaseError(f"GitHub checkout must be on {config['branch']}")
    sha = git(dev, "rev-parse", "--verify", source + "^{commit}")
    git(dev, "merge-base", "--is-ancestor", sha, states[0]["head"])
    refs = remote_refs(github)
    base = refs.get("refs/heads/" + config["branch"])
    if base != states[2]["head"]:
        raise ReleaseError("Public checkout and remote branch differ; inspect remote-only changes before preparing")
    public_identity = identity(github)
    versions = [tuple(map(int, ref[11:].split("."))) for ref in refs if re.fullmatch(r"refs/tags/v\d+\.\d+\.\d+", ref)]
    if not versions:
        raise ReleaseError("Cannot propose a version without a public release tag")
    latest = max(versions)
    version = version or f"{latest[0]}.{latest[1] + 1}.0"
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or tuple(map(int, version.split("."))) <= latest:
        raise ReleaseError(f"Version must be a stable version newer than {'.'.join(map(str, latest))}")
    tag = "v" + version
    for repo in (dev, staging, github):
        if tag in git(repo, "tag", "--list", tag).splitlines():
            raise ReleaseError(f"Target tag {tag} already exists in {repo}")
    source_files, modes = read_tree(dev, sha)
    if review:
        if sha != states[0]["head"]:
            raise ReleaseError("ReviewWorkingTree requires source HEAD")
        names = git_bytes(dev, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
        source_files = {}
        for raw in filter(None, names):
            name = raw.decode()
            if exclusion(name, config):
                # Keep tracked excluded blobs in the inventory without loading local private contents.
                source_files[name] = b""
            elif (dev / name).is_file():
                if (dev / name).is_symlink():
                    raise ReleaseError(f"Unsupported symlink: {name}")
                data = (dev / name).read_bytes()
                try:
                    data.decode("utf-8")
                except UnicodeDecodeError:
                    pass
                else:
                    data = data.replace(b"\r\n", b"\n")
                source_files[name] = data
    else:
        # The tool, policy and overlay themselves must be recorded at the selected source commit.
        for path, data in content.items():
            if source_files.get(path, b"").replace(b"\r\n", b"\n") != data:
                raise ReleaseError(f"Tool/overlay differs from selected source commit: {path}")
    files, inventory = assemble(source_files, config, content, version)
    guides = guide_metadata(files)
    # Require absence even if the source used an added guide target or overlay copied one back.
    if any(p in files for p in GUIDES):
        raise ReleaseError("Official guide survived export")
    if output.exists():
        raise ReleaseError(f"Output already exists; choose a new preparation directory: {output}")
    for repo in (dev, github):
        if output == repo or repo in output.parents:
            raise ReleaseError("Preparation output must be outside development and GitHub checkouts")
    output.mkdir(parents=True)
    (output / ".gitignore").write_text("*\n", encoding="utf-8")
    snapshot = output / "snapshot"
    snapshot.mkdir()
    manifest = {"schema": 1, "status": "preparing", "publishable": False, "version": version, "tag": tag,
                "source_sha": sha, "source_tree": git(dev, "rev-parse", sha + "^{tree}"),
                "overlay_id": overlay_id, "overlay_revision": sha, "config_sha256": digest(json.dumps(config, sort_keys=True).encode()),
                "repositories": states, "remote": config["remote"], "branch": config["branch"], "public_base": base,
                "identity": public_identity, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "dry_run": dry_run, "review_working_tree": review, "guides": guides, "checks": {},
                "blockers": ["Working-tree review is not a committed release source"] if review else []}
    try:
        git(snapshot, "init", "-q")
        git(snapshot, "config", "core.autocrlf", "false")
        # Fetch only PUBLIC history into the disposable object database; no development parents.
        git(snapshot, "fetch", "--no-tags", config["remote"], f"refs/heads/{config['branch']}", "+refs/tags/*:refs/tags/*")
        if git(snapshot, "rev-parse", "FETCH_HEAD") != base:
            raise ReleaseError("Remote changed during preparation; prepare again")
        manifest["previous_version"] = validate_public_delta(snapshot, base, refs, files)
        write_files(snapshot, files)
        tree = index_tree(snapshot, files, modes)
        manifest["public_tree"] = tree
        manifest["files"] = {p: {"sha256": digest(data), "mode": modes.get(p, "100644")} for p, data in sorted(files.items())}
        save_json(output / "inventory.json", inventory)
        (output / "preview.diff").write_bytes(git_bytes(snapshot, "diff", "--binary", base, tree))
        (output / "preview.txt").write_text(git(snapshot, "diff", "--stat", base, tree) + "\n" + git(snapshot, "diff", "--name-status", base, tree) + "\n", encoding="utf-8")
        save_json(output / "manifest.json", manifest)
        manifest["checks"] = validator(snapshot, output)
        actual, _ = read_tree(snapshot, tree)
        audit(actual, config)
        verify_snapshot(output, manifest, config)
        manifest["checks"]["tree_audit"] = {"passed": True}
        if set(manifest["checks"]) != set(CHECKS) or not all(c.get("passed") for c in manifest["checks"].values()):
            raise ReleaseError("A required check is missing or failed")
        if [status(p) for p in (dev, staging, github)] != states:
            raise ReleaseError("An input repository changed during preparation")
        manifest["status"] = "verified"
        manifest["publishable"] = not dry_run and not review
    except Exception as exc:
        manifest["status"] = "blocked"
        manifest["blockers"].append(str(exc))
        save_json(output / "manifest.json", manifest)
        raise
    save_json(output / "manifest.json", manifest)
    (output / "manifest.sha256").write_text(digest((output / "manifest.json").read_bytes()) + "\n")
    return manifest


def verify_snapshot(package: Path, manifest: dict, config: dict) -> None:
    snapshot = package / "snapshot"
    names = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob("*") if p.is_file() and ".git" not in p.relative_to(snapshot).parts}
    if names != set(manifest["files"]):
        raise ReleaseError("Snapshot file set changed after preparation")
    for path, entry in manifest["files"].items():
        target = snapshot / path
        if target.is_symlink() or digest(target.read_bytes()) != entry["sha256"]:
            raise ReleaseError(f"Snapshot bytes changed after preparation: {path}")
    if git(snapshot, "write-tree") != manifest["public_tree"]:
        raise ReleaseError("Snapshot index changed after preparation")
    files, modes = read_tree(snapshot, manifest["public_tree"])
    if {p: {"sha256": digest(data), "mode": modes[p]} for p, data in files.items()} != manifest["files"]:
        raise ReleaseError("Manifest files and public Git tree disagree")
    audit(files, config)


def publish(package: Path, *, confirm=input) -> dict:
    package = package.resolve()
    data = (package / "manifest.json").read_bytes()
    if digest(data) != (package / "manifest.sha256").read_text().strip():
        raise ReleaseError("Preparation manifest changed")
    m = json.loads(data)
    if m["status"] != "verified" or not m["publishable"] or m["dry_run"] or m["review_working_tree"]:
        raise ReleaseError("This preparation is not publishable; run a clean Prepare")
    if set(m["checks"]) != set(CHECKS) or not all(c.get("passed") for c in m["checks"].values()):
        raise ReleaseError("Required checks are missing/failed")
    for check in m["checks"].values():
        if "log_sha256" in check:
            name = next(k for k, v in m["checks"].items() if v is check)
            if digest((package / f"{name}.log").read_bytes()) != check["log_sha256"]:
                raise ReleaseError(f"Check log changed: {name}")
    dev, staging, github = [Path(s["path"]) for s in m["repositories"]]
    config, _, overlay_id = overlay(dev)
    runtime = overlay(ROOT)[1]
    recorded = overlay(dev)[1]
    if any(runtime[p] != recorded[p] for p in ("tools/release.py", "tools/release_policy.py", "release.ps1")):
        raise ReleaseError("Executing release tool differs from the prepared source")
    if overlay_id != m["overlay_id"]:
        raise ReleaseError("Overlay/tool changed after Prepare")
    if digest(json.dumps(config, sort_keys=True).encode()) != m["config_sha256"]:
        raise ReleaseError("Release configuration changed")
    for repo, previous in zip((dev, staging), m["repositories"]):
        if clean(repo) != previous:
            raise ReleaseError(f"Source or staging changed after Prepare: {repo}")
    clean(github)
    if git(github, "symbolic-ref", "--short", "HEAD") != m["branch"]:
        raise ReleaseError("Public checkout branch changed")
    remote_url(github, m["remote"])
    if identity(github) != m["identity"]:
        raise ReleaseError("Public author identity changed after Prepare")
    verify_snapshot(package, m, config)
    snapshot = package / "snapshot"
    who = m["identity"]
    env = {"GIT_AUTHOR_NAME": who["name"], "GIT_AUTHOR_EMAIL": who["email"], "GIT_AUTHOR_DATE": m["timestamp"],
           "GIT_COMMITTER_NAME": who["name"], "GIT_COMMITTER_EMAIL": who["email"], "GIT_COMMITTER_DATE": m["timestamp"]}
    message = f"Release MiniConstruct {m['version']}\n\nSource: {m['source_sha']}\nOverlay: {m['overlay_id']}\n"
    # Deterministic objects give retries the SAME commit and annotated tag, including lost acknowledgements.
    commit = git(snapshot, "commit-tree", m["public_tree"], "-p", m["public_base"], env=env, input=message.encode())
    stamp = int(datetime.fromisoformat(m["timestamp"]).timestamp())
    tag_body = f"object {commit}\ntype commit\ntag {m['tag']}\ntagger {who['name']} <{who['email']}> {stamp} +0000\n\nMiniConstruct {m['version']}\n"
    tag_oid = git(snapshot, "mktag", input=tag_body.encode())
    branch_ref, tag_ref = "refs/heads/" + m["branch"], "refs/tags/" + m["tag"]

    def check_remote():
        refs = remote_refs(github)
        if refs.get(branch_ref) not in {m["public_base"], commit}:
            raise ReleaseError("Remote branch diverged since Prepare; no publication attempted")
        if refs.get(tag_ref) not in {None, tag_oid}:
            raise ReleaseError("Remote release tag already exists with different content")
        return refs

    refs = check_remote()
    local_head = git(github, "rev-parse", "HEAD")
    if local_head not in {m["public_base"], commit}:
        raise ReleaseError("Public checkout has unexpected local history")
    local_tags = git(github, "show-ref", "--tags") if git(github, "tag", "--list") else ""
    if any(line.endswith(" " + tag_ref) and not line.startswith(tag_oid + " ") for line in local_tags.splitlines()):
        raise ReleaseError("Local release tag has unexpected content")
    print(f"Publish {m['tag']}\nTree: {m['public_tree']}\nSource: {m['source_sha']}\n"
          f"Origin: {m['remote']}\nAuthor: {who['name']} <{who['email']}>\n", flush=True)
    if confirm(f"Type PUBLISH {m['tag']} to publish this exact verified tree: ") != f"PUBLISH {m['tag']}":
        raise ReleaseError("Confirmation did not match; live repositories unchanged")
    # Recheck after the potentially long human review.
    for repo, previous in zip((dev, staging), m["repositories"]):
        if clean(repo) != previous:
            raise ReleaseError(f"Source or staging changed during confirmation: {repo}")
    if overlay(dev)[2] != m["overlay_id"] or identity(github) != m["identity"]:
        raise ReleaseError("Overlay or public identity changed during confirmation")
    current = clean(github)
    if current["head"] != local_head or current["branch"] != m["branch"]:
        raise ReleaseError("Public checkout changed during confirmation")
    remote_url(github, m["remote"])
    refs = check_remote()
    verify_snapshot(package, m, config)
    git(snapshot, "update-ref", "refs/heads/release", commit)
    git(snapshot, "update-ref", tag_ref, tag_oid)
    git(github, "fetch", "--no-tags", str(snapshot), "refs/heads/release", tag_ref)
    git(github, "merge-base", "--is-ancestor", m["public_base"], commit)
    if git(github, "rev-parse", commit + "^{tree}") != m["public_tree"]:
        raise ReleaseError("Imported public tree does not equal tested tree")
    if refs.get(branch_ref) != commit or refs.get(tag_ref) != tag_oid:
        # No fallback to sequential or force push if atomic publication is unsupported.
        git(github, "push", "--atomic", "origin", f"{commit}:{branch_ref}", f"{tag_oid}:{tag_ref}")
    refs = remote_refs(github)
    if refs.get(branch_ref) != commit or refs.get(tag_ref) != tag_oid or refs.get(tag_ref + "^{}") != commit:
        raise ReleaseError("Publication acknowledgement incomplete; rerun Publish with the SAME manifest")
    save_json(package / "publication.json", {"commit": commit, "tag": tag_oid, "tree": m["public_tree"], "remote_verified": True})
    # Git protects untracked collisions; failed checkout sync is safely retryable after remote success.
    git(github, "merge", "--ff-only", commit)
    git(github, "update-ref", tag_ref, tag_oid, "0" * 40 if not any(line.endswith(" " + tag_ref) for line in local_tags.splitlines()) else tag_oid)
    return {"commit": commit, "tag": tag_oid, "tree": m["public_tree"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    p = commands.add_parser("prepare")
    p.add_argument("--dev", type=Path, default=ROOT)
    p.add_argument("--staging", type=Path)
    p.add_argument("--github", type=Path)
    p.add_argument("--output", type=Path)
    p.add_argument("--version")
    p.add_argument("--source", default="HEAD")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--review-working-tree", action="store_true")
    p = commands.add_parser("publish")
    p.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.operation == "publish":
            result = publish(args.manifest.parent)
            print(json.dumps(result, indent=2))
        else:
            staging = args.staging or args.dev.with_name(args.dev.name + "-public")
            github = args.github or args.dev.with_name(args.dev.name + "-GitHub")
            output = args.output or (Path(tempfile.gettempdir()) if args.dry_run else staging / ".release-artifacts") / ("miniconstruct-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            print(f"Preparing in {output}", flush=True)
            result = prepare(args.dev, staging, github, output, version=args.version, source=args.source,
                             dry_run=args.dry_run, review=args.review_working_tree)
            print(f"{'DRY RUN' if args.dry_run else 'PREPARE'} VERIFIED: {result['tag']}\nTree: {result['public_tree']}\nManifest: {output / 'manifest.json'}\nPublishable: {result['publishable']}")
        return 0
    except (ReleaseError, OSError, KeyError, ValueError) as exc:
        print(f"RELEASE BLOCKED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
