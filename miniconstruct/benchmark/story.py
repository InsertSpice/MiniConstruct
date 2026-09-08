"""E5 adapter for the existing benchmark runner; never applies a Story draft."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from miniconstruct import story_planning as service
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_planning import StoryBuildRequest, StoryPlanProposal, StoryRevisionRequest
from miniconstruct.models.planning import RelationshipToPrevious
from miniconstruct.project_store import ProjectStore, ProjectStoreError

from .recording import RecordingClient, SingleAttemptClient
from .runner import (
    _PLAN_FIELDS, _check_result, _effective_request_parameters, _load_workspace_file,
    _messages_provenance, _reference_inputs, _resolve_fixture_assets, _workspace_image_mappings,
    evaluate_expectations,
)


STORY_BENCHMARK_NAMESPACE = UUID("7d4c2b34-8a7f-5c28-9b8f-2c6d3c2f4a61")
STORY_BENCHMARK_TIMESTAMP = "2020-01-01T00:00:00.000Z"


class DeterministicStoryProjectStore(ProjectStore):
    """Benchmark-only identity/clock seam; normal ProjectStore is untouched."""

    def __init__(self, root: Path, case_id: str) -> None:
        super().__init__(root)
        self._story_project_id = str(uuid5(STORY_BENCHMARK_NAMESPACE, f"project:{case_id}"))
        self._story_generation_ordinal = 0

    def _new_project_id(self) -> str:
        return self._story_project_id

    def _new_generation_id(self) -> str:
        self._story_generation_ordinal += 1
        return str(uuid5(UUID(self._story_project_id), f"generation:{self._story_generation_ordinal}"))

    def _now(self) -> str:
        return STORY_BENCHMARK_TIMESTAMP


def load_story_workspaces(case, case_dir, source_root):
    """Reuse FixtureAsset resolution, allowing asset IDs to repeat across sources."""
    loaded = []
    known_ids = set()
    for generation in case.story.generations:
        workspace = _load_workspace_file(case, case_dir, generation.workspace_path)
        ids = {asset.id for asset in workspace.assets}
        known_ids |= ids
        local_ids = {asset.asset_id for asset in generation.fixture_assets}
        if not local_ids <= ids:
            raise ValueError("Generation fixtureAssets reference unknown assets")
        fixtures = [asset for asset in case.fixture_assets if asset.asset_id in ids] + generation.fixture_assets
        scoped_case = case.model_copy(update={"fixture_assets": fixtures})
        workspace = _resolve_fixture_assets(scoped_case, workspace, case_dir, source_root)
        loaded.append((scoped_case, workspace))
    if {asset.asset_id for asset in case.fixture_assets} - known_ids:
        raise ValueError("Story fixtureAssets reference unknown assets")
    return loaded


def materialize_story_project(store, case, loaded):
    """Use normal saved v4 authoring operations, in fixture order."""
    project = None
    for generation, (_, workspace) in zip(case.story.generations, loaded):
        kwargs = dict(
            sequence_context=case.story.sequence_context,
            incoming_state=generation.incoming_state or "",
            intended_outgoing_state=generation.intended_outgoing_state or "",
            planning=generation.planning,
        )
        if project is None:
            project = store.create_project(workspace, **kwargs)
        else:
            store.create_generation(project.project_id, workspace=workspace)
            project = store.save_project(project.project_id, workspace, **kwargs)
    return store.read_story_snapshot(project.project_id)


def revision_request(case, snapshot, settings):
    revision = case.story.revision
    template = service.initial_story_draft(snapshot, len(revision.draft.items))
    items = []
    for index, fixture in enumerate(revision.draft.items):
        item = fixture.model_dump(mode="json", by_alias=True, exclude={"existing_generation", "reference_recommendations"})
        item.update(
            planItemId=str(uuid5(NAMESPACE_URL, f"miniconstruct-benchmark:{case.id}:item:{index + 1}")),
            existingGenerationId=(snapshot.generations[fixture.existing_generation - 1].generation_id
                                  if fixture.existing_generation else None),
            referenceRecommendations=[{
                "sourceGenerationId": snapshot.generations[ref.source_generation - 1].generation_id,
                "assetId": ref.asset_id, "recommendation": ref.recommendation,
            } for ref in fixture.reference_recommendations],
        )
        items.append(item)
    draft_data = template.model_dump(mode="json", by_alias=True)
    draft_data.update(items=items, sequenceContext=revision.draft.sequence_context)
    draft = StoryPlanProposal.model_validate(draft_data)
    if any((r.source_generation_id, r.asset_id) not in snapshot.allowed_refs
           for item in draft.items for r in item.reference_recommendations):
        raise ValueError("Revision draft reference does not exist in its source Generation")
    return StoryRevisionRequest(
        draft=draft, selectedPlanItemIds=[draft.items[i - 1].plan_item_id for i in revision.selected_items],
        scopes=revision.scopes, instruction=revision.instruction, llm=settings,
    )


def evaluate_story_checks(checks, plan, snapshot, request, model_input, raw_model_output=None):
    """Hard scope checks use the effective production result, never raw paraphrases."""
    actual = plan.model_dump(mode="json", by_alias=True)
    before = request.draft.model_dump(mode="json", by_alias=True) if isinstance(request, StoryRevisionRequest) else None
    canonical = [{
        "creativeRequest": g.workspace.creative_request, "incomingState": g.incoming_state,
        "intendedOutgoingState": g.intended_outgoing_state,
        "relationshipToPrevious": g.planning.relationship_to_previous, "cameraIntent": g.planning.camera_intent,
    } for g in snapshot.generations]
    expected_ids = [g.generation_id for g in snapshot.generations]
    pair_lists = [[(r.source_generation_id, r.asset_id) for r in item.reference_recommendations] for item in plan.items]
    results = []
    for check in checks:
        kind = check.kind
        selected = actual["items"] if check.item is None else actual["items"][check.item - 1:check.item]
        baseline = before["items"] if before else canonical
        originals = baseline if check.item is None else baseline[check.item - 1:check.item]
        values = [item.get(check.field) for item in selected]
        passed = False
        if kind == "story_item_count_equals":
            passed = len(plan.items) == check.value
        elif kind == "story_existing_generation_count_equals":
            passed = sum(i.existing_generation_id is not None for i in plan.items) == check.value
        elif kind == "story_existing_generation_ids_preserved":
            passed = [i.existing_generation_id for i in plan.items] == expected_ids + [None] * (len(plan.items) - len(expected_ids))
        elif kind == "story_new_items_have_no_existing_generation_id":
            passed = all(i.existing_generation_id is None for i in plan.items[len(expected_ids):])
        elif kind == "story_first_relationship_null":
            passed = plan.items[0].relationship_to_previous is None
        elif kind == "story_relationship_valid":
            passed = plan.items[0].relationship_to_previous is None and all(i.relationship_to_previous in set(RelationshipToPrevious) for i in plan.items[1:])
        elif kind in {"story_relationship_equals", "story_relationship_not_equals"}:
            passed = bool(selected) and all((i["relationshipToPrevious"] == check.value) == (kind == "story_relationship_equals") for i in selected)
        elif kind == "story_sequence_context_equals":
            passed = plan.sequence_context == check.value
        elif kind == "story_sequence_context_not_empty":
            passed = bool(plan.sequence_context.strip())
        elif kind == "story_field_equals":
            passed = bool(selected) and all(check.field in i and i[check.field] == check.value for i in selected)
        elif kind == "story_field_not_empty":
            passed = bool(values) and all(v is not None and bool(str(v).strip()) for v in values)
        elif kind in {"story_field_changed", "story_field_unchanged"}:
            passed = bool(selected) and len(selected) == len(originals) and all(
                check.field in item and check.field in old and
                ((item[check.field] != old[check.field]) == (kind == "story_field_changed"))
                for item, old in zip(selected, originals)
            )
        elif kind == "story_selected_fields_only_changed" and before:
            fields = {service.SCOPE_FIELDS[scope.value] for scope in request.scopes}
            passed = all(actual[key] == before[key] for key in (
                "baseProjectId", "baseProjectUpdatedAt", "baseSequenceFingerprint", "sequenceContext",
            )) and len(plan.items) == len(request.draft.items)
            for new, old in zip(plan.items, request.draft.items):
                editable = fields if new.plan_item_id in request.selected_plan_item_ids else set()
                passed &= (new.plan_item_id, new.existing_generation_id) == (old.plan_item_id, old.existing_generation_id)
                passed &= all(getattr(new, attr) == getattr(old, attr) for attr in _PLAN_FIELDS.values() if attr not in editable)
                if not editable:
                    passed &= new.reference_recommendations == old.reference_recommendations
        elif kind == "story_recommendation_pairs_valid":
            passed = all(pair in snapshot.allowed_refs for pairs in pair_lists for pair in pairs)
        elif kind == "story_no_duplicate_recommendation_pairs":
            # The same reference may legitimately be recommended for multiple shots.
            passed = all(len(pairs) == len(set(pairs)) for pairs in pair_lists)
        elif kind == "story_warning_exists":
            passed = any(
                (check.item is None or (check.item <= len(plan.items) and w.plan_item_id == plan.items[check.item - 1].plan_item_id))
                and (check.related_item is None or (check.related_item <= len(plan.items) and w.related_plan_item_id == plan.items[check.related_item - 1].plan_item_id))
                and (check.direction is None or w.direction == check.direction)
                for w in plan.consistency_warnings
            )
        elif kind == "image_input_count_equals":
            passed = model_input["imageInputCount"] == check.value
        elif kind == "forbidden_text_absent":
            text = json.dumps(actual, ensure_ascii=False) + "\n" + (raw_model_output or "")
            passed = (not check.text or check.text.lower() not in text.lower()) and all(
                not re.search(pattern, text, re.IGNORECASE) for pattern in check.patterns
            )
        else:
            raise ValueError(f"Unsupported Story check {kind!r}")
        results.append(_check_result(kind, check.model_dump(mode="json", by_alias=True, exclude_none=True), bool(passed)))
    return results


def _sanitize(value, sensitive):
    """Also protect raw response/error echoes; preserve ordinary raw model text."""
    if isinstance(value, dict):
        return {key: _sanitize(item, sensitive) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize(item, sensitive) for item in value]
    if isinstance(value, str):
        for item in sensitive:
            if item:
                for spelling in {item, item.replace("\\", "/"), item.replace("\\", "\\\\")}:
                    value = value.replace(spelling, "[redacted]")
        return re.sub(r"data:[^\s\"'<>]+", "[media payload omitted]", value)
    return value


async def run_story_case(runner, profile, case, case_dir):
    settings = runner._planner_settings(profile)
    base = runner._planner_skip_result(profile, case)
    base.pop("skip")
    base["settings"].update(repair="never", automaticRetry="never", llmJudge="none")
    sensitive = [str(runner.source_root)] if runner.source_root else []
    if settings.endpoint.api_key:
        sensitive.append(settings.endpoint.api_key.get_secret_value())
    started = time.perf_counter()
    captured, raw_outputs = [], []
    image_mappings = {}
    try:
        loaded = load_story_workspaces(case, case_dir, runner.source_root)
        # A response/error may echo only the encoded body, without a data-URL header.
        sensitive.extend(asset.image.data_url.split(",", 1)[1]
                         for _, workspace in loaded for asset in workspace.assets
                         if asset.image and "," in asset.image.data_url)
        # OS temporary storage, never the repository or the user's Projects root.
        with TemporaryDirectory(prefix="miniconstruct-story-benchmark-") as temporary:
            sensitive.append(temporary)
            store = DeterministicStoryProjectStore(Path(temporary), case.id)
            snapshot = materialize_story_project(store, case, loaded)
            base["canonicalProjectSnapshot"] = {
                "projectId": snapshot.manifest.project_id,
                "createdAt": snapshot.manifest.created_at,
                "updatedAt": snapshot.manifest.updated_at,
                "baseSequenceFingerprint": snapshot.fingerprint,
            }
            base["canonicalGenerations"] = [
                {"ordinal": g.ordinal, "generationId": g.generation_id,
                 "createdAt": snapshot.manifest.sequence.generations[g.ordinal - 1].created_at,
                 "updatedAt": snapshot.manifest.sequence.generations[g.ordinal - 1].updated_at}
                for g in snapshot.generations
            ]
            base["workspace"] = {"schemaVersion": 1, "sources": [
                {"path": generation.workspace_path,
                 "sourceSha256": hashlib.sha256((case_dir / generation.workspace_path).read_bytes()).hexdigest()}
                for generation in case.story.generations
            ]}
            for g, (scoped_case, workspace) in zip(snapshot.generations, loaded):
                for entry in _reference_inputs(scoped_case, workspace, case_dir, runner.source_root):
                    base["referenceInputs"].append({"sourceGenerationId": g.generation_id, "sourceGeneration": g.ordinal, **entry})
                for digest, mappings in _workspace_image_mappings(scoped_case, workspace).items():
                    image_mappings.setdefault(digest, []).extend({"sourceGenerationId": g.generation_id, **m} for m in mappings)

            factory = SingleAttemptClient if runner.client_factory is OpenAICompatibleClient else runner.client_factory

            def recording_factory(llm):
                client = RecordingClient(factory(llm), raw_outputs, llm)
                captured.append(client)
                return client

            started = time.perf_counter()
            if case.story.build:
                request = StoryBuildRequest(**case.story.build.model_dump(), llm=settings)
                item_counter = 0
                def item_id_factory():
                    nonlocal item_counter
                    item_counter += 1
                    return str(uuid5(UUID(snapshot.manifest.project_id), f"story-plan-item:{item_counter}"))
                plan = await service.build_story_plan(
                    store, snapshot.manifest.project_id, request, recording_factory,
                    item_id_factory=item_id_factory,
                )
            else:
                request = revision_request(case, snapshot, settings)
                base["initialStoryDraft"] = request.draft.model_dump(mode="json", by_alias=True)
                plan = await service.revise_story_plan(store, snapshot.manifest.project_id, request, recording_factory)
            model_input = _messages_provenance(captured[-1].payload["messages"], image_mappings)
            machine_checks = evaluate_story_checks(
                case.expectations.checks, plan, snapshot, request, model_input, raw_outputs[-1],
            )
            machine_checks += evaluate_expectations(case.expectations, plan.model_dump_json(by_alias=True), set())
            base.update(
                status="completed", structuralValid=True,
                structuredStoryPlannerOutput=plan.model_dump(mode="json", by_alias=True),
                machineChecks={"checks": machine_checks, "passed": all(c["passed"] for c in machine_checks)},
            )
            base["expectations"] = base["machineChecks"]
    except (LLMBackendError, ProjectStoreError, ValueError, OSError) as exc:
        base.update(status="error", structuralValid=False, error={
            "type": "provider_or_validation" if isinstance(exc, LLMBackendError) else "fixture_or_validation",
            "message": str(exc), "statusCode": getattr(exc, "status_code", None),
            "upstreamStatus": getattr(exc, "upstream_status", None),
        })
    finally:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        base.update(latencyMs=elapsed, rawModelOutput=raw_outputs[-1] if raw_outputs else None)
        if captured:
            client = captured[-1]
            if client.payload:
                base["modelInput"] = _messages_provenance(client.payload["messages"], image_mappings)
                base["effectiveRequest"] = _effective_request_parameters(client.payload)
            base["provider"] = {"usage": getattr(client, "last_usage", {}), "requestDurationMs": elapsed,
                                "compatibilityFallback": bool(getattr(client, "compatibility_fallback", False))}
    return _sanitize(base, sensitive)
