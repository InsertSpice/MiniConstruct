from __future__ import annotations

import pytest

from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import RepairRequest, RevisionRequest
from miniconstruct.models.workspace import ReferenceLayout, SubjectIdentityFocus, SubjectIdentityView, Workspace


def test_old_subject_identity_assets_materialize_subject_one_and_auto_layout(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()])
    identity = workspace.assets[0].subject_identity
    assert identity.focus == SubjectIdentityFocus.GENERAL
    assert identity.view == SubjectIdentityView.UNSPECIFIED
    assert identity.layout == ReferenceLayout.AUTO
    assert identity.subject_id == "subject-1"
    assert [(subject.id, subject.number) for subject in workspace.subjects] == [("subject-1", 1)]
    assert build_reference_manifest(workspace)["assets"][0]["subjectIdentity"]["subjectLabel"] == "Subject 1"


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
    assert round_tripped.assets[0].subject_identity.model_dump(mode="json") == {
        "subject_id": "subject-1", "focus": "face", "view": "front", "layout": "auto",
    }


def test_manifest_includes_specialist_identity_metadata_and_notes_but_not_inactive_metadata(workspace_factory, image_asset_factory):
    face = image_asset_factory()
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    face.subject_identity.view = SubjectIdentityView.FRONT
    face.notes = "Narrow amber eyes and asymmetric bangs are especially important."
    inactive = image_asset_factory("environment", role="environment", order=1)
    inactive.subject_identity.focus = SubjectIdentityFocus.FACE
    inactive.subject_identity.view = SubjectIdentityView.REAR
    manifest = build_reference_manifest(workspace_factory(mode="Ref2VA", assets=[face, inactive]))
    assert manifest["assets"][0]["subjectIdentity"] == {
        "subjectId": "subject-1", "subjectLabel": "Subject 1", "focus": "face", "layout": "auto", "view": "front", "viewActive": True,
    }
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
    assert "Facial Identity fidelity (Picture 1)" in text
    assert "stable-morphology anchors" in text and "persistent identifiers" in text
    assert "does not require a profile shot" in text and "does not require a rear shot" in text
    assert "New action, expression, pose, composition, framing, and camera movement remain free" in text


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


def test_identity_sets_are_stable_across_picture_reorder_and_export_round_trip(workspace_factory, image_asset_factory):
    first = image_asset_factory("first", order=1)
    first.subject_identity.subject_id = "subject-1"
    second = image_asset_factory("second", order=0)
    second.subject_identity.subject_id = "subject-2"
    workspace = workspace_factory(mode="Ref2VA", assets=[first, second], subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}], nextSubjectNumber=3)
    manifest = build_reference_manifest(workspace)
    assert manifest["assets"][0]["subjectIdentity"]["subjectLabel"] == "Subject 2"
    assert manifest["assets"][1]["subjectIdentity"]["subjectLabel"] == "Subject 1"
    restored = Workspace.model_validate(workspace.model_dump(mode="json", by_alias=True))
    assert [(item.id, item.subject_identity.subject_id) for item in restored.assets] == [("first", "subject-1"), ("second", "subject-2")]
    assert restored.next_subject_number == 3


def test_reference_sheet_guidance_is_safe_with_or_without_vision(workspace_factory, image_asset_factory):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    sheet.subject_identity.focus = SubjectIdentityFocus.FULL_BODY
    sheet.notes = "Front, three-quarter and rear turnaround of the same character."
    workspace = workspace_factory(mode="Ref2VA", assets=[sheet])
    vision_text = assemble_prompt(workspace, True).inspector_text
    no_vision_text = assemble_prompt(workspace, False).inspector_text
    assert "Reference Sheet semantics" in vision_text and "never reproduce panels" in vision_text
    assert "large facial panel" in vision_text.lower() and "one coherent Subject identity" in vision_text
    assert "do not invent their views" in no_vision_text
    auto = image_asset_factory()
    auto_text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[auto]), False).inspector_text
    assert "Auto layout with vision unavailable" in auto_text
    assert "Do not infer a reference sheet" in auto_text


def test_general_reference_sheet_with_vision_synthesizes_one_subject_identity(workspace_factory, image_asset_factory):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[sheet]), True).inspector_text
    assert "Visually synthesize clearly visible complementary depictions" in text
    assert "one coherent Subject identity" in text and "do not enumerate panels" in text
    assert "large facial panel may help" in text
    assert "even though its primary focus is general" not in text


def test_full_body_reference_sheet_prioritizes_multi_view_appearance_and_face_support(workspace_factory, image_asset_factory):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    sheet.subject_identity.focus = SubjectIdentityFocus.FULL_BODY
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[sheet]), True).inspector_text
    assert "Full-body prioritizes proportions, silhouette, cross-view hair, clothing, footwear, and accessories" in text
    assert "large facial panel may help" in text


def test_facial_reference_sheet_strictly_consolidates_head_evidence_without_extra_subjects(workspace_factory, image_asset_factory):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    sheet.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_factory(mode="Ref2VA", assets=[sheet], creativeControls={"subjectIdentityFidelity": {"level": "strict"}})
    text = assemble_prompt(workspace, True).inspector_text
    assert "Facial prioritizes facial/head evidence" in text
    assert "Facial Identity fidelity (Picture 1)" in text
    assert "Picture provenance" in text
    assert "one assigned Subject, not separate people" in text


def test_reference_sheet_no_vision_does_not_claim_visual_synthesis(workspace_factory, image_asset_factory):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[sheet]), False).inspector_text
    assert "Vision is unavailable" in text
    assert "do not invent their views" in text
    assert "Visually inspect clearly visible complementary depictions" not in text


@pytest.mark.parametrize("creative_request, expected", [
    ("A profile close-up of Subject 1.", "profile/side"),
    ("Show Subject 1 from the rear.", "rear/back"),
])
def test_reference_sheet_keeps_one_global_identity_but_uses_target_view_evidence(workspace_factory, image_asset_factory, creative_request, expected):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[sheet], creativeRequest=creative_request), True).inspector_text
    assert "one coherent Subject identity" in text
    assert expected in text
    assert "selectively use matching visible sheet evidence" in text
    assert "do not invent traits or reproduce the sheet" in text


def test_facial_identity_excludes_transient_reference_expression_and_strict_keeps_expression_free(workspace_factory, image_asset_factory):
    face = image_asset_factory()
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_factory(mode="Ref2VA", assets=[face], creativeRequest="Subject 1 becomes angry.", creativeControls={"subjectIdentityFidelity": {"level": "strict"}})
    text = assemble_prompt(workspace, True).inspector_text
    assert "Facial Identity semantics" in text
    assert "not a reference's smile, frown, scowl" in text
    assert "gaze, mouth openness" in text and "head pose, or blush" in text
    assert "transient performance belong to the target video" in text
    assert "New action, expression, pose, composition, framing, and camera movement remain free" in text


def test_reference_sheet_expression_examples_are_not_retained_traits(workspace_factory, image_asset_factory):
    sheet = image_asset_factory()
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[sheet]), True).inspector_text
    assert "Expression examples show deformation only, never retained traits" in text


def test_creative_request_expression_is_target_performance_not_identity_anchor(workspace_factory, image_asset_factory):
    face = image_asset_factory()
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    text = assemble_prompt(
        workspace_factory(mode="Ref2VA", assets=[face], creativeRequest="Subject 1 becomes angry and narrows her eyes."), True,
    ).inspector_text
    assert "Expression, emotion, gaze, pose" in text
    assert "transient performance belong to the target video, never identity anchors" in text


def test_requested_smile_and_matching_reference_smile_remain_transient(workspace_factory, image_asset_factory):
    face = image_asset_factory()
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_factory(mode="Ref2VA", assets=[face], creativeRequest="Subject 1 smiles warmly.", creativeControls={"subjectIdentityFidelity": {"level": "strict"}})
    text = assemble_prompt(workspace, True).inspector_text
    assert "not a reference's smile, frown, scowl" in text
    assert "transient performance belong to the target video, never identity anchors" in text


def test_only_permanent_morphology_notes_can_reinforce_facial_identity(workspace_factory, image_asset_factory):
    face = image_asset_factory()
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    face.notes = "Naturally upturned mouth corners are an identity trait; she is smiling in this reference."
    text = assemble_prompt(
        workspace_factory(mode="Ref2VA", assets=[face], creativeControls={"subjectIdentityFidelity": {"level": "strict"}}), True,
    ).inspector_text
    assert "Notes may establish permanent morphology" in text
    assert "naturally upturned mouth corners" in text
    assert "not that the Subject is smiling in this reference" in text


def test_identity_pictures_are_subject_provenance_not_standalone_retention_items(workspace_factory, image_asset_factory):
    identity = image_asset_factory()
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[identity]), True).inspector_text
    assert "Cite identity-only Picture provenance inside its <Subject N> definition" in text
    assert "do not create standalone Picture retention entries" in text
    assert "<Subject 1> is defined by <Picture 1>." in text


def test_identity_provenance_groups_multiple_pictures_under_one_subject(workspace_factory, image_asset_factory):
    body = image_asset_factory("body")
    face = image_asset_factory("face", order=1)
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[body, face]), True).inspector_text
    assert "<Subject 1> is defined by <Picture 1>, <Picture 2>." in text
    assert "<Picture 2> is Auto-layout identity evidence for <Subject 1>" in text


def test_concrete_picture_roles_remain_eligible_for_standalone_tracking(workspace_factory, image_asset_factory):
    identity = image_asset_factory("identity")
    storyboard = image_asset_factory("storyboard", role="storyboard_composition", order=1)
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[identity, storyboard]), True).inspector_text
    assert "Separately configured concrete Picture roles remain independent" in text


def test_retention_shot_membership_means_visible_subject_presence(workspace_factory, image_asset_factory):
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]), True).inspector_text
    assert "retention_analysis lists only shots where the Subject is visibly in-frame" in text
    assert "off-screen mentions do not count" in text


def test_common_sheet_and_facial_semantics_are_emitted_once(workspace_factory, image_asset_factory):
    one = image_asset_factory("one")
    one.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    two = image_asset_factory("two", order=1)
    two.subject_identity.subject_id = "subject-2"
    two.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    face_one = image_asset_factory("face-one", order=2)
    face_one.subject_identity.focus = SubjectIdentityFocus.FACE
    face_two = image_asset_factory("face-two", order=3)
    face_two.subject_identity.subject_id = "subject-2"
    face_two.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_factory(
        mode="Ref2VA", assets=[one, two, face_one, face_two],
        subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}], nextSubjectNumber=3,
        creativeControls={"subjectIdentityFidelity": {"level": "strict"}},
    )
    text = assemble_prompt(workspace, True).inspector_text
    controls = compile_creative_controls(workspace, True)
    assert text.count("Reference Sheet semantics:") == 1
    assert text.count("Facial Identity semantics:") == 1
    assert controls.count("Facial Identity fidelity (") == 1
    assert "Reference Sheet semantics" not in controls and "turnaround grids" not in controls


@pytest.mark.parametrize("focus, layout, creative_request", [
    (SubjectIdentityFocus.GENERAL, ReferenceLayout.REFERENCE_SHEET, "Subject 1 becomes angry and narrows her eyes."),
    (SubjectIdentityFocus.FULL_BODY, ReferenceLayout.SINGLE_VIEW, "Subject 1 smiles warmly."),
])
def test_all_identity_focuses_keep_requested_expression_as_target_performance(workspace_factory, image_asset_factory, focus, layout, creative_request):
    identity = image_asset_factory()
    identity.subject_identity.focus = focus
    identity.subject_identity.layout = layout
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[identity], creativeRequest=creative_request), True).inspector_text
    assert "Dedicated Subject / Identity Pictures define stable identity" in text
    assert "Expression, emotion, gaze, pose" in text
    assert "never identity anchors" in text
    assert "Facial Identity semantics:" not in text


def test_retention_membership_is_planned_before_emission_and_preserved_in_detail(workspace_factory, image_asset_factory):
    text = assemble_prompt(workspace_factory(mode="Ref2VA", assets=[image_asset_factory()]), True).inspector_text
    assert "retention_analysis lists only shots where the Subject is visibly in-frame" in text
    assert "Use canonical <Subject N> labels for visibly present referenced Subjects in each shot" in text


def test_multiple_identity_pictures_compile_as_one_subject_and_comparison_is_not_composition(workspace_factory, image_asset_factory):
    sheet = image_asset_factory("sheet", order=0)
    sheet.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    face = image_asset_factory("face", order=1)
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    second = image_asset_factory("second", order=2)
    second.subject_identity.subject_id = "subject-2"
    second.subject_identity.layout = ReferenceLayout.REFERENCE_SHEET
    comparison = image_asset_factory("compare", role="character_comparison_scale", order=3)
    comparison.comparison_subject_ids = ["subject-1", "subject-2"]
    comparison.notes = "Subject 2's head reaches Subject 1's upper chest."
    workspace = workspace_factory(mode="Ref2VA", assets=[sheet, face, second, comparison], subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}], nextSubjectNumber=3)
    text = assemble_prompt(workspace, True).inspector_text
    assert "<Subject 1> is defined by <Picture 1>, <Picture 2>." in text
    assert "<Subject 2> is defined by <Picture 3>." in text
    assert "Character Comparison / Scale reference" in text
    assert "REQUIRED: include one <Picture 4> relationship statement in subject_definitions" in text
    assert "retain its scale relationship (normally fully_preserved when maintained)" in text
    assert "never using Subject-style appears in [Shot ...] wording for <Picture 4>" in text
    assert "do not absorb it into Subject identity" in text
    assert "not target-shot composition authority" in text
    assert "head reaches Subject 1's upper chest" in text
    restored = Workspace.model_validate(workspace.model_dump(mode="json", by_alias=True))
    assert restored.assets[3].comparison_subject_ids == ["subject-1", "subject-2"]


def test_auto_and_single_view_metadata_remain_distinct(workspace_factory, image_asset_factory):
    auto = image_asset_factory("auto")
    auto.subject_identity.view = SubjectIdentityView.PROFILE
    single = image_asset_factory("single", order=1)
    single.subject_identity.layout = ReferenceLayout.SINGLE_VIEW
    single.subject_identity.view = SubjectIdentityView.REAR
    workspace = workspace_factory(mode="Ref2VA", assets=[auto, single])
    vision_text = assemble_prompt(workspace, True).inspector_text
    manifest = build_reference_manifest(workspace)
    assert "If it is clearly a multi-view reference sheet" in vision_text
    assert "rear view metadata" in vision_text
    assert manifest["assets"][0]["subjectIdentity"]["viewActive"] is True
    assert manifest["assets"][1]["subjectIdentity"]["viewActive"] is True


def test_three_subject_comparison_and_no_vision_stay_non_numeric(workspace_factory, image_asset_factory):
    assets = [image_asset_factory("one"), image_asset_factory("two", order=1), image_asset_factory("three", order=2)]
    assets[1].subject_identity.subject_id = "subject-2"
    assets[2].subject_identity.subject_id = "subject-3"
    comparison = image_asset_factory("comparison", role="character_comparison_scale", order=3)
    comparison.comparison_subject_ids = ["subject-1", "subject-2", "subject-3"]
    workspace = workspace_factory(
        mode="Ref2VA", assets=[*assets, comparison],
        subjects=[{"id": "subject-1", "number": 1}, {"id": "subject-2", "number": 2}, {"id": "subject-3", "number": 3}], nextSubjectNumber=4,
    )
    text = assemble_prompt(workspace, False).inspector_text
    assert "<Subject 1> and <Subject 2> and <Subject 3>" in text
    assert "never uninspected visual ratios" in text
    assert "1.4x" not in text


@pytest.mark.parametrize("subjects", [["subject-1"], ["subject-1", "missing"]])
def test_comparison_requires_two_known_subjects(workspace_factory, image_asset_factory, subjects):
    comparison = image_asset_factory(role="character_comparison_scale")
    comparison.comparison_subject_ids = subjects
    with pytest.raises(ValueError):
        workspace_factory(mode="Ref2VA", assets=[comparison], subjects=[{"id": "subject-1", "number": 1}])
