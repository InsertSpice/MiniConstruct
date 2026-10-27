from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest

from miniconstruct.benchmark import environment_consistency as bench
from miniconstruct.benchmark.media import decode_data_image
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.h3.vision_input import prompt_writer_image
from miniconstruct.llm.compatibility import build_generation_payload, DIRECT_OUTPUT_INSTRUCTION
from miniconstruct.models.api import GenerationRequest
from test_benchmark import profile

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"
FIXTURE = ROOT / bench.FIXTURE_NAME


class FakeClient:
    def __init__(self, settings):
        self.settings = settings
        self.last_payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, **kwargs):
        # Yield to exercise overlapping request-local treatment assembly.
        await asyncio.sleep(0)
        self.last_payload = build_generation_payload(self.settings, messages, stream=False,
            response_instruction=kwargs.get("response_instruction", DIRECT_OUTPUT_INSTRUCTION))[0]
        system = messages[0]["content"]
        if "## Benchmark-only concrete composition control" in system:
            return (
                "subject_definitions:\n" + bench.COMPOSITION_RELATIONSHIP.format(picture="<Picture 1>") + "\n"
                "summary:\n[keyframe completion] A restrained move preserving <Picture 1>'s source view.\n"
                "retention_analysis:\n<Picture 1> ([Shot 1] composition anchor): fully_preserved - source composition retained.\n"
                "detailed_description:\n[Shot 1] Retain <Picture 1>'s doorway-wide framing and perspective during a restrained lateral move.\n"
                "overall_soundscape:\nQuiet room tone.\nnon_diegetic_music:\nNone."
            )
        relation = ""
        if "## Benchmark-only no composition transfer treatment" in system:
            relation = " " + bench.ANTI_COMPOSITION_RELATIONSHIP.format(picture="<Picture 1>", subject="<Subject 1>")
        return (
            "subject_definitions:\n<Subject 1> is the bedroom sourced from <Picture 1>." + relation + "\n"
            "summary:\n[reference generation] The same bedroom from a closer view.\n"
            "retention_analysis:\n<Subject 1> (appears in [Shot 1]): fully_preserved - same room.\n"
            "detailed_description:\n[Shot 1] The camera slides slowly beside the desk in <Subject 1>.\n"
            "overall_soundscape:\nQuiet room tone.\nnon_diegetic_music:\nNone."
        )


def copy_fixture(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / bench.FIXTURE_NAME)
    return tmp_path


def test_suite_case_fixture_and_four_treatment_construction():
    cases = bench.load_cases(FIXTURE / "cases.json")
    assert [c.id for c in cases] == ["bedroom-new-camera", bench.SIDE_CASE, bench.GATE_CASE]
    data, info = bench.fixture_info(FIXTURE)
    assert info["sha256"] == hashlib.sha256(data).hexdigest()
    assert (info["width"], info["height"], info["format"]) == (1184, 888, "PNG")
    base = cases[0].model_workspace(FIXTURE)
    assert (base.mode.value, base.duration_seconds, base.shots, base.aspect_ratio) == ("Ref2VA", 8, 1, "16:9")
    assert len(bench.TREATMENTS) == 4
    assert not cases[0].requires_vision
    for name in bench.TREATMENTS:
        arm = bench.treatment_workspace(base, name)
        assert decode_data_image(arm.assets[0].image.data_url)[0] == data
        assert arm.creative_request == base.creative_request
        arm.assets[0].role = "environment"
        assert arm == base
    assert base.assets[0].role == "environment"


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_fake_four_arm_effective_isolation_and_ordinary_vision(vision):
    result = await bench.run_suite(ROOT, profile(vision=vision), FakeClient)
    case = result["cases"][0]
    assert all(c["passed"] for c in case["checks"]), case["checks"]
    assert list(case["variants"]) == list(bench.TREATMENTS)
    assert case["renderComparisonStatus"] == "pending"
    base = bench.load_cases(FIXTURE / "cases.json")[0].model_workspace(FIXTURE)
    derivative = decode_data_image(prompt_writer_image(base.assets[0].image.data_url)[0])[0]
    for name, arm in case["variants"].items():
        assert arm["fixture"]["sha256"] == hashlib.sha256((FIXTURE / bench.FIXTURE_PATH).read_bytes()).hexdigest()
        assert arm["visionEnabled"] == vision
        assert arm["requestedSeed"] == 1
        assert arm["seed"]["status"] == "accepted_by_provider"
        assert arm["referenceMap"][0]["h3Label"] == "Picture 1"
        assert arm["referenceRoles"] == {"Picture 1": bench.TREATMENTS[name]["role"]}
        system = arm["modelFacingMessages"][0]["content"]
        assert system.count(bench.SUBJECT_SECTION) == (name in {"environment-subject", "environment-subject-no-composition"})
        anti_composition = bench.NO_COMPOSITION_SECTION.format(picture="<Picture 1>", subject="<Subject 1>", relationship=bench.ANTI_COMPOSITION_RELATIONSHIP.format(picture="<Picture 1>", subject="<Subject 1>"))
        assert system.count(anti_composition) == (name == "environment-subject-no-composition")
        assert system.count(bench.COMPOSITION_SECTION.format(picture="<Picture 1>", relationship=bench.COMPOSITION_RELATIONSHIP.format(picture="<Picture 1>"))) == (name == "composition-anchor")
        assert system.count(bench.OUTPUT_CONTRACT) == (name != "production-control")
        assert arm["renderEligible"] is True
        assert arm["treatmentCompliant"] is True
        assert arm["matchedRenderEligible"] is True
        assert arm["treatmentSection"] == bench.treatment_section(bench.treatment_workspace(base, name), name)
        assert "missing_task_relationship" not in {f["code"] for f in arm["validation"]["findings"]}
        assert arm["validation"]["valid"]
        if vision:
            assert len(arm["visionInputs"]) == 1
            assert arm["visionInputs"][0]["sha256"] == hashlib.sha256(derivative).hexdigest()
            assert hashlib.sha256(derivative).hexdigest() != arm["fixture"]["sha256"]
        else:
            assert arm["visionInputs"] == []
    saved = json.dumps(result)
    assert "data:image" not in saved and "base64" not in saved
    assert str(FIXTURE) not in saved


@pytest.mark.asyncio
async def test_control_equals_ordinary_production_and_concurrent_runs_do_not_leak():
    fixture = bench.load_cases(FIXTURE / "cases.json")[0]
    settings = profile(vision=True).to_settings()
    ordinary = await generate_workspace_prompts(
        GenerationRequest(workspace=fixture.model_workspace(FIXTURE), llm=settings, resolvedSeeds=[1]), FakeClient)
    first, second = await asyncio.gather(
        bench.run_suite(ROOT, profile(vision=True), FakeClient),
        bench.run_suite(ROOT, profile(vision=False), FakeClient))
    arm = first["cases"][0]["variants"]["production-control"]
    # The persisted messages sanitize images; modelInput still records their exact hashes.
    assert arm["modelFacingMessages"][0] == ordinary.variations[0].model_payload["messages"][0]
    from miniconstruct.benchmark.temporal_directing import _safe_model_messages
    assert arm["modelFacingMessages"] == _safe_model_messages(ordinary.variations[0].model_payload["messages"], arm["visionInputs"])
    for result in (first, second):
        assert all(c["passed"] for c in result["cases"][0]["checks"])
    assert fixture.model_workspace(FIXTURE).assets[0].role == "environment"


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["image", "user", "layer"])
async def test_effective_isolation_rejects_nonvacuous_provider_mutations(mutation):
    class MutatingClient(FakeClient):
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages)
            if bench.SUBJECT_SECTION in messages[0]["content"]:
                effective = self.last_payload["messages"]
                if mutation == "layer":
                    effective[0]["content"] = effective[0]["content"].replace("reusable Environment", "different Environment", 1)
                elif mutation == "user":
                    effective[1]["content"][0]["text"] += "\nUnrelated changed authority."
                else:
                    part = next(p for p in effective[1]["content"] if p["type"] == "image_url")
                    part["image_url"]["url"] = bench.load_cases(FIXTURE / "cases.json")[0].model_workspace(FIXTURE).assets[0].image.data_url
            return output
    result = await bench.run_suite(ROOT, profile(vision=True), MutatingClient)
    checks = {c["name"]: c["passed"] for c in result["cases"][0]["checks"]}
    assert checks["production-control.exact_treatment_isolation"]
    assert not checks["environment-subject.exact_treatment_isolation"]
    assert not checks["environment-subject.only_overlay_differs_from_control"]


@pytest.mark.asyncio
async def test_invalid_h3_still_validated_without_repair():
    class InvalidClient(FakeClient):
        async def generate(self, messages, **kwargs):
            await super().generate(messages)
            return "subject_definitions:\n<Subject 1> is from <Picture 2>."
    result = await bench.run_suite(ROOT, profile(vision=False), InvalidClient)
    case = result["cases"][0]
    assert not next(c for c in case["checks"] if c["name"] == "all_outputs_structurally_valid")["passed"]
    for arm in case["variants"].values():
        assert not arm["validation"]["valid"]
        assert arm["renderEligible"] is False
        assert "dangling_asset" in {f["code"] for f in arm["validation"]["findings"]}


@pytest.mark.asyncio
async def test_existing_render_packs_exact_fixture_and_metadata(tmp_path):
    root = copy_fixture(tmp_path)
    result = await bench.run_suite(root, profile(vision=True), FakeClient)
    report = bench.write_run(root, result)
    for name, arm in result["cases"][0]["variants"].items():
        with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
            assert sorted(archive.namelist()) == ["manual-render-manifest.json", "prompt.txt", "references/picture-1.png"]
            assert archive.read("references/picture-1.png") == (FIXTURE / bench.FIXTURE_PATH).read_bytes()
            assert archive.read("prompt.txt").decode() == arm["exactOutput"]
            manifest = json.loads(archive.read("manual-render-manifest.json"))
            assert manifest["treatment"] == name
            assert manifest["renderEligible"] is True
            assert manifest["experimentRevision"] == 3
            assert manifest["treatmentCompliant"] is True
            assert manifest["matchedRenderEligible"] is True
            assert manifest["treatmentDescription"] == bench.TREATMENTS[name]["description"]
            assert manifest["h3Mode"] == "Ref2VA"
            assert (manifest["durationSeconds"], manifest["shotCount"], manifest["aspectRatio"]) == (8, 1, "16:9")
            assert manifest["profile"]["modelId"] == profile(vision=True).model_id
            assert manifest["settings"]["reasoningMode"] == "off"
            assert manifest["fixture"]["sha256"] == manifest["references"][0]["sha256"]
            assert manifest["references"][0]["role"] == bench.TREATMENTS[name]["role"]
            assert manifest["references"][0]["fixture"]["path"] == bench.FIXTURE_PATH
            assert manifest["referenceMap"][0]["h3Label"] == "Picture 1"
    assert "Structurally render eligible: **yes" in report.read_text(encoding="utf-8")
    assert "Matched render priority: production-control, environment-subject-no-composition, composition-anchor" in report.read_text(encoding="utf-8")
    assert bench.PROMPT_REVIEW[0] in report.read_text(encoding="utf-8")
    assert bench.RENDER_REVIEW[0] in report.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_fixture_decode_missing_and_changed_since_generation(tmp_path):
    root = copy_fixture(tmp_path)
    path = root / bench.FIXTURE_NAME / bench.FIXTURE_PATH
    result = await bench.run_suite(root, profile(vision=False), FakeClient)
    from PIL import Image
    Image.new("RGB", (3, 3), "red").save(path)
    with pytest.raises(ValueError, match="changed since generation"):
        bench.write_run(root, result)
    path.write_bytes(b"not an image")
    with pytest.raises(OSError):
        bench.load_cases(root / bench.FIXTURE_NAME / "cases.json")
    path.unlink()
    with pytest.raises(FileNotFoundError):
        bench.load_cases(root / bench.FIXTURE_NAME / "cases.json")


@pytest.mark.asyncio
async def test_unknown_case_never_calls_provider():
    def forbidden(settings):
        pytest.fail("Provider must not be constructed")
    with pytest.raises(ValueError, match="Unknown"):
        await bench.run_suite(ROOT, profile(vision=False), forbidden, {"missing"})


def test_dynamic_labels_and_final_treatment_contracts():
    base = bench.load_cases(FIXTURE / "cases.json")[0].model_workspace(FIXTURE)
    base.next_subject_number = 7
    section = bench.treatment_section(base, "environment-subject-no-composition")
    assert "<Picture 1> supplies environment identity and layout evidence for <Subject 7>" in section
    assert "retain <Subject 7> as fully_preserved" in section
    assert "<Subject 1>" not in section
    assert section.endswith(bench.OUTPUT_CONTRACT)
    assert bench.treatment_section(base, "production-control") is None
    for name in ("environment-subject", "environment-subject-no-composition", "composition-anchor"):
        assert "must literally include" in bench.treatment_section(base, name) or "literally referenced" in bench.treatment_section(base, name)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["markdown", "subject", "relationship"])
async def test_observed_live_failures_eligibility_and_exact_archival(tmp_path, failure):
    outputs = []
    class ObservedFailureClient(FakeClient):
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages, **kwargs)
            if failure == "markdown":
                output = "```text\n" + output + "\n```"
            elif failure == "subject":
                output = output.replace("beside the desk in <Subject 1>", "beside the desk in the bedroom")
            elif "## Benchmark-only concrete composition control" in messages[0]["content"]:
                output = output.replace("[keyframe completion]", "[reference generation]")
            outputs.append(output)
            return output
    result = await bench.run_suite(copy_fixture(tmp_path), profile(vision=False), ObservedFailureClient)
    report = bench.write_run(tmp_path, result)
    arms = result["cases"][0]["variants"]
    for index, (name, arm) in enumerate(arms.items()):
        findings = {f["code"]: f["severity"] for f in arm["validation"]["findings"]}
        assert arm["exactOutput"] == outputs[index]
        if failure == "markdown":
            assert findings["markdown_fence"] == "ERROR"
            assert findings["extraneous_preamble"] == "ERROR"
            assert arm["renderEligible"] is False
        elif failure == "subject" and name != "composition-anchor":
            assert findings["retention_subject_not_in_shot"] == "ERROR"
            assert arm["renderEligible"] is False
        elif failure == "relationship" and name == "composition-anchor":
            assert findings["missing_task_relationship"] == "WARNING"
            assert arm["renderEligible"] is True  # Warnings remain visible, do not block structural eligibility.
            assert arm["treatmentCompliant"] is False
            assert arm["matchedRenderEligible"] is False
        else:
            assert arm["renderEligible"] is True
        with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
            assert archive.read("prompt.txt").decode("utf-8") == outputs[index]
            manifest = json.loads(archive.read("manual-render-manifest.json"))
            assert manifest["renderEligible"] == arm["renderEligible"]
            assert manifest["treatmentCompliant"] == arm["treatmentCompliant"]
            assert manifest["matchedRenderEligible"] == arm["matchedRenderEligible"]
    review = report.read_text(encoding="utf-8")
    if failure in {"markdown", "subject"}:
        assert "Structurally render eligible: **no" in review
    else:
        assert "WARNING `missing_task_relationship`" in review


@pytest.mark.asyncio
@pytest.mark.parametrize("arm_name,defect", [
    ("environment-subject-no-composition", "missing"),
    ("environment-subject-no-composition", "altered"),
    ("environment-subject-no-composition", "duplicated"),
    ("environment-subject-no-composition", "wrong_section"),
    ("environment-subject-no-composition", "partial_retention"),
    ("composition-anchor", "missing"),
    ("composition-anchor", "altered"),
    ("composition-anchor", "duplicated"),
    ("composition-anchor", "task"),
    ("composition-anchor", "shot"),
    ("composition-anchor", "retention"),
])
async def test_incomplete_contracts_are_structural_but_not_matched_candidates(tmp_path, arm_name, defect):
    base = bench.load_cases(FIXTURE / "cases.json")[0].model_workspace(FIXTURE)
    sentence = bench.canonical_relationship(base, arm_name)
    outputs = []
    header = "## Benchmark-only no composition transfer treatment" if arm_name.endswith("no-composition") else "## Benchmark-only concrete composition control"
    class IncompleteContractClient(FakeClient):
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages, **kwargs)
            if header in messages[0]["content"]:
                if defect == "missing":
                    output = output.replace(sentence, "")
                elif defect == "altered":
                    output = output.replace(sentence, sentence.replace("composition", "view", 1))
                elif defect == "duplicated":
                    output = output.replace(sentence, sentence + " " + sentence)
                elif defect == "wrong_section":
                    output = output.replace(sentence, "").replace("summary:\n", "summary:\n" + sentence + "\n")
                elif defect == "partial_retention":
                    output = output.replace("fully_preserved - same room", "partially_preserved - only the camera changes")
                elif defect == "task":
                    output = output.replace("[keyframe completion]", "[reference generation]")
                elif defect == "shot":
                    output = output.replace("[Shot 1] Retain <Picture 1>'s", "[Shot 1] Retain the source's")
                elif defect == "retention":
                    output = output.replace("([Shot 1] composition anchor)", "(general guidance)")
            outputs.append(output)
            return output
    result = await bench.run_suite(copy_fixture(tmp_path), profile(vision=False), IncompleteContractClient)
    arm = result["cases"][0]["variants"][arm_name]
    assert arm["renderEligible"] is True
    assert arm["treatmentCompliant"] is False
    assert arm["matchedRenderEligible"] is False
    assert any(not item["passed"] for item in arm["treatmentComplianceChecks"])
    codes = {f["code"]: f["severity"] for f in arm["validation"]["findings"]}
    if defect == "task":
        assert codes["missing_task_relationship"] == "WARNING"
    for name, other in result["cases"][0]["variants"].items():
        if name != arm_name:
            assert other["treatmentCompliant"] is True
            assert other["matchedRenderEligible"] is True
    report = bench.write_run(tmp_path, result)
    with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
        assert archive.read("prompt.txt").decode() == arm["exactOutput"] == outputs[list(bench.TREATMENTS).index(arm_name)]
        manifest = json.loads(archive.read("manual-render-manifest.json"))
        assert manifest["renderEligible"] is True
        assert manifest["treatmentCompliant"] is False
        assert manifest["matchedRenderEligible"] is False
        assert manifest["canonicalRelationship"] == sentence
    review = report.read_text(encoding="utf-8")
    assert "Treatment compliant: **no**" in review
    assert "Matched render candidate: **no**" in review


@pytest.mark.asyncio
async def test_fenced_a_keeps_no_contract_and_is_not_a_matched_candidate():
    class FencedControlClient(FakeClient):
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages, **kwargs)
            if bench.OUTPUT_CONTRACT not in messages[0]["content"]:
                return "```text\n" + output + "\n```"
            return output
    result = await bench.run_suite(ROOT, profile(vision=False), FencedControlClient)
    arm = result["cases"][0]["variants"]["production-control"]
    assert arm["renderEligible"] is False
    assert arm["treatmentCompliant"] is True
    assert arm["treatmentComplianceChecks"] == []
    assert arm["matchedRenderEligible"] is False
    assert arm["exactOutput"].startswith("```text")
    assert "human review" in result["renderPriorityNote"]
    assert "Record that substitution" in result["renderPriorityNote"]


@pytest.mark.asyncio
async def test_b_abstraction_contract_and_dynamic_c_labels():
    base = bench.load_cases(FIXTURE / "cases.json")[0].model_workspace(FIXTURE)
    output = await FakeClient(profile(vision=False).to_settings()).generate([{"role": "system", "content": "ordinary"}])
    assert bench.treatment_compliance(base, "environment-subject", output)["compliant"]
    missing_source = output.replace("sourced from <Picture 1>", "without supplied evidence")
    assert not bench.treatment_compliance(base, "environment-subject", missing_source)["compliant"]
    missing_shot = output.replace("beside the desk in <Subject 1>", "beside the desk")
    assert not bench.treatment_compliance(base, "environment-subject", missing_shot)["compliant"]
    base.next_subject_number = 7
    sentence = bench.canonical_relationship(base, "environment-subject-no-composition")
    changed = output.replace("<Subject 1>", "<Subject 7>").replace("summary:", sentence + "\nsummary:")
    assert bench.treatment_compliance(base, "environment-subject-no-composition", changed)["compliant"]
    assert "<Subject 7>" in sentence and "<Subject 1>" not in sentence
    assert "Include this exact" in bench.treatment_section(base, "environment-subject-no-composition") or "include this exact" in bench.treatment_section(base, "environment-subject-no-composition")


def side_workspace(name):
    case = bench.load_cases(FIXTURE / "cases.json")[1]
    return bench.treatment_case(case, name).model_workspace(FIXTURE)


def side_output(name):
    workspace = side_workspace(name)
    return (
        "subject_definitions:\n" + bench.canonical_relationship(workspace, name) + "\n"
        "summary:\n[reference generation] A lateral bedroom view.\n"
        "retention_analysis:\n<Subject 1> (appears in [Shot 1]): " + bench.SIDE_TREATMENTS[name]["retention"] + " - same room; new camera allowed.\n"
        "detailed_description:\n[Shot 1] In <Subject 1>, Hatsune Miku lies lengthwise on the bed, head-to-toe across the horizontal frame. "
        "At mattress height beside the long edge, the camera looks nearly perpendicular across the mattress "
        "in a true lateral view and slowly pushes in; do not begin from the elevated doorway.\n"
        "overall_soundscape:\nQuiet room tone.\nnon_diegetic_music:\nNone."
    )


class SideClient(FakeClient):
    async def generate(self, messages, **kwargs):
        legacy = await super().generate(messages, **kwargs)
        system = messages[0]["content"]
        if "## Benchmark-only viewpoint reconstruction treatment" not in system:
            return legacy
        for name in bench.SIDE_TREATMENTS:
            if (bench.canonical_relationship(side_workspace(name), name) + "\n" in system
                    and ": " + bench.SIDE_TREATMENTS[name]["retention"] + " - stable" in system):
                return side_output(name)
        raise AssertionError("Unrecognized treatment")


def test_side_treatments_reference_bytes_and_semantic_isolation():
    assert list(bench.SIDE_TREATMENTS) == ["minimal-partial-original", "minimal-full-original",
        "screen-space-partial-original", "minimal-partial-panorama"]
    original, oi = bench.fixture_info(FIXTURE)
    panorama, pi = bench.fixture_info(FIXTURE, bench.PANORAMA_PATH)
    assert oi["sha256"] == hashlib.sha256(original).hexdigest()
    assert pi["sha256"] == hashlib.sha256(panorama).hexdigest()
    assert original != panorama
    requests = []
    for name, treatment in bench.SIDE_TREATMENTS.items():
        workspace = side_workspace(name)
        requests.append(workspace.creative_request)
        assert not workspace.subjects and len(workspace.assets) == 1
        assert decode_data_image(workspace.assets[0].image.data_url)[0] == (panorama if name.endswith("panorama") else original)
        relationship = bench.canonical_relationship(workspace, name)
        assert bench.treatment_compliance(workspace, name, side_output(name))["compliant"]
        if not name.startswith("screen-space"):
            assert bench.neutral_definition(relationship)
        else:
            assert "bookshelves on the left" in relationship and "lamp on the right" in relationship
    assert len(set(requests)) == 1 and "Hatsune Miku" in requests[0]
    partial = bench.treatment_section(side_workspace("minimal-partial-original"), "minimal-partial-original")
    full = bench.treatment_section(side_workspace("minimal-full-original"), "minimal-full-original")
    assert partial.replace("partially_preserved", "fully_preserved") == full
    a = bench.canonical_relationship(side_workspace("minimal-partial-original"), "minimal-partial-original")
    d = bench.canonical_relationship(side_workspace("minimal-partial-panorama"), "minimal-partial-panorama")
    assert d == a + bench.PANORAMA_RELATIONSHIP


@pytest.mark.parametrize("name,defect", [
    (name, defect) for name in bench.SIDE_TREATMENTS
    for defect in ["retention", "extra_subject", "fake_picture", "duplicate", "misplaced", "shot", "fence"]
] + [(name, "screen") for name in bench.SIDE_TREATMENTS if not name.startswith("screen-space")])
def test_side_negative_contracts(name, defect):
    workspace = side_workspace(name)
    output = side_output(name)
    sentence = bench.canonical_relationship(workspace, name)
    if defect == "retention":
        marker = bench.SIDE_TREATMENTS[name]["retention"]
        output = output.replace(marker, "fully_preserved" if marker == "partially_preserved" else "partially_preserved")
    elif defect == "extra_subject":
        output = output.replace("summary:", "<Subject 2> is Hatsune Miku.\nsummary:")
    elif defect == "fake_picture":
        output = output.replace("Hatsune Miku lies", "Hatsune Miku from <Picture 2> lies")
    elif defect == "duplicate":
        output = output.replace(sentence, sentence + " " + sentence)
    elif defect == "misplaced":
        output = output.replace(sentence, "<Subject 1> is the bedroom.").replace("summary:\n", "summary:\n" + sentence)
    elif defect == "shot":
        output = output.replace("In <Subject 1>", "In the bedroom")
    elif defect == "fence":
        output = "```\n" + output + "\n```"
    else:
        output = output.replace(sentence, sentence + " Bookshelves on the left and desk on the right.")
    assert not bench.treatment_compliance(workspace, name, output)["compliant"]


@pytest.mark.parametrize("name", list(bench.SIDE_TREATMENTS))
def test_wrong_reference_representation_rejected(name):
    workspace = side_workspace(name)
    wrong = "minimal-partial-original" if name.endswith("panorama") else "minimal-partial-panorama"
    workspace.assets = side_workspace(wrong).assets
    assert not bench.treatment_compliance(workspace, name, side_output(name))["compliant"]
    assert not bench.isolation_passes(side_workspace(name), workspace, name, [], [],
                                     bench.fixture_info(FIXTURE, bench.SIDE_TREATMENTS[name]["path"])[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("vision", [False, True])
async def test_side_run_exact_packs_report_and_request_locality(tmp_path, vision):
    root = copy_fixture(tmp_path)
    side, legacy = await asyncio.gather(
        bench.run_suite(root, profile(vision=vision), SideClient, {bench.SIDE_CASE}),
        bench.run_suite(root, profile(vision=False), FakeClient, {"bedroom-new-camera"}))
    assert side["experimentRevision"] == 4 and legacy["experimentRevision"] == 3
    assert list(legacy["cases"][0]["variants"]) == list(bench.TREATMENTS)
    case = side["cases"][0]
    assert all(c["passed"] for c in case["checks"]), case["checks"]
    assert len(case["fixtures"]) == 2
    report = bench.write_run(root, side)
    for name, arm in case["variants"].items():
        assert arm["renderEligible"] and arm["treatmentCompliant"] and arm["matchedRenderEligible"]
        assert len(arm["visionInputs"]) == int(vision)
        assert bench.SUBJECT_SECTION not in arm["modelFacingMessages"][0]["content"]
        with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
            assert archive.read("prompt.txt").decode() == side_output(name)
            assert archive.read("references/picture-1.png") == (FIXTURE / bench.SIDE_TREATMENTS[name]["path"]).read_bytes()
            manifest = json.loads(archive.read("manual-render-manifest.json"))
            assert len(manifest["references"]) == 1
            assert manifest["fixture"]["sha256"] == manifest["references"][0]["sha256"]
            assert manifest["experimentRevision"] == 4
            assert manifest["referenceRepresentation"] == ("panorama" if name.endswith("panorama") else "perspective")
    text = report.read_text(encoding="utf-8")
    assert all(q in text for q in bench.SIDE_PROMPT_REVIEW + bench.SIDE_RENDER_REVIEW)
    assert bench.SIDE_RENDER_PLAN in text
    assert "Treatment compliant: **yes**" in text
    assert "data:image" not in json.dumps(side)


@pytest.mark.asyncio
async def test_side_structural_validity_separate_from_contracts_and_raw_output():
    class WrongRetention(SideClient):
        async def generate(self, messages, **kwargs):
            return (await super().generate(messages, **kwargs)).replace("partially_preserved", "fully_preserved")
    result = await bench.run_suite(ROOT, profile(vision=False), WrongRetention, {bench.SIDE_CASE})
    for name, arm in result["cases"][0]["variants"].items():
        assert arm["renderEligible"]
        assert arm["treatmentCompliant"] == (name == "minimal-full-original")
        assert arm["matchedRenderEligible"] == (name == "minimal-full-original")
        assert "partially_preserved" not in arm["exactOutput"]


def test_legacy_fixture_registry_still_loads_without_panorama(tmp_path):
    root = copy_fixture(tmp_path) / bench.FIXTURE_NAME
    registry = root / "cases.json"
    raw = json.loads(registry.read_text())
    raw["cases"] = raw["cases"][:1]
    registry.write_text(json.dumps(raw))
    (root / bench.PANORAMA_PATH).unlink()
    assert [c.id for c in bench.load_cases(registry)] == ["bedroom-new-camera"]


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["image", "user", "layer"])
async def test_side_effective_payload_leakage_rejected(defect):
    class Mutated(SideClient):
        async def generate(self, messages, **kwargs):
            output = await super().generate(messages, **kwargs)
            effective = self.last_payload["messages"]
            if defect == "layer":
                effective[0]["content"] += " Unrelated treatment."
            elif defect == "user":
                effective[1]["content"][0]["text"] += " Unrelated instruction."
            else:
                part = next(p for p in effective[1]["content"] if p["type"] == "image_url")
                part["image_url"]["url"] = side_workspace("minimal-partial-panorama").assets[0].image.data_url
            return output
    result = await bench.run_suite(ROOT, profile(vision=True), Mutated, {bench.SIDE_CASE})
    assert all(not c["passed"] for c in result["cases"][0]["checks"] if c["name"].endswith("exact_treatment_isolation"))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [bench.FIXTURE_PATH, bench.PANORAMA_PATH])
async def test_side_missing_corrupt_and_changed_asset(tmp_path, path):
    root = copy_fixture(tmp_path)
    asset = root / bench.FIXTURE_NAME / path
    result = await bench.run_suite(root, profile(vision=False), SideClient, {bench.SIDE_CASE})
    from PIL import Image
    Image.new("RGB", (4, 4), "blue").save(asset)
    with pytest.raises(ValueError, match="changed since generation"):
        bench.write_run(root, result)
    asset.write_bytes(b"corrupt PNG")
    with pytest.raises(OSError):
        bench.load_cases(root / bench.FIXTURE_NAME / "cases.json")
    asset.unlink()
    with pytest.raises(FileNotFoundError):
        bench.load_cases(root / bench.FIXTURE_NAME / "cases.json")


def test_invented_miku_asset_rejected():
    workspace = side_workspace("minimal-partial-original")
    extra = workspace.assets[0].model_copy(deep=True)
    extra.id = "fake-miku-reference"
    extra.filename = "miku.png"
    workspace.assets.append(extra)
    assert not bench.treatment_compliance(workspace, "minimal-partial-original",
                                          side_output("minimal-partial-original"))["compliant"]


@pytest.mark.asyncio
async def test_combined_case_selection_keeps_case_revision_metadata():
    result = await bench.run_suite(ROOT, profile(vision=False), SideClient,
                                   {"bedroom-new-camera", bench.SIDE_CASE})
    assert [c["experimentRevision"] for c in result["cases"]] == [3, 4]
    assert list(result["caseTreatments"]["bedroom-new-camera"]) == list(bench.TREATMENTS)
    assert list(result["caseTreatments"][bench.SIDE_CASE]) == list(bench.SIDE_TREATMENTS)
    for case in result["cases"]:
        assert all(arm["renderMetadata"]["experimentRevision"] == case["experimentRevision"]
                   for arm in case["variants"].values())
