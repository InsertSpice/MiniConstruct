from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.h3.vision_input import VISION_TRANSFORM_VERSION, prompt_writer_image
from miniconstruct.llm.compatibility import build_generation_payload
from miniconstruct.models.api import LLMSettings, RepairRequest, RevisionRequest


def picture(asset, size, color):
    image = Image.new("RGB", size, color)
    output = BytesIO()
    image.save(output, "PNG")
    payload = output.getvalue()
    asset.image.data_url = "data:image/png;base64," + base64.b64encode(payload).decode()
    asset.image.width, asset.image.height = size
    return payload


def transmitted(messages):
    parts = messages[-1]["content"]
    result = []
    for part in parts:
        if part["type"] == "image_url":
            result.append(base64.b64decode(part["image_url"]["url"].split(",", 1)[1]))
    return result


def test_actual_provider_payload_bounds_multiple_pictures_without_mutation(workspace_factory, image_asset_factory):
    assets = [image_asset_factory(f"pic-{n}", role="general_visual", order=n) for n in range(3)]
    sizes = [(1536, 1536), (1536, 768), (320, 240)]
    originals = [picture(asset, size, (n * 30, 50, 100)) for n, (asset, size) in enumerate(zip(assets, sizes))]
    workspace = workspace_factory(mode="Ref2VA", assets=assets)
    before = workspace.model_dump(mode="json", by_alias=True)
    settings = LLMSettings.model_validate({"baseUrl": "http://local.test/v1", "model": "writer", "supportsVision": True})
    assembled = assemble_prompt(workspace, True)
    payload, _ = build_generation_payload(settings, assembled.messages, stream=True)
    images = transmitted(payload["messages"])
    assert len(images) == 3
    for encoded, size in zip(images, sizes):
        with Image.open(BytesIO(encoded)) as image:
            assert image.format == "JPEG" and image.mode == "RGB"
            assert max(image.size) <= 768
            assert abs(image.width / image.height - size[0] / size[1]) < .01
            if max(size) < 768:
                assert image.size == size
        assert len(encoded) < 300_000
    assert images[0] != originals[0]
    assert workspace.model_dump(mode="json", by_alias=True) == before
    assert [base64.b64decode(asset.image.data_url.split(",", 1)[1]) for asset in assets] == originals
    assert [part["text"] for part in payload["messages"][-1]["content"] if part["type"] == "text"][1:] == [
        f"Visual input for <Picture {n + 1}> (reference.png):" for n in range(3)]
    assert VISION_TRANSFORM_VERSION == "h3-prompt-jpeg-768-q84-v1"


def test_revision_and_repair_transmit_same_derivative(workspace_factory, image_asset_factory):
    asset = image_asset_factory(role="general_visual")
    picture(asset, (1536, 1536), (20, 40, 60))
    workspace = workspace_factory(mode="Ref2VA", assets=[asset])
    settings = {"baseUrl": "http://local.test/v1", "model": "writer", "supportsVision": True}
    prompt = "[Shot 1] A person walks."
    revision = RevisionRequest.model_validate({"workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": settings, "outputIndex": 0, "instruction": "Walk faster.", "selection": {
            "fullPrompt": prompt, "beforeSelection": "", "selectedText": prompt, "afterSelection": ""}})
    repair = RepairRequest.model_validate({"workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": settings, "prompt": prompt, "findings": []})
    generated = transmitted(assemble_prompt(workspace, True).messages)
    assert transmitted(assemble_revision_prompt(revision).messages) == generated
    assert transmitted(assemble_repair_prompt(repair, []).messages) == generated


def test_malformed_and_oversized_images_fail_before_provider(workspace_factory, image_asset_factory):
    asset = image_asset_factory(role="general_visual")
    asset.image.data_url = "data:image/png;base64,AA=="
    with pytest.raises(ValueError, match="cannot be prepared safely"):
        assemble_prompt(workspace_factory(mode="Ref2VA", assets=[asset]), True)
    asset.image.data_url = "data:image/png;base64," + base64.b64encode(b"x" * (32 * 1024 * 1024 + 1)).decode()
    with pytest.raises(ValueError, match="cannot be prepared safely"):
        assemble_prompt(workspace_factory(mode="Ref2VA", assets=[asset]), True)


def test_exif_orientation_is_applied_before_resize():
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    Image.new("RGB", (1200, 600), (10, 20, 30)).save(output, "JPEG", exif=exif)
    url = "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()
    derived, width, height, encoded_bytes = prompt_writer_image(url)
    assert (width, height) == (384, 768)
    assert encoded_bytes == len(base64.b64decode(derived.split(",", 1)[1]))
