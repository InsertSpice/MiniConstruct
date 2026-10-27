"""Writer-contract checks exercise real assembly/routing, never model prose quality."""
from __future__ import annotations

from pathlib import Path

import pytest

from miniconstruct.benchmark.runner import load_case, load_suite
from miniconstruct.h3.builder import assemble_prompt, build_h3_input_map, numbered_assets
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import RepairRequest, RevisionRequest


def user_text(assembled):
    content = assembled.messages[-1]["content"]
    return content[0]["text"] if isinstance(content, list) else content


def visual_parts(assembled):
    content = assembled.messages[-1]["content"]
    return content[1:] if isinstance(content, list) else []


@pytest.mark.parametrize("preset,observations", [
    ("animated_2d_anime", "cel-shading and shadow layering"),
    ("animated_2d", "background painting"),
    ("cg_3d", "visible material and shader appearance"),
    ("cg_3d_stylized", "stylized geometry and proportions"),
    ("live_action", "photographic qualities"),
    ("watercolor", "wash transparency"),
    ("claymation", "tactile irregularities"),
    ("cinematic", "image treatment"),
    ("vintage_film", "visible grain"),
])
def test_each_medium_gets_grounded_observation_choices(workspace_factory, image_asset_factory, preset, observations):
    asset = image_asset_factory(role="style_appearance")
    asset.notes = "Preserve the muted palette; avoid added glossy highlights."
    workspace = workspace_factory(mode="Ref2VA", assets=[asset], creativeControls={"visualStyle": {"preset": preset}})
    before = workspace.model_dump(mode="json", by_alias=True)
    assembled = assemble_prompt(workspace, True)
    text = user_text(assembled)
    assert "Visual Style (authoritative)" in text and "Visual Style (Auto)" not in text
    assert "selected preset establishes the target medium or treatment" in text
    assert observations in text
    assert "possible observations, not a mandatory checklist" in text
    assert "include only traits supported" in text
    assert "user-authored style requirements and qualifications outrank speculative observations" in text
    assert "do not invent studios, artists" in text
    assert "shader techniques" in text and "unsupported materials and textures" in text
    assert "not a new section or technical inventory" in text
    assert "crowd out action, motion, choreography" in text
    assert asset.notes in assembled.messages[0]["content"]
    assert len([p for p in visual_parts(assembled) if p["type"] == "image_url"]) == 1
    assert workspace.model_dump(mode="json", by_alias=True) == before


@pytest.mark.parametrize("roles", [
    ["subject_identity"], ["environment"], ["general_visual"],
    ["style_appearance", "subject_identity", "environment", "general_visual"],
])
def test_roles_contribute_together_without_crossing_scope(workspace_factory, image_asset_factory, roles):
    assets = [image_asset_factory(str(n), role, order=n) for n, role in enumerate(roles)]
    workspace = workspace_factory(mode="Ref2VA", assets=assets, creativeControls={"visualStyle": {"preset": "animated_2d_anime"}})
    assembled = assemble_prompt(workspace, True)
    text = user_text(assembled)
    phrases = {
        "style_appearance": "Visually inspect Picture {n} as the strongest overall visual-style authority",
        "subject_identity": "Visually inspect Picture {n} as strong evidence for the mapped character design",
        "environment": "Use Picture {n} primarily for environment appearance and rendering",
        "general_visual": "Use Picture {n} as supporting visual-style evidence",
    }
    for n, role in enumerate(roles, 1):
        assert phrases[role].format(n=n) in text
    assert "focus and visible content" in text
    assert "never character eye shape" in text
    assert "camera angle, pose, or transient expression" in text
    assert "does not make a Picture an exact compositional template" in text
    assert len([p for p in visual_parts(assembled) if p["type"] == "image_url"]) == len(roles)
    assert [label for _, label in numbered_assets(workspace)] == [f"Picture {n}" for n in range(1, len(roles) + 1)]


@pytest.mark.parametrize("preset,source", [
    ("animated_2d_anime", "Photograph: dark bob haircut, green sports shirt, soft side lighting."),
    ("live_action", "Anime illustration: dark bob haircut, green sports shirt, outlined cel shadows."),
])
def test_medium_conversion_preserves_semantics_not_incompatible_rendering(workspace_factory, image_asset_factory, preset, source):
    # Notes supply the source-medium facts; the tiny image is only a routing fixture.
    asset = image_asset_factory(role="subject_identity")
    asset.notes = source
    workspace = workspace_factory(mode="Ref2VA", assets=[asset], creativeControls={"visualStyle": {"preset": preset}})
    assembled = assemble_prompt(workspace, True)
    text = user_text(assembled)
    assert source in assembled.messages[0]["content"]
    assert "translate transferable identity, outfit, colors, design, and scene semantics" in text
    assert "Do not carry incompatible source rendering into the target" in text
    assert "contradictory photographic and hand-drawn output" in text


@pytest.mark.parametrize("vision", [False, None])
@pytest.mark.parametrize("role", ["style_appearance", "subject_identity", "environment", "general_visual", "first_frame_anchor"])
def test_no_vision_uses_notes_without_claiming_observations(workspace_factory, image_asset_factory, vision, role):
    asset = image_asset_factory(role=role)
    asset.notes = "Use thin dark outlines and a muted blue palette."
    workspace = workspace_factory(mode="I2VA" if role == "first_frame_anchor" else "Ref2VA", assets=[asset],
                                  creativeControls={"visualStyle": {"preset": "animated_2d_anime"}})
    assembled = assemble_prompt(workspace, vision)
    text = user_text(assembled)
    assert "Vision is unavailable" in text
    assert "do not claim to have inspected Pictures or infer unseen rendering traits" in text
    assert "user-authored Notes, descriptions, Reference Labels" in text
    assert "scoped to their reference roles" in text
    assert "filenames and reference-sheet layout do not establish a visual style" in text
    assert "Visually inspect" not in text
    assert asset.notes in assembled.messages[0]["content"]
    assert visual_parts(assembled) == [] and assembled.warnings


@pytest.mark.parametrize("vision", [True, False, None])
def test_no_references_keeps_concise_preset_fallback(workspace_factory, vision):
    workspace = workspace_factory(creativeControls={"visualStyle": {"preset": "animated_2d_anime"}})
    text = user_text(assemble_prompt(workspace, vision))
    assert "Use a 2D-animated anime presentation" in text
    assert "If useful appearance evidence is absent, keep the preset's concise default direction" in text
    assert "Visually inspect" not in text


def test_mixed_references_follow_roles_and_user_qualifications(workspace_factory, image_asset_factory):
    style = image_asset_factory("style", "style_appearance", order=2)
    style.notes = "Watercolor washes. Use only its muted palette for this anime output."
    subject = image_asset_factory("subject", "subject_identity", order=1)
    subject.notes = "Photographed person; preserve the outfit design."
    environment = image_asset_factory("environment", "environment", order=3)
    environment.notes = "CG room; preserve its layout, not glossy surface rendering."
    workspace = workspace_factory(mode="Ref2VA", assets=[style, subject, environment],
                                  creativeControls={"visualStyle": {"preset": "animated_2d_anime"}})
    assembled = assemble_prompt(workspace, True)
    text = user_text(assembled)
    assert "explicit user intent and reference roles rather than averaging contradictory traits" in text
    assert "Visually inspect Picture 2 as the strongest" in text
    assert "Visually inspect Picture 1 as strong evidence for the mapped character" in text
    assert "Use Picture 3 primarily for environment" in text
    for asset in (style, subject, environment):
        assert asset.notes in assembled.messages[0]["content"]


@pytest.mark.parametrize("mode,roles", [
    ("T2VA", []), ("I2VA", ["first_frame_anchor"]),
    ("FL2VA", ["first_frame_anchor", "last_frame_anchor"]),
    ("L2VA", ["last_frame_anchor"]), ("Ref2VA", ["subject_identity", "keyframe_anchor"]),
])
def test_style_only_changes_authoring_controls_not_mode_manifest_or_images(workspace_factory, image_asset_factory, mode, roles):
    workspace = workspace_factory(mode=mode, shots=2, assets=[
        image_asset_factory(str(n), role, order=n) for n, role in enumerate(roles)
    ])
    original = assemble_prompt(workspace, True)
    input_map = build_h3_input_map(workspace)
    workspace.creative_controls.visual_style.preset = "animated_2d_anime"
    explicit = assemble_prompt(workspace, True)
    assert explicit.messages[0] == original.messages[0]  # official grammar, mode, manifest, identity, continuity
    assert visual_parts(explicit) == visual_parts(original)
    assert build_h3_input_map(workspace) == input_map  # physical H3 slots unchanged
    if "first_frame_anchor" in roles:
        assert "existing first-frame anchor authority and required alignment remain unchanged" in user_text(explicit)
        assert "does not authorize replacing or restyling an anchored frame" in user_text(explicit)
    if mode == "I2VA":
        assert "at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced" in explicit.messages[0]["content"]


def test_generation_story_revision_and_repair_share_explicit_policy(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory()],
                                  creativeControls={"visualStyle": {"preset": "animated_2d_anime"}})
    generated = assemble_prompt(workspace, True)
    story = assemble_prompt(workspace, True, story_context={"currentGeneration": {"scenePlan": "Subject 1 walks."}})
    prompt = "detailed_description:\n[Shot 1] Subject 1 walks."
    settings = {"baseUrl": "http://test/v1", "model": "writer", "supportsVision": True}
    revision = assemble_revision_prompt(RevisionRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True), "llm": settings,
        "outputIndex": 0, "instruction": "Keep the style; make the walk slower.",
        "selection": {"fullPrompt": prompt, "beforeSelection": "detailed_description:\n",
                      "selectedText": "[Shot 1] Subject 1 walks.", "afterSelection": ""},
    }))
    repair = assemble_repair_prompt(RepairRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True), "llm": settings,
        "prompt": prompt, "findings": [],
    }), [])
    for assembled in (generated, story, revision, repair):
        assert "selected preset establishes the target medium or treatment" in user_text(assembled)
        assert visual_parts(assembled) == visual_parts(generated)
    assert "Text outside <MINICONSTRUCT_SELECTION> is immutable" in revision.messages[0]["content"]
    assert "may intentionally override workspace Creative Controls" in revision.messages[0]["content"]
    assert "not authority to reconcile" in repair.messages[0]["content"]


@pytest.mark.parametrize("vision", [True, False, None])
def test_custom_keeps_authored_style_with_conflicting_reference_notes(workspace_factory, image_asset_factory, vision):
    asset = image_asset_factory(role="style_appearance")
    asset.notes = "Photographic source image."
    custom = "Flat hand-painted cel animation with muted colors; no glossy surfaces"
    workspace = workspace_factory(mode="Ref2VA", assets=[asset],
                                  creativeControls={"visualStyle": {"preset": "custom", "custom": custom}})
    assembled = assemble_prompt(workspace, vision)
    text = user_text(assembled)
    assert f"Follow this custom target style: {custom}." in text
    assert "selected preset establishes" not in text
    assert "Visual Style (Auto)" not in text
    assert bool(visual_parts(assembled)) == (vision is True)


def test_opt_in_benchmark_uses_identical_scene_and_real_reference_bytes():
    root = Path(__file__).resolve().parents[1] / "benchmarks"
    suite = load_suite(root, "h3-explicit-visual-style")
    assert len(suite.cases) == 3
    assert not set(suite.cases).intersection(load_suite(root, "all").cases)
    scenes, images, presets = [], [], []
    for case_id in suite.cases:
        case, workspace, _, _ = load_case(root, case_id)
        assert case.requirements.vision
        assembled = assemble_prompt(workspace, True)
        images.append(visual_parts(assembled))
        presets.append(workspace.creative_controls.visual_style.preset.value)
        snapshot = workspace.model_dump(mode="json", by_alias=True)
        snapshot["creativeControls"]["visualStyle"]["preset"] = "auto"
        scenes.append(snapshot)
    assert presets == ["auto", "animated_2d_anime", "live_action"]
    assert scenes[0] == scenes[1] == scenes[2]
    assert images[0] == images[1] == images[2]
