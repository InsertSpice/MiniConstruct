"""Fail-closed public export policy, applied to bytes rather than gitignore."""
from __future__ import annotations

import fnmatch
import hashlib
import re
import struct
import zlib
from pathlib import PurePosixPath


class ReleaseError(RuntimeError):
    pass


GUIDES = tuple(f"miniconstruct/h3/guides/{name}" for name in ("SKILL.md", "base-en.txt", "ref-en.txt"))
ROOT_FILES = {".gitattributes", ".gitignore", "LICENSE", "THIRD_PARTY_NOTICES.md", "README.md",
              "RELEASING.md", "pyproject.toml", "release.ps1", "run.ps1", "run.sh",
              "setup-miniconstruct.bat", "start-miniconstruct.bat"}
ROOT_DIRS = {"miniconstruct", "web", "tests", "benchmarks", "docs", "tools", "release"}
DENIED_PARTS = {".venv", "venv", "env", "__pycache__", ".pytest_cache", "node_modules", ".local",
                "private", "projects", "generations", "logs", "tmp", "temp", "runs", "results",
                "outputs", ".release-artifacts", ".upstream-minimax-h3", ".git", "dist", "build"}
DENIED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak", ".sqlite", ".sqlite3", ".db",
                   ".zip", ".7z", ".rar", ".tar", ".gz", ".exe", ".dll", ".pyd", ".mp4", ".wav", ".mp3"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".toml", ".mjs", ".js", ".css", ".html",
                 ".ps1", ".sh", ".bat", ".svg", ".yml", ".yaml", ".patch"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def strip_png_metadata(data: bytes) -> bytes:
    """Remove textual/workflow/EXIF chunks without decoding or changing pixels."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ReleaseError("Expected a PNG asset for metadata sanitization")
    output, offset, ended = bytearray(data[:8]), 8, False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ReleaseError("Truncated PNG chunk")
        size = struct.unpack(">I", data[offset:offset + 4])[0]
        end = offset + size + 12
        chunk = data[offset:end]
        if end > len(data) or zlib.crc32(chunk[4:-4]) != struct.unpack(">I", chunk[-4:])[0]:
            raise ReleaseError("Invalid PNG chunk integrity")
        kind = chunk[4:8]
        if kind not in {b"tEXt", b"zTXt", b"iTXt", b"eXIf"}:
            output.extend(chunk)
        offset = end
        if kind == b"IEND":
            ended = True
            break
    if not ended or offset != len(data):
        raise ReleaseError("Unexpected PNG trailer or missing end chunk")
    return bytes(output)


def safe_path(name: str) -> None:
    p = PurePosixPath(name)
    if (not name or p.is_absolute() or "\\" in name or ":" in name or
            any(part in {"", ".", ".."} or part.rstrip(". ") != part for part in name.split("/")) or
            any(part.lower() == ".git" for part in p.parts)):
        raise ReleaseError(f"Unsafe export path: {name}")


def exclusion(name: str, config: dict) -> str | None:
    safe_path(name)
    p = PurePosixPath(name)
    lower = name.lower()
    if name.casefold() in {p.casefold() for p in GUIDES}:
        return "official third-party guide (acquire on the user's machine)"
    for pattern, reason in config.get("exclude", {}).items():
        if fnmatch.fnmatchcase(name.lower(), pattern.lower()):
            return reason
    if any(part.lower() in DENIED_PARTS or part.lower().endswith(".egg-info") for part in p.parts):
        return "private/runtime/environment/generated directory"
    if p.suffix.lower() in DENIED_SUFFIXES or p.name.lower().startswith(".env") or lower.endswith(".local.json"):
        return "environment, runtime, archive or generated artifact"
    if p.parts[0] not in ROOT_DIRS and name not in ROOT_FILES:
        raise ReleaseError(f"Unclassified tracked file; explicitly classify before export: {name}")
    if re.search(r"(?i)(credential|secret|token|profile[-_.]?snapshot|private[-_.]?hyperflow)", p.name):
        raise ReleaseError(f"Suspicious tracked file requires explicit policy: {name}")
    return None


def audit(files: dict[str, bytes], config: dict) -> None:
    failures = []
    case_names: set[str] = set()
    for name, data in files.items():
        if name.casefold() in case_names:
            failures.append(f"{name}: case-colliding path")
        case_names.add(name.casefold())
        why = exclusion(name, config)
        if why:
            failures.append(f"{name}: excluded path survived export ({why})")
        if len(data) > config.get("max_file_bytes", 5_000_000):
            failures.append(f"{name}: unexpectedly large file ({len(data)} bytes)")
        p = PurePosixPath(name)
        if p.suffix.lower() not in TEXT_SUFFIXES and p.name not in ROOT_FILES:
            if config.get("binary_sha256", {}).get(name) != digest(data):
                failures.append(f"{name}: unreviewed binary/type or changed asset hash")
            elif p.suffix.lower() == ".png" and strip_png_metadata(data) != data:
                failures.append(f"{name}: embedded PNG workflow/text/EXIF metadata requires sanitization")
            continue
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError:
            failures.append(f"{name}: unexpected binary in text file")
            continue
        # Report only the location/rule, never credential values.
        rules = {
            "conflict marker": r"(?m)^(?:<{7}|>{7}|={7})[ \t]*(?:\S.*)?$",
            "private key": r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----",
            "provider credential": r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})",
            "credential literal": r'''(?i)["']?(?:api[_-]?key|access[_-]?token|password|client[_-]?secret)["']?\s*[:=]\s*["'][A-Za-z0-9_+/=-]{16,}["']''',
            "personal absolute path": r"(?i)(?:\b[A-Z]:[\\/]+(?:Users|AI-Models|Pinokio)[\\/]+|/(?:home|Users)/[A-Za-z0-9_.-]+/)",
        }
        for rule, pattern in rules.items():
            for match in re.finditer(pattern, content):
                line = content.count("\n", 0, match.start()) + 1
                # Exceptions are narrow, content-addressed, and carry a review reason.
                exception = config.get("text_exceptions", {}).get(name, {})
                if exception.get("sha256") == digest(data) and rule in exception.get("rules", []) and exception.get("reason"):
                    continue
                failures.append(f"{name}:{line}: {rule}")
    if failures:
        raise ReleaseError("Public file audit failed:\n" + "\n".join(failures))
