from __future__ import annotations

import pytest

from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import RepairRequest, RevisionRequest
from miniconstruct.models.workspace import PerformanceEnergy, PerformanceStyle, ToneLevel, Workspace


def controls(**overrides):
    value = {
        "sensuality": "auto", "drama": "auto", "horror": "auto", "tension": "auto",
        "romance": "auto", "whimsy": "auto", "performanceStyle": "auto", "performanceEnergy": "auto",
    }
    value.update(overrides)
    return {"tonePerformance": value}


def test_legacy_workspace_defaults_tone_performance_to_auto(workspace_factory):
    workspace = workspace_factory()
    tone = workspace.creative_controls.tone_performance
    assert all(getattr(tone, name).value == "auto" for name in ("sensuality", "drama", "horror", "tension", "romance", "whimsy", "performance_style", "performance_energy"))
    assert "Tone / Performance" not in compile_creative_controls(workspace)


@pytest.mark.parametrize("level", list(ToneLevel))
def test_tone_levels_validate(workspace_factory, level):
    workspace = workspace_factory(creativeControls=controls(sensuality=level.value))
    assert workspace.creative_controls.tone_performance.sensuality == level


@pytest.mark.parametrize("level", list(PerformanceStyle))
def test_performance_style_values_validate(workspace_factory, level):
    assert workspace_factory(creativeControls=controls(performanceStyle=level.value)).creative_controls.tone_performance.performance_style == level


@pytest.mark.parametrize("level", list(PerformanceEnergy))
def test_performance_energy_values_validate(workspace_factory, level):
    assert workspace_factory(creativeControls=controls(performanceEnergy=level.value)).creative_controls.tone_performance.performance_energy == level


def test_sensuality_levels_compile_distinctly(workspace_factory):
    texts = [compile_creative_controls(workspace_factory(creativeControls=controls(sensuality=level))) for level in ("subtle", "moderate", "strong", "intense")]
    assert len(set(texts)) == 4
    assert "dominant artistic quality through performance, pose, movement, expression" in texts[-1]
    assert "nudity" not in texts[-1] and "explicit sexual acts" not in texts[-1]


def test_tone_dimensions_compile_distinctly_and_together(workspace_factory):
    text = compile_creative_controls(workspace_factory(creativeControls=controls(drama="strong", horror="strong", tension="moderate", romance="subtle", whimsy="subtle")))
    assert all(word in text for word in ("emotional weight", "frightening", "suspense", "romantic warmth", "playful"))
    assert "Blend active dimensions coherently" in text


def test_performance_style_and_energy_are_separate_without_cut_pacing(workspace_factory):
    text = compile_creative_controls(workspace_factory(creativeControls=controls(performanceStyle="expressive", performanceEnergy="calm")))
    assert "animated body language" in text and "relaxed, composed, and low-energy" in text
    assert "cut pacing" in text and "shot duration" not in text and "montage" not in text


def test_tone_preserves_camera_identity_and_structured_precedence(workspace_factory, image_asset_factory):
    workspace = workspace_factory(
        mode="Ref2VA", assets=[image_asset_factory()], creativeRequest="Keep everything restrained.",
        creativeControls={
            "camera": {"arc": "avoid"}, "subjectIdentityFidelity": {"level": "strict"},
            **controls(sensuality="strong", performanceStyle="exaggerated"),
        },
    )
    text = compile_creative_controls(workspace)
    assert "Avoid: Arc" in text
    assert "must not override explicit Camera Creative Controls or identity-fidelity requirements" in text
    assert "override conflicting freeform Creative Request wording" in text
    assert "Subject Identity Fidelity (strict" in text


def test_tone_round_trips_and_revision_repair_precedence(workspace_factory):
    workspace = workspace_factory(creativeControls=controls(drama="strong", performanceEnergy="intense"))
    restored = Workspace.model_validate_json(workspace.model_dump_json(by_alias=True))
    assert restored.creative_controls.tone_performance.drama.value == "strong"
    prompt = "integrated_multimodal_description:\n[Shot 1] A scene.\n\noverall_soundscape:\nRain.\n\nnon_diegetic_music:\nN/A"
    selected = "[Shot 1] A scene."
    start = prompt.index(selected)
    revision = RevisionRequest.model_validate({"workspace": workspace.model_dump(by_alias=True), "llm": {"baseUrl": "http://test/v1", "model": "writer"}, "outputIndex": 0, "selection": {"fullPrompt": prompt, "beforeSelection": prompt[:start], "selectedText": selected, "afterSelection": prompt[start + len(selected):]}, "instruction": "Make this shot cold and detached."})
    repair = RepairRequest.model_validate({"workspace": workspace.model_dump(by_alias=True), "llm": {"baseUrl": "http://test/v1", "model": "writer"}, "prompt": prompt, "findings": []})
    assert "may intentionally override workspace Creative Controls" in assemble_revision_prompt(revision).inspector_text
    assert "not authority to reconcile" in str(assemble_repair_prompt(repair, []).messages)
