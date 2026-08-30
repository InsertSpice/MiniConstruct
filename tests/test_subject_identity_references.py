from __future__ import annotations

import pytest

from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import RepairRequest, RevisionRequest
from miniconstruct.models.workspace import SubjectIdentityFocus, SubjectIdentityView, Workspace


def test_old_subject_identity_assets_default_to_general_and_unspecified(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    identity = workspace.assets[0].subject_identity
    assert identity.focus == SubjectIdentityFocus.GENERAL
    assert identity.view == SubjectIdentityView.UNSPECIFIED
    assert "subjectIdentity" not in build_reference_manifest(workspace)["assets"][0]


@pytest.mark.parametrize("focus", [item.value for item in SubjectIdentityFocus])
def test_all_identity_focus_values_validate(workspace_factory, image_asset_factory, focus):
    asset = image_asset_factory().model_validate({
        **image_asset_factory().model_dump(mode="json", by_alias=True), "subjectIdentity": {"focus": focus},
    })
    assert asset.subject_identity.focus.value == focus


@pytest.mark.parametrize("view", [item.value for item in SubjectIdentityView])
def test_all_identity_view_values_validate(workspace_factory, image_asset_factory, view):
    asset = image_asset_factory().model_validate({
        **image_asset_factory().model_dump(mode="json", by_alias=True), "subjectIdentity": {"view": view},
    })
    assert asset.subject_identity.view.value == view


def test_project_round_trip_preserves_configured_subject_identity(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    asset.subject_identity.view = SubjectIdentityView.FRONT
    round_tripped = Workspace.model_validate(workspace_factory(mode="Ref2VA", assets=[asset]).model_dump(mode="json", by_alias=True))
    assert round_tripped.assets[0].subject_identity.model_dump(mode="json") == {"focus": "face", "view": "front"}


def test_manifest_includes_specialist_identity_metadata_and_notes_but_not_inactive_metadata(workspace_factory, image_asset_factory):
    face = image_asset_factory()
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    face.subject_identity.view = SubjectIdentityView.FRONT
    face.notes = "Narrow amber eyes and asymmetric bangs are especially important."
    inactive = image_asset_factory("environment", role="environment", order=1)
    inactive.subject_identity.focus = SubjectIdentityFocus.FACE
    inactive.subject_identity.view = SubjectIdentityView.REAR
    manifest = build_reference_manifest(workspace_factory(mode="Ref2VA", assets=[face, inactive]))
    assert manifest["assets"][0]["subjectIdentity"] == {"focus": "face", "view": "front"}
    assert "amber eyes" in manifest["assets"][0]["notes"]
    assert "subjectIdentity" not in manifest["assets"][1]


def test_strict_fidelity_uses_facial_and_view_semantics_without_freezing_new_shots(workspace_factory, image_asset_factory):
    face = image_asset_factory("face")
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    face.subject_identity.view = SubjectIdentityView.FRONT
    profile = image_asset_factory("profile", order=1)
    profile.subject_identity.view = SubjectIdentityView.PROFILE
    rear = image_asset_factory("rear", order=2)
    rear.subject_identity.view = SubjectIdentityView.REAR
    workspace = workspace_factory(mode="Ref2VA", assets=[face, profile, rear], creativeControls={"subjectIdentityFidelity": {"level": "strict"}})
    text = compile_creative_controls(workspace)
    assert "Picture 1 is a facial identity anchor" in text
    assert "eye shape, spacing or iris treatment" in text and "feature-level anchor" in text
    assert "does not require a profile shot" in text and "does not require a rear shot" in text
    assert "new action, expression, pose, composition, framing, and camera movement" in text


def test_vision_and_revision_context_keep_enriched_manifest(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    asset.subject_identity.view = SubjectIdentityView.FRONT
    workspace = workspace_factory(mode="Ref2VA", assets=[asset])
    assembled = assemble_prompt(workspace, True)
    assert any(part.get("type") == "image_url" for part in assembled.messages[-1]["content"])
    prompt = "integrated_multimodal_description:\n[Shot 1] A subject.\n\noverall_soundscape:\nQuiet.\n\nnon_diegetic_music:\nN/A"
    selected = "[Shot 1] A subject."
    start = prompt.index(selected)
    request = RevisionRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True), "llm": {"baseUrl": "http://test/v1", "model": "writer"}, "outputIndex": 0,
        "selection": {"fullPrompt": prompt, "beforeSelection": prompt[:start], "selectedText": selected, "afterSelection": prompt[start + len(selected):]}, "instruction": "Match the eyes.",
    })
    assert '"focus": "face"' in assemble_revision_prompt(request).inspector_text


def test_repair_keeps_subject_metadata_context_syntax_only(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_factory(mode="Ref2VA", assets=[asset])
    request = RepairRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True), "llm": {"baseUrl": "http://test/v1", "model": "writer"},
        "prompt": "integrated_multimodal_description:\n[Shot 1] A subject.\n\noverall_soundscape:\nQuiet.\n\nnon_diegetic_music:\nN/A", "findings": [],
    })
    text = str(assemble_repair_prompt(request, []).messages)
    assert "not authority to reconcile" in text and "subjectIdentity" in text
