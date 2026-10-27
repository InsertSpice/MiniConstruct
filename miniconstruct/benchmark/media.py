"""Shared media helpers for benchmark inputs and outputs."""
from __future__ import annotations

import base64
from urllib.parse import unquote_to_bytes


def decode_data_image(url: str) -> tuple[bytes, str | None] | None:
    """Decode MiniConstruct's data URL image input without retaining its source."""
    if not url.startswith("data:") or "," not in url:
        return None
    header, payload = url[5:].split(",", 1)
    mime_type = header.split(";", 1)[0] or None
    try:
        data = base64.b64decode(payload, validate=True) if ";base64" in header.lower() else unquote_to_bytes(payload)
    except (ValueError, UnicodeEncodeError):
        return None
    return data, mime_type
