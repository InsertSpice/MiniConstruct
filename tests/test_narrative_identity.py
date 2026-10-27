"""Offline Sequence narrative identity, authoring boundaries and historical provenance."""
from copy import deepcopy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from miniconstruct.app import app
from miniconstruct.api import routes
from miniconstruct.execution import InvalidBindings
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.execution_state import SelectionWrite
from miniconstruct.generation import generate_workspace_prompts, resolve_narrative_request
from miniconstruct.h3.builder import assemble_prompt, build_reference_manifest
from miniconstruct.h3.repair import assemble_repair_prompt
from miniconstruct.h3.revision import assemble_revision_prompt
from miniconstruct.models.api import GenerationRequest, RepairRequest
from miniconstruct.models.h3_continuity import H3ContinuityContext
from miniconstruct.models.sequence_subject import SequenceSubject
from miniconstruct.models.story_planning import StoryBuildRequest, StoryRevisionRequest, StoryReferenceRecommendation
from miniconstruct.models.workspace import SubjectRecord, validate_authoring_workspace
from miniconstruct.project_store import ProjectStore, ProjectStoreError, StaleProjectSnapshotError
from miniconstruct.story_planning import build_story_plan, initial_story_draft, revise_story_plan
from miniconstruct.workflow_preparation import prepare_workflow
from test_execution_provenance import FakeWriter
from test_revision import revision_request, decode_event
from test_validator import VALID_BASE
from test_workflow_preparation import profile


LLM = {"baseUrl": "http://never-call.test/v1", "model": "offline"}
CAST = [{"id": "opaque/id α", "number": 1}, {"id": "other ?/#", "number": 2},
        {"id": "dormant", "number": 3}]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Live model and ComfyUI transport forbidden")
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)


@pytest.fixture
def named(tmp_path, monkeypatch, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path / "projects")
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    assets = []
    for subject in CAST[:2]:
        asset = image_asset_factory(f"asset-{subject['number']}")
        asset.subject_identity.subject_id = subject["id"]
        assets.append(asset)
    project = store.create_project(workspace_factory(subjects=CAST, assets=assets,
        creativeRequest="Subject 1 approaches Subject 2."))
    # Commit structural pruning first; the third member is canonically dormant.
    store.save_project(project.project_id, project.workspace)
    for subject, name in zip(CAST, ["Alice", "Robert", "Carol"]):
        project = store.set_subject_narrative_name(project.project_id, subject["id"], name)
    return store, project


def manifest_path(store, project):
    return store.root / f"project-{project.project_id}" / "project.json"


def req(project):
    return GenerationRequest(workspace=project.workspace, llm=LLM, filesystemProjectId=project.project_id)


def cast(project):
    return {subject.id: subject for subject in project.sequence.subjects}


def test_schema6_migration_is_read_only_and_does_not_rewrite_prose(named):
    store, project = named
    path = manifest_path(store, project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sequence"].pop("storyMemory", None)
    for legacy_generation in raw["sequence"]["generations"]:
        legacy_generation.pop("storyMemoryAfter", None)
        legacy_generation.pop("storyMemoryStatus", None)
    raw["schemaVersion"] = 6
    raw["sequence"]["subjects"] = CAST
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    loaded = store.load_project(project.project_id)
    assert path.read_bytes() == before
    assert all(s.narrative_name is None and s.narrative_aliases == [] for s in loaded.sequence.subjects)
    saved = store.save_project(project.project_id, loaded.workspace)
    upgraded = json.loads(path.read_text(encoding="utf-8"))
    assert upgraded["schemaVersion"] == 17
    assert upgraded["sequence"]["subjects"] == [{
        **s, "narrativeName": None, "narrativeAliases": [],
        "characterProfile": {"roleBackground": "", "personality": "", "appearance": "",
                             "speechMannerisms": "", "notes": ""},
    } for s in CAST]
    assert saved.workspace.creative_request == raw["sequence"]["generations"][0]["workspace"]["creativeRequest"]
    assert saved.workspace.schema_version == 1


def test_rename_alias_promotion_persistence_and_atomic_uniqueness(named):
    store, project = named
    pid, sid = project.project_id, CAST[1]["id"]
    renamed = store.set_subject_narrative_name(pid, sid, "  Bob  ")
    assert cast(renamed)[sid].narrative_name == "Bob"
    assert cast(renamed)[sid].narrative_aliases == ["Robert"]
    for conflict in ["bob", "ROBERT", "Ｂｏｂ"]:
        before = manifest_path(store, project).read_bytes()
        with pytest.raises(ProjectStoreError, match="reserved"):
            store.set_subject_narrative_name(pid, CAST[0]["id"], conflict)
        assert manifest_path(store, project).read_bytes() == before
    promoted = store.set_subject_narrative_name(pid, sid, "Robert")
    assert cast(promoted)[sid].narrative_aliases == ["Bob"]
    store.set_subject_narrative_name(pid, sid, "robert")
    reloaded = ProjectStore(store.root).load_project(pid)
    assert cast(reloaded)[sid].narrative_name == "robert"
    assert cast(reloaded)[sid].narrative_aliases == ["Bob"]
    cycled = store.set_subject_narrative_name(pid, sid, "Bob")
    cycled = store.set_subject_narrative_name(pid, sid, "NewName")
    subject = cast(cycled)[sid]
    keys = [value.casefold() for value in subject.narrative_aliases]
    assert subject.narrative_name == "NewName"
    assert subject.narrative_name.casefold() not in keys
    assert len(keys) == len(set(keys))
    assert reloaded.workspace == project.workspace


@pytest.mark.parametrize("name", ["", "  ", "A\nB", "A\tB", "A\x00B", "A\u2028B", "A\u200bB", "x" * 81, "Subject 4", "<subject 2>"])
def test_invalid_names_rejected(name):
    with pytest.raises(ValueError):
        SequenceSubject(id="opaque", number=1, narrativeName=name)


@pytest.mark.parametrize("name", ["Alice", "John Smith", "Captain Reynolds", "Élodie", "李 明", "O'Connor"])
def test_human_unicode_names_supported(name):
    assert SequenceSubject(id="opaque", number=41, narrativeName=f" {name} ").narrative_name == name


def test_workspace_cannot_own_metadata_and_save_dematerialization_apply_fork_preserve_it(named):
    store, project = named
    pid = project.project_id
    project = store.set_subject_narrative_name(pid, CAST[1]["id"], "Bob")
    canonical = project.sequence.subjects
    with pytest.raises(ValidationError):
        SubjectRecord.model_validate({**CAST[0], "narrativeName": "Spoof"})
    with pytest.raises(ValidationError):
        validate_authoring_workspace({**project.workspace.model_dump(by_alias=True), "subjects": [
            {**CAST[0], "narrativeAliases": ["Spoof"]}]})
    saved = store.save_project(pid, project.workspace)
    assert saved.sequence.subjects == canonical
    snapshot = store.read_story_snapshot(pid)
    draft = initial_story_draft(snapshot, 2)
    draft.items[1].reference_recommendations = [StoryReferenceRecommendation(sourceGenerationId=project.active_generation_id,
        assetId="asset-2", recommendation="Same identity")]
    # Revalidate the test proposal just like an HTTP Apply request.
    draft = type(draft).model_validate(draft.model_dump())
    store.apply_story_plan(pid, draft)
    target = store.read_story_snapshot(pid).generations[1]
    assert [s.model_dump() for s in target.workspace.subjects] == CAST[1:2]
    assert store.load_project(pid).sequence.subjects == canonical
    data = saved.workspace.model_dump(by_alias=True)
    data["assets"] = []
    saved = store.save_project(pid, validate_authoring_workspace(data))
    assert saved.workspace.subjects == [] and saved.sequence.subjects == canonical
    fork = store.duplicate_project(pid, "Fork")
    assert fork.sequence.subjects == canonical and fork.workspace.subjects == []
    duplicate = store.duplicate_generation(pid)
    assert duplicate.sequence.subjects == canonical


def test_deletion_frees_names_but_not_numbers_or_history(named):
    store, project = named
    sid = CAST[2]["id"]
    project = store.set_subject_narrative_name(project.project_id, sid, "Fred")
    deleted = store.delete_sequence_subject(project.project_id, sid)
    assert [s.number for s in deleted.sequence.subjects] == [1, 2]
    assert deleted.sequence.next_subject_number == 4
    data = deleted.workspace.model_dump(by_alias=True)
    data["subjects"].append({"id": "new identity", "number": 4})
    created = store.save_project(project.project_id, validate_authoring_workspace(data))
    created = store.set_subject_narrative_name(project.project_id, "new identity", "Dora")
    assert cast(created)["new identity"].number == 4
    assert cast(created)["new identity"].narrative_aliases == []
    assert created.workspace.creative_request == project.workspace.creative_request
    with pytest.raises(ProjectStoreError, match=r"Robert \(Subject 2\).*Generation 1"):
        store.delete_sequence_subject(project.project_id, CAST[1]["id"])


def test_api_opaque_id_names_and_authoritative_assembly(named):
    store, project = named
    with TestClient(app) as client:
        renamed = client.patch(f"/api/projects/{project.project_id}/subjects",
            json={"subjectId": CAST[1]["id"], "narrativeName": "Bob"})
        assert renamed.status_code == 200
        assert renamed.json()["sequence"]["subjects"][1]["narrativeAliases"] == ["Robert"]
        assembled = client.post("/api/assemble", json=req(project).model_dump(by_alias=True))
        assert assembled.status_code == 200
        assert '"narrativeName": "Bob"' in assembled.json()["instructions"]
        assert '"Bob" == <Subject 2>' not in assembled.json()["instructions"]
        assert '"Robert"' not in assembled.json()["instructions"]
        assert "Carol" not in assembled.json()["instructions"]
        assert client.patch(f"/api/projects/{project.project_id}/subjects",
            json={"subjectId": CAST[1]["id"], "narrativeName": "\n"}).status_code == 422
        assert client.patch(f"/api/projects/{project.project_id}/subjects",
            json={"subjectId": "not found", "narrativeName": "Bob"}).status_code == 400
        spoof = req(project).model_dump(by_alias=True)
        spoof["narrativeSubjects"] = ["Spoof"]
        assert client.post("/api/assemble", json=spoof).status_code == 422


@pytest.mark.parametrize("vision", [False, True])
def test_ref2va_named_mapping_preserves_saved_alias_prose_and_dialogue(named, vision):
    store, project = named
    project = store.set_subject_narrative_name(project.project_id, CAST[1]["id"], "Bob")
    data = project.workspace.model_dump(by_alias=True)
    data.update(mode="Ref2VA", creativeRequest="Alice grabs Robert by the shoulders.", dialogue="Robert: Hi, Alice!")
    workspace = validate_authoring_workspace(data)
    assembly = assemble_prompt(workspace, vision, project.sequence.subjects)
    assert '"Alice" == <Subject 1>' in assembly.inspector_text
    assert '"Bob" == <Subject 2>' in assembly.inspector_text
    assert "<Subject 1> is defined by <Picture 1>" in assembly.inspector_text
    assert "<Subject 2> is defined by <Picture 2>" in assembly.inspector_text
    assert "Carol" not in assembly.inspector_text and "dormant" not in assembly.inspector_text
    assert "narrativeAliases" not in assembly.inspector_text
    assert "Alice grabs Robert by the shoulders." in assembly.inspector_text
    assert "Robert: Hi, Alice!" in assembly.inspector_text
    assert workspace.dialogue == "Robert: Hi, Alice!"
    manifest = build_reference_manifest(workspace, project.sequence.subjects)
    assert [s["identityPictures"] for s in manifest["subjects"]] == [["Picture 1"], ["Picture 2"]]
    assert [s["label"] for s in manifest["subjects"]] == ["Subject 1", "Subject 2"]


def test_alias_limit_rejects_without_discarding_old_names(named):
    store, project = named
    path = manifest_path(store, project)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["sequence"]["subjects"][0]["narrativeAliases"] = [f"Old {i}" for i in range(100)]
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ProjectStoreError):
        store.set_subject_narrative_name(project.project_id, CAST[0]["id"], "Newest")
    assert path.read_bytes() == before
    promoted = store.set_subject_narrative_name(project.project_id, CAST[0]["id"], "Old 2")
    assert len(cast(promoted)[CAST[0]["id"]].narrative_aliases) == 100
    assert "Alice" in cast(promoted)[CAST[0]["id"]].narrative_aliases


def test_unnamed_provenance_retains_v1_and_local_name_number_conflicts_fail(named):
    store, project = named
    original = project.workspace
    unnamed = [SequenceSubject(**s) for s in CAST]
    assert execution_authoring_fingerprint(original, unnamed) == execution_authoring_fingerprint(original)
    # Metadata for an absent Subject never enters the authoring fingerprint.
    assert execution_authoring_fingerprint(original, project.sequence.subjects[2:]) == execution_authoring_fingerprint(original)
    data = original.model_dump(by_alias=True)
    data["subjects"][0]["number"] = 9
    divergent = validate_authoring_workspace(data)
    with pytest.raises(ProjectStoreError, match="conflicts"):
        store.generation_narrative_subjects(project.project_id, divergent)


async def test_production_generate_stream_revision_repair_freeze_local_mapping(named, monkeypatch):
    store, project = named
    store.set_subject_narrative_name(project.project_id, CAST[1]["id"], "Bob")
    project = store.load_project(project.project_id)
    project.workspace.creative_request = "Alice confronts Robert."
    request = resolve_narrative_request(req(project), store)
    expected = request.source_fingerprint
    assert [s.id for s in request.narrative_subjects] == [s["id"] for s in CAST[:2]]
    manifest = build_reference_manifest(project.workspace, project.sequence.subjects)
    assert len(manifest["subjects"]) == 2
    assert manifest["subjects"][1]["narrativeName"] == "Bob"
    assert "narrativeAliases" not in manifest["subjects"][1]
    seen = []
    class Writer(FakeWriter):
        async def generate(self, messages):
            seen.append(messages)
            store.set_subject_narrative_name(project.project_id, CAST[1]["id"], "Fred")
            return VALID_BASE
    result = await generate_workspace_prompts(request, Writer)
    assert result.variations[0].generated.source_fingerprint == expected
    context = json.dumps(seen, ensure_ascii=False)
    assert "Bob" in context and "Robert" in context and "Carol" not in context and "Fred" not in context
    assert result.variations[0].generated.source_fingerprint != resolve_narrative_request(req(project), store).source_fingerprint
    events = [decode_event(e) async for e in routes.stream_generation_events(request, FakeWriter)]
    assert next(data for event, data in events if event == "complete")["sourceFingerprint"] == expected
    revision = revision_request(project.workspace)
    revision.filesystem_project_id = project.project_id
    revision = resolve_narrative_request(revision, store)
    repair = resolve_narrative_request(RepairRequest(**req(project).model_dump(by_alias=True), prompt="invalid"), store)
    for assembly in [assemble_revision_prompt(revision), assemble_repair_prompt(repair, [])]:
        assert '"Fred"' in assembly.inspector_text and "Carol" not in assembly.inspector_text
        assert '"Robert"' not in assembly.inspector_text
    class RevisionWriter(FakeWriter):
        text = "[Shot 2] The cyclist accelerates."
    events = [decode_event(e) async for e in routes.stream_revision_events(revision, RevisionWriter)]
    assert next(data for event, data in events if event == "complete")["sourceFingerprint"] == revision.source_fingerprint
    monkeypatch.setattr(routes, "OpenAICompatibleClient", FakeWriter)
    for prompt in [VALID_BASE, "invalid"]:
        output = await routes.repair(repair.model_copy(update={"prompt": prompt}))
        assert output.source_fingerprint == repair.source_fingerprint
    standalone = req(project).model_copy(update={"filesystem_project_id": None})
    assert resolve_narrative_request(standalone).narrative_subjects == []
    assert "narrativeName" not in json.dumps(build_reference_manifest(project.workspace))


def test_selected_prompt_staleness_prepare_and_unrelated_generation(named, profile):
    store, project = named
    pid, gid = project.project_id, project.active_generation_id
    fingerprint = resolve_narrative_request(req(project), store).source_fingerprint
    store.select_execution_prompt(pid, gid, SelectionWrite(prompt=VALID_BASE, sourceFingerprint=fingerprint))
    prepared = prepare_workflow(store, pid, gid, profile)
    assert prepared.current_fingerprint == fingerprint
    other = store.create_generation(pid)
    data = other.workspace.model_dump(by_alias=True)
    data.update(creativeRequest="Alice waits.", subjects=[CAST[2]], assets=[
        {**project.workspace.assets[0].model_dump(by_alias=True), "subjectIdentity": {"subjectId": CAST[2]["id"]}}])
    other = store.save_project(pid, validate_authoring_workspace(data))
    other_fingerprint = store.execution_snapshot(pid, other.active_generation_id)[1].current_fingerprint
    store.select_execution_prompt(pid, other.active_generation_id, SelectionWrite(prompt=VALID_BASE, sourceFingerprint=other_fingerprint))
    historical = (manifest_path(store, project).parent / "execution-state.json").read_bytes()
    snapshot = store.read_story_snapshot(pid)
    draft = initial_story_draft(snapshot, 2)
    store.set_subject_narrative_name(pid, CAST[1]["id"], "Bob")
    assert (manifest_path(store, project).parent / "execution-state.json").read_bytes() == historical
    assert store.execution_snapshot(pid, gid)[1].stale
    assert not store.execution_snapshot(pid, other.active_generation_id)[1].stale
    assert store.execution_snapshot(pid, other.active_generation_id)[1].current_fingerprint == other_fingerprint
    assert prepare_workflow(store, pid, gid, profile).graph["h3"]["inputs"]["prompt"] == VALID_BASE
    adopted = store.select_execution_prompt(pid, gid,
        SelectionWrite(prompt=VALID_BASE, sourceFingerprint=fingerprint)).selected
    assert adopted.source_kind == "manual_edit" and adopted.prompt == VALID_BASE
    assert store.read_story_snapshot(pid).fingerprint != snapshot.fingerprint
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan(pid, draft)


async def test_story_full_cast_new_authoring_and_revision_authority(named):
    store, project = named
    pid = project.project_id
    store.set_subject_narrative_name(pid, CAST[1]["id"], "Bob")
    contexts = []
    class StoryWriter(FakeWriter):
        async def generate(self, messages, **kwargs):
            context = json.loads(messages[1]["content"])
            contexts.append(context)
            system = messages[0]["content"]
            assert "current narrativeName" in system
            assert "technical Subject N label" in system
            assert "selected fields" in system
            assert context["sequenceNarrativeCast"][1]["narrativeName"] == "Bob"
            assert "narrativeAliases" not in context["sequenceNarrativeCast"][1]
            if "selectedPlanItemIds" in context:
                assert len(context["sequenceNarrativeCast"]) == 2
                assert "Robert" not in json.dumps(context)
                return json.dumps({"patches": [{"planItemId": item_id, "scenePlan": "Bob waits with Alice."}
                                               for item_id in context["selectedPlanItemIds"]]})
            assert len(context["sequenceNarrativeCast"]) == 3
            assert context["idea"] == "Alice confronts Robert"
            assert context["sequenceNarrativeCast"][2]["narrativeName"] == "Carol"
            return json.dumps({"sequenceContext": "Alice and Bob are neighbors.", "consistencyWarnings": [],
                "items": [{"planItemId": item["planItemId"], "scenePlan": "Alice confronts Bob.",
                    "incomingState": "Alice arrives.", "intendedOutgoingState": "Bob waits.", "cameraIntent": "Frame Bob.",
                    "shotCount": 1,
                    "relationshipToPrevious": "independent" if index else None,
                    "referenceRecommendations": [{"sourceGenerationId": project.active_generation_id,
                        "assetId": "asset-2", "recommendation": "Bob identity"}]}
                    for index, item in enumerate(context["draft"]["items"])]})
    draft = await build_story_plan(store, pid, StoryBuildRequest(idea="Alice confronts Robert", desiredGenerationCount=2, llm=LLM), StoryWriter)
    assert draft.items[0].scene_plan == "Alice confronts Bob."
    assert draft.items[1].scene_plan == "Alice confronts Bob."
    assert draft.items[1].reference_recommendations[0].asset_id == "asset-2"
    revised = await revise_story_plan(store, pid, StoryRevisionRequest(draft=draft,
        selectedPlanItemIds=[draft.items[1].plan_item_id], scopes=["scene_plan"], instruction="Have Bob wait", llm=LLM), StoryWriter)
    assert revised.items[0] == draft.items[0]
    assert revised.items[1].scene_plan == "Bob waits with Alice."
    assert revised.items[1].incoming_state == draft.items[1].incoming_state
    assert all(context["sourceGenerations"][0]["subjects"] == CAST[:2] for context in contexts)


@pytest.mark.parametrize("mention,location", [("Carol", "dialogue"), ("Caz", "camera_intent"),
    ("Subject 3", "incoming_state"), ("Ｃａｒｏｌ", "instruction"), ("Caz", "sequence_context")])
async def test_revision_resolves_dormant_mentions_and_retained_aliases_in_local_context(named, mention, location):
    from test_story_planning import PatchModel
    store, project = named
    store.set_subject_narrative_name(project.project_id, CAST[2]["id"], "Caz")
    snapshot = store.read_story_snapshot(project.project_id)
    draft = initial_story_draft(snapshot, 4)
    instruction = "Revise the camera."
    if location == "instruction":
        instruction += " " + mention
    elif location == "sequence_context":
        draft.sequence_context = mention
    else:
        setattr(draft.items[2], location, mention + ": Wait.")
    model = PatchModel()
    await revise_story_plan(store, project.project_id, StoryRevisionRequest(draft=draft,
        selectedPlanItemIds=[draft.items[1].plan_item_id], scopes=["camera_intent"],
        instruction=instruction, llm=LLM), model)
    mapped = next(s for s in model.context["sequenceNarrativeCast"] if s["subjectId"] == CAST[2]["id"])
    assert mapped["narrativeName"] == "Caz" and "narrativeAliases" not in mapped
    assert mapped["canonicalH3Label"] == "<Subject 3>"


def test_common_word_aliases_do_not_rewrite_h3_prose_or_exact_dialogue():
    subject = SequenceSubject(id="june", number=1, narrativeName="June", narrativeAliases=["May", "Will"])
    text = "It may rain later. Will the train arrive?"
    dialogue = "I will wait. May I come along?"
    workspace = validate_authoring_workspace({
        "schemaVersion": 1, "projectName": "Alias prose", "mode": "T2VA", "durationSeconds": 8,
        "aspectRatio": "16:9", "variations": 1, "creativeRequest": text, "dialogue": dialogue,
        "subjects": [{"id": "june", "number": 1}], "assets": [],
    })
    assembly = assemble_prompt(workspace, False, [subject], H3ContinuityContext(
        incomingState="It may rain later.", intendedOutgoingState="I will wait.",
    ))
    assert text in assembly.inspector_text
    assert dialogue in assembly.inspector_text
    assert '"June"' in assembly.inspector_text
    assert '"June" == <Subject 1>' not in assembly.inspector_text
    assert "narrativeAliases" not in assembly.inspector_text


@pytest.mark.parametrize("operation", ["generate", "revision", "repair"])
async def test_each_operation_keeps_its_mapping_when_renamed_during_provider_response(named, monkeypatch, operation):
    store, project = named
    expected = resolve_narrative_request(req(project), store).source_fingerprint
    seen = []

    def capture_then_rename(messages):
        seen.append(messages)
        store.set_subject_narrative_name(project.project_id, CAST[1]["id"], "Bob")

    class Writer(FakeWriter):
        async def generate(self, messages):
            capture_then_rename(messages)
            return VALID_BASE

        async def stream_events(self, messages):
            from miniconstruct.llm.client import LLMStreamEvent
            capture_then_rename(messages)
            yield LLMStreamEvent("content", "[Shot 2] The cyclist accelerates." if operation == "revision" else VALID_BASE)

    if operation == "repair":
        monkeypatch.setattr(routes, "OpenAICompatibleClient", Writer)
        request = RepairRequest(**req(project).model_dump(by_alias=True), prompt="invalid")
        actual = (await routes.repair(request)).source_fingerprint
    else:
        request = req(project)
        stream = routes.stream_generation_events
        if operation == "revision":
            request = revision_request(project.workspace)
            request.filesystem_project_id = project.project_id
            stream = routes.stream_revision_events
        events = [decode_event(e) async for e in stream(request, Writer)]
        actual = next(data for event, data in events if event == "complete")["sourceFingerprint"]
    assert len(seen) == 1
    assert "Robert" in json.dumps(seen) and "Bob" not in json.dumps(seen)
    assert actual == expected
    assert actual != resolve_narrative_request(req(project), store).source_fingerprint
