"""Offline writer-policy assembly and structural validation are separate contracts."""
import httpx
import pytest

from miniconstruct.generation import resolve_narrative_request
from miniconstruct.h3.builder import assemble_prompt, instruction_layers
from miniconstruct.h3.builder import build_reference_manifest
from miniconstruct.h3.repair import assemble_repair_prompt, repair_identity_provenance
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.h3.subject_identity import H3_SUBJECT_IDENTITY_POLICY
from miniconstruct.h3.validator import (
    canonicalize_bare_reference_labels, normalize_comparison_scale_references,
    validate_workspace_prompt,
)
from miniconstruct.models.api import GenerationRequest, RepairRequest
from miniconstruct.models.sequence_subject import SequenceSubject
from miniconstruct.models.story_operations import StoryInitialRangeRequest
from miniconstruct.project_store import ProjectStore
from miniconstruct.story_operations import build_initial_range
from test_revision import revision_request
from test_story_initial_range import LLM, OfflineInitialRangeClient


DIALOGUE = "Alice: Bob, look at me.\nBob: Alice, I see it."
PROFILE = "A patient archivist who notices small details."


@pytest.fixture(autouse=True)
def forbid_live_transport(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("This regression must never call a live provider")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


@pytest.fixture
def named_project(tmp_path, workspace_factory, image_asset_factory):
    cast = [SequenceSubject(id="opaque-alice", number=1, narrativeName="Alice"),
            SequenceSubject(id="opaque-bob", number=2, narrativeName="Bob", narrativeAliases=["Robert"])]
    assets = []
    for subject in cast:
        asset = image_asset_factory(f"identity-{subject.number}", order=subject.number)
        asset.subject_identity.subject_id = subject.id
        assets.append(asset)
    workspace = workspace_factory(mode="Ref2VA", shots=2, assets=assets,
        subjects=[{"id": s.id, "number": s.number} for s in cast], nextSubjectNumber=3,
        creativeRequest="Alice approaches Bob. Bob watches Alice. The camera follows Alice.",
        dialogue=DIALOGUE)
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace, sequence_subjects=cast,
                                   story_idea="Alice and Bob search the station.")
    project = store.save_project(project.project_id, project.workspace, character_profiles=[{
        "subjectId": cast[0].id, "characterProfile": {"personality": PROFILE}}])
    return store, project


def assert_ref2va_writer_contract(assembly):
    system = assembly.messages[0]["content"]
    assert system.count(H3_SUBJECT_IDENTITY_POLICY) == 1
    assert assembly.inspector_text.count(H3_SUBJECT_IDENTITY_POLICY) == 1
    for instruction in (
        "Narrative Names are interpretation aliases",
        "exact <Subject N> token is the required canonical technical identity",
        "Names must not replace Subject tokens in shots",
        "CURRENT Narrative Name may appear once",
        "existing valid subject_definitions entry",
        "if the mode has no suitable Subject-definition location, omit the name",
        "Exact dialogue and lyrics remain verbatim",
        "Names spoken or sung inside that literal payload are allowed",
        "revision must not regress a Subject token to a name",
        "never introduce new Subjects",
    ):
        assert instruction in system
    assert "use it in newly authored prose" not in system
    assert "Character Profiles remain Story Planner context" in system


@pytest.mark.parametrize("operation", ["generate", "revision", "repair"])
def test_shared_policy_in_production_operation_assembly(named_project, operation):
    store, project = named_project
    original = project.workspace.model_dump(mode="json", by_alias=True)
    if operation == "revision":
        request = revision_request(project.workspace, instruction="Follow Alice and Bob in Shot 2.")
        request.filesystem_project_id = project.project_id
        assembly = assemble_revision_prompt(resolve_narrative_request(request, store))
    else:
        request = GenerationRequest(workspace=project.workspace, llm=LLM,
                                    filesystemProjectId=project.project_id)
        if operation == "repair":
            request = RepairRequest(**request.model_dump(by_alias=True), prompt=two_subject_prompt(named_shot=True))
        resolved = resolve_narrative_request(request, store)
        assembly = (assemble_repair_prompt(resolved, validate_workspace_prompt(
            resolved.prompt, resolved.prompt_workspace).findings) if operation == "repair" else
            assemble_prompt(resolved.prompt_workspace, False, resolved.narrative_subjects))
    assert_ref2va_writer_contract(assembly)
    assert '"Alice" == <Subject 1>' in assembly.inspector_text
    assert '"Bob" == <Subject 2>' in assembly.inspector_text
    assert '"Robert"' not in assembly.inspector_text
    assert PROFILE not in assembly.inspector_text
    assert project.workspace.creative_request in assembly.inspector_text
    assert DIALOGUE in assembly.inspector_text
    assert project.workspace.model_dump(mode="json", by_alias=True) == original
    assert store.load_project(project.project_id).workspace.model_dump(mode="json", by_alias=True) == original


def test_ref2va_named_subject_has_optional_definition_name_policy(workspace_factory, image_asset_factory):
    asset = image_asset_factory()
    asset.subject_identity.subject_id = "opaque"
    workspace = workspace_factory(mode="Ref2VA", subjects=[{"id": "opaque", "number": 1}],
                                  assets=[asset], creativeRequest="Alice walks. Alice turns. Follow Alice.")
    subject = SequenceSubject(id="opaque", number=1, narrativeName="Alice")
    assembly = assemble_prompt(workspace, False, [subject])
    assert_ref2va_writer_contract(assembly)
    assert '"Alice" == <Subject 1>' in assembly.inspector_text
    assert workspace.creative_request in assembly.inspector_text


@pytest.mark.parametrize("mode", ["T2VA", "I2VA"])
def test_base_modes_keep_name_context_without_ref2va_output_vocabulary(
    workspace_factory, image_asset_factory, mode,
):
    assets = [image_asset_factory("first", "first_frame_anchor")] if mode == "I2VA" else []
    workspace = workspace_factory(mode=mode, subjects=[{"id": "opaque", "number": 1}],
                                  assets=assets, creativeRequest="Alice walks. Alice turns. Follow Alice.")
    subject = SequenceSubject(id="opaque", number=1, narrativeName="Alice")
    assembly = assemble_prompt(workspace, False, [subject])
    system = assembly.messages[0]["content"]
    assert "H3 Subject identity policy" not in system
    assert "required canonical technical identity" not in system
    assert "Names must not replace Subject tokens in shots" not in system
    assert "subject_definitions" not in system
    assert "retention_analysis" not in system
    assert '"Alice" ==' not in system
    assert '"Alice"' in system
    assert all("label" not in item for item in build_reference_manifest(workspace, [subject])["subjects"])
    assert workspace.creative_request in assembly.inspector_text
    assert "Current Narrative Names below orient interpretation" in system
    assert "follow the active base Video guide's ordinary prose" in system
    unnamed = assemble_prompt(workspace, False)
    assert '"Alice"' not in unnamed.messages[0]["content"]


def test_revision_and_repair_share_base_mode_scope(workspace_factory):
    workspace = workspace_factory(mode="T2VA", subjects=[{"id": "alice", "number": 1}],
                                  creativeRequest="Alice waits.")
    subject = SequenceSubject(id="alice", number=1, narrativeName="Alice")
    revision = revision_request(workspace, instruction="Keep Alice in frame.")
    revision._narrative_subjects = [subject]
    repair = RepairRequest(workspace=workspace, llm=LLM, prompt="[Shot 1] Alice waits.")
    repair._narrative_subjects = [subject]
    for assembly in (assemble_revision_prompt(revision), assemble_repair_prompt(repair, [])):
        system = assembly.messages[0]["content"]
        assert "H3 Subject identity policy" not in system
        assert "required canonical technical identity" not in system
        assert "subject_definitions" not in system and "retention_analysis" not in system
        assert '"Alice"' in system
    assert "<Subject 1>" not in H3_SUBJECT_IDENTITY_POLICY


async def test_story_planner_profiles_and_human_storage_then_h3_compilation(named_project):
    store, project = named_project
    def story_response(result, context):
        cast = {s["narrativeName"]: s for s in context["sequenceNarrativeCast"]}
        assert cast["Alice"]["characterProfile"]["personality"] == PROFILE
        assert "narrativeAliases" not in cast["Bob"]
        result["sequenceContext"] = "Alice and Bob search together."
        for item in result["items"]:
            item.update(scenePlan="Alice turns toward Bob. Bob watches Alice.", dialogue=DIALOGUE,
                incomingState="Alice stands beside Bob.", intendedOutgoingState="Bob follows Alice.",
                cameraIntent="Follow Alice toward Bob.", shotCount=2,
                participatingSubjectIds=["opaque-alice", "opaque-bob"], memoryPatchAfter={})
    writer = OfflineInitialRangeClient(story_response)
    draft = await build_initial_range(store, project.project_id,
        StoryInitialRangeRequest(requestedTotalCount=2, llm=LLM), writer)
    assert H3_SUBJECT_IDENTITY_POLICY not in writer.calls[0][0]["content"]
    assert "Use current Narrative Names naturally" in writer.calls[0][0]["content"]
    store.apply_story_initial_range(project.project_id, draft)
    saved = store.read_story_snapshot(project.project_id).generations[1]
    manifest_path = store.root / f"project-{project.project_id}" / "project.json"
    original = manifest_path.read_bytes()
    assert saved.planning.scene_plan == "Alice turns toward Bob. Bob watches Alice."
    assert draft.items[1].participating_subject_ids == ["opaque-alice", "opaque-bob"]
    assert [s.id for s in saved.workspace.subjects] == draft.items[1].participating_subject_ids
    resolved = resolve_narrative_request(GenerationRequest(workspace=saved.workspace, llm=LLM,
        filesystemProjectId=project.project_id, generationId=saved.generation_id, authoringPath="story"), store)
    assembly = assemble_prompt(resolved.prompt_workspace, False, resolved.narrative_subjects,
                               resolved.continuity_context, resolved.story_context)
    assert_ref2va_writer_contract(assembly)
    assert '"Alice" == <Subject 1>' in assembly.inspector_text
    assert '"Bob" == <Subject 2>' in assembly.inspector_text
    assert saved.planning.scene_plan in assembly.inspector_text
    assert resolved.story_context["characterProfiles"][0]["characterProfile"]["personality"] == PROFILE
    assert "characterProfile" not in assembly.messages[0]["content"]
    assert DIALOGUE in assembly.inspector_text
    assert manifest_path.read_bytes() == original


def two_subject_prompt(*, named_shot=False):
    shot2 = "Alice (S1) says, <d>[English] Bob, look at me.</d> Bob (S2) replies, <d>[English] Alice, I see it.</d>" if named_shot else (
        "<Subject 1> (S1) says, <d>[English] Bob, look at me.</d> "
        "<Subject 2> (S2) replies, <d>[English] Alice, I see it.</d>")
    return f"""subject_definitions:
<Subject 1> is Alice, the woman in <Picture 1>.
<Subject 2> is Bob, the man in <Picture 2>.

summary:
[reference generation] <Subject 1> turns toward <Subject 2>.

retention_analysis:
<Subject 1> (appears in [Shot 1], [Shot 2]): fully_preserved - identity is retained.
<Subject 2> (appears in [Shot 2]): fully_preserved - identity is retained.

detailed_description:
[Shot 1] Live action. <Subject 1> enters the room.
[Shot 2] At 00:04.000, the camera cuts. {shot2}

overall_soundscape:
Quiet room ambience.

non_diegetic_music:
N/A"""


def test_two_subject_retention_reproduces_screenshot_and_accepts_technical_tokens(named_project):
    _, project = named_project
    valid = validate_workspace_prompt(two_subject_prompt(), project.workspace)
    assert valid.valid, valid.findings
    invalid = validate_workspace_prompt(two_subject_prompt(named_shot=True), project.workspace)
    errors = [f.message for f in invalid.findings if f.code == "retention_subject_not_in_shot"]
    assert len(errors) == 2
    assert all(f"Subject {n} in Shot 2" in errors[n - 1] for n in (1, 2))


def test_deterministic_repairs_never_replace_names_in_prose_dialogue_or_lyrics(named_project):
    _, project = named_project
    prompt = two_subject_prompt(named_shot=True)
    findings = validate_workspace_prompt(prompt, project.workspace).findings
    result = normalize_comparison_scale_references(canonicalize_bare_reference_labels(prompt), project.workspace)
    result = repair_identity_provenance(result, project.workspace, findings)
    assert result == prompt  # Ambiguous name-based prose is left for narrow model repair.
    lyrics = prompt.replace("Bob, look at me.", "Alice, Bob, May we meet again.")
    assert canonicalize_bare_reference_labels(lyrics) == lyrics


def test_definition_name_does_not_break_canonical_picture_repair(named_project):
    _, project = named_project
    prompt = two_subject_prompt().replace("the woman in <Picture 1>", "the woman")
    findings = validate_workspace_prompt(prompt, project.workspace).findings
    fixed = repair_identity_provenance(prompt, project.workspace, findings)
    assert "<Subject 1> is Alice, the woman, shown in <Picture 1>." in fixed
    assert "<d>[English] Bob, look at me.</d>" in fixed
    assert "<d>[English] Alice, I see it.</d>" in fixed
    assert validate_workspace_prompt(fixed, project.workspace).valid


def test_common_word_names_and_aliases_are_never_substituted(workspace_factory):
    workspace = workspace_factory(subjects=[{"id": "may", "number": 1}],
        creativeRequest="May I visit? It may rain.", dialogue="May: Will Alice sing in May?")
    subject = SequenceSubject(id="may", number=1, narrativeName="May", narrativeAliases=["Will"])
    original = workspace.model_dump()
    assembly = assemble_prompt(workspace, False, [subject])
    system = assembly.messages[0]["content"]
    assert "H3 Subject identity policy" not in system
    assert "Narrative Names below orient interpretation" in system
    assert workspace.creative_request in assembly.inspector_text
    assert workspace.dialogue in assembly.inspector_text
    assert '"narrativeName": "May"' in system and "canonicalH3Label" not in system
    assert '"Will"' not in assembly.messages[0]["content"]
    assert workspace.model_dump() == original
    assert "H3 Subject identity policy" not in dict(instruction_layers(workspace, False, [subject]))
