from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import Any


def _normalized(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"url"} and isinstance(value["url"], str) and value["url"].startswith("data:"):
            header, _, encoded = value["url"].partition(",")
            try:
                content = base64.b64decode(encoded, validate=True) if ";base64" in header else encoded.encode()
            except (ValueError, binascii.Error):
                content = value["url"].encode()
            return {
                "dataUrlMime": header[5:].split(";", 1)[0],
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        return {key: _normalized(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    return value


def cache_input_fingerprint(payload: dict[str, Any]) -> str:
    """Hash cache-relevant model input without retaining image bytes or secrets."""
    canonical = json.dumps(_normalized(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def safe_usage(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    allowed = ("prompt_tokens", "completion_tokens", "total_tokens")
    return {key: value[key] for key in allowed if isinstance(value.get(key), int)}
