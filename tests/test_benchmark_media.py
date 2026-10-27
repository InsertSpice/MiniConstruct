import base64

import pytest

from miniconstruct.benchmark.media import decode_data_image


def test_decode_data_image_base64_bytes_and_mime():
    source = b"benchmark image bytes"
    url = "data:image/png;base64," + base64.b64encode(source).decode("ascii")

    assert decode_data_image(url) == (source, "image/png")


def test_decode_data_image_percent_encoded_payload():
    assert decode_data_image("data:image/svg+xml,%3Csvg%3E%3C/svg%3E") == (b"<svg></svg>", "image/svg+xml")


@pytest.mark.parametrize("url", [
    "not-a-data-url",
    "data:image/png",
    "data:image/png;base64,%%%",
    "data:image/png;base64,é",
])
def test_decode_data_image_returns_none_for_malformed_input(url):
    assert decode_data_image(url) is None
