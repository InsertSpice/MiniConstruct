from __future__ import annotations

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import RepairRequest, RevisionRequest
from miniconstruct.models.workspace import SubjectIdentityFocus, SubjectIdentityView, Workspace


def workspace_with_controls(workspace_factory, controls=None, **overrides):
    return workspace_factory(creativeControls=controls or {}, **overrides)


def test_auto_controls_emit_no_model_visible_block(workspace_factory):
    assembled = assemble_prompt(workspace_factory(), False)
    assert compile_creative_controls(workspace_factory()) == ""
    assert "ACTIVE CREATIVE CONTROLS" not in assembled.inspector_text


def test_music_and_camera_controls_compile_concisely_with_precedence(workspace_factory):
    workspace = workspace_with_controls(workspace_factory, {
        "music": {"mode": "off", "description": "kept for later"},
        "camera": {"arc": "avoid", "zoom": "avoid", "pedestal": "prefer", "tracking": "prefer"},
    })
    text = compile_creative_controls(workspace)
    assert "exactly one non_diegetic_music section" in text
    assert "Avoid: Zoom, Arc" in text
    assert "Prefer: Pedestal, Tracking" in text
    assert "Roll" not in text
    assert "override conflicting freeform Creative Request wording" in assemble_prompt(workspace, False).inspector_text


def test_music_on_and_subject_identity_fidelity_only_emit_for_subject_identity_picture(workspace_factory, image_asset_factory):
    controls = {"music": {"mode": "on", "description": "dark electronic pulse"}, "subjectIdentityFidelity": {"level": "strict"}}
    no_picture = workspace_with_controls(workspace_factory, controls)
    assert "dark electronic pulse" in compile_creative_controls(no_picture)
    assert "Subject Identity Fidelity" not in compile_creative_controls(no_picture)

    picture = workspace_with_controls(workspace_factory, controls, mode="Ref2VA", assets=[image_asset_factory()])
    text = compile_creative_controls(picture)
    assert "do not output N/A" in text and "dark electronic pulse" in text
    assert "Subject Identity Fidelity (strict; anchors: Picture 1)" in text
    assert "identity and retention" in text
    assert "New action, expression, pose, composition, framing, and camera movement remain free" in text


def test_music_on_without_a_description_is_still_an_active_valid_instruction(workspace_factory):
    workspace = workspace_with_controls(workspace_factory, {"music": {"mode": "on"}})
    text = compile_creative_controls(workspace)
    assert "Non-diegetic music: required" in text
    assert "Follow this music direction" not in text


def test_all_camera_fields_and_fidelity_values_validate(workspace_factory):
    camera = {key: "prefer" for key in ["zoom", "pushPull", "pan", "truck", "tilt", "pedestal", "arc", "tracking", "static", "shake", "pov", "roll"]}
    for level in ("balanced", "strong", "strict"):
        workspace = workspace_with_controls(workspace_factory, {"camera": camera, "subjectIdentityFidelity": {"level": level}})
        assert workspace.creative_controls.subject_identity_fidelity.level.value == level
    assert all(label in compile_creative_controls(workspace) for label in ("Zoom", "Push/Pull", "Pedestal", "POV", "Roll"))


def test_subject_identity_fidelity_levels_have_distinct_identity_wording(workspace_factory, image_asset_factory):
    for level, expected in (
        ("balanced", "recognizable character identity"),
        ("strong", "facial identity and structure, body proportions, hair, clothing"),
        ("strict", "feature-level anchors"),
    ):
        workspace = workspace_with_controls(
            workspace_factory,
            {"subjectIdentityFidelity": {"level": level}},
            mode="Ref2VA",
            assets=[image_asset_factory()],
        )
        assert expected in compile_creative_controls(workspace)


def test_subject_identity_fidelity_ignores_non_identity_picture_roles(workspace_factory, image_asset_factory):
    controls = {"subjectIdentityFidelity": {"level": "strict"}}
    for role in ("environment", "style_appearance"):
        workspace = workspace_with_controls(
            workspace_factory, controls, mode="Ref2VA", assets=[image_asset_factory(role=role)],
        )
        assert "Subject Identity Fidelity" not in compile_creative_controls(workspace)


def test_facial_identity_strong_and_strict_inspect_visible_traits_only_with_vision(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    asset.subject_identity.view = SubjectIdentityView.FRONT
    asset.notes = "Small cheek marks and the unusual eye design are especially important."
    for level, expected_strength in (("strong", "prefer a concise"), ("strict", "strongly prefer a concise")):
        workspace = workspace_with_controls(
            workspace_factory, {"subjectIdentityFidelity": {"level": level}}, mode="Ref2VA", assets=[asset],
        )
        text = compile_creative_controls(workspace, True)
        assert "visually inspect these stable-morphology anchors" in text
        assert expected_strength in text
        assert "eyes, brows, lashes, nose" in text
        assert "persistent marks, hairline/bangs" in text
        assert "Picture provenance" in text
        assert "without repeating it in every shot" in text
        assert "Notes" in text
        assert "Facial Identity Notes may strengthen genuinely permanent morphology" in text

    disabled = compile_creative_controls(workspace, False)
    assert "vision is unavailable" in disabled
    assert "visually inspect these stable-morphology anchors" not in disabled
    assert "without claiming a visually inspected feature inventory" in disabled
    assembled = assemble_prompt(workspace, True)
    assert "visually inspect these stable-morphology anchors" in assembled.inspector_text
    assert "keep them consistent" in assembled.inspector_text


def test_balanced_facial_identity_does_not_require_feature_inventory(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_with_controls(
        workspace_factory, {"subjectIdentityFidelity": {"level": "balanced"}}, mode="Ref2VA", assets=[asset],
    )
    text = compile_creative_controls(workspace, True)
    assert "recognizable character identity" in text
    assert "visually inspect" not in text and "feature-level anchor" not in text


def test_reference_aware_style_uses_visible_specifics_without_preset_filler(workspace_factory, image_asset_factory):
    style_asset = image_asset_factory(role="style_appearance")
    workspace = workspace_with_controls(
        workspace_factory, {"visualStyle": {"preset": "animated_2d_anime"}}, mode="Ref2VA", assets=[style_asset],
    )
    text = compile_creative_controls(workspace, True)
    assert "2D-animated anime presentation" in text
    assert "primary overall visual-style authority" in text
    assert "facial or eye treatment, linework, coloring, shading" in text
    assert "preset-only genre embellishment" in text
    assert "vibrant colors" not in text and "smooth cel shading" not in text

    stylized_3d = workspace_with_controls(
        workspace_factory, {"visualStyle": {"preset": "cg_3d_stylized"}}, mode="Ref2VA", assets=[image_asset_factory()],
    )
    style_text = compile_creative_controls(stylized_3d, True)
    assert "stylized 3D CG presentation" in style_text
    assert "Pixar" not in style_text and "luminous lighting" not in style_text


def test_reference_aware_style_scopes_roles_and_keeps_structural_references_out(workspace_factory, image_asset_factory):
    subject = image_asset_factory(role="subject_identity")
    environment = image_asset_factory(role="environment")
    structural = image_asset_factory(role="keyframe_anchor")
    workspace = workspace_with_controls(
        workspace_factory, {"visualStyle": {"preset": "animated_2d_anime"}}, mode="Ref2VA",
        assets=[subject, environment, structural],
    )
    text = compile_creative_controls(workspace, True)
    assert "supporting character-design evidence" in text
    assert "not treat it as authority for unrelated environment artwork" in text
    assert "environment rendering and appearance only" in text
    assert "Picture 3" not in text


def test_auto_style_remains_zero_context_even_with_references(workspace_factory, image_asset_factory):
    workspace = workspace_with_controls(workspace_factory, {}, mode="Ref2VA", assets=[image_asset_factory(role="style_appearance")])
    assert "Visual Style" not in compile_creative_controls(workspace, True)


def test_style_and_facial_policy_do_not_claim_visual_inspection_without_vision(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_with_controls(
        workspace_factory,
        {"visualStyle": {"preset": "live_action"}, "subjectIdentityFidelity": {"level": "strict"}},
        mode="Ref2VA", assets=[asset],
    )
    text = assemble_prompt(workspace, False).inspector_text
    assert "Vision is unavailable" in text
    assert "visually inspect it" not in text
    assert "anime" not in text.lower()
    assert any(part.get("type") == "image_url" for part in assemble_prompt(workspace, True).messages[-1]["content"])


def test_revision_gets_refined_policy_and_repair_remains_non_authoritative(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.focus = SubjectIdentityFocus.FACE
    workspace = workspace_with_controls(
        workspace_factory, {"subjectIdentityFidelity": {"level": "strict"}}, mode="Ref2VA", assets=[asset],
    )
    from miniconstruct.models.api import RepairRequest, RevisionRequest
    from miniconstruct.h3.revision import assemble_revision_prompt
    from miniconstruct.h3.repair import assemble_repair_prompt

    prompt = "subject_definitions:\n<Subject 1> appears.\n\nsummary:\n[reference generation] A scene.\n\nretention_analysis:\n<Subject 1>: fully_preserved.\n\ndetailed_description:\n[Shot 1] A face.\n\noverall_soundscape:\nQuiet.\n\nnon_diegetic_music:\nN/A"
    selected = "[Shot 1] A face."
    start = prompt.index(selected)
    revision = RevisionRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True), "llm": {"baseUrl": "http://test/v1", "model": "writer", "supportsVision": True}, "outputIndex": 0,
        "selection": {"fullPrompt": prompt, "beforeSelection": prompt[:start], "selectedText": selected, "afterSelection": prompt[start + len(selected):]}, "instruction": "Match Picture 1 more closely.",
    })
    assert "visually inspect" in assemble_revision_prompt(revision).inspector_text
    repair = RepairRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True), "llm": {"baseUrl": "http://test/v1", "model": "writer"}, "prompt": prompt, "findings": [],
    })
    assert "not authority to reconcile" in str(assemble_repair_prompt(repair, []).messages)


def test_visual_style_presets_compile_only_when_active_with_shot_one_placement(workspace_factory):
    workspace = workspace_with_controls(
        workspace_factory,
        {"visualStyle": {"preset": "animated_2d_anime"}},
        creativeRequest="Make this look like live action.",
    )
    text = compile_creative_controls(workspace)
    assert "2D-animated anime presentation" in text
    assert "beginning of Shot 1" in text
    assert "override conflicting freeform Creative Request wording" in assemble_prompt(workspace, False).inspector_text
    assert compile_creative_controls(workspace_factory()) == ""


def test_visual_style_variants_and_custom_text_are_distinct(workspace_factory):
    presets = {
        "animated_2d": "Use a 2D-animated presentation.",
        "animated_2d_anime": "Use a 2D-animated anime presentation.",
        "cg_3d": "Use a 3D CG presentation.",
        "cg_3d_stylized": "Use a stylized 3D CG presentation.",
        "watercolor": "watercolor visual aesthetic",
    }
    for preset, expected in presets.items():
        workspace = workspace_with_controls(workspace_factory, {"visualStyle": {"preset": preset}})
        assert expected in compile_creative_controls(workspace)
    custom = workspace_with_controls(workspace_factory, {"visualStyle": {"preset": "custom", "custom": "hand-painted cel animation"}})
    assert "hand-painted cel animation" in compile_creative_controls(custom)
    blank = workspace_with_controls(workspace_factory, {"visualStyle": {"preset": "custom", "custom": "   "}})
    assert compile_creative_controls(blank) == ""


def test_all_visual_style_presets_validate(workspace_factory):
    presets = (
        "auto", "cinematic", "live_action", "animated_2d", "animated_2d_anime", "cg_3d",
        "cg_3d_stylized", "claymation", "watercolor", "vintage_film", "custom",
    )
    for preset in presets:
        workspace = workspace_with_controls(workspace_factory, {"visualStyle": {"preset": preset}})
        assert workspace.creative_controls.visual_style.preset.value == preset


def test_old_workspace_defaults_controls_and_round_trips():
    workspace = Workspace.model_validate({
        "schemaVersion": 1, "projectName": "Old", "mode": "T2VA", "durationSeconds": 6,
        "shots": None, "aspectRatio": "auto", "variations": 1, "creativeRequest": "A cyclist", "assets": [],
    })
    assert workspace.creative_controls.music.mode.value == "auto"
    dumped = workspace.model_dump(mode="json", by_alias=True)
    assert dumped["creativeControls"]["camera"]["pedestal"] == "auto"
    assert dumped["creativeControls"]["visualStyle"]["preset"] == "auto"
    assert dumped["creativeControls"]["subjectIdentityFidelity"]["level"] == "auto"


def test_pass_one_reference_fidelity_projects_still_load(workspace_factory):
    workspace = workspace_with_controls(workspace_factory, {"referenceFidelity": {"level": "strong"}})
    assert workspace.creative_controls.subject_identity_fidelity.level.value == "strong"


def test_revision_request_can_intentionally_override_controls(workspace_factory):
    prompt = "integrated_multimodal_description:\n[Shot 1] An arc move.\n\noverall_soundscape:\nRain.\n\nnon_diegetic_music:\nN/A"
    workspace = workspace_with_controls(workspace_factory, {"camera": {"arc": "avoid"}, "visualStyle": {"preset": "animated_2d_anime"}})
    start = prompt.index("[Shot 1]")
    request = RevisionRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://test/v1", "model": "writer"}, "outputIndex": 0,
        "selection": {"fullPrompt": prompt, "beforeSelection": prompt[:start], "selectedText": prompt[start:], "afterSelection": ""},
        "instruction": "Use a slow arc around the subject.",
    })
    text = str(assemble_revision_prompt(request).messages)
    assert "may intentionally override workspace Creative Controls" in text


def test_repair_does_not_reconcile_conflicting_creative_controls(workspace_factory):
    prompt = "integrated_multimodal_description:\n[Shot 1] A scene.\n\noverall_soundscape:\nRain.\n\nnon_diegetic_music:\nScore"
    workspace = workspace_with_controls(workspace_factory, {"music": {"mode": "off"}, "visualStyle": {"preset": "watercolor"}})
    request = RepairRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://test/v1", "model": "writer"}, "prompt": prompt, "findings": [],
    })
    assert "Creative Controls" in str(assemble_repair_prompt(request, []).messages)
    assert "not authority to reconcile" in str(assemble_repair_prompt(request, []).messages)
