"""Offline production-policy and single-case promotion-gate regressions."""
from pathlib import Path
import zipfile

import pytest

from miniconstruct.benchmark import environment_consistency as bench
from miniconstruct.generation import generate_workspace_prompts, resolve_narrative_request
from miniconstruct.h3.builder import assemble_prompt, ENVIRONMENT_REFERENCE_POLICY, instruction_layers
from miniconstruct.models.api import GenerationRequest
from miniconstruct.project_store import ProjectStore
from test_benchmark import profile
from test_benchmark_environment_consistency import FakeClient, ROOT, FIXTURE, copy_fixture

OUTPUT = (Path(__file__).parent / "fixtures/environment/production-lateral.txt").read_text(encoding="utf-8")


def semantics(workspace, vision=False):
    return dict(instruction_layers(workspace, vision))["Subject and reference semantics"]


@pytest.mark.parametrize("vision", [False, True, None])
def test_sparse_environment_policy(workspace_factory, image_asset_factory, vision):
    workspace = workspace_factory(mode="Ref2VA", assets=[image_asset_factory(role="environment")])
    text = semantics(workspace, vision)
    assert "stable environment identity and appearance" in text
    assert "canonical minimal, viewpoint-neutral Environment Subject" in text
    assert "does NOT define target first-frame composition" in text
    assert "lens/framing, perspective, required 2D projection, source-screen coordinates" in text
    assert "Do not preserve its camera angle, framing, perspective or composition" in text
    assert "MODEL-INFERRED screen-space observations" in text
    assert "Do not assume H3 reliably transforms source-frame left/right" in text
    assert "Do not manufacture an exhaustive textual floor plan" in text
    assert "stronger authored/continuity authority" in text
    assert "relevant [Shot N]" in text
    assert "do not invent extra blocking" in text
    assert "normally use partially_preserved" in text
    assert "not permission to deliberately redesign the room" in text
    assert "With no meaningful viewpoint change" in text
    assert "required mode frame anchor" in text
    assert "explicitly reproduces the source composition" in text


@pytest.mark.parametrize("role", [None, "storyboard_composition", "keyframe_anchor", "first_frame_anchor", "last_frame_anchor"])
def test_role_isolation(workspace_factory, image_asset_factory, role):
    assets = [image_asset_factory(role=role)] if role else []
    workspace = workspace_factory(mode="Ref2VA" if role else "T2VA", assets=assets)
    text = assemble_prompt(workspace, False).messages[0]["content"]
    assert "Environment-only evidence" not in text
    assert "MODEL-INFERRED screen-space observations" not in text
    if role:
        assert workspace.assets[0].role == role
        assert role in text  # Original role remains in the canonical manifest.


@pytest.mark.parametrize("mode,roles,required", [
    ("I2VA", ["first_frame_anchor"], "at 0.00 seconds"),
    ("FL2VA", ["first_frame_anchor", "last_frame_anchor"], "Picture 2"),
    ("L2VA", ["last_frame_anchor"], "official last-frame alignment grammar"),
])
def test_mode_anchor_isolation(workspace_factory, image_asset_factory, mode, roles, required):
    anchors = [image_asset_factory(str(i), role, i) for i, role in enumerate(roles)]
    workspace = workspace_factory(mode=mode, shots=1, assets=anchors)
    anchor_guidance = dict(instruction_layers(workspace, False))["Mode and reference guidance"]
    workspace.assets.append(image_asset_factory("room", "environment", 9))
    assembled = assemble_prompt(workspace, False)
    assert dict(instruction_layers(workspace, False))["Mode and reference guidance"] == anchor_guidance
    assert required in assembled.messages[0]["content"]
    # These modes number only their anchors, so inactive Environment evidence
    # cannot add policy or weaken exact-frame authority.
    assert "Environment-only evidence" not in assembled.messages[0]["content"]


def test_environment_policy_once_and_composition_coexists(workspace_factory, image_asset_factory):
    workspace = workspace_factory(mode="Ref2VA", assets=[
        image_asset_factory("room", "environment"), image_asset_factory("second", "environment", 1),
        image_asset_factory("composition", "storyboard_composition", 2)])
    text = assemble_prompt(workspace, False).messages[0]["content"]
    assert text.count("MODEL-INFERRED screen-space observations") == 1
    assert "<Picture 1>, <Picture 2>: Environment-only" in text
    assert "storyboard_composition" in text
    assert "separately configured composition/anchor use" in text


class ProductionWriter(FakeClient):
    async def generate(self, messages, **kwargs):
        await super().generate(messages, **kwargs)
        system = messages[0]["content"]
        assert "canonical minimal, viewpoint-neutral Environment Subject" in system
        assert "normally use partially_preserved" in system
        assert "Benchmark-only" not in system
        return OUTPUT


@pytest.mark.asyncio
async def test_main_and_story_generation_share_policy_and_authored_topology(tmp_path, workspace_factory, image_asset_factory):
    authored = "Subject 1 stands on the left side of the doorway; the desk is on the right. The headboard is beneath the window; the desk is beside the bed; the red cabinet is against the north wall."
    workspace = workspace_factory(mode="Ref2VA", shots=1, creativeRequest=authored,
        referenceLabels=authored, assets=[image_asset_factory(role="environment")])
    workspace.assets[0].notes = authored
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace, story_idea="A quiet bedroom scene.")
    store.save_project(project.project_id, workspace, planning={"scenePlan": authored,
        "cameraIntent": "New lateral bedside view at mattress height."})
    systems = []
    for path in ("one_shot", "story"):
        request = GenerationRequest.model_validate({"workspace": workspace.model_dump(mode="json", by_alias=True),
            "filesystemProjectId": project.project_id, "generationId": project.active_generation_id,
            "authoringPath": path, "llm": profile(vision=False).to_settings().model_dump(mode="json", by_alias=True)})
        resolved = resolve_narrative_request(request, store)
        result = await generate_workspace_prompts(resolved, ProductionWriter)
        system, user = [m["content"] for m in result.assembled.messages]
        assert authored in user
        assert authored in system  # Notes in manifest also survive unchanged.
        assert "Preserve explicit user-authored spatial relationships" in system
        assert result.variations[0].generated.prompt == OUTPUT
        assert result.variations[0].generated.validation.valid
        systems.append(system)
    policy = ENVIRONMENT_REFERENCE_POLICY.format(pictures="<Picture 1>")
    assert all(s.count(policy) == 1 for s in systems)
    sections = bench._sections(OUTPUT)
    assert bench.neutral_definition(sections["subject_definitions"])
    assert "partially_preserved" in sections["retention_analysis"]
    assert "without redesigning" in sections["retention_analysis"]
    assert "mattress height" in bench._shot_one(sections["detailed_description"])


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_production_gate_uses_exact_production_messages_and_fixture(tmp_path, vision):
    root = copy_fixture(tmp_path)
    case = next(c for c in bench.load_cases(FIXTURE / "cases.json") if c.id == bench.GATE_CASE)
    workspace = case.model_workspace(FIXTURE)
    assert (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio) == ("Ref2VA", 8, 1, "4:3")
    assert list(bench.case_treatments(case)) == ["production-gate"]
    assert bench.treatment_section(workspace, "production-gate") is None
    assert bench.canonical_relationship(workspace, "production-gate") is None
    result = await bench.run_suite(root, profile(vision=vision), ProductionWriter, {bench.GATE_CASE})
    record = result["cases"][0]
    arm = record["variants"]["production-gate"]
    assert all(c["passed"] for c in record["checks"]), record["checks"]
    assert arm["renderEligible"] and arm["treatmentCompliant"] and arm["matchedRenderEligible"]
    assert record["aspectRatio"] == "4:3" and record["experimentRevision"] == 5
    assert record["renderComparisonStatus"] == "pending"
    assert arm["referenceRoles"] == {"Picture 1": "environment"}
    assert arm["treatmentSection"] is None
    assert len(arm["visionInputs"]) == int(vision)
    report = bench.write_run(root, result)
    with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
        assert archive.read("prompt.txt").decode() == OUTPUT
        assert archive.read("references/picture-1.png") == (FIXTURE / bench.FIXTURE_PATH).read_bytes()
    assert bench.GATE_PLAN in report.read_text(encoding="utf-8")


@pytest.mark.parametrize("before,after", [
    ("partially_preserved", "fully_preserved"),
    ("<Subject 1> is the bedroom from <Picture 1>.", "<Subject 1> is a room."),
    ("summary:", "<Picture 1> is a composition anchor.\nsummary:"),
    ("Preserve its design", "Bookshelf on the left. Preserve its design"),
    ("Preserve its design", "The physical layout remains fixed. Preserve its design"),
    ("at mattress height", "at ceiling height"),
    ("In <Subject 1>,", "In the room,"),
])
def test_gate_detects_specific_semantic_regressions(before, after):
    case = next(c for c in bench.load_cases(FIXTURE / "cases.json") if c.id == bench.GATE_CASE)
    workspace = case.model_workspace(FIXTURE)
    assert bench.treatment_compliance(workspace, "production-gate", OUTPUT)["compliant"]
    assert not bench.treatment_compliance(workspace, "production-gate", OUTPUT.replace(before, after))["compliant"]



def test_viewpoint_freedom_can_be_conveyed_across_retention_and_shot():
    case = next(c for c in bench.load_cases(FIXTURE / "cases.json") if c.id == bench.GATE_CASE)
    workspace = case.model_workspace(FIXTURE)
    output = OUTPUT.replace(
        "Do not preserve the source camera angle, framing, perspective or composition; reconstruct the same room from the requested viewpoint.",
        "Preserve the same room design, furniture, materials and lighting.")
    assert "composition" not in bench._sections(output)["subject_definitions"].lower()
    assert "partially_preserved" in bench._sections(output)["retention_analysis"]
    assert bench.treatment_compliance(workspace, "production-gate", output)["compliant"]


def test_viewpoint_freedom_requires_retained_identity_and_novel_view_request():
    case = next(c for c in bench.load_cases(FIXTURE / "cases.json") if c.id == bench.GATE_CASE)
    workspace = case.model_workspace(FIXTURE)
    without_identity = OUTPUT.replace(
        "preserve stable room and furniture identity, colors, materials, lighting and style;",
        "discard room identity, furniture, colors, materials, lighting and style;")
    checks = bench.treatment_compliance(workspace, "production-gate", without_identity)["checks"]
    assert not next(item["passed"] for item in checks if item["requirement"] == "viewpoint_reconstruction_freedom")

    # Same retained room and camera details, but no novel-viewpoint instruction
    # and no emitted statement that the viewpoint changed.
    no_change = OUTPUT.replace(
        "Do not preserve the source camera angle, framing, perspective or composition; reconstruct the same room from the requested viewpoint.",
        "Preserve the same room design, furniture, materials and lighting.")
    no_change = no_change.replace(
        "freely change camera viewpoint and perspective without redesigning the room.",
        "keep the camera viewpoint and perspective while retaining the room.")
    no_change_workspace = workspace.model_copy(update={
        "creative_request": "Show a quiet bedroom scene with Miku lying on the bed."})
    checks = bench.treatment_compliance(no_change_workspace, "production-gate", no_change)["checks"]
    assert not next(item["passed"] for item in checks if item["requirement"] == "viewpoint_reconstruction_freedom")


def test_fenced_raw_output_separates_structure_from_semantic_compliance():
    case = next(c for c in bench.load_cases(FIXTURE / "cases.json") if c.id == bench.GATE_CASE)
    workspace = case.model_workspace(FIXTURE)
    raw = "```text\n" + OUTPUT + "\n```"
    checks = bench.treatment_compliance(workspace, "production-gate", raw)
    assert checks["compliant"]
    from miniconstruct.h3.validator import validate_workspace_prompt
    validation = validate_workspace_prompt(raw, workspace)
    assert bench.render_eligible({"findings": [{"severity": f.severity} for f in validation.findings]}) is False
