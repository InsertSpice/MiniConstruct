from __future__ import annotations

import pytest

from miniconstruct.h3 import builder
from miniconstruct.models.workspace import ReferenceAsset, Workspace


@pytest.fixture(autouse=True)
def isolated_h3_guides(monkeypatch, tmp_path):
    """Keep public tests offline while preserving builder/validator coverage."""
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "base-en.txt").write_text("integrated_multimodal_description", encoding="utf-8")
    (guides / "ref-en.txt").write_text("official six-section full-reference grammar", encoding="utf-8")
    monkeypatch.setattr(builder, "GUIDES", guides)
    builder._read_text.cache_clear()
    yield
    builder._read_text.cache_clear()


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
                "image": {"data_url": "data:image/png;base64,AA==", "width": 1, "height": 1},
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

