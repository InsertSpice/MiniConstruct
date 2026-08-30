from __future__ import annotations

from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import RepairRequest, RevisionRequest
from miniconstruct.models.workspace import SubjectIdentityFocus


def strict_workspace(workspace_factory, assets, **overrides):
    data = {
        "mode": "Ref2VA",
        "assets": assets,
        "creativeControls": {"subjectIdentityFidelity": {"level": "strict"}},
    }
    data.update(overrides)
    return workspace_factory(**data)


def test_full_body_and_general_identity_define_current_appearance_wardrobe_semantics(workspace_factory, image_asset_factory):
    general = image_asset_factory("general")
    full_body = image_asset_factory("full", order=1)
    full_body.subject_identity.focus = SubjectIdentityFocus.FULL_BODY
    text = compile_creative_controls(strict_workspace(workspace_factory, [general, full_body]), True)

    assert "overall recognizable identity and current appearance" in text
    assert "current clothing, footwear, accessories, and complete visible character appearance" in text
    assert "Visually compare Picture 1, Picture 2 for a consistent or compatible current wardrobe" in text
    assert "a dedicated Outfit / Clothing Picture is not required" in text
    assert "explicit part of appearance retention" in text
    assert "subject_definitions and/or retention_analysis" in text

    strong = workspace_factory(
        mode="Ref2VA",
        assets=[general, full_body],
        creativeControls={"subjectIdentityFidelity": {"level": "strong"}},
    )
    strong_text = compile_creative_controls(strong, True)
    assert "Under strong fidelity, encourage concise current-wardrobe consistency when it is clearly established" in strong_text


def test_outfit_focus_is_primary_wardrobe_authority_without_being_required(workspace_factory, image_asset_factory):
    outfit = image_asset_factory("outfit")
    outfit.subject_identity.focus = SubjectIdentityFocus.OUTFIT
    text = compile_creative_controls(strict_workspace(workspace_factory, [outfit]), True)

    assert "primary wardrobe authority for clothing, footwear, wearable design, fine detail, and disambiguation" in text
    assert "Outfit / Clothing primary wardrobe authority: Picture 1" in text


def test_facial_only_reference_does_not_invent_wardrobe_policy(workspace_factory, image_asset_factory):
    face = image_asset_factory("face")
    face.subject_identity.focus = SubjectIdentityFocus.FACE
    text = compile_creative_controls(strict_workspace(workspace_factory, [face]), True)

    assert "facial identity anchor" in text
    assert "Reference-derived wardrobe policy" not in text
    assert "current wardrobe" not in text


def test_tone_does_not_independently_change_established_wardrobe(workspace_factory, image_asset_factory):
    general = image_asset_factory("general")
    workspace = strict_workspace(
        workspace_factory,
        [general],
        creativeControls={
            "subjectIdentityFidelity": {"level": "strict"},
            "tonePerformance": {"sensuality": "intense"},
        },
    )
    text = compile_creative_controls(workspace, True)

    assert "They do not independently change a reference-established wardrobe" in text
    assert "sensual presentation" in text
    assert "nudity" not in text.lower()
    assert "must remain clothed" not in text.lower()
    assert "do not remove" not in text.lower()


def test_explicit_creative_request_wardrobe_change_has_precedence(workspace_factory, image_asset_factory):
    general = image_asset_factory("general")
    workspace = strict_workspace(
        workspace_factory,
        [general],
        creativeRequest="Change the subject into a red formal suit for this scene.",
    )
    inspector = assemble_prompt(workspace, True).inspector_text

    assert "Change the subject into a red formal suit" in inspector
    assert "Creative Request or authoritative reference-relationship statements that specify wardrobe or a wardrobe change take precedence" in inspector


def test_conflicting_wardrobe_references_are_not_flattened(workspace_factory, image_asset_factory):
    general = image_asset_factory("general")
    full_body = image_asset_factory("full", order=1)
    full_body.subject_identity.focus = SubjectIdentityFocus.FULL_BODY
    text = compile_creative_controls(strict_workspace(workspace_factory, [general, full_body]), True)

    assert "If the references materially conflict, do not flatten them into one wardrobe" in text
    assert "Outfit / Clothing focus, Notes, the Creative Request, or authoritative relationships to resolve intent" in text


def test_vision_disabled_wardrobe_policy_does_not_claim_unseen_analysis(workspace_factory, image_asset_factory):
    full_body = image_asset_factory("full")
    full_body.subject_identity.focus = SubjectIdentityFocus.FULL_BODY
    text = compile_creative_controls(strict_workspace(workspace_factory, [full_body]), False)

    assert "Vision is unavailable" in text
    assert "Visually compare" not in text
    assert "do not infer uninspected wardrobe details" in text
    assert "current clothing, footwear, accessories" not in text


def test_revision_can_locally_change_wardrobe_and_repair_remains_syntax_only(workspace_factory, image_asset_factory):
    workspace = strict_workspace(workspace_factory, [image_asset_factory("general")])
    prompt = "integrated_multimodal_description:\n[Shot 1] <Subject 1> wears a green shirt.\n\noverall_soundscape:\nQuiet.\n\nnon_diegetic_music:\nN/A"
    selected = "[Shot 1] <Subject 1> wears a green shirt."
    start = prompt.index(selected)
    revision = RevisionRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://test/v1", "model": "writer"},
        "outputIndex": 0,
        "selection": {"fullPrompt": prompt, "beforeSelection": prompt[:start], "selectedText": selected, "afterSelection": prompt[start + len(selected):]},
        "instruction": "Change the selected outfit to a red formal suit.",
    })
    repair = RepairRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "llm": {"baseUrl": "http://test/v1", "model": "writer"},
        "prompt": prompt,
        "findings": [],
    })

    assert "may intentionally override workspace Creative Controls" in assemble_revision_prompt(revision).inspector_text
    repair_text = str(assemble_repair_prompt(repair, []).messages)
    assert "Repair only the listed structural or syntactic H3 failures" in repair_text
    assert "not authority to reconcile" in repair_text
