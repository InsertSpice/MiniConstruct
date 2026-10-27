"""Shared text protection for H3 structural checks and deterministic repair."""
import re


def mask_literal_text(text: str) -> str:
    """Mask literal dialogue and quoted scene text before structural label checks."""
    masked = re.sub(r"(?is)<d>.*?</d>", lambda match: " " * len(match.group()), text)
    return re.sub(r'"(?:\\.|[^"\\])*"', lambda match: " " * len(match.group()), masked)
