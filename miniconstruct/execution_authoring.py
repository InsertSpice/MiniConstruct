"""Deterministic prompt-source identity, independent of Project storage layout."""
import hashlib

from miniconstruct.execution import canonical_json
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.models.workspace import Workspace
from miniconstruct.project_store import _parse_image_data_url


def execution_authoring_fingerprint(workspace: Workspace, narrative_subjects=(), continuity_context=None,
                                    visual_handoff=None, *, authoring_path="one_shot", story_context=None,
                                    canonical_payloads=None) -> str:
    # Project identity/name are deliberately outside semantic prompt authority.
    # Canonical hydration supplies the same bytes as an inline generation request.
    data = workspace.model_dump(mode="json", by_alias=True, exclude={
        "project_id", "project_name", "assets", "next_subject_number",
    })
    if authoring_path == "story":
        if story_context is None:
            raise ValueError("Story H3 fingerprint requires canonical saved Story context")
        data.pop("creativeRequest", None)
        # One-shot H3 articulation detail has no authority in Story generation.
        data.get("creativeControls", {}).pop("promptDetail", None)
    elif data.get("creativeControls", {}).get("promptDetail") == "standard":
        # Standard is behaviorally identical to the pre-field one-shot contract.
        data["creativeControls"].pop("promptDetail", None)
    assets = []
    for asset, label in numbered_assets(workspace):
        item = asset.model_dump(mode="json", by_alias=True, exclude={"image", "order", "media_payload", "payload_availability", "payload_sha256"})
        item["label"] = label
        if asset.kind != "image":
            item["attached"] = False
        if asset.image is not None:
            mime, payload = _parse_image_data_url(asset.image.data_url, asset.id)
            item["payload"] = {"sha256": hashlib.sha256(payload).hexdigest(), "mimeType": mime,
                               "width": asset.image.width, "height": asset.image.height}
        if asset.media_payload is not None:
            item["payload"] = {"sha256": asset.media_payload.sha256, "mimeType": asset.mime_type}
        if canonical_payloads is not None and asset.id in canonical_payloads:
            # Server manifest identity for status inspection, never byte readiness.
            item["payload"] = canonical_payloads[asset.id]
        assets.append(item)
    data["assets"] = assets
    from miniconstruct.models.sequence_subject import local_narrative_subjects, narrative_mapping
    mapping = narrative_mapping(local_narrative_subjects(workspace, narrative_subjects))
    source = {"executionAuthoringVersion": 1, "workspace": data}
    if mapping:
        source.update(executionAuthoringVersion=2, narrativeSubjects=mapping)
    if continuity_context is not None and any(continuity_context.model_dump().values()):
        source.update(executionAuthoringVersion=3, plannedContinuity=continuity_context.model_dump(mode="json", by_alias=True))
    if visual_handoff is not None:
        source.update(executionAuthoringVersion=4, visualHandoff=visual_handoff)
    if authoring_path == "story":
        source.update(executionAuthoringVersion=5, authoringPath="story", storyContext=story_context)
    return hashlib.sha256(canonical_json(source)).hexdigest()
