"""Disposable, bounded Picture representation for H3 prompt-writing requests."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


VISION_TRANSFORM_VERSION = "h3-prompt-jpeg-768-q84-v1"
VISION_MAX_SIDE = 768
VISION_JPEG_QUALITY = 84
VISION_MAX_SOURCE_BYTES = 32 * 1024 * 1024
VISION_MAX_SOURCE_PIXELS = 16_000_000


def prompt_writer_image(data_url: str) -> tuple[str, int, int, int]:
    """Return a new JPEG data URL, width, height and encoded byte count.

    The source data URL is read only; no Workspace or ReferenceAsset is modified.
    """
    try:
        header, encoded = data_url.split(",", 1)
        if not header.startswith("data:image/") or not header.endswith(";base64"):
            raise ValueError
        if len(encoded) > ((VISION_MAX_SOURCE_BYTES + 2) // 3) * 4 + 4:
            raise ValueError
        source = base64.b64decode(encoded, validate=True)
        if len(source) > VISION_MAX_SOURCE_BYTES:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(source)) as opened:
                width, height = opened.size
                if width * height > VISION_MAX_SOURCE_PIXELS:
                    raise ValueError
                opened.load()
                image = ImageOps.exif_transpose(opened).convert("RGB")
        image.thumbnail((VISION_MAX_SIDE, VISION_MAX_SIDE), Image.Resampling.LANCZOS)
        output = BytesIO()
        image.save(output, format="JPEG", quality=VISION_JPEG_QUALITY)
        payload = output.getvalue()
        return "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii"), image.width, image.height, len(payload)
    except (ValueError, TypeError, binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Picture cannot be prepared safely for prompt-writing vision input") from exc


def model_vision_stats(messages: list[dict]) -> list[dict[str, int]]:
    """Read dimensions and encoded bytes from the actual assembled model images."""
    result = []
    for message in messages:
        for part in message.get("content", []) if isinstance(message.get("content"), list) else []:
            if part.get("type") != "image_url":
                continue
            url = part["image_url"]["url"]
            payload = base64.b64decode(url.split(",", 1)[1], validate=True)
            with Image.open(BytesIO(payload)) as image:
                result.append({"width": image.width, "height": image.height, "encodedBytes": len(payload)})
    return result
