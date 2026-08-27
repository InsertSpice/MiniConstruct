from __future__ import annotations

import pytest
from pydantic import ValidationError

from miniconstruct.models.api import ProjectEnvelope
from miniconstruct.models.workspace import ReferenceAsset, Workspace


@pytest.mark.parametrize("shots", [None, 1, 5, 17])
def test_valid_shot_counts(workspace_factory, shots):
    assert workspace_factory(shots=shots).shots == shots


@pytest.mark.parametrize("shots", [0, -1, 1.5, "4", "bad"])
def test_invalid_shot_counts(workspace_factory, shots):
    with pytest.raises(ValidationError):
        workspace_factory(shots=shots)


@pytest.mark.parametrize("duration", [3.99, 15.01])
def test_official_duration_bounds(workspace_factory, duration):
    with pytest.raises(ValidationError):
        workspace_factory(durationSeconds=duration)


def test_mode_specific_frame_requirements(workspace_factory, image_asset_factory):
    first = image_asset_factory(role="first_frame_anchor")
    last = image_asset_factory("img-2", "last_frame_anchor", 1)
    assert workspace_factory(mode="I2VA", assets=[first]).mode.value == "I2VA"
    assert workspace_factory(mode="FL2VA", assets=[first, last]).mode.value == "FL2VA"
    assert workspace_factory(mode="L2VA", assets=[last]).mode.value == "L2VA"
    with pytest.raises(ValidationError):
        workspace_factory(mode="FL2VA", assets=[first])


def test_ref2va_requires_reference(workspace_factory):
    with pytest.raises(ValidationError):
        workspace_factory(mode="Ref2VA")


def test_asset_role_is_kind_specific():
    with pytest.raises(ValidationError):
        ReferenceAsset.model_validate({
            "id": "bad", "kind": "video", "filename": "a.mp4", "mimeType": "video/mp4",
            "role": "voice_timbre",
        })


def test_project_schema_round_trip_persists_image(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    project = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project", "schemaVersion": 1, "workspace": workspace.model_dump(by_alias=True),
    })
    dumped = project.model_dump(mode="json", by_alias=True)
    assert dumped["workspace"]["assets"][0]["image"]["data_url"].startswith("data:image/")


def test_project_backwards_compatible_optional_defaults(image_asset_factory):
    project = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project",
        "schemaVersion": 1,
        "workspace": {
            "mode": "Ref2VA", "durationSeconds": 6, "creativeRequest": "Use the reference.",
            "assets": [image_asset_factory().model_dump(by_alias=True)],
        },
    })
    assert project.workspace.dialogue == ""
    assert project.workspace.variations == 1


def test_video_audio_metadata_round_trip(workspace_factory):
    assets = [
        {"id": "v", "kind": "video", "filename": "tail.mp4", "mimeType": "video/mp4", "durationSeconds": 1.72, "role": "continuation_source", "attached": False},
        {"id": "a", "kind": "audio", "filename": "voice.wav", "mimeType": "audio/wav", "durationSeconds": 2.5, "role": "voice_timbre", "attached": False},
    ]
    workspace = workspace_factory(mode="Ref2VA", assets=assets)
    dumped = Workspace.model_validate_json(workspace.model_dump_json(by_alias=True))
    assert dumped.assets[0].image is None and dumped.assets[1].image is None
    assert [asset.duration_seconds for asset in dumped.assets] == [1.72, 2.5]


def test_workspace_asset_order_matches_h3_category_order(workspace_factory, image_asset_factory):
    workspace = workspace_factory(
        mode="Ref2VA",
        assets=[
            {"id": "audio", "kind": "audio", "filename": "voice.wav", "mimeType": "audio/wav", "role": "voice_timbre", "order": 0},
            {"id": "video", "kind": "video", "filename": "take.mp4", "mimeType": "video/mp4", "role": "motion_action", "order": 0},
            image_asset_factory("image", order=0),
        ],
    )
    assert [asset.kind.value for asset in workspace.ordered_assets()] == ["image", "video", "audio"]
