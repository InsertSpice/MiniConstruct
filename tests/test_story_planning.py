from __future__ import annotations

from copy import deepcopy
import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from miniconstruct.app import app
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.story_planning import StoryBuildRequest, StoryPlanProposal, StoryRevisionRequest
from miniconstruct.models.workspace import ReferenceAsset, validate_authoring_workspace
from miniconstruct.planning import PLANNER_MEDIA_POLICY
from miniconstruct.project_store import ProjectStore, StaleProjectSnapshotError, _blank_workspace
from miniconstruct.story_planning import build_story_plan, normalize_story_model_json, revise_story_plan


def settings(vision=False):
    return LLMSettings(endpoint={"id": "fake", "displayName": "Fake", "baseUrl": "http://never-call.test/v1"},
                       modelId="fake", supportsVision=vision)


class Model:
    """Echo the structured template, optionally changing it; never opens a socket."""
    def __init__(self, transform=None, wrapper=None):
        self.transform = transform
        self.wrapper = wrapper
        self.calls = []

    def __call__(self, llm):
        self.settings = llm
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def generate(self, messages, *, response_instruction="H3 sentinel"):
        self.calls.append(messages)
        self.response_instruction = response_instruction
        content = messages[1]["content"]
        context = json.loads(content[0]["text"] if isinstance(content, list) else content)
        result = deepcopy(context["draft"])
        # Emulate a legacy full-draft reply from the sparse Build source context.
        result.setdefault("sequenceContext", "Planned world")
        result.setdefault("consistencyWarnings", [])
        for index, item in enumerate(result["items"]):
            for field in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
                item.setdefault(field, f"Planned {field}")
            item.setdefault("relationshipToPrevious", "independent" if index else None)
            item.setdefault("referenceRecommendations", [])
        if "baseProjectId" not in result and getattr(self, "snapshot", None):
            snapshot = self.snapshot
            result.update(baseProjectId=snapshot.manifest.project_id, baseProjectUpdatedAt=snapshot.manifest.updated_at,
                          baseSequenceFingerprint=snapshot.fingerprint)
        if "baseProjectId" not in result:
            for item in result["items"]:
                item.pop("existingGenerationId")
        if self.transform:
            self.transform(result)
        raw = json.dumps(result)
        return self.wrapper(raw) if self.wrapper else raw


@pytest.fixture
def saved(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory())
    return store, project


def files(store):
    return {p.relative_to(store.root).as_posix(): p.read_bytes() for p in store.root.rglob("*") if p.is_file()}


async def build(saved, count=4, model=None, vision=False):
    store, project = saved
    model = model or Model()
    model.snapshot = store.read_story_snapshot(project.project_id)
    return await build_story_plan(store, project.project_id,
        StoryBuildRequest(idea="A cyclist finds a lost dog and brings it home.", desiredGenerationCount=count, llm=settings(vision)),
        model)


def revision(draft, *, selected=None, scopes=None):
    return StoryRevisionRequest(draft=draft, selectedPlanItemIds=selected or [draft.items[1].plan_item_id],
        scopes=scopes or ["camera_intent"], instruction="Make this more dynamic.", llm=settings())


async def revise(saved, draft, transform, *, selected=None, scopes=None):
    store, project = saved
    return await revise_story_plan(store, project.project_id,
        revision(draft, selected=selected, scopes=scopes), Model(transform))


async def test_build_exact_count_identity_and_no_writes(saved):
    store, project = saved
    before = files(store)
    model = Model()
    result = await build(saved, model=model)
    assert len(result.items) == 4
    assert result.items[0].existing_generation_id == project.active_generation_id
    assert all(i.existing_generation_id is None for i in result.items[1:])
    assert len({i.plan_item_id for i in result.items}) == 4
    assert all(UUID(i.plan_item_id).version == 4 for i in result.items)
    assert result.items[0].relationship_to_previous is None
    assert model.response_instruction is None
    assert "Output the finished H3 prompt directly." not in str(model.calls)
    assert len(model.calls) == 1
    assert files(store) == before


@pytest.mark.parametrize("vision", [False, True])
async def test_assembled_build_contract_distinguishes_authority_and_required_planning(saved, vision):
    model = Model()
    await build(saved, model=model, vision=vision)
    system = " ".join(model.calls[0][0]["content"].split())
    # Inspect the actual service request, not a duplicate prompt or a whole-string snapshot.
    assert "sparse draft is read-only source authority, not a response template" in system
    assert "MUST fill each with a useful proposal, including on Generation 1" in system
    assert "backend constructs baseProjectId, baseProjectUpdatedAt, baseSequenceFingerprint" in system
    assert "Do not echo these fields" in system
    assert "No provisional relationship is supplied" in system
    assert "On new slots, Apply will attach each recommended reference and its required saved Subjects." in system
    assert "New slots inherit the immediately preceding Generation's mode." in system
    assert "Existing-slot references remain advisory." in system
    assert "Only recommend committed sourceGenerationId + assetId pairs present in sourceGenerations." in system
    assert "Proposed new items own no assets" not in system
    content = model.calls[0][1]["content"]
    context = json.loads(content[0]["text"] if vision else content)
    assert "baseProjectId" not in context["responseSchema"]["properties"]
    assert "existingGenerationId" not in context["responseSchema"]["$defs"]["StoryBuildItem"]["properties"]



@pytest.mark.parametrize("saved_relationship", [None, "independent", "continue_previous_shot"])
async def test_build_completes_mixed_authority_and_plans_only_provisional_relationships(saved, saved_relationship):
    store, project = saved
    blank = _blank_workspace(project.project_id, project.name)
    store.save_project(project.project_id, blank)
    store.create_generation(project.project_id)
    authored = blank.model_copy(update={"creative_request": "  Keep this wording.\nExactly."})
    store.save_project(project.project_id, authored, sequence_context="  Keep this world.\nExactly.",
        intended_outgoing_state="The parcel is on the doorstep.",
        planning={"cameraIntent": "", "relationshipToPrevious": saved_relationship})
    before = files(store)
    def complete(d):
        for index, item in enumerate(d["items"]):
            for field in ("creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent"):
                if not item[field].strip():
                    item[field] = f"Proposed {field} for segment {index + 1}."
            if index and (item["existingGenerationId"] is None or saved_relationship is None):
                item["relationshipToPrevious"] = "new_shot_preserve_continuity"
    model = Model(complete)
    result = await build(saved, model=model)
    context = json.loads(model.calls[0][1]["content"])
    assert context["sourceGenerations"][1]["planning"]["relationshipToPrevious"] == saved_relationship
    assert context["draft"]["items"][1].get("relationshipToPrevious") == saved_relationship
    assert all("relationshipToPrevious" not in i for i in context["draft"]["items"][2:])
    assert result.items[0].relationship_to_previous is None
    assert result.items[1].relationship_to_previous == (saved_relationship or "new_shot_preserve_continuity")
    assert all(i.relationship_to_previous == "new_shot_preserve_continuity" for i in result.items[2:])
    assert result.items[1].creative_request == authored.creative_request
    assert result.items[1].intended_outgoing_state == "The parcel is on the doorstep."
    assert result.sequence_context == "  Keep this world.\nExactly."
    assert all(getattr(i, f).strip() for i in result.items for f in (
        "creative_request", "incoming_state", "intended_outgoing_state", "camera_intent",
    ))
    assert files(store) == before


@pytest.mark.parametrize("corruption", ["inserted_digits", "valid_but_wrong_hash"])
async def test_build_still_rejects_fingerprint_echo_errors(saved, corruption):
    def corrupt(d):
        fingerprint = d["baseSequenceFingerprint"]
        d["baseSequenceFingerprint"] = (fingerprint[:45] + "698" + fingerprint[45:]
            if corruption == "inserted_digits" else ("1" if fingerprint[0] == "0" else "0") + fingerprint[1:])
    model = Model(corrupt)
    before = files(saved[0])
    error = "invalid structured JSON" if corruption == "inserted_digits" else "changed the base Project snapshot"
    with pytest.raises(LLMBackendError, match=error):
        await build(saved, model=model)
    assert len(model.calls) == 1
    assert files(saved[0]) == before


def test_story_response_normalization_accepts_raw_and_one_json_fence(saved):
    store, project = saved
    # Use a real proposal-shaped object so this test exercises the exact boundary helper input.
    raw = json.dumps({"hello": "world"})
    assert normalize_story_model_json(raw) == {"hello": "world"}
    assert normalize_story_model_json(f"```json\n{raw}\n```") == {"hello": "world"}
    assert normalize_story_model_json(f"```\n{raw}\n```") == {"hello": "world"}


async def test_build_accepts_exact_valid_json_fence(saved):
    result = await build(saved, model=Model(wrapper=lambda raw: f"```json\n{raw}\n```"))
    assert len(result.items) == 4


@pytest.mark.parametrize("raw", [
    "prose before\n```json\n{}\n```",
    "```json\n{}\n```\ntrailing prose",
    "```json\n{}\n```\n```json\n{}\n```",
    "```json\n{malformed}\n```",
])
def test_story_response_normalization_rejects_prose_multiple_fences_and_malformed(raw):
    with pytest.raises(ValueError):
        normalize_story_model_json(raw)


@pytest.mark.parametrize("count", [2, 3, 12])
async def test_requested_total_count(saved, count):
    assert len((await build(saved, count)).items) == count


@pytest.mark.parametrize("count", [1, 13, 2.0, True, "4"])
def test_count_bounds_and_strict_integer(count):
    with pytest.raises(ValueError):
        StoryBuildRequest(idea="Idea", desiredGenerationCount=count, llm=settings())


@pytest.mark.parametrize("idea", ["", " \n ", "x" * 20001])
def test_idea_limits(idea):
    with pytest.raises(ValueError):
        StoryBuildRequest(idea=idea, desiredGenerationCount=4, llm=settings())


async def test_full_committed_existing_order_and_too_small_before_call(saved):
    store, project = saved
    store.create_generation(project.project_id)
    store.create_generation(project.project_id)
    result = await build(saved, 5)
    canonical = store.load_project(project.project_id)
    assert [i.existing_generation_id for i in result.items[:3]] == [g.generation_id for g in canonical.sequence.generations]
    model = Model()
    with pytest.raises(ValueError, match="smaller"):
        await build(saved, 2, model)
    assert not model.calls


@pytest.mark.parametrize("field", ["creativeRequest", "incomingState", "intendedOutgoingState", "cameraIntent", "relationshipToPrevious", "sequenceContext"])
async def test_build_projects_authority_rewrite(saved, workspace_factory, field):
    store, project = saved
    store.create_generation(project.project_id)
    store.save_project(project.project_id, workspace_factory(), sequence_context="Canonical world",
        incoming_state="Canonical start", intended_outgoing_state="Canonical end",
        planning={"cameraIntent": "Canonical camera", "relationshipToPrevious": "continue_previous_shot"})
    before = files(store)
    def change(d):
        if field == "sequenceContext":
            d[field] = "Changed"
        else:
            d["items"][1][field] = "independent" if field == "relationshipToPrevious" else "Changed"
    model = Model(change)
    result = await build(saved, model=model)
    if field == "sequenceContext":
        assert result.sequence_context == "Canonical world"
    else:
        expected = {"creativeRequest": workspace_factory().creative_request,
                    "incomingState": "Canonical start", "intendedOutgoingState": "Canonical end",
                    "cameraIntent": "Canonical camera", "relationshipToPrevious": "continue_previous_shot"}
        assert result.items[1].model_dump(mode="json", by_alias=True)[field] == expected[field]
    assert len(model.calls) == 1
    assert files(store) == before


async def test_fill_blank_existing_fields_and_context(saved):
    store, project = saved
    store.save_project(project.project_id, _blank_workspace(project.project_id, project.name))
    def fill(d):
        d["sequenceContext"] = "Rainy city"
        d["items"][0].update(creativeRequest="New idea", incomingState="Start", intendedOutgoingState="End", cameraIntent="Wide")
    result = await build(saved, model=Model(fill))
    assert result.sequence_context == "Rainy city"
    assert result.items[0].creative_request == "New idea"
    assert store.load_project(project.project_id).workspace.creative_request == ""


@pytest.mark.parametrize("corruption", ["short", "long", "duplicate_plan", "duplicate_existing", "missing_existing", "reorder", "insert_new", "invent_existing", "first_relationship", "later_null", "bad_enum", "extra", "length", "base"])
async def test_structural_model_violations_rejected_atomically(saved, corruption):
    store, project = saved
    store.create_generation(project.project_id)
    def corrupt(d):
        items = d["items"]
        if corruption == "short": items.pop()
        if corruption == "long": items.append({**items[-1], "planItemId": str(uuid4())})
        if corruption == "duplicate_plan": items[2]["planItemId"] = items[1]["planItemId"]
        if corruption == "duplicate_existing": items[1]["existingGenerationId"] = items[0]["existingGenerationId"]
        if corruption == "missing_existing": items[1]["existingGenerationId"] = None
        if corruption == "reorder": items[0]["existingGenerationId"], items[1]["existingGenerationId"] = items[1]["existingGenerationId"], items[0]["existingGenerationId"]
        if corruption == "insert_new": items[1]["existingGenerationId"], items[2]["existingGenerationId"] = None, items[1]["existingGenerationId"]
        if corruption == "invent_existing": items[-1]["existingGenerationId"] = str(uuid4())
        if corruption == "first_relationship": items[0]["relationshipToPrevious"] = "independent"
        if corruption == "later_null": items[1]["relationshipToPrevious"] = None
        if corruption == "bad_enum": items[1]["relationshipToPrevious"] = "teleport"
        if corruption == "extra": items[0]["workspace"] = {}
        if corruption == "length": items[0]["cameraIntent"] = "x" * 20001
        if corruption == "base": d["baseProjectUpdatedAt"] = "forged"
    before = files(store)
    with pytest.raises(LLMBackendError):
        await build(saved, model=Model(corrupt))
    assert files(store) == before


async def test_arbitrary_reference_sources_media_and_noncloning(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    picture = image_asset_factory(asset_id="shared")
    audio = ReferenceAsset.model_validate({"id": "sound", "kind": "audio", "filename": "voice.wav", "mimeType": "audio/wav", "role": "voice_timbre", "notes": "Intended voice reference", "attached": True})
    video = ReferenceAsset.model_validate({"id": "video", "kind": "video", "filename": "move.mp4", "mimeType": "video/mp4", "role": "camera_movement", "attached": True})
    workspace = validate_authoring_workspace({**workspace_factory().model_dump(mode="json", by_alias=True), "assets": [a.model_dump(mode="json", by_alias=True) for a in [picture, audio, video]]})
    project = store.create_project(workspace)
    second = store.create_generation(project.project_id, workspace=workspace)
    sources = [project.active_generation_id, second.active_generation_id]
    original_workspaces = [g.workspace.model_dump() for g in store.read_story_snapshot(project.project_id).generations]
    original_mappings = [g["assetFiles"] for g in json.loads(next(store.root.rglob("project.json")).read_text())["sequence"]["generations"]]
    def recommend(d):
        d["items"][2]["referenceRecommendations"] = [{"sourceGenerationId": g, "assetId": "shared", "recommendation": "Use identity"} for g in sources]
    model = Model(recommend)
    before = files(store)
    draft = await build((store, project), model=model, vision=True)
    assert files(store) == before
    messages = model.calls[0]
    assert PLANNER_MEDIA_POLICY in messages[0]["content"]
    image_parts = [p for p in messages[1]["content"] if p["type"] == "image_url"]
    assert len(image_parts) == 2
    assert all(p["image_url"]["url"] == picture.image.data_url for p in image_parts)
    assert all(g in str(messages) for g in sources)
    context = json.loads(messages[1]["content"][0]["text"])
    for source in context["sourceGenerations"]:
        for asset in source["assets"]:
            assert "image" not in asset
            if asset["kind"] in {"audio", "video"}:
                assert asset["contentAnalysis"] is None
    result = store.apply_story_plan(project.project_id, draft)
    assert result.active_generation_id == second.active_generation_id
    assert result.generation_count == 4
    after = files(store)
    assert {p: b for p, b in before.items() if p != f"project-{project.project_id}/project.json"} == {p: b for p, b in after.items() if p != f"project-{project.project_id}/project.json"}
    for index, generation in enumerate(result.sequence.generations):
        loaded = store.read_generation(project.project_id, generation.generation_id)
        if index < 2:
            assert generation.generation_id == sources[index]
            assert loaded.workspace.model_dump() == original_workspaces[index]
            assert len(loaded.workspace.assets) == 3
        else:
            assert UUID(generation.generation_id).version == 4
            assert generation.generation_id not in {i.plan_item_id for i in draft.items}
            expected = _blank_workspace(project.project_id, project.name).model_dump()
            expected["creative_request"] = draft.items[index].creative_request
            expected["mode"] = original_workspaces[-1]["mode"]
            if index == 2:
                assert len(loaded.workspace.assets) == 2
                assert len({a.id for a in loaded.workspace.assets}) == 2
                assert all(UUID(a.id).version == 5 for a in loaded.workspace.assets)
                assert loaded.workspace.subjects == workspace.subjects
                expected.update(assets=loaded.workspace.model_dump()["assets"],
                                subjects=loaded.workspace.model_dump()["subjects"], next_subject_number=2)
            assert loaded.workspace.model_dump() == expected
    manifest = json.loads(next(p.read_text() for p in store.root.rglob("project.json")))
    assert manifest["schemaVersion"] == 5
    assert [g["assetFiles"] for g in manifest["sequence"]["generations"][:2]] == original_mappings
    assert not any(term in json.dumps(manifest) for term in ["planItemId", "referenceRecommendations", "baseSequenceFingerprint", "lost dog"])
    assert all(g["updatedAt"] == manifest["updatedAt"] for g in manifest["sequence"]["generations"])


@pytest.mark.parametrize("kind", ["invented_source", "invented_asset", "duplicate"])
async def test_invalid_advisory_references_are_filtered_and_valid_pairs_preserved(saved, kind):
    store, project = saved
    # The committed Project has no assets, so every invented pair is dropped.
    def change(d):
        ref = {"sourceGenerationId": str(uuid4()) if kind == "invented_source" else project.active_generation_id,
               "assetId": "imaginary", "recommendation": "Use"}
        d["items"][1]["referenceRecommendations"] = [ref, ref] if kind == "duplicate" else [ref]
    result = await build(saved, model=Model(change))
    assert result.items[1].reference_recommendations == []


async def test_valid_and_duplicate_qualified_references_are_sanitized(saved, workspace_factory, image_asset_factory):
    store, project = saved
    asset = image_asset_factory(asset_id="shared")
    store.save_project(project.project_id, workspace_factory(assets=[asset]))
    snapshot = store.read_story_snapshot(project.project_id)
    source = snapshot.generations[0].generation_id
    def change(d):
        d["items"][1]["referenceRecommendations"] = [
            {"sourceGenerationId": source, "assetId": "shared", "recommendation": "Keep this"},
            {"sourceGenerationId": source, "assetId": "shared", "recommendation": "Drop duplicate"},
            {"sourceGenerationId": source, "assetId": "video_01", "recommendation": "Drop invented"},
        ]
    result = await build((store, store.load_project(project.project_id)), model=Model(change))
    assert [r.model_dump() for r in result.items[1].reference_recommendations] == [{
        "source_generation_id": source, "asset_id": "shared", "recommendation": "Keep this",
    }]


async def test_revision_filters_invented_recommendations_and_keeps_distinct_sources(tmp_path, workspace_factory, image_asset_factory):
    store = ProjectStore(tmp_path)
    asset = image_asset_factory(asset_id="shared")
    workspace = workspace_factory(assets=[asset])
    project = store.create_project(workspace)
    store.create_generation(project.project_id, workspace=workspace)
    draft = await build((store, project))
    snapshot = store.read_story_snapshot(project.project_id)
    sources = [g.generation_id for g in snapshot.generations]
    def change(d):
        d["items"][1]["cameraIntent"] = "Track"
        d["items"][1]["referenceRecommendations"] = [
            {"sourceGenerationId": sources[0], "assetId": "shared", "recommendation": "First source"},
            {"sourceGenerationId": sources[1], "assetId": "shared", "recommendation": "Second source"},
            {"sourceGenerationId": sources[0], "assetId": "invented", "recommendation": "Drop"},
        ]
    result = await revise((store, store.load_project(project.project_id)), draft, change)
    assert [(r.source_generation_id, r.asset_id, r.recommendation) for r in result.items[1].reference_recommendations] == [
        (sources[0], "shared", "First source"), (sources[1], "shared", "Second source"),
    ]


async def test_revision_latest_manual_values_selected_multiple_and_no_write(saved):
    draft = await build(saved)
    draft.items[1].creative_request = "Manual wording\n  preserved exactly 🙂"
    draft.sequence_context = "Manually edited context"
    original = draft.model_dump()
    before = files(saved[0])
    def change(d):
        d["items"][1]["cameraIntent"] = "Dynamic"
        d["items"][2]["cameraIntent"] = "Close"
    result = await revise(saved, draft, change, selected=[i.plan_item_id for i in draft.items[1:3]])
    assert result.items[1].camera_intent == "Dynamic"
    assert result.items[2].camera_intent == "Close"
    assert result.items[1].creative_request == draft.items[1].creative_request
    assert result.items[0] == draft.items[0] and result.items[3] == draft.items[3]
    assert result.sequence_context == draft.sequence_context
    assert draft.model_dump() == original
    assert files(saved[0]) == before


@pytest.mark.parametrize("target", ["unselected_item", "unselected_field", "context"])
async def test_revision_projects_selected_scope_and_restores_out_of_scope_echoes(saved, target):
    draft = await build(saved)
    draft.items[1].creative_request = "Manual edit"
    original = draft.model_copy(deep=True)
    before = draft.model_dump()
    def change(d):
        if target == "unselected_item": d["items"][2]["cameraIntent"] = "Changed"
        if target == "unselected_field": d["items"][1]["creativeRequest"] = "Changed"
        if target == "context": d["sequenceContext"] = "Changed"
        d["items"][1]["intendedOutgoingState"] = "Accepted selected ending"
    result = await revise(saved, draft, change, scopes=["intended_outgoing_state"])
    assert result.items[1].intended_outgoing_state == "Accepted selected ending"
    assert result.items[1].creative_request == original.items[1].creative_request
    assert result.items[2] == original.items[2]
    assert result.sequence_context == original.sequence_context
    assert draft.model_dump() == before


@pytest.mark.parametrize("target", ["identity", "existing_identity", "order", "count", "base"])
async def test_revision_still_rejects_immutable_corruption(saved, target):
    draft = await build(saved)
    def change(d):
        if target == "identity": d["items"][1]["planItemId"] = str(uuid4())
        if target == "existing_identity": d["items"][0]["existingGenerationId"] = str(uuid4())
        if target == "order": d["items"][1], d["items"][2] = d["items"][2], d["items"][1]
        if target == "count": d["items"].pop()
        if target == "base": d["baseSequenceFingerprint"] = "0" * 64
    with pytest.raises(LLMBackendError):
        await revise(saved, draft, change)


async def test_live_scoped_outgoing_revision_preserves_manual_wet_glove_and_warns_downstream(saved):
    draft = await build(saved)
    draft.items[1].creative_request = "The cyclist removes one wet glove and reaches toward the dog."
    draft.items[1].intended_outgoing_state = "The dog tentatively licks the cyclist's hand."
    draft.items[3].creative_request = "The cyclist walks home with the dog."
    def change(d):
        d["items"][1]["intendedOutgoingState"] = "The cyclist holds the dog's red leash beside a closed blue gate."
        d["items"][3]["creativeRequest"] = "The cyclist returns home beneath a blue gate."
    result = await revise(saved, draft, change, scopes=["intended_outgoing_state"])
    assert result.items[1].intended_outgoing_state == "The cyclist holds the dog's red leash beside a closed blue gate."
    assert result.items[1].creative_request == draft.items[1].creative_request
    assert result.items[3].creative_request == draft.items[3].creative_request
    assert result.items[2] == draft.items[2]
    warning = next(w for w in result.consistency_warnings if w.direction == "downstream")
    assert warning.plan_item_id == draft.items[1].plan_item_id
    assert warning.related_plan_item_id == draft.items[2].plan_item_id


async def test_camera_projection_accepts_multiple_selected_cameras_only(saved):
    draft = await build(saved)
    def change(d):
        d["items"][1]["cameraIntent"] = "Dynamic tracking"
        d["items"][2]["cameraIntent"] = "Low angle push-in"
        d["items"][1]["creativeRequest"] = "Unauthorized rewrite"
        d["items"][3]["incomingState"] = "Unauthorized neighbor rewrite"
    selected = [draft.items[1].plan_item_id, draft.items[2].plan_item_id]
    result = await revise(saved, draft, change, selected=selected, scopes=["camera_intent"])
    assert result.items[1].camera_intent == "Dynamic tracking"
    assert result.items[2].camera_intent == "Low angle push-in"
    assert result.items[1].creative_request == draft.items[1].creative_request
    assert result.items[3] == draft.items[3]


@pytest.mark.parametrize("field,scope,direction", [
    ("intendedOutgoingState", "intended_outgoing_state", "downstream"),
    ("incomingState", "incoming_state", "upstream"),
    ("relationshipToPrevious", "relationship_to_previous", "upstream"),
    ("creativeRequest", "main_prompt", "downstream"),
])
async def test_deterministic_boundary_warning(saved, field, scope, direction):
    draft = await build(saved)
    def change(d):
        d["items"][1][field] = "continue_previous_shot" if field == "relationshipToPrevious" else "Changed"
        d["consistencyWarnings"] = []
    result = await revise(saved, draft, change, scopes=[scope])
    warning = next(w for w in result.consistency_warnings if w.direction == direction)
    assert warning.plan_item_id == draft.items[1].plan_item_id
    assert warning.related_plan_item_id == draft.items[2 if direction == "downstream" else 0].plan_item_id
    assert result.items[0] == draft.items[0] and result.items[2] == draft.items[2]


async def test_no_noisy_warning_on_camera_only_independent_or_selected_neighbor(saved):
    draft = await build(saved)
    result = await revise(saved, draft, lambda d: d["items"][1].update(cameraIntent="Dynamic"))
    assert result.consistency_warnings == []
    result = await revise(saved, draft, lambda d: d["items"][1].update(intendedOutgoingState="Changed"),
        selected=[draft.items[1].plan_item_id, draft.items[2].plan_item_id], scopes=["intended_outgoing_state"])
    assert result.consistency_warnings == []


async def test_stale_revision_and_apply_even_with_same_updated_at(saved, workspace_factory):
    store, project = saved
    draft = await build(saved)
    path = next(store.root.rglob("project.json"))
    manifest = json.loads(path.read_text())
    manifest["sequence"]["generations"][0]["workspace"]["creativeRequest"] = "External edit with unchanged timestamp"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = files(store)
    model = Model()
    with pytest.raises(StaleProjectSnapshotError):
        await revise_story_plan(store, project.project_id, revision(draft), model)
    assert not model.calls
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan(project.project_id, draft)
    assert files(store) == before


async def test_apply_atomic_failure_and_success(saved, monkeypatch):
    store, project = saved
    draft = await build(saved)
    draft.sequence_context = "Applied context"
    draft.items[0].creative_request = "Manual replacement"
    draft.items[1].incoming_state = "Arrives"
    before = files(store)
    import miniconstruct.project_store as storage
    original_replace = storage.os.replace
    def fail(*_):
        raise OSError("Simulated final rename failure")
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError):
        store.apply_story_plan(project.project_id, draft)
    assert files(store) == before
    writes = []
    def record(source, destination):
        writes.append(destination)
        original_replace(source, destination)
    monkeypatch.setattr(storage.os, "replace", record)
    response = store.apply_story_plan(project.project_id, draft)
    assert len(writes) == 1 and writes[0].name == "project.json"
    assert response.active_generation_id == project.active_generation_id
    assert response.workspace.creative_request == "Manual replacement"
    assert response.sequence_context == "Applied context"
    assert store.read_generation(project.project_id, response.sequence.generations[1].generation_id).incoming_state == "Arrives"
    with pytest.raises(StaleProjectSnapshotError):
        store.apply_story_plan(project.project_id, draft)


@pytest.mark.parametrize("corruption", ["existing_order", "extra", "relationship", "reference", "identity"])
async def test_apply_complete_validation_before_write(saved, corruption):
    store, project = saved
    draft = await build(saved)
    if corruption == "existing_order": draft.items[0].existing_generation_id = None
    if corruption == "extra":
        payload = draft.model_dump(by_alias=True)
        payload["items"][0]["workspace"] = {}
        with pytest.raises(ValueError): StoryPlanProposal.model_validate(payload)
        return
    if corruption == "relationship": draft.items[1].relationship_to_previous = None
    if corruption == "reference":
        from miniconstruct.models.story_planning import StoryReferenceRecommendation
        draft.items[1].reference_recommendations = [StoryReferenceRecommendation(sourceGenerationId=project.active_generation_id, assetId="fake", recommendation="Use")]
    if corruption == "identity": draft.items[2].plan_item_id = draft.items[1].plan_item_id
    before = files(store)
    if corruption == "reference":
        applied = store.apply_story_plan(project.project_id, draft)
        assert applied.generation_count == 4
        assert files(store) != before  # canonical manifest changed for the Apply itself
        assert "fake" not in json.dumps(json.loads(next(store.root.rglob("project.json")).read_text()))
    else:
        with pytest.raises(ValueError): store.apply_story_plan(project.project_id, draft)
        assert files(store) == before


@pytest.mark.parametrize("version", [1, 2, 3, 4])
async def test_legacy_read_build_no_migration_apply_v4(saved, version):
    store, project = saved
    path = next(store.root.rglob("project.json"))
    raw = json.loads(path.read_text())
    raw.pop("storyIdea", None)
    raw["schemaVersion"] = version
    if version == 1:
        first = raw.pop("sequence")["generations"][0]
        raw.update(workspace=first["workspace"], assetFiles=first["assetFiles"])
    elif version < 4:
        for g in raw["sequence"]["generations"]:
            g.pop("planning")
            if version == 2: g.pop("continuity")
        if version == 2: raw["sequence"].pop("continuityContext")
    path.write_text(json.dumps(raw), encoding="utf-8")
    before = files(store)
    draft = await build(saved)
    assert files(store) == before
    existing_id = store.load_project(project.project_id).active_generation_id
    result = store.apply_story_plan(project.project_id, draft)
    assert result.active_generation_id == existing_id
    assert json.loads(path.read_text())["schemaVersion"] == 5


async def test_api_build_revise_apply_and_conflicts(saved, monkeypatch):
    store, project = saved
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(store.root))
    import miniconstruct.api.routes as routes
    async def fake_build(s, pid, request):
        return await build_story_plan(s, pid, request, Model())
    async def fake_revise(s, pid, request):
        return await revise_story_plan(s, pid, request, Model())
    monkeypatch.setattr(routes, "build_story_plan", fake_build)
    monkeypatch.setattr(routes, "revise_story_plan", fake_revise)
    with TestClient(app) as client:
        url = f"/api/projects/{project.project_id}/story-plan"
        result = client.post(url + "/build", json={"idea": "Idea", "desiredGenerationCount": 4, "llm": settings().model_dump(mode="json", by_alias=True)})
        assert result.status_code == 200, result.text
        draft = StoryPlanProposal.model_validate(result.json())
        rev = revision(draft).model_dump(mode="json", by_alias=True)
        assert client.post(url + "/revise", json=rev).status_code == 200
        applied = client.post(url + "/apply", json={"draft": result.json()})
        assert applied.status_code == 200, applied.text
        assert len(applied.json()["sequence"]["generations"]) == 4
        assert client.post(url + "/revise", json=rev).status_code == 409
        assert client.post(url + "/apply", json={"draft": result.json()}).status_code == 409
        assert client.post(url + "/build", json={"idea": "", "desiredGenerationCount": 1}).status_code == 422


async def test_snapshot_change_during_model_call_rejects_result(saved, workspace_factory):
    store, project = saved
    def external_save(_):
        store.save_project(project.project_id, workspace_factory(creativeRequest="Saved while model was running"))
    model = Model(external_save)
    with pytest.raises(StaleProjectSnapshotError):
        await build(saved, model=model)
    assert len(model.calls) == 1
    assert store.load_project(project.project_id).workspace.creative_request == "Saved while model was running"
    assert store.load_project(project.project_id).generation_count == 1


async def test_vision_off_uses_no_image_parts(saved):
    model = Model()
    await build(saved, model=model, vision=False)
    assert isinstance(model.calls[0][1]["content"], str)
    assert model.response_instruction is None


async def test_apply_and_normal_save_share_transaction_lock(saved, workspace_factory, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    store, project = saved
    draft = await build(saved)
    preparing, release, saving = Event(), Event(), Event()
    prepare = store._prepare_generation
    def paused_prepare(*args, **kwargs):
        preparing.set()
        assert release.wait(5)
        return prepare(*args, **kwargs)
    monkeypatch.setattr(store, "_prepare_generation", paused_prepare)
    def save():
        saving.set()
        return ProjectStore(store.root).save_project(project.project_id, workspace_factory(creativeRequest="Later canonical save"))
    with ThreadPoolExecutor(max_workers=2) as pool:
        apply_future = pool.submit(store.apply_story_plan, project.project_id, draft)
        try:
            assert preparing.wait(5)
            save_future = pool.submit(save)
            assert saving.wait(5)
            assert not save_future.done()
        finally:
            release.set()
        assert apply_future.result(timeout=5).generation_count == 4
        assert save_future.result(timeout=5).generation_count == 4
    assert store.load_project(project.project_id).workspace.creative_request == "Later canonical save"
