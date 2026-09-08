from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "release.ps1"


def run_powershell(*arguments: str, cwd: Path = PROJECT_ROOT, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is not installed")
    return subprocess.run(
        [executable, "-NoProfile", "-File", str(SCRIPT), *arguments],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
    )


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def run_release_helper(tmp_path: Path, function: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is not installed")
    helper = tmp_path / "release-functions.ps1"
    source = SCRIPT.read_text(encoding="utf-8")
    helper.write_text(source[source.index("Set-StrictMode"):source.index("\ntry {\n")], encoding="utf-8")
    return subprocess.run(
        [executable, "-NoProfile", "-Command", "& { . $args[0]; $callArgs = @($args[2..($args.Count - 1)]); & $args[1] @callArgs }", str(helper), function, *arguments],
        check=False,
        capture_output=True,
        text=True,
    )


def init_repo(root: Path, branch: str) -> None:
    root.mkdir()
    git(root, "init", "-b", branch)
    git(root, "config", "user.name", "Release Tool Tests")
    git(root, "config", "user.email", "tests@example.invalid")
    (root / "README.md").write_text("fixture\n", encoding="utf-8")
    git(root, "add", "README.md")
    git(root, "commit", "-m", "fixture")


def fixture_repositories(tmp_path: Path) -> tuple[Path, Path, Path]:
    dev, public, github = tmp_path / "dev", tmp_path / "public", tmp_path / "github"
    init_repo(dev, "master")
    init_repo(public, "public-release")
    init_repo(github, "master")
    (dev / "pyproject.toml").write_text('[project]\nversion = "0.3.0"\n', encoding="utf-8")
    (dev / "miniconstruct/api").mkdir(parents=True)
    (dev / "miniconstruct/h3").mkdir(parents=True)
    (dev / "miniconstruct/__init__.py").write_text('__version__ = "0.3.0"\n', encoding="utf-8")
    (dev / "miniconstruct/app.py").write_text('app = object(\n    version="0.3.0",\n)\n', encoding="utf-8")
    (dev / "miniconstruct/api/routes.py").write_text('payload = {"version": "0.3.0"}\n', encoding="utf-8")
    (dev / "miniconstruct/h3/__init__.py").write_text("", encoding="utf-8")
    (dev / "miniconstruct/h3/guide_acquisition.py").write_text(
        'import sys\n'
        'TARGETS = ("miniconstruct/h3/guides/SKILL.md", "miniconstruct/h3/guides/base-en.txt", "miniconstruct/h3/guides/ref-en.txt")\n'
        'if sys.argv[1:] == ["--managed-targets"]:\n'
        '    print("\\n".join(TARGETS))\n',
        encoding="utf-8",
    )
    git(dev, "add", ".")
    git(dev, "commit", "-m", "add version fixtures")
    return dev, public, github


def test_release_script_parses_without_running_a_release():
    executable = shutil.which("pwsh") or shutil.which("powershell")
    if executable is None:
        pytest.skip("PowerShell is not installed")
    result = subprocess.run(
        [
            executable,
            "-NoProfile",
            "-Command",
            "& { $tokens = $null; $errors = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$errors); if ($errors.Count) { $errors | ForEach-Object Message; exit 1 } }",
            str(SCRIPT),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_dry_run_checks_fixture_layout_without_mutating_it(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    heads_before = [git(repo, "rev-parse", "HEAD") for repo in (dev, public, github)]

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "PREFLIGHT PASSED" in result.stdout
    assert "does not simulate the full release sequence" in result.stdout
    assert heads_before == [git(repo, "rev-parse", "HEAD") for repo in (dev, public, github)]
    assert all(not git(repo, "status", "--porcelain") for repo in (dev, public, github))


@pytest.mark.parametrize("which", ["dev", "public", "github"])
def test_dirty_repository_stops_before_any_release_mutation(tmp_path, which):
    dev, public, github = fixture_repositories(tmp_path)
    selected = {"dev": dev, "public": public, "github": github}[which]
    (selected / "dirty.txt").write_text("do not touch\n", encoding="utf-8")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode != 0
    assert "uncommitted changes" in (result.stderr + result.stdout)
    assert (selected / "dirty.txt").read_text(encoding="utf-8") == "do not touch\n"


def test_dry_run_refuses_prohibited_public_guide(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    guide = public / "miniconstruct/h3/guides/base-en.txt"
    guide.parent.mkdir(parents=True)
    guide.write_text("third-party guide", encoding="utf-8")
    git(public, "add", ".")
    git(public, "commit", "-m", "forbidden fixture")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode != 0
    assert "prohibited MiniMax guide" in (result.stderr + result.stdout)


def test_dry_run_refuses_an_existing_target_tag(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    git(dev, "tag", "v0.4.0")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode != 0
    assert "Target tag v0.4.0 already exists" in (result.stderr + result.stdout)


def test_dry_run_recognizes_a_pending_target_version_bump(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    for relative in ("pyproject.toml", "miniconstruct/__init__.py", "miniconstruct/app.py", "miniconstruct/api/routes.py"):
        path = dev / relative
        path.write_text(path.read_text(encoding="utf-8").replace("0.3.0", "0.4.0"), encoding="utf-8")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode == 0, result.stderr
    assert "version-bump-pending" in result.stdout


def set_fixture_target_version(dev: Path) -> None:
    for relative in ("pyproject.toml", "miniconstruct/__init__.py", "miniconstruct/app.py", "miniconstruct/api/routes.py"):
        path = dev / relative
        path.write_text(path.read_text(encoding="utf-8").replace("0.3.0", "0.4.0"), encoding="utf-8")


def add_isolated_origin(repo: Path, origin: Path) -> None:
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True, text=True)
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "origin", "master")
    git(repo, "fetch", "origin", "master")


def pending_release_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    github, _, _ = fixture_repositories(tmp_path)
    origin = tmp_path / "origin.git"
    add_isolated_origin(github, origin)
    set_fixture_target_version(github)
    git(github, "add", ".")
    git(github, "commit", "-m", "Release MiniConstruct 0.4.0")
    git(github, "tag", "-a", "v0.4.0", "-m", "MiniConstruct 0.4.0")
    return github, origin, git(github, "rev-parse", "HEAD^{tree}")


def publication_state(tmp_path: Path, github: Path, expected_tree: str) -> subprocess.CompletedProcess[str]:
    return run_release_helper(tmp_path, "Get-GitHubPublicationState", str(github), "v0.4.0", "0.4.0", expected_tree)


def test_publication_state_accepts_normal_fresh_release(tmp_path):
    _, _, github = fixture_repositories(tmp_path)
    add_isolated_origin(github, tmp_path / "origin.git")

    result = publication_state(tmp_path, github, git(github, "rev-parse", "HEAD^{tree}"))

    assert result.returncode == 0, result.stderr
    assert "normal" in result.stdout


def test_publication_state_recognizes_exact_push_pending_release(tmp_path):
    github, _, expected_tree = pending_release_repository(tmp_path)
    commit_before = git(github, "rev-parse", "HEAD")
    tag_before = git(github, "rev-parse", "v0.4.0")

    result = publication_state(tmp_path, github, expected_tree)

    assert result.returncode == 0, result.stderr
    assert "push-pending" in result.stdout
    assert "resuming" in result.stdout
    assert git(github, "rev-parse", "HEAD") == commit_before
    assert git(github, "rev-parse", "v0.4.0") == tag_before


def test_publication_state_rejects_extra_local_history_before_release(tmp_path):
    github, _, _ = fixture_repositories(tmp_path)
    add_isolated_origin(github, tmp_path / "origin.git")
    set_fixture_target_version(github)
    git(github, "add", ".")
    git(github, "commit", "-m", "unintended intermediate local history")
    git(github, "commit", "--allow-empty", "-m", "Release MiniConstruct 0.4.0")
    git(github, "tag", "-a", "v0.4.0", "-m", "MiniConstruct 0.4.0")
    expected_tree = git(github, "rev-parse", "HEAD^{tree}")

    result = publication_state(tmp_path, github, expected_tree)

    assert result.returncode != 0
    assert "exact parent" in (result.stderr + result.stdout)


def test_publication_state_rejects_master_only_partial_publication(tmp_path):
    github, _, expected_tree = pending_release_repository(tmp_path)
    git(github, "push", "origin", "master")
    git(github, "fetch", "origin", "master")

    result = publication_state(tmp_path, github, expected_tree)

    assert result.returncode != 0
    assert "exact parent" in (result.stderr + result.stdout)


def test_publication_state_recognizes_lost_atomic_push_acknowledgement(tmp_path):
    github, _, expected_tree = pending_release_repository(tmp_path)
    git(github, "push", "--atomic", "origin", "master", "v0.4.0")
    git(github, "fetch", "origin", "master")

    result = publication_state(tmp_path, github, expected_tree)

    assert result.returncode == 0, result.stderr
    assert "already-pushed" in result.stdout
    assert "already publish" in result.stdout


def test_publication_state_rejects_wrong_local_tag_or_staging_tree(tmp_path):
    github, _, expected_tree = pending_release_repository(tmp_path)
    git(github, "tag", "-d", "v0.4.0")
    git(github, "tag", "-a", "v0.4.0", "HEAD~1", "-m", "MiniConstruct 0.4.0")

    wrong_tag = publication_state(tmp_path, github, expected_tree)
    assert wrong_tag.returncode != 0
    assert "does not peel to local master HEAD" in (wrong_tag.stderr + wrong_tag.stdout)

    git(github, "tag", "-d", "v0.4.0")
    git(github, "tag", "-a", "v0.4.0", "-m", "MiniConstruct 0.4.0")
    wrong_tree = publication_state(tmp_path, github, "0" * 40)
    assert wrong_tree.returncode != 0
    assert "differs from the expected public staging tree" in (wrong_tree.stderr + wrong_tree.stdout)


def test_publication_state_rejects_wrong_remote_tag_and_advanced_origin(tmp_path):
    github, origin, expected_tree = pending_release_repository(tmp_path)
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(origin), str(other)], check=True, capture_output=True, text=True)
    git(other, "config", "user.name", "Release Tool Tests")
    git(other, "config", "user.email", "tests@example.invalid")
    (other / "remote-only.txt").write_text("different\n", encoding="utf-8")
    git(other, "add", ".")
    git(other, "commit", "-m", "other remote state")
    git(other, "tag", "-a", "v0.4.0", "-m", "MiniConstruct 0.4.0")
    git(other, "push", "origin", "v0.4.0")

    wrong_remote = publication_state(tmp_path, github, expected_tree)
    assert wrong_remote.returncode != 0
    assert "partial, divergent, or ambiguous" in (wrong_remote.stderr + wrong_remote.stdout)

    git(other, "push", "origin", "master")
    git(github, "fetch", "origin", "master")
    git(other, "push", "origin", ":refs/tags/v0.4.0")
    advanced = publication_state(tmp_path, github, expected_tree)
    assert advanced.returncode != 0
    assert "exact parent" in (advanced.stderr + advanced.stdout)


def test_dry_run_recognizes_an_exact_already_staged_version_bump(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    set_fixture_target_version(dev)
    git(dev, "add", "pyproject.toml", "miniconstruct/__init__.py", "miniconstruct/app.py", "miniconstruct/api/routes.py")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode == 0, result.stderr
    assert "version-bump-pending" in result.stdout


def test_version_bump_resume_rejects_unrelated_staged_version_file_edit(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    set_fixture_target_version(dev)
    app = dev / "miniconstruct/app.py"
    app.write_text(app.read_text(encoding="utf-8") + "# staged-only edit\n", encoding="utf-8")
    git(dev, "add", "miniconstruct/app.py")
    app.write_text(app.read_text(encoding="utf-8").replace("# staged-only edit\n", ""), encoding="utf-8")
    index_blob_before = git(dev, "rev-parse", ":miniconstruct/app.py")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode != 0
    assert "beyond the exact version" in (result.stderr + result.stdout)
    assert git(dev, "rev-parse", ":miniconstruct/app.py") == index_blob_before


def test_version_bump_resume_rejects_extra_edit_inside_version_source(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    for relative in ("pyproject.toml", "miniconstruct/__init__.py", "miniconstruct/app.py", "miniconstruct/api/routes.py"):
        path = dev / relative
        path.write_text(path.read_text(encoding="utf-8").replace("0.3.0", "0.4.0"), encoding="utf-8")
    app = dev / "miniconstruct/app.py"
    app.write_text(app.read_text(encoding="utf-8") + "# unrelated edit\n", encoding="utf-8")

    rejected = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )
    accepted_as_correction = run_powershell(
        "-Version", "0.4.0", "-DryRun", "-RefreshDevelopmentSource",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert rejected.returncode != 0
    assert "beyond the exact version" in (rejected.stderr + rejected.stdout)
    assert accepted_as_correction.returncode == 0, accepted_as_correction.stderr
    assert "intentional-correction" in accepted_as_correction.stdout


def test_atomic_version_validation_does_not_write_earlier_files(tmp_path):
    dev, _, _ = fixture_repositories(tmp_path)
    malformed = dev / "miniconstruct/api/routes.py"
    malformed.write_text('payload = {"version": "0.3.0", "version": "0.3.0"}\n', encoding="utf-8")
    before = {path: (dev / path).read_bytes() for path in ("pyproject.toml", "miniconstruct/__init__.py", "miniconstruct/app.py", "miniconstruct/api/routes.py")}

    result = run_release_helper(tmp_path, "Set-ProjectVersion", str(dev), "0.4.0")

    assert result.returncode != 0
    assert "Expected exactly one authoritative version field" in (result.stderr + result.stdout)
    assert before == {path: (dev / path).read_bytes() for path in before}


def test_cached_conflict_marker_scan_handles_match_no_match_and_error(tmp_path):
    repo = tmp_path / "repo"
    init_repo(repo, "master")
    (repo / "README.md").write_text("staged without markers\n", encoding="utf-8")
    git(repo, "add", "README.md")
    clean = run_release_helper(tmp_path, "Assert-NoCachedConflictMarkers", str(repo))
    assert clean.returncode == 0, clean.stderr
    (repo / "README.md").write_text("<<<<<<< ours\n", encoding="utf-8")
    git(repo, "add", "README.md")
    found = run_release_helper(tmp_path, "Assert-NoCachedConflictMarkers", str(repo))
    assert found.returncode != 0
    assert "contains merge-conflict markers" in (found.stderr + found.stdout)
    failed = run_release_helper(tmp_path, "Assert-NoCachedConflictMarkers", str(tmp_path / "missing-repo"))
    assert failed.returncode != 0
    assert "conflict-marker scan failed" in (failed.stderr + failed.stdout)


def test_unrelated_merge_head_is_rejected_without_committing_or_aborting(tmp_path):
    _, public, _ = fixture_repositories(tmp_path)
    make_unresolved_public_merge(public)
    head_before = git(public, "rev-parse", "HEAD")
    merge_head_before = git(public, "rev-parse", "-q", "--verify", "MERGE_HEAD")

    result = run_release_helper(tmp_path, "Complete-PublicMerge", str(public), "0" * 40)

    assert result.returncode != 0
    assert "unrelated merge in progress" in (result.stderr + result.stdout)
    assert git(public, "rev-parse", "HEAD") == head_before
    assert git(public, "rev-parse", "-q", "--verify", "MERGE_HEAD") == merge_head_before


def test_prepared_github_index_requires_exact_tree_and_no_untracked_files(tmp_path):
    repo = tmp_path / "github"
    init_repo(repo, "master")
    (repo / "README.md").write_text("prepared\n", encoding="utf-8")
    git(repo, "add", "README.md")
    expected_tree = git(repo, "write-tree")

    accepted = run_release_helper(tmp_path, "Assert-PreparedGitHubState", str(repo), expected_tree)
    wrong_tree = run_release_helper(tmp_path, "Assert-PreparedGitHubState", str(repo), "0" * 40)
    (repo / "unexpected.txt").write_text("no\n", encoding="utf-8")
    untracked = run_release_helper(tmp_path, "Assert-PreparedGitHubState", str(repo), expected_tree)

    assert accepted.returncode == 0, accepted.stderr
    assert wrong_tree.returncode != 0
    assert "do not equal" in (wrong_tree.stderr + wrong_tree.stdout)
    assert untracked.returncode != 0
    assert "untracked files" in (untracked.stderr + untracked.stdout)


def test_release_script_retains_the_critical_resume_and_publish_guards():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "--no-commit" in source
    assert source.index("Invoke-PublicTests $PublicRepo $python") < source.index("Complete-PublicMerge $PublicRepo $developmentHead")
    assert "MERGE_HEAD is $mergeHead, not the established tested source" in source
    assert "Assert-PreparedGitHubState $GitHubRepo $stagingTree" in source
    assert "-RefreshDevelopmentSource" in source
    assert "Expected exactly one authoritative version field" in source
    assert "push', '--atomic', 'origin', 'master', $tag" in source
    assert "Get-GitHubPublicationState $GitHubRepo $tag $Version $stagingTree" in source
    assert "Get-RemoteTagCommit" in source
    assert "--managed-targets" in source
    assert "tag', '-d'" not in source
    assert "push', '--force" not in source
    assert "reset', '--hard'" not in source
    assert "Git ref publication is confirmed; only the GitHub Release object failed" in source
    assert source.index("Release notes file does not exist") < source.index("Set-ProjectVersion $DevRepo $Version")


def test_public_test_failure_cleans_guides_and_leaves_merge_uncommitted(tmp_path):
    seed = tmp_path / "seed"
    init_repo(seed, "master")
    (seed / ".gitignore").write_text("__pycache__/\n*.pyc\n.pytest_cache/\n", encoding="utf-8")
    for relative, content in {
        "pyproject.toml": '[project]\nversion = "0.3.0"\n',
        "miniconstruct/__init__.py": '__version__ = "0.3.0"\n',
        "miniconstruct/app.py": 'app = object(\n    version="0.3.0",\n)\n',
        "miniconstruct/api/routes.py": 'payload = {"version": "0.3.0"}\n',
        "tests/test_ok.py": 'def test_ok(): assert True\n',
        "tests/js/ok.test.mjs": 'import test from "node:test"; test("ok", () => {});\n',
            "miniconstruct/h3/__init__.py": "",
            "miniconstruct/h3/guide_acquisition.py": (
                'import sys\n'
                'from pathlib import Path\n'
                'TARGETS = ("SKILL.md", "base-en.txt", "ref-en.txt", "future-en.txt")\n'
                'if sys.argv[1:] == ["--managed-targets"]:\n'
                '    print("\\n".join(f"miniconstruct/h3/guides/{name}" for name in TARGETS))\n'
                '    raise SystemExit(0)\n'
                'for name in TARGETS:\n'
                '    target = Path(__file__).parent / "guides" / name\n'
                '    target.parent.mkdir(parents=True, exist_ok=True)\n'
                '    target.write_text("temporary", encoding="utf-8")\n'
        ),
    }.items():
        path = seed / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "release fixture")
    for relative in ("pyproject.toml", "miniconstruct/__init__.py", "miniconstruct/app.py", "miniconstruct/api/routes.py"):
        path = seed / relative
        path.write_text(path.read_text(encoding="utf-8").replace("0.3.0", "0.4.0"), encoding="utf-8")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "Release MiniConstruct 0.4.0", "-m", "MiniConstruct-Release-Version: 0.4.0")
    dev, public, github = tmp_path / "dev", tmp_path / "public", tmp_path / "github"
    subprocess.run(["git", "clone", str(seed), str(dev)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "clone", str(seed), str(public)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "clone", str(seed), str(github)], check=True, capture_output=True, text=True)
    for repo in (dev, public, github):
        git(repo, "config", "user.name", "Release Tool Tests")
        git(repo, "config", "user.email", "tests@example.invalid")
    git(public, "checkout", "-b", "public-release", "HEAD~1")
    (public / "tests/test_public_failure.py").write_text('def test_public_failure(): assert False\n', encoding="utf-8")
    git(public, "add", ".")
    git(public, "commit", "-m", "simulate public test failure")
    public_head_before = git(public, "rev-parse", "HEAD")

    result = run_powershell(
        "-Version", "0.4.0", "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github),
        "-PythonExe", sys.executable,
    )

    assert result.returncode != 0
    assert "public staging repository Python tests failed" in (result.stderr + result.stdout)
    assert git(public, "rev-parse", "HEAD") == public_head_before
    assert git(public, "rev-parse", "-q", "--verify", "MERGE_HEAD")
    assert not any((public / "miniconstruct/h3/guides" / name).exists() for name in ("SKILL.md", "base-en.txt", "ref-en.txt", "future-en.txt"))


def make_unresolved_public_merge(public: Path) -> None:
    (public / "README.md").write_text("staging change\n", encoding="utf-8")
    git(public, "add", "README.md")
    git(public, "commit", "-m", "staging change")
    git(public, "checkout", "-b", "release-side", "HEAD~1")
    (public / "README.md").write_text("side change\n", encoding="utf-8")
    git(public, "add", "README.md")
    git(public, "commit", "-m", "side change")
    git(public, "checkout", "public-release")
    result = subprocess.run(["git", "-C", str(public), "merge", "release-side"], capture_output=True, text=True)
    assert result.returncode != 0


def test_dry_run_leaves_an_unresolved_staging_merge_in_progress(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    make_unresolved_public_merge(public)

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode != 0
    assert "merge with unresolved paths" in (result.stderr + result.stdout)
    assert git(public, "rev-parse", "-q", "--verify", "MERGE_HEAD")


def test_dry_run_recognizes_a_resolved_staged_staging_merge(tmp_path):
    dev, public, github = fixture_repositories(tmp_path)
    make_unresolved_public_merge(public)
    (public / "README.md").write_text("intentional resolution\n", encoding="utf-8")
    git(public, "add", "README.md")
    merge_head_before = git(public, "rev-parse", "-q", "--verify", "MERGE_HEAD")

    result = run_powershell(
        "-Version", "0.4.0", "-DryRun",
        "-DevRepo", str(dev), "-PublicRepo", str(public), "-GitHubRepo", str(github), "-PythonExe", sys.executable,
    )

    assert result.returncode == 0, result.stderr
    assert "resolved, staged merge" in result.stdout
    assert git(public, "rev-parse", "-q", "--verify", "MERGE_HEAD") == merge_head_before
