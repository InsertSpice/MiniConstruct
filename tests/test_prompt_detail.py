from copy import deepcopy
import hashlib
import json

from miniconstruct.execution import canonical_json
from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.h3.prompt_detail import h3_prompt_detail_policy
from miniconstruct.h3.natural_liveliness import natural_liveliness_layer
from miniconstruct.models.api import ProjectEnvelope
from miniconstruct.models.workspace import H3PromptDetail, Workspace
from miniconstruct.project_store import ProjectStore


def test_legacy_workspace_defaults_prompt_detail_to_standard_without_schema_change():
    workspace = Workspace.model_validate({
        "schemaVersion": 1, "creativeRequest": "A cyclist crosses a wet street.",
    })
    assert workspace.creative_controls.prompt_detail == H3PromptDetail.STANDARD
    assert workspace.model_dump(mode="json", by_alias=True)["schemaVersion"] == 1
    assert workspace.model_dump(mode="json", by_alias=True)["creativeControls"]["promptDetail"] == "standard"


def test_standard_main_prompt_messages_preserve_baseline_plus_shared_policies():
    workspace = Workspace.model_validate({
        "creativeRequest": "A courier waits beneath a station clock, then notices the last train pull away.",
    })
    messages = assemble_prompt(workspace, False).messages
    # Pin base-mode prompt assembly after removing the later liveliness layer.
    base_mode_digest = "bc62df4da011f66f19ac3cf65566d3df8da03c9d70d2a11b87aae6de2de7bebd"
    section = "\n\n## %s\n\n%s" % natural_liveliness_layer()
    assert messages[0]["content"].count(section) == 1
    stripped = deepcopy(messages)
    stripped[0]["content"] = stripped[0]["content"].replace(section, "", 1)
    assert "H3 Subject identity policy" not in stripped[0]["content"]
    assert "Narrative Names used as structural Subject references" not in stripped[1]["content"]
    stripped[0]["content"] = stripped[0]["content"].replace(
        "- Follow the active official guide's exact H3 grammar. Use its supported `<Picture N>`, `<Video N>`, and `<Audio N>` labels. Only Ref2VA uses canonical `<Subject N>` output labels and its full-reference sections; never introduce those Ref2VA constructs in a base Video mode.",
        "- Follow the official guide's exact H3 grammar. Use canonical `<Subject N>`, `<Picture N>`, `<Video N>`, and `<Audio N>` labels.",
    ).replace(
        "- In Ref2VA, `<Subject N>` is a stable visible entity independent of asset numbering; one Subject may combine several assets.",
        "- `<Subject N>` is a stable visible entity independent of asset numbering; one Subject may combine several assets.",
    )
    assert hashlib.sha256(canonical_json(stripped)).hexdigest() == base_mode_digest
    assert hashlib.sha256(canonical_json(messages)).hexdigest() == "7beed78637c740a161dcb6e4a5183feae51b4dca88b5d7386cd6ea380988b928"


def test_one_shot_prompt_detail_emits_only_selected_non_default_policy(workspace_factory):
    concise = assemble_prompt(workspace_factory(creativeControls={"promptDetail": "concise"}), False).messages
    standard = assemble_prompt(workspace_factory(creativeControls={"promptDetail": "standard"}), False).messages
    detailed = assemble_prompt(workspace_factory(creativeControls={"promptDetail": "detailed"}), False).messages
    assert "H3 Prompt Detail: Concise." in concise[0]["content"]
    assert "H3 Prompt Detail: Detailed." in detailed[0]["content"]
    assert "H3 Prompt Detail" not in standard[0]["content"]
    assert "Prompt Detail: Standard" not in str(standard)


def test_concise_policy_is_compact_complete_and_directable_without_count_thresholds():
    policy = h3_prompt_detail_policy(H3PromptDetail.CONCISE)
    for phrase in (
        "compact, complete, directable", "every meaningful requested development",
        "necessary timing", "reference authority", "Remove decorative prose", "redundant adjectives",
    ):
        assert phrase in policy
    assert "word" not in policy.lower() and "token" not in policy.lower()


def test_detailed_policy_adds_observable_temporal_detail_with_boundaries():
    policy = h3_prompt_detail_policy(H3PromptDetail.DETAILED)
    for phrase in (
        "action progression", "cause and reaction", "posture", "gaze or attention",
        "hand/object interaction", "spatial blocking", "resulting state", "do not add plot events",
        "Obey required H3 timestamp grammar", "do not increase shot count",
        "extra cuts or timestamped beats merely to add detail", "Preserve deliberate stillness", "no fixed beat count",
    ):
        assert phrase in policy


def test_story_generation_omits_one_shot_prompt_detail_policy(workspace_factory):
    workspace = workspace_factory(creativeControls={"promptDetail": "detailed"})
    story = {"currentGeneration": {"scenePlan": "The subject waits and listens."}}
    assembled = assemble_prompt(workspace, False, story_context=story)
    assert "H3 Prompt Detail" not in str(assembled.messages)
    assert "Story-aware directing policy" in assembled.messages[0]["content"]


def test_prompt_detail_stales_one_shot_authoring_but_not_story(workspace_factory):
    source = workspace_factory()
    legacy_data = source.model_dump(mode="json", by_alias=True)
    legacy_data["creativeControls"].pop("promptDetail")
    legacy_workspace = Workspace.model_validate(legacy_data)
    standard = workspace_factory(creativeControls={"promptDetail": "standard"})
    concise = workspace_factory(creativeControls={"promptDetail": "concise"})
    detailed = workspace_factory(creativeControls={"promptDetail": "detailed"})
    fingerprint = execution_authoring_fingerprint
    assert "promptDetail" not in legacy_data["creativeControls"]
    assert fingerprint(legacy_workspace) == fingerprint(standard)
    assert fingerprint(standard) != fingerprint(concise)
    assert fingerprint(standard) != fingerprint(detailed)
    assert fingerprint(concise) != fingerprint(detailed)
    story_context = {"currentGeneration": {"scenePlan": "The subject waits."}}
    story_fingerprints = {
        fingerprint(workspace, authoring_path="story", story_context=story_context)
        for workspace in (standard, concise, detailed)
    }
    assert len(story_fingerprints) == 1


def test_prompt_detail_survives_project_save_reload_duplicate_and_portable_round_trip(tmp_path, workspace_factory):
    store = ProjectStore(tmp_path / "projects")
    workspace = workspace_factory(creativeControls={"promptDetail": "detailed"})
    created = store.create_project(workspace)
    reloaded = store.load_project(created.project_id)
    assert reloaded.workspace.creative_controls.prompt_detail == H3PromptDetail.DETAILED

    duplicate_generation = store.duplicate_generation(created.project_id)
    assert duplicate_generation.workspace.creative_controls.prompt_detail == H3PromptDetail.DETAILED
    fork = store.duplicate_project(created.project_id, "Detail fork")
    assert fork.workspace.creative_controls.prompt_detail == H3PromptDetail.DETAILED

    exported = ProjectEnvelope.model_validate({
        "format": "MiniConstruct Project", "schemaVersion": 1,
        "workspace": reloaded.workspace.model_dump(mode="json", by_alias=True),
    })
    imported = ProjectEnvelope.model_validate(exported.model_dump(mode="json", by_alias=True))
    assert imported.workspace.creative_controls.prompt_detail == H3PromptDetail.DETAILED

    legacy = store.create_project(workspace_factory())
    legacy_path = store.root / f"project-{legacy.project_id}" / "project.json"
    legacy_manifest = json.loads(legacy_path.read_text(encoding="utf-8"))
    legacy_manifest["sequence"]["generations"][0]["workspace"]["creativeControls"].pop("promptDetail")
    legacy_path.write_text(json.dumps(legacy_manifest), encoding="utf-8")
    before_read = legacy_path.read_bytes()
    opened = store.load_project(legacy.project_id)
    assert opened.workspace.creative_controls.prompt_detail == H3PromptDetail.STANDARD
    assert legacy_path.read_bytes() == before_read
