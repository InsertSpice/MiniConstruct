"""Provider-free production writer contracts; no semantic NLP in the validator."""
import pytest

from miniconstruct.generation import generate_workspace_prompts, resolve_narrative_request
from miniconstruct.h3.builder import ENVIRONMENT_SUBJECT_DEFINITION_TEMPLATE
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.models.api import GenerationRequest
from miniconstruct.project_store import ProjectStore
from test_benchmark import profile
from test_benchmark_environment_consistency import FakeClient


DEFINITION = (
    "<Subject 3> is the environment from <Picture 3>. Use <Picture 3> only as a reference "
    "for <Subject 3>'s environment identity, design, furniture, materials, colors, lighting, "
    "and visual style. Do NOT preserve <Picture 3>'s camera angle, framing, perspective, "
    "or composition. Reconstruct <Subject 3> from the camera viewpoint requested in each shot."
)
AUTHORED = (
    "The headboard is beneath the window; the desk is beside the bed; the duvet is pink; "
    "the cabinet is against the north wall. The desk is on the right."
)
CAMERA = (
    "Shot 1 is a profile close-up of Subject 1. Do not use Picture 3 as first-frame "
    "composition authority. Shot 2 is a close-up of Subject 1 from a new viewpoint."
)


def output(extreme=False):
    membership = "[Shot 2]" if extreme else "[Shot 1], [Shot 2]"
    first = (
        "An extreme profile close-up frames <Subject 1>; no identifiable room content is visible."
        if extreme else
        "Inside <Subject 3>, a profile close-up frames <Subject 1> with recognizable room content behind her."
    )
    return (
        "subject_definitions:\n<Subject 1> is the character from <Picture 1>.\n"
        "<Subject 2> is the companion from <Picture 2>.\n" + DEFINITION + "\n"
        "summary:\n[reference generation] Two views of <Subject 1> in the same environment.\n"
        "retention_analysis:\n<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - same character.\n"
        f"<Subject 3> (appears in {membership}): partially_preserved - stable room identity with a new viewpoint, without redesigning the room.\n"
        "detailed_description:\n[Shot 1] " + first + "\n"
        "[Shot 2] At 00:04.000, inside <Subject 3>, a close-up frames <Subject 1> from a new viewpoint. "
        + AUTHORED + "\noverall_soundscape:\nQuiet room tone.\nnon_diegetic_music:\nNone."
    )


def assert_output_contract(text, extreme=False):
    definitions = text.split("subject_definitions:\n", 1)[1].split("summary:", 1)[0]
    environment = definitions.split("<Subject 3>", 1)[1]
    assert "<Subject 3>" + environment.rstrip() == DEFINITION
    # These are fixture expectations, not production language heuristics.
    for bad in ("featuring a white bed", "bookshelf on the left", "desk on the right"):
        assert bad not in environment
    assert AUTHORED in text.split("detailed_description:", 1)[1]
    retention = text.split("retention_analysis:", 1)[1].split("detailed_description:", 1)[0]
    entry = retention.split("<Subject 3>", 1)[1]
    assert "partially_preserved" in entry
    assert ("[Shot 1]" in entry) is (not extreme)
    assert "[Shot 2]" in entry
    shots = text.split("detailed_description:", 1)[1].split("overall_soundscape:", 1)[0]
    first, second = shots.split("[Shot 2]")
    assert "profile close-up frames <Subject 1>" in first
    assert ("<Subject 3>" in first) is (not extreme)
    assert "inside <Subject 3>, a close-up frames <Subject 1>" in second


class BindingWriter(FakeClient):
    extreme = False

    async def generate(self, messages, **kwargs):
        await super().generate(messages, **kwargs)
        system = messages[0]["content"]
        assert "<Picture 3>: Environment-only" in system
        assert ENVIRONMENT_SUBJECT_DEFINITION_TEMPLATE in system
        assert "FINAL H3 subject_definitions" in system
        assert "Do NOT add MODEL-INFERRED visual object inventory" in system
        assert "MODEL-INFERRED screen-space observations" in system
        assert "Image-derived OCR/vision observations are never user-authored facts" in system
        assert "keep <Subject 1> as the framing target" in system
        assert "each listed shot MUST explicitly reference" in system
        assert "omit that shot from Environment retention membership" in system
        content = messages[1]["content"]
        user = content if isinstance(content, str) else "\n".join(
            item["text"] for item in content if item["type"] == "text")
        assert CAMERA in user
        assert AUTHORED in user
        if self.extreme:
            assert "no identifiable environment visible" in user
        return output(self.extreme)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["one_shot", "story"])
@pytest.mark.parametrize("vision", [False, True])
@pytest.mark.parametrize("extreme", [False, True])
async def test_production_environment_binding(tmp_path, workspace_factory, image_asset_factory, path, vision, extreme):
    assets = [image_asset_factory("character", "subject_identity", 0),
              image_asset_factory("companion", "general_visual", 1),
              image_asset_factory("room", "environment", 2)]
    assets[2].notes = AUTHORED
    camera = CAMERA + (" Shot 1 has no identifiable environment visible." if extreme else "")
    workspace = workspace_factory(mode="Ref2VA", shots=2, assets=assets,
                                  creativeRequest=camera + " " + AUTHORED)
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace, story_idea="Two quiet moments in a bedroom.")
    store.save_project(project.project_id, workspace,
                       planning={"scenePlan": AUTHORED, "cameraIntent": camera})
    request = GenerationRequest.model_validate({
        "workspace": workspace.model_dump(mode="json", by_alias=True),
        "filesystemProjectId": project.project_id, "generationId": project.active_generation_id,
        "authoringPath": path,
        "llm": profile(vision=vision).to_settings().model_dump(mode="json", by_alias=True),
    })
    class Writer(BindingWriter):
        pass
    Writer.extreme = extreme
    result = await generate_workspace_prompts(resolve_narrative_request(request, store), Writer)
    generated = result.variations[0].generated
    assert generated.prompt == output(extreme)
    assert generated.validation.valid, generated.validation.findings
    assert_output_contract(generated.prompt, extreme)


@pytest.mark.parametrize("bad", ["featuring a white bed, desk, lamp, bookshelf, window",
                                "bookshelf on the left", "desk on the right"])
def test_expected_writer_contract_rejects_inferred_inventory(bad):
    with pytest.raises(AssertionError):
        assert_output_contract(output().replace(DEFINITION, DEFINITION + " " + bad + "."))


@pytest.mark.parametrize("before,after,extreme", [
    ("profile close-up frames <Subject 1>", "medium close-up of <Subject 3>", False),
    ("inside <Subject 3>,", "inside the room,", False),
    ("<Subject 3> (appears in [Shot 2])", "<Subject 3> (appears in [Shot 1], [Shot 2])", True),
    (AUTHORED, "The room layout follows the reference projection.", False),
    ("Do NOT preserve <Picture 3>'s camera angle, framing, perspective, or composition.", "", False),
])
def test_expected_writer_contract_detects_smoke_regressions(before, after, extreme):
    with pytest.raises(AssertionError):
        assert_output_contract(output(extreme).replace(before, after), extreme)


def test_retention_membership_validator_remains_strict(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", shots=2,
        assets=[image_asset_factory(str(i), "general_visual" if i < 2 else "environment", i)
                for i in range(3)])
    broken = output().replace("inside <Subject 3>,", "inside the room,")
    findings = validate_workspace_prompt(broken, workspace).findings
    assert any(f.code == "retention_subject_not_in_shot" and "Subject 3" in f.message
               and "Shot 2" in f.message for f in findings)
