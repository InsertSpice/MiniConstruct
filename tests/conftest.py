from __future__ import annotations

import pytest

from miniconstruct.models.workspace import ReferenceAsset, Workspace


@pytest.fixture
def image_asset_factory():
    def make(
        asset_id: str = "img-1",
        role: str = "subject_identity",
        order: int = 0,
        filename: str = "reference.png",
    ) -> ReferenceAsset:
        return ReferenceAsset.model_validate(
            {
                "id": asset_id,
                "kind": "image",
                "filename": filename,
                "mimeType": "image/png",
                "role": role,
                "order": order,
                "image": {"data_url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC", "width": 1, "height": 1},
            }
        )
    return make


@pytest.fixture
def workspace_factory():
    def make(**overrides) -> Workspace:
        data = {
            "schemaVersion": 1,
            "projectName": "Test",
            "mode": "T2VA",
            "durationSeconds": 8,
            "shots": None,
            "aspectRatio": "16:9",
            "variations": 1,
            "creativeRequest": "A cyclist crosses a wet street.",
            "dialogue": "",
            "referenceLabels": "",
            "assets": [],
        }
        data.update(overrides)
        return Workspace.model_validate(data)
    return make
