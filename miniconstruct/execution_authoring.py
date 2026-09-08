"""Deterministic prompt-source identity, independent of Project storage layout."""
import hashlib

from miniconstruct.execution import canonical_json
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.models.workspace import Workspace
from miniconstruct.project_store import _parse_image_data_url


def execution_authoring_fingerprint(workspace: Workspace) -> str:
    # Project identity/name are deliberately outside semantic prompt authority.
    # Canonical hydration supplies the same bytes as an inline generation request.
    data = workspace.model_dump(mode="json", by_alias=True, exclude={
        "project_id", "project_name", "assets", "next_subject_number",
    })
    assets = []
    for asset, label in numbered_assets(workspace):
        item = asset.model_dump(mode="json", by_alias=True, exclude={"image", "order", "media_payload"})
        item["label"] = label
        if asset.kind != "image":
            item["attached"] = False
        if asset.image is not None:
            mime, payload = _parse_image_data_url(asset.image.data_url, asset.id)
            item["payload"] = {"sha256": hashlib.sha256(payload).hexdigest(), "mimeType": mime,
                               "width": asset.image.width, "height": asset.image.height}
        if asset.media_payload is not None:
            item["payload"] = {"sha256": asset.media_payload.sha256, "mimeType": asset.mime_type}
        assets.append(item)
    data["assets"] = assets
    return hashlib.sha256(canonical_json({"executionAuthoringVersion": 1, "workspace": data})).hexdigest()
