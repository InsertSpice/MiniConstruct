import pytest
from fastapi.testclient import TestClient
from miniconstruct.app import app
from miniconstruct.models.workspace import Workspace, validate_authoring_workspace

from miniconstruct.dialogue_language import dialogue_fingerprint, exact_dialogue, resolve_dialogue, resolution_is_current, validate_translation_structure, workspace_for_h3
from miniconstruct.generation import generate_workspace_prompts, resolve_narrative_request
from miniconstruct.llm.client import LLMBackendError
from miniconstruct.models.api import GenerationRequest, LLMSettings
from miniconstruct.models.workspace import DialogueLanguage, DialogueResolution


@pytest.mark.parametrize("language", ["as_written", "ja"])
def test_current_format_audit_without_dialogue_needs_no_resolution(language):
    from test_validator import VALID_BASE
    result = TestClient(app).post("/api/validate", json={"prompt": VALID_BASE,
        "mode": "T2VA", "durationSeconds": 8, "dialogue": "", "dialogueLanguage": language})
    assert result.status_code == 200, result.text
    codes = {item["code"] for item in result.json()["findings"]}
    assert "spoken_output_unavailable" not in codes
    assert "dialogue_changed" not in codes


def test_as_written_format_audit_compares_source_dialogue_without_resolution():
    from test_validator import VALID_BASE
    source = "Makoto: You're so beautiful."
    prompt = VALID_BASE.replace("a cyclist waits.",
        "Makoto says <d>You're so beautiful.</d>.")
    request = {"prompt": prompt, "mode": "T2VA", "durationSeconds": 8,
        "dialogue": source, "dialogueLanguage": "as_written"}
    client = TestClient(app)
    correct = client.post("/api/validate", json=request)
    assert correct.status_code == 200, correct.text
    assert not {"dialogue_changed", "spoken_output_unavailable"} & {f["code"] for f in correct.json()["findings"]}
    changed = client.post("/api/validate", json={**request,
        "prompt": prompt.replace("You're so beautiful.", "You are beautiful.")})
    assert changed.status_code == 200, changed.text
    codes = {f["code"] for f in changed.json()["findings"]}
    assert "dialogue_changed" in codes and "spoken_output_unavailable" not in codes


def test_current_format_audit_uses_current_spoken_output_and_skips_stale_translation():
    from test_validator import VALID_BASE
    source = 'Makoto: "You\'re so beautiful."'
    spoken = 'Makoto: 「君はとても美しいね。」'
    prompt = VALID_BASE.replace("a cyclist waits.", "Makoto says <d>君はとても美しいね。</d>.")
    resolution = DialogueResolution(sourceText=source, targetLanguage="ja", resolvedText=spoken,
        sourceFingerprint=dialogue_fingerprint(source, DialogueLanguage.JA))
    request = {"prompt": prompt, "mode": "T2VA", "durationSeconds": 8, "dialogue": source,
        "dialogueLanguage": "ja", "dialogueResolution": resolution.model_dump(by_alias=True)}
    client = TestClient(app)
    current = client.post("/api/validate", json=request)
    assert current.status_code == 200, current.text
    assert not any(item["code"] == "dialogue_changed" for item in current.json()["findings"])
    changed = client.post("/api/validate", json={**request, "prompt": prompt.replace("君はとても美しいね。", "君は美しい。")})
    assert any(item["code"] == "dialogue_changed" for item in changed.json()["findings"])
    for stale in (None, {**request["dialogueResolution"], "sourceText": "older source"},
                  {**request["dialogueResolution"], "resolvedText": ""},
                  {**request["dialogueResolution"], "contractVersion": 2}):
        result = client.post("/api/validate", json={**request, "dialogueResolution": stale,
            "prompt": prompt + " <Audio 99>"})
        assert result.status_code == 200, result.text
        codes = {item["code"] for item in result.json()["findings"]}
        assert "spoken_output_unavailable" in codes and "dialogue_changed" not in codes
        assert "dangling_asset" in codes


def test_filesystem_format_audit_uses_saved_generation_dialogue(monkeypatch, tmp_path, workspace_factory):
    from miniconstruct.project_store import ProjectStore
    from test_validator import VALID_BASE
    monkeypatch.setenv("MINICONSTRUCT_PROJECTS_ROOT", str(tmp_path / "projects"))
    source = 'Makoto: "You\'re so beautiful."'
    resolution = DialogueResolution(sourceText=source, targetLanguage="ja",
        resolvedText='Makoto: 「君はとても美しいね。」',
        sourceFingerprint=dialogue_fingerprint(source, DialogueLanguage.JA))
    workspace = workspace_factory(dialogue=source, dialogueResolution=resolution.model_dump(by_alias=True))
    project = ProjectStore().create_project(workspace, dialogue_language=DialogueLanguage.JA)
    prompt = VALID_BASE.replace("a cyclist waits.", "Makoto says <d>君はとても美しいね。</d>.")
    result = TestClient(app).post("/api/validate", json={"prompt": prompt, "mode": "T2VA",
        "durationSeconds": 8, "filesystemProjectId": project.project_id,
        "generationId": project.active_generation_id, "dialogue": "wrong browser value"})
    assert result.status_code == 200, result.text
    assert not any(f["code"] == "dialogue_changed" for f in result.json()["findings"])


def test_story_blank_main_prompt_dialogue_route_and_one_shot_boundary(monkeypatch):
    import miniconstruct.dialogue_language as dialogue_module
    monkeypatch.setattr(dialogue_module, "OpenAICompatibleClient", Translator)
    body = {"workspace": {"creativeRequest": "", "dialogue": "Kent: Run.", "dialogueLanguage": "ja"},
            "llm": {"modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}}}
    with pytest.raises(ValueError, match="main prompt / idea is required"):
        Workspace.model_validate(body["workspace"])
    assert validate_authoring_workspace(body["workspace"]).creative_request == ""
    client = TestClient(app)
    rejected = client.post("/api/dialogue/resolve", json={**body, "authoringPath": "one_shot"})
    assert rejected.status_code == 422
    assert "main prompt / idea is required" in rejected.text
    one_shot = client.post("/api/generate", json={**body, "authoringPath": "one_shot"})
    assert one_shot.status_code == 422
    assert "main prompt / idea is required" in one_shot.text
    Translator.calls = 0
    accepted = client.post("/api/dialogue/resolve", json={**body, "authoringPath": "story"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["resolution"]["resolvedText"] == "Kent: 早く逃げよう。"
    assert Translator.calls == 1
    as_written = client.post("/api/dialogue/resolve", json={**body, "authoringPath": "story",
        "workspace": {"creativeRequest": "", "dialogue": "Kent: Run.", "dialogueLanguage": "as_written"}})
    assert as_written.status_code == 200 and as_written.json()["resolution"] is None
    assert Translator.calls == 1


def test_story_generate_blank_main_prompt_resolves_japanese_then_writes_h3(tmp_path, monkeypatch):
    import miniconstruct.dialogue_language as dialogue_module
    import miniconstruct.generation as generation_module
    from miniconstruct.api import routes
    from miniconstruct.project_store import ProjectStore
    store = ProjectStore(tmp_path)
    workspace = validate_authoring_workspace({"creativeRequest": "", "dialogue": "Kent: Run.", "dialogueLanguage": "ja"})
    project = store.create_project(workspace, story_idea="Kent escapes the station.")
    store.save_project(project.project_id, workspace, planning={"scenePlan": "Kent runs through the station."},
                       dialogue_language=DialogueLanguage.JA)
    monkeypatch.setattr("miniconstruct.project_store.ProjectStore", lambda: store)
    # The resolver imports its client from its own module; the H3 writer is
    # injected into the ordinary generation function at the route boundary.
    monkeypatch.setattr(dialogue_module, "OpenAICompatibleClient", Translator)
    original = generation_module.generate_workspace_prompts
    async def offline_generate(request):
        return await original(request, StoryTranslationAndH3Client)
    monkeypatch.setattr(routes, "generate_workspace_prompts", offline_generate)
    StoryH3Client.calls = Translator.calls = 0
    body = {"workspace": workspace.model_dump(mode="json", by_alias=True), "authoringPath": "story",
            "filesystemProjectId": project.project_id, "generationId": project.active_generation_id,
            "llm": {"modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}}}
    response = TestClient(app).post("/api/generate", json=body)
    assert response.status_code == 200, response.text
    assert Translator.calls == 1 and StoryTranslationAndH3Client.calls == 1
    assert "Kent: 早く逃げよう。" in StoryTranslationAndH3Client.saw_dialogue
    import asyncio
    async def stream_start():
        async for event in routes.stream_generation_events(GenerationRequest.model_validate(body), StoryTranslationAndH3Client):
            if event.startswith("event: start"):
                return event
    assert "event: start" in asyncio.run(stream_start())
    Translator.calls = 0


def test_saved_japanese_reaches_initial_range_prompt(tmp_path):
    from types import SimpleNamespace
    from miniconstruct.project_store import ProjectStore
    from miniconstruct.story_operations import assemble_initial_range_prompt
    store = ProjectStore(tmp_path)
    workspace = validate_authoring_workspace({"creativeRequest": ""})
    project = store.create_project(workspace, story_idea="Kent escapes the station.")
    store.save_project(project.project_id, workspace, dialogue_language=DialogueLanguage.JA)
    messages = assemble_initial_range_prompt(store.read_story_snapshot(project.project_id),
        SimpleNamespace(requested_total_count=2, new_fact_ids=[], new_thread_ids=[], allowed_subject_ids=[]),
        LLMSettings.model_validate({"modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}}))
    assert "Sequence Dialogue Language is Japanese (ja)" in messages[0]["content"]


class Settings:
    def model_copy(self, **_kwargs): return self


class Translator:
    calls = 0
    def __init__(self, settings): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass
    async def generate(self, *_args, **_kwargs):
        Translator.calls += 1
        return "Kent: 早く逃げよう。"


class BlankTranslator(Translator):
    async def generate(self, *_args, **_kwargs):
        return "   "


class FailingTranslator(Translator):
    async def generate(self, *_args, **_kwargs):
        raise LLMBackendError("provider unavailable", 503, category="provider_error")


class StoryH3Client:
    calls = 0
    saw_dialogue = ""

    def __init__(self, settings):
        self.seed_unsupported = False
        self.effective_seed = settings.seed
        self.seed_known_unsupported = False
        self.compatibility_fallback = False
        self.last_usage = {}
        self.last_payload = None

    async def __aenter__(self): return self
    async def __aexit__(self, *_): return None

    async def generate(self, messages, *, response_instruction=None):
        assert response_instruction is None
        type(self).calls += 1
        type(self).saw_dialogue = messages[1]["content"]
        return "integrated_multimodal_description:\n[Shot 1] A shot. <d>Kent: 急いで、出口へ！</d>\noverall_soundscape:\nQuiet.\nnon_diegetic_music:\nNone.\n"


class StoryTranslationAndH3Client(StoryH3Client):
    async def generate(self, messages, *, response_instruction=None):
        if response_instruction:
            Translator.calls += 1
            return "Kent: 早く逃げよう。"
        return await super().generate(messages, response_instruction=response_instruction)


@pytest.mark.asyncio
async def test_as_written_never_calls_translation(workspace_factory, monkeypatch):
    monkeypatch.setattr("miniconstruct.dialogue_language.OpenAICompatibleClient", Translator)
    workspace = workspace_factory(dialogue="Kent: Run.")
    assert await resolve_dialogue(workspace, Settings()) is None
    assert Translator.calls == 0 and exact_dialogue(workspace) == "Kent: Run."


@pytest.mark.asyncio
async def test_target_resolution_caches_source_and_h3_uses_only_resolved_text(workspace_factory, monkeypatch):
    monkeypatch.setattr("miniconstruct.dialogue_language.OpenAICompatibleClient", Translator)
    workspace = workspace_factory(dialogue="Kent: Run.", dialogueLanguage="ja")
    resolution = await resolve_dialogue(workspace, Settings())
    translated = workspace.model_copy(update={"dialogue_resolution": resolution})
    assert resolution.source_text == "Kent: Run." and resolution.target_language == DialogueLanguage.JA
    assert resolution_is_current(translated)
    assert workspace_for_h3(translated).dialogue == "Kent: 早く逃げよう。"
    assert await resolve_dialogue(translated, Settings()) == resolution
    assert Translator.calls == 1


@pytest.mark.asyncio
async def test_blank_translator_output_is_rejected(workspace_factory, monkeypatch):
    monkeypatch.setattr("miniconstruct.dialogue_language.OpenAICompatibleClient", BlankTranslator)
    with pytest.raises(LLMBackendError, match="blank output"):
        await resolve_dialogue(workspace_factory(dialogue="Kent: Run.", dialogueLanguage="ja"), Settings())


@pytest.mark.asyncio
async def test_provider_translation_failure_is_preserved(workspace_factory, monkeypatch):
    monkeypatch.setattr("miniconstruct.dialogue_language.OpenAICompatibleClient", FailingTranslator)
    with pytest.raises(LLMBackendError, match="provider unavailable"):
        await resolve_dialogue(workspace_factory(dialogue="Kent: Run.", dialogueLanguage="ja"), Settings())


def test_source_or_language_change_invalidates_but_user_override_is_current(workspace_factory):
    source = "Kent: Run."
    resolution = DialogueResolution(sourceText=source, targetLanguage="ja", resolvedText="Kent: 走れ。",
                                   sourceFingerprint=dialogue_fingerprint(source, DialogueLanguage.JA))
    workspace = workspace_factory(dialogue=source, dialogueLanguage="ja", dialogueResolution=resolution)
    assert resolution_is_current(workspace)
    assert not resolution_is_current(workspace.model_copy(update={"dialogue": "Kent: Hide."}))
    assert not resolution_is_current(workspace.model_copy(update={"dialogue_language": "es"}))
    edited = resolution.model_copy(update={"resolved_text": "Kent: 急いで！", "status": "user_edited"})
    assert exact_dialogue(workspace.model_copy(update={"dialogue_resolution": edited})) == "Kent: 急いで！"


def test_translator_output_must_preserve_line_and_speaker_structure():
    validate_translation_structure("[00:01] Kent: Run.\nMira: Wait!", "[00:01] Kent: 走れ。\nMira: 待って！")
    with pytest.raises(LLMBackendError, match="speaker label"):
        validate_translation_structure("Kent: Run.", "Mira: 走れ。")
    with pytest.raises(LLMBackendError, match="number of spoken lines"):
        validate_translation_structure("Kent: Run.\nMira: Wait.", "Kent: 走れ。")


def test_project_sequence_language_is_canonical_across_generation_switches_and_fork(tmp_path, workspace_factory):
    from miniconstruct.project_store import ProjectStore
    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(projectName="Language sequence", dialogue="Kent: Run."))
    store.save_project(project.project_id, project.workspace, dialogue_language=DialogueLanguage.JA)
    second = store.create_generation(project.project_id)
    third = store.create_generation(project.project_id)
    for generation_id in (project.active_generation_id, second.active_generation_id, third.active_generation_id):
        active = store.activate_generation(project.project_id, generation_id)
        assert active.dialogue_language == DialogueLanguage.JA
        assert active.workspace.dialogue_language == DialogueLanguage.JA
    fork = store.duplicate_project(project.project_id, "Language sequence fork")
    assert fork.dialogue_language == DialogueLanguage.JA


@pytest.mark.asyncio
async def test_story_authored_target_dialogue_persists_identity_resolution_and_skips_translation(tmp_path, workspace_factory):
    from miniconstruct.project_store import ProjectStore
    from miniconstruct.story_planning import initial_story_draft

    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(dialogueLanguage="ja", creativeRequest="Story"))
    draft = initial_story_draft(store.read_story_snapshot(project.project_id), 2)
    draft.items[1].scene_plan = "The tunnel collapses as Kent runs."
    draft.items[1].dialogue = "Kent: 急いで、出口へ！"
    applied = store.apply_story_plan(project.project_id, draft)
    generated_workspace = store.read_generation(project.project_id, applied.sequence.generations[1].generation_id).workspace
    assert generated_workspace.dialogue == "Kent: 急いで、出口へ！"
    assert generated_workspace.dialogue_resolution is not None
    assert generated_workspace.dialogue_resolution.status == "current"
    assert generated_workspace.dialogue_resolution.resolved_text == generated_workspace.dialogue
    assert generated_workspace.dialogue_resolution.source_fingerprint == dialogue_fingerprint(
        generated_workspace.dialogue, DialogueLanguage.JA,
    )
    StoryH3Client.calls = 0
    await generate_workspace_prompts(
        GenerationRequest(workspace=generated_workspace.model_copy(update={"creative_request": "The tunnel collapses."}), llm=LLMSettings.model_validate({"modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}})),
        StoryH3Client,
    )
    assert StoryH3Client.calls == 1
    assert "Kent: 急いで、出口へ！" in StoryH3Client.saw_dialogue


def test_request_only_language_override_preserves_unsaved_japanese_for_h3_and_saved_authority(tmp_path, workspace_factory):
    from miniconstruct.project_store import ProjectStore

    store = ProjectStore(tmp_path)
    source = workspace_factory(dialogue="Kent: We need to leave.", dialogueLanguage="as_written")
    project = store.create_project(source)
    identity = DialogueResolution(
        sourceText=source.dialogue, targetLanguage="ja", resolvedText="Kent: 出なければならない。",
        sourceFingerprint=dialogue_fingerprint(source.dialogue, DialogueLanguage.JA),
    )
    live = source.model_copy(update={"dialogue_language": DialogueLanguage.JA, "dialogue_resolution": identity})
    request = GenerationRequest(
        workspace=live, llm=LLMSettings.model_validate({"modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}}),
        filesystemProjectId=project.project_id, sequenceDialogueLanguage="ja",
    )
    resolved = resolve_narrative_request(request, store)
    assert resolved.workspace.dialogue_language == DialogueLanguage.JA
    assert store.load_project(project.project_id).dialogue_language == DialogueLanguage.AS_WRITTEN
    StoryH3Client.calls = 0
    import asyncio
    asyncio.run(generate_workspace_prompts(resolved, StoryH3Client))
    assert StoryH3Client.calls == 1
    assert "Kent: 急いで、出口へ！" not in StoryH3Client.saw_dialogue
    assert "Kent: 出なければならない。" in StoryH3Client.saw_dialogue


def test_omitted_request_language_uses_saved_project_sequence_authority(tmp_path, workspace_factory):
    from miniconstruct.project_store import ProjectStore

    store = ProjectStore(tmp_path)
    project = store.create_project(workspace_factory(dialogueLanguage="ja"))
    stale_workspace = project.workspace.model_copy(update={"dialogue_language": DialogueLanguage.AS_WRITTEN})
    request = GenerationRequest(
        workspace=stale_workspace,
        llm=LLMSettings.model_validate({"modelId": "offline", "endpoint": {"id": "offline", "displayName": "Offline", "baseUrl": "http://never.test/v1"}}),
        filesystemProjectId=project.project_id,
    )
    assert resolve_narrative_request(request, store).workspace.dialogue_language == DialogueLanguage.JA
