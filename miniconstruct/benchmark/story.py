"""Story benchmark adapter; only explicit stateful scenarios apply drafts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import time
from uuid import NAMESPACE_URL, UUID, uuid5

from miniconstruct import story_planning as service
from miniconstruct.story_operations import (
    build_initial_range, initial_range_memory_states, next_memory_states, plan_insert, plan_next, plan_replan, replan_memory_states,
)
from miniconstruct.models.story_operations import (
    StoryInitialRangeRequest, StoryInsertRequest, StoryNextRequest, StoryReplanRequest, STORY_LOCAL_CONTEXT_GENERATIONS,
)
from miniconstruct.models.story_memory import StoryContinuityMemory
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.story_planning import StoryBuildRequest, StoryPlanProposal, StoryRevisionRequest
from miniconstruct.models.planning import RelationshipToPrevious
from miniconstruct.project_store import ProjectStore, ProjectStoreError

from .recording import (
    RecordingCaptureError, RecordingClient, SingleAttemptClient, require_single_recorded_request,
)
from .runner import (
    _check_result, _effective_request_parameters, _load_workspace_file,
    _messages_provenance, _reference_inputs, _resolve_fixture_assets, _seed_provenance, _workspace_image_mappings,
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

    @staticmethod
    def _manifest_timestamp(previous: str, proposed: str) -> str:
        # Offline fixture snapshots intentionally freeze all clocks. They never
        # issue browser mutation receipts or participate in execution sync.
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
        overrides = {}
        if generation.dialogue is not None:
            overrides["dialogue"] = generation.dialogue
        if overrides:
            workspace = workspace.model_copy(update=overrides)
        loaded.append((scoped_case, workspace))
    if {asset.asset_id for asset in case.fixture_assets} - known_ids:
        raise ValueError("Story fixtureAssets reference unknown assets")
    return loaded


def materialize_story_project(store, case, loaded):
    """Use normal saved Project authoring operations, in fixture order."""
    project = None
    for generation, (_, workspace) in zip(case.story.generations, loaded):
        kwargs = dict(
            sequence_context=case.story.sequence_context,
            story_idea=case.story.story_idea,
            story_creative_direction=case.story.story_creative_direction,
            incoming_state=generation.incoming_state or "",
            intended_outgoing_state=generation.intended_outgoing_state or "",
            planning={**generation.planning.model_dump(mode="json", by_alias=True),
                      **({"scenePlan": generation.scene_plan} if generation.scene_plan is not None else {})},
        )
        if project is None:
            project = store.create_project(workspace, **kwargs)
        else:
            store.create_generation(project.project_id, workspace=workspace)
            project = store.save_project(project.project_id, workspace, **kwargs)
    # Names and aliases are authored through the same production persistence
    # operation as the UI.  Applying aliases first makes the supplied current
    # name authoritative while retaining every earlier name as an alias.
    for subject in case.story.narrative_subjects:
        for alias in subject.narrative_aliases:
            store.set_subject_narrative_name(project.project_id, subject.subject_id, alias)
        store.set_subject_narrative_name(project.project_id, subject.subject_id, subject.narrative_name)
    profiles = [
        {"subjectId": subject.subject_id,
         "characterProfile": subject.character_profile.model_dump(mode="json", by_alias=True)}
        for subject in case.story.narrative_subjects if not subject.character_profile.is_blank()
    ]
    if profiles:
        current = store.load_project(project.project_id)
        store.save_project(project.project_id, current.workspace, character_profiles=profiles)
    # Generation-owned memory is authored through the production persistence
    # path so nonterminal Insert/Replan fixtures can state their historical
    # truth without a benchmark-only manifest mutation.
    for generation, fixture in zip(store.read_story_snapshot(project.project_id).generations, case.story.generations):
        if fixture.story_memory_after is None:
            continue
        active = store.activate_generation(project.project_id, generation.generation_id)
        store.save_project(project.project_id, active.workspace, story_memory=fixture.story_memory_after,
                           story_memory_generation_id=generation.generation_id)
    terminal_fixture = case.story.generations[-1]
    if case.story.next and terminal_fixture.story_memory_after is None:
        project = store.load_project(project.project_id)
        store.save_project(project.project_id, project.workspace, story_memory=case.story.story_memory,
                           story_memory_generation_id=project.active_generation_id)
    return store.read_story_snapshot(project.project_id)


def _story_metrics(profile, snapshot, request, model_input, raw_output, usage):
    """Measured Story request/output dimensions; `None` means unavailable."""
    prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
    completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
    # Do not synthesize total usage from component fields: providers differ in
    # accounting and the benchmark must distinguish measured from unavailable.
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
    response = raw_output or ""
    asset_counts = {}
    for generation in snapshot.generations:
        for asset in generation.workspace.assets:
            asset_counts[asset.kind.value] = asset_counts.get(asset.kind.value, 0) + 1
    selected = getattr(request, "selected_plan_item_ids", [])
    scopes = [scope.value for scope in getattr(request, "scopes", [])]
    context_utilization = None
    if profile.context_window and isinstance(prompt_tokens, int):
        context_utilization = round(prompt_tokens / profile.context_window * 100, 2)
    total_context_utilization = None
    if profile.context_window and isinstance(total_tokens, int):
        total_context_utilization = round(total_tokens / profile.context_window * 100, 2)
    is_revision = isinstance(request, StoryRevisionRequest)
    is_initial_range = isinstance(request, StoryInitialRangeRequest)
    is_next = isinstance(request, StoryNextRequest)
    is_insert = isinstance(request, StoryInsertRequest)
    is_replan = isinstance(request, StoryReplanRequest)
    operation = "build"
    for request_type, name in [
        (StoryInitialRangeRequest, "initialRange"), (StoryNextRequest, "next"),
        (StoryInsertRequest, "insert"), (StoryReplanRequest, "replan"), (StoryRevisionRequest, "revision"),
    ]:
        if isinstance(request, request_type):
            operation = name
            break
    return {
        "operation": operation,
        "sourceSequenceLength": len(snapshot.generations),
        "localContextGenerationCount": min(STORY_LOCAL_CONTEXT_GENERATIONS, len(snapshot.generations)) if is_next else None,
        "requestedNewGenerationCount": request.requested_count if is_next or is_insert else None,
        "storyMemoryFactCount": len(ProjectStore._story_memory(snapshot.manifest).durable_facts),
        "storyMemoryThreadCount": len(ProjectStore._story_memory(snapshot.manifest).open_threads),
        "storyCreativeDirection": ProjectStore._story_creative_direction(snapshot.manifest).model_dump(
            mode="json", by_alias=True,
        ),
        # generationCount is retained with its historical meaning for old
        # result JSON: the number of canonical source Generations in the
        # snapshot, not the requested/returned Story item count.
        "generationCount": len(snapshot.generations),
        "sourceGenerationCount": len(snapshot.generations),
        "desiredGenerationCount": None if is_revision or is_initial_range or is_next or is_insert or is_replan else request.desired_generation_count,
        "requestedTotalRange": request.requested_total_count if is_initial_range else None,
        "requestDraftItemCount": len(request.draft.items) if is_revision else None,
        "selectedRevisionItemCount": len(selected),
        "selectedRevisionScopes": scopes,
        "requestedChangedItemCount": len(selected),
        "requestedChangedScopeCount": len(scopes),
        "visionEnabled": profile.supports_vision,
        "sourcePictureInputCount": model_input["imageInputCount"],
        "sourceAssetCountsByKind": asset_counts,
        "modelInput": {key: model_input[key] for key in (
            "systemTextCharacters", "systemTextBytes", "userTextCharacters", "userTextBytes",
            "totalTextInputCharacters", "totalTextInputBytes",
        )},
        "responseCharacters": len(response),
        "responseBytes": len(response.encode("utf-8")),
        "providerPromptTokens": prompt_tokens,
        "providerCompletionTokens": completion_tokens,
        "providerTotalTokens": total_tokens,
        "contextWindow": profile.context_window,
        "promptContextUtilization": context_utilization,
        "totalContextUtilization": total_context_utilization,
    }


def revision_request(case, snapshot, settings):
    revision = case.story.revision
    template = service.initial_story_draft(snapshot, len(revision.draft.items))
    items = []
    for index, fixture in enumerate(revision.draft.items):
        item = fixture.model_dump(mode="json", by_alias=True, exclude={"existing_generation", "reference_recommendations", "repeat"})
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
    profile_check_kinds = {
        "story_relevant_subject_ids_equals", "story_character_profile_present", "story_character_profile_absent",
        "story_character_profile_policy_present", "story_character_profile_policy_absent",
    }
    request_context = json.JSONDecoder().raw_decode(model_input.get("user", "{}"))[0]
    relevant_subjects = request_context.get("sequenceNarrativeCast", [])
    relevant_ids = [subject.get("subjectId") for subject in relevant_subjects]
    profiles = {
        subject.get("subjectId"): subject.get("characterProfile")
        for subject in relevant_subjects
        if isinstance(subject, dict) and subject.get("subjectId")
    }

    def profile_check(check):
        kind = check.kind
        if kind == "story_relevant_subject_ids_equals":
            passed = relevant_ids == check.value
        elif kind == "story_character_profile_present":
            profile = profiles.get(check.value)
            passed = isinstance(profile, dict) and any(str(value).strip() for value in profile.values())
        elif kind == "story_character_profile_absent":
            profile = profiles.get(check.value)
            passed = not (isinstance(profile, dict) and any(str(value).strip() for value in profile.values()))
        elif kind == "story_character_profile_policy_present":
            passed = "Character Profiles are stable author-provided Subject guidance" in model_input.get("system", "")
        else:
            passed = "Character Profiles are stable author-provided Subject guidance" not in model_input.get("system", "")
        return _check_result(kind, check.model_dump(mode="json", by_alias=True, exclude_none=True), bool(passed))

    context_results = [profile_check(check) for check in checks if check.kind in profile_check_kinds]
    checks = [check for check in checks if check.kind not in profile_check_kinds]
    if isinstance(request, (StoryNextRequest, StoryInsertRequest)):
        results = []
        for check in checks:
            if check.kind == "story_item_count_equals":
                passed = len(plan.slots) == check.value
            elif check.kind == "story_recommendation_pairs_valid":
                passed = all((r.source_generation_id, r.asset_id) in snapshot.source.allowed_refs
                             for item in plan.slots for r in item.reference_recommendations)
            elif check.kind == "story_new_items_have_no_existing_generation_id":
                passed = all("existingGenerationId" not in i.model_dump(by_alias=True) for i in plan.slots)
            elif check.kind == "image_input_count_equals":
                passed = model_input["imageInputCount"] == check.value
            elif check.kind == "story_tone_performance_controls_valid":
                passed = all("tonePerformance" not in item.model_dump(by_alias=True) for item in plan.slots)
            else:
                raise ValueError(f"Unsupported Plan Next check {check.kind!r}")
            results.append(_check_result(check.kind, check.model_dump(mode="json", by_alias=True, exclude_none=True), passed))
        return context_results + results
    if isinstance(request, StoryReplanRequest):
        expected_ids = getattr(plan.authority, "target_generation_ids", [])
        pairs = [(reference.source_generation_id, reference.asset_id)
                 for item in plan.items for reference in item.reference_recommendations]
        results = []
        for check in checks:
            if check.kind == "story_item_count_equals":
                passed = len(plan.items) == check.value
            elif check.kind in {"story_replan_target_generation_ids_preserved", "story_existing_generation_ids_preserved"}:
                passed = [item.existing_generation_id for item in plan.items] == expected_ids
            elif check.kind == "story_recommendation_pairs_valid":
                passed = all(pair in snapshot.source.allowed_refs for pair in pairs)
            elif check.kind == "story_no_duplicate_recommendation_pairs":
                passed = all(len({(reference.source_generation_id, reference.asset_id)
                                  for reference in item.reference_recommendations}) == len(item.reference_recommendations)
                             for item in plan.items)
            elif check.kind == "image_input_count_equals":
                passed = model_input["imageInputCount"] == check.value
            elif check.kind == "forbidden_text_absent":
                text = json.dumps(plan.model_dump(mode="json", by_alias=True), ensure_ascii=False) + "\n" + (raw_model_output or "")
                passed = (not check.text or check.text.lower() not in text.lower()) and all(
                    not re.search(pattern, text, re.IGNORECASE) for pattern in check.patterns
                )
            else:
                raise ValueError(f"Unsupported Replan Range check {check.kind!r}")
            results.append(_check_result(check.kind, check.model_dump(mode="json", by_alias=True, exclude_none=True), bool(passed)))
        return context_results + results
    actual = plan.model_dump(mode="json", by_alias=True)
    before = request.draft.model_dump(mode="json", by_alias=True) if isinstance(request, StoryRevisionRequest) else None
    canonical = [{
        "scenePlan": g.planning.scene_plan, "incomingState": g.incoming_state,
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
        elif kind == "story_initial_range_new_generation_count_equals":
            passed = isinstance(request, StoryInitialRangeRequest) and len(plan.items) - 1 == check.value
        elif kind == "story_initial_range_ordered_slots":
            passed = isinstance(request, StoryInitialRangeRequest) and (
                [item.plan_item_id for item in plan.items] == plan.authority.slot_ids
                and [item.existing_generation_id for item in plan.items]
                == [plan.authority.g1_generation_id] + [None] * (len(plan.items) - 1)
            )
        elif kind == "story_initial_range_memory_available":
            try:
                memory_states = initial_range_memory_states(snapshot, plan) if isinstance(request, StoryInitialRangeRequest) else []
                passed = len(memory_states) == len(plan.items) and all(
                    isinstance(state, StoryContinuityMemory) for state in memory_states
                )
            except (TypeError, ValueError):
                passed = False
        elif kind == "story_tone_performance_controls_valid":
            passed = all("tonePerformance" not in item.model_dump(by_alias=True) for item in plan.items)
        elif kind == "story_tone_performance_protected_non_auto_preserved":
            passed = all("tonePerformance" not in item.model_dump(by_alias=True) for item in plan.items)
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
        elif kind == "story_field_contains":
            passed = bool(values) and all(check.text is not None and check.text in str(value) for value in values)
        elif kind == "story_field_matches_regex":
            passed = bool(values) and bool(check.patterns) and all(
                all(re.search(pattern, str(value)) for pattern in check.patterns) for value in values
            )
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
                passed &= all(getattr(new, attr) == getattr(old, attr)
                              for attr in set(service.SCOPE_FIELDS.values()) if attr not in editable)
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
    return context_results + results


def _request_context(payload):
    """Read the one JSON context part without retaining image payloads."""
    content = payload["messages"][1]["content"]
    text = content[0]["text"] if isinstance(content, list) else content
    return json.loads(text)


def _memory_counts(memory):
    def normalized_duplicates(entries):
        counts = {}
        for entry in entries:
            normalized = re.sub(r"\W+", " ", entry.text.casefold()).strip()
            if normalized:
                counts[normalized] = counts.get(normalized, 0) + 1
        return sum(count - 1 for count in counts.values() if count > 1)

    return {
        "factCount": len(memory.durable_facts),
        "threadCount": len(memory.open_threads),
        "duplicateNormalizedFactTextCount": normalized_duplicates(memory.durable_facts),
        "duplicateNormalizedThreadTextCount": normalized_duplicates(memory.open_threads),
        "summaryLength": len(memory.recent_summary),
        "relatedSubjectLinks": sorted({
            subject_id for entry in memory.durable_facts + memory.open_threads
            for subject_id in entry.related_subject_ids
        }),
    }


def memory_hygiene_metrics(before, states):
    """Report compact structural signals without treating prose rewording as failure."""
    after = states[-1] if states else before
    before_fact_ids = {entry.id for entry in before.durable_facts}
    before_thread_ids = {entry.id for entry in before.open_threads}
    after_fact_ids = {entry.id for entry in after.durable_facts}
    after_thread_ids = {entry.id for entry in after.open_threads}
    result = {
        "before": _memory_counts(before), "after": _memory_counts(after),
        "factIdsPreserved": sorted(before_fact_ids & after_fact_ids),
        "factIdsNew": sorted(after_fact_ids - before_fact_ids),
        "threadIdsPreserved": sorted(before_thread_ids & after_thread_ids),
        "threadIdsNew": sorted(after_thread_ids - before_thread_ids),
        "resolvedFactIds": sorted(before_fact_ids - after_fact_ids),
        "resolvedThreadIds": sorted(before_thread_ids - after_thread_ids),
        "perSlot": [_memory_counts(state) for state in states],
    }
    result["suspiciousDuplicateCount"] = (
        result["after"]["duplicateNormalizedFactTextCount"]
        + result["after"]["duplicateNormalizedThreadTextCount"]
    )
    return result


def _structured_prose(plan):
    items = getattr(plan, "slots", getattr(plan, "items", []))
    fields = ("scene_plan", "dialogue", "incoming_state", "intended_outgoing_state", "camera_intent")
    return "\n".join(
        f"Slot {index}: " + " | ".join(
            f"{field}: {getattr(item, field)}" for field in fields if getattr(item, field, "")
        )
        for index, item in enumerate(items, start=1)
    )


def _highlight_evidence(text, concepts):
    highlighted = text
    for concept in concepts:
        if concept:
            highlighted = re.sub(re.escape(concept), lambda match: f"[[{match.group(0)}]]",
                                 highlighted, flags=re.IGNORECASE)
    return highlighted[:1600]


def reference_metrics(operation_snapshot, full_snapshot, plan, model_input, context):
    """Keep source/semantic/pixel identities explicit and keep payloads out of results."""
    ordinals = {generation.generation_id: generation.ordinal for generation in full_snapshot.generations}
    historical = context.get("historicalReferenceContext", [])
    candidates = [{
        "sourceGenerationId": reference.generation.generation_id,
        "sourceGenerationOrdinal": reference.generation.ordinal,
        "assetId": reference.asset_id,
        "subjectIds": list(reference.subject_ids),
        "payloadHash": reference.payload_hash,
    } for reference in operation_snapshot.historical_references]
    recommendations = []
    for item in getattr(plan, "slots", getattr(plan, "items", [])):
        recommendations.extend({
            "sourceGenerationId": reference.source_generation_id,
            "sourceGenerationOrdinal": ordinals.get(reference.source_generation_id),
            "assetId": reference.asset_id,
            "recommendation": reference.recommendation,
        } for reference in item.reference_recommendations)
    offered = [{"sourceGenerationId": source_id, "sourceGenerationOrdinal": ordinals.get(source_id), "assetId": asset_id}
               for source_id, asset_id in sorted(operation_snapshot.source.allowed_refs)]
    transmitted_hashes = {image.get("sha256") for image in model_input.get("images", []) if image.get("sha256")}
    # The bounded operation already performed relevance selection while
    # assembling historicalReferenceContext/sequenceNarrativeCast.  Reuse
    # that authoritative projection; do not repeat its matching logic here.
    relevant_subjects = context.get("sequenceNarrativeCast", [])
    sequence_subjects = [subject.model_dump(mode="json", by_alias=True)
                         for subject in operation_snapshot.source.manifest.sequence.subjects]
    return {
        "sequenceSubjects": sequence_subjects,
        "relevantSubjects": relevant_subjects,
        "historicalReferenceCandidates": candidates,
        "selectedHistoricalReferenceMetadata": historical,
        "historicalReferenceSemanticCount": len(historical),
        "historicalReferenceUniquePicturePayloadCount": len({item["payloadHash"] for item in candidates}),
        "transmittedPicturePayloadCount": model_input.get("imageInputCount", 0),
        "transmittedUniquePicturePayloadCount": len(transmitted_hashes),
        "allowedReferencePairs": offered,
        "allowedReferencePairCount": len(offered),
        "recommendedReferencePairs": recommendations,
        "recommendedReferencePairCount": len(recommendations),
    }


def _insert_apply_projection(full_snapshot, insert, states, after_proposal):
    upstream = insert.upstream_generation - 1
    downstream_before = full_snapshot.generations[upstream + 1:]
    downstream_after = after_proposal.generations[upstream + 1:]
    unchanged = len(downstream_before) == len(downstream_after) and all(
        before.generation_id == after.generation_id
        and before.story_memory_after == after.story_memory_after
        and before.story_memory_status == after.story_memory_status
        for before, after in zip(downstream_before, downstream_after)
    )
    return {
        "proposalOnly": True,
        "insertedMemoryStatesValidated": len(states),
        "insertedStatesWouldBeTrusted": bool(states),
        "storedDownstreamSnapshotsUnchanged": unchanged,
        "downstreamStatusAfterApply": [{
            "generationId": generation.generation_id,
            "currentStatus": generation.story_memory_status,
            "wouldBeStatus": "legacy_unknown" if generation.story_memory_status == "legacy_unknown" else "needs_review",
        } for generation in downstream_before],
    }


def collect_behavioral_checks(checks, plan, context, before_memory, states, operation_snapshot, insert_projection=None):
    """Evaluate only safe structural facts; prose commitments stay explicitly reviewable."""
    after = states[-1] if states else before_memory
    historical_subjects = {
        subject_id for entry in context.get("historicalReferenceContext", [])
        for subject_id in entry.get("subjectIds", [])
    }
    generated = _structured_prose(plan)
    results = []
    for check in checks:
        status, evidence = "review", {
            "expectedConcepts": check.expected_concepts,
            "forbiddenConcepts": check.forbidden_concepts,
            "generatedProse": _highlight_evidence(generated, check.expected_concepts + check.forbidden_concepts),
        }
        memory_text = json.dumps(context.get("storyMemory", {}), ensure_ascii=False).casefold()
        request_text = json.dumps(context, ensure_ascii=False).casefold()
        if check.kind == "request_memory_contains":
            status = "pass" if check.text.casefold() in memory_text else "fail"
            evidence["requestMemoryContains"] = check.text
        elif check.kind == "request_memory_absent":
            status = "pass" if check.text.casefold() not in request_text else "fail"
            evidence["requestAbsent"] = check.text
        elif check.kind == "memory_thread_resolved":
            status = "pass" if check.memory_id not in {entry.id for entry in after.open_threads} else "fail"
            evidence["memoryId"] = check.memory_id
        elif check.kind == "memory_thread_id_preserved":
            status = "pass" if check.memory_id in {entry.id for entry in after.open_threads} else "fail"
            evidence["memoryId"] = check.memory_id
        elif check.kind == "memory_fact_id_preserved":
            status = "pass" if check.memory_id in {entry.id for entry in after.durable_facts} else "fail"
            evidence["memoryId"] = check.memory_id
        elif check.kind == "historical_subject_available":
            status = "pass" if check.subject_id in historical_subjects else "fail"
            evidence["historicalSubjectIds"] = sorted(historical_subjects)
        elif check.kind == "historical_subject_absent":
            status = "pass" if check.subject_id not in historical_subjects else "fail"
            evidence["historicalSubjectIds"] = sorted(historical_subjects)
        elif check.kind == "replan_target_generation_ids_preserved":
            expected = getattr(plan.authority, "target_generation_ids", [])
            actual = [item.existing_generation_id for item in getattr(plan, "items", [])]
            status = "pass" if actual == expected else "fail"
            evidence["targetGenerationIds"] = actual
        elif check.kind == "insert_apply_projection":
            passed = bool(insert_projection and insert_projection["insertedStatesWouldBeTrusted"]
                          and insert_projection["storedDownstreamSnapshotsUnchanged"])
            status = "pass" if passed else "fail"
            evidence["applyProjection"] = insert_projection
        results.append({"id": check.id, "description": check.description, "status": status, "evidence": evidence})
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


def _stress_check(check_id, description, passed, **evidence):
    """Use the normal result vocabulary without turning prose into a score."""
    return {"id": check_id, "description": description, "passed": bool(passed), "evidence": evidence}


def _stress_memory_entry(memory, entry_id, collection):
    return next((entry for entry in getattr(memory, collection) if entry.id == entry_id), None)


def _stress_memory_ids(memory, collection):
    return {entry.id for entry in getattr(memory, collection)}


def _stress_bridge_state(memory, bridge_id):
    """Return semantic bridge-state evidence for one projected memory slot."""
    bridge = _stress_memory_entry(memory, bridge_id, "durable_facts")
    bridge_text = bridge.text.casefold() if bridge else ""
    bridge_entries = [entry.text for entry in memory.durable_facts
                      if re.search(r"\bbridge\b", entry.text, re.IGNORECASE)]
    collapsed = bridge is not None and bool(re.search(r"collapsed|impassable", bridge_text))
    current_intact = any(re.search(r"intact|open", text, re.IGNORECASE) for text in bridge_entries)
    return {
        "stableIdPresent": bridge is not None,
        "collapsedOrImpassable": collapsed,
        "currentIntactOrOpen": current_intact,
        "factText": bridge.text if bridge else None,
        "bridgeFactTexts": bridge_entries,
        "validCollapsedCurrentTruth": collapsed and not current_intact,
    }


def _stress_phrase_absent(value, patterns):
    text = json.dumps(value, ensure_ascii=False).casefold()
    return not any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _stress_milestone_guard(plan, states):
    """Find only clear completed-milestone claims in benchmark authoring/state."""
    slots = getattr(plan, "slots", getattr(plan, "items", []))
    sources = []
    for index, slot in enumerate(slots, start=1):
        sources.extend([
            (f"slot{index}.scenePlan", getattr(slot, "scene_plan", "")),
            (f"slot{index}.intendedOutgoingState", getattr(slot, "intended_outgoing_state", "")),
            (f"slot{index}.memoryPatchAfter", getattr(slot, "memory_patch_after", {}).model_dump(mode="json", by_alias=True)),
        ])
    sources.extend((f"projectedMemoryAfterSlot{index}", state.model_dump(mode="json", by_alias=True))
                   for index, state in enumerate(states, start=1))
    patterns = {
        "crossedOldStoneBridge": r"\b(?:cross(?:ed|es|ing)?|went across|made it across|on the far side of)\s+(?:the )?old stone bridge\b",
        "reachedArchiveDestination": r"\b(?:entered|reached|arrived (?:at|in)|made it to)\s+(?:the )?archive(?: district)?\b",
        "reachedArchivist": r"\b(?:reached|met|found|arrived (?:at|with)|stood before)\s+(?:the )?archivist\b",
        "deliveredLedger": r"(?:\b(?:delivered|gave|handed)\b.{0,80}\bsealed ledger\b|\bsealed ledger\b.{0,80}\b(?:delivered|given|handed)\b)",
    }
    matches = {name: [] for name in patterns}
    for source, value in sources:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        for name, pattern in patterns.items():
            if re.search(pattern, text, re.IGNORECASE):
                matches[name].append(source)
    return {
        "passed": not any(matches.values()),
        "checkedFields": [source for source, _value in sources],
        "patterns": patterns,
        "matches": matches,
    }


def _stress_trust_transitions(before, after):
    old = {generation.generation_id: generation.story_memory_status for generation in before.generations}
    return [{
        "generationId": generation.generation_id,
        "before": old.get(generation.generation_id),
        "after": generation.story_memory_status,
    } for generation in after.generations if old.get(generation.generation_id) != generation.story_memory_status]


def _stress_context_count(context):
    """Report the actual bounded text projection for every operation shape."""
    if "localContext" in context:
        return len(context["localContext"])
    return len(context.get("upstreamHistory", [])) + len(context.get("targetRange", [])) + len(
        context.get("downstreamProtectedBoundary", [])
    )


def _stress_phase_metrics(profile, snapshot, request, model_input, raw_output, client, context, plan):
    metrics = _story_metrics(profile, snapshot, request, model_input, raw_output, getattr(client, "last_usage", None))
    slots = getattr(plan, "slots", getattr(plan, "items", []))
    metrics.update(
        canonicalSourceGenerationCount=len(snapshot.generations),
        localContextGenerationCount=_stress_context_count(context),
        requestedSlotCount=request.requested_count if isinstance(request, (StoryNextRequest, StoryInsertRequest)) else len(slots),
        returnedSlotCount=len(slots),
        picturesTransmitted=model_input.get("imageInputCount", 0),
        requestDurationMs=getattr(client, "last_request_duration_ms", None),
    )
    return metrics


def _stress_identity_ids(memory):
    """Make fixture intent explicit, while retaining opaque canonical IDs."""
    facts = memory.durable_facts
    threads = memory.open_threads
    key = next((entry for entry in facts if "brass key" in entry.text.casefold()), None)
    bridge = next((entry for entry in facts if "old stone bridge" in entry.text.casefold()), None)
    ledger = next((entry for entry in threads if "sealed ledger" in entry.text.casefold()), None)
    if not (key and bridge and ledger):
        raise ValueError("story-longform-stress G3 memory must contain brass-key, bridge and sealed-ledger fixture entries")
    return {"key": key.id, "bridge": bridge.id, "ledger": ledger.id}


def _stress_phase_checks(phase_name, states, identities, plan):
    """Evaluate identity/timing facts against actual production-derived states."""
    checks = []
    after = states[-1]
    key = _stress_memory_entry(after, identities["key"], "durable_facts")
    checks.append(_stress_check(
        "brass-key-id-preserved", "The brass-key durable fact survives with its stable canonical ID.", key is not None,
        memoryId=identities["key"], factText=key.text if key else None,
    ))
    bridge = _stress_memory_entry(after, identities["bridge"], "durable_facts")
    bridge_text = bridge.text.casefold() if bridge else ""
    if phase_name in {"Phase A", "Phase B"}:
        checks.append(_stress_check(
            "bridge-intact-before-collapse", "Before Phase C the bridge fact remains intact/open.",
            bridge is not None and bool(re.search(r"intact|open", bridge_text)) and not bool(re.search(r"collapsed|impassable", bridge_text)),
            memoryId=identities["bridge"], factText=bridge.text if bridge else None,
        ))
        milestone = _stress_milestone_guard(plan, states)
        checks.append(_stress_check(
            "early-milestone-guard", "Early journey and bridge-approach beats do not complete bridge, archive, Archivist, or ledger milestones.",
            milestone.pop("passed"), **milestone,
        ))
    elif phase_name in {"Phase C", "Phase D", "Phase E", "Phase F"}:
        bridge_state = _stress_bridge_state(after, identities["bridge"])
        checks.append(_stress_check(
            "bridge-updated-in-place", "After collapse the same bridge fact ID is collapsed/impassable and not current intact/open truth.",
            bridge_state["validCollapsedCurrentTruth"],
            memoryId=identities["bridge"], **bridge_state,
        ))
        if phase_name == "Phase C":
            slot_states = [_stress_bridge_state(state, identities["bridge"]) for state in states]
            checks.append(_stress_check(
                "bridge-first-beat-transition", "Phase C changes the existing bridge fact on its first projected slot.",
                all(slot["validCollapsedCurrentTruth"] for slot in slot_states),
                memoryId=identities["bridge"], slotStates=slot_states,
            ))

    ledger_id = identities["ledger"]
    present = [ledger_id in _stress_memory_ids(state, "open_threads") for state in states]
    if phase_name in {"Phase A", "Phase B", "Phase C", "Phase D"}:
        checks.append(_stress_check(
            "ledger-remains-open", "The sealed-ledger thread remains open before the delivery batch.", all(present),
            memoryId=ledger_id, presentAfterSlots=present,
        ))
        if phase_name == "Phase D":
            milestone = _stress_milestone_guard(plan, states)
            delivered_sources = milestone["matches"]["deliveredLedger"]
            checks.append(_stress_check(
                "phase-d-delivery-prose-guard", "Phase D authoring and projected state do not claim the sealed ledger was delivered.",
                not delivered_sources, checkedFields=milestone["checkedFields"],
                pattern=milestone["patterns"]["deliveredLedger"], matches=delivered_sources,
            ))
    elif phase_name == "Phase E":
        checks.append(_stress_check(
            "ledger-resolves-only-at-final-beat", "The sealed-ledger thread remains open through G17 and resolves at G18 only.",
            present == [True, True, False], memoryId=ledger_id, presentAfterSlots=present,
        ))
    elif phase_name == "Phase F":
        checks.append(_stress_check(
            "ledger-stays-resolved", "The sealed-ledger thread remains resolved after delivery.", not any(present),
            memoryId=ledger_id, presentAfterSlots=present,
        ))
    return checks


async def run_story_longform_stress(runner, profile, case, case_dir):
    """Run the one expensive, opt-in 20+ Generation Story scenario.

    It is benchmark-only orchestration: each proposal and Apply remains the
    normal production operation and ProjectStore path.  There is intentionally
    no retry, repair, model judge, or hidden semantic correction here.
    """
    settings = runner._story_settings(profile)
    base = runner._planner_skip_result(profile, case, settings=settings)
    base.pop("skip")
    base["settings"].update(repair="never", automaticRetry="never", llmJudge="none")
    base["providerModel"] = {"modelId": profile.model_id, "endpointId": profile.endpoint.id}
    base.update(
        stateful=True,
        estimatedModelCalls=8,
        completedPhases=[],
        failedPhase=None,
        phaseEvidence=[],
        rawModelOutput=[],
    )
    sensitive = [str(runner.source_root)] if runner.source_root else []
    if settings.endpoint.api_key:
        sensitive.append(settings.endpoint.api_key.get_secret_value())
    raw_outputs, captured = [], []
    failed_phase_evidence = None
    started = time.perf_counter()

    try:
        loaded = load_story_workspaces(case, case_dir, runner.source_root)
        sensitive.extend(asset.image.data_url.split(",", 1)[1]
                         for _, workspace in loaded for asset in workspace.assets
                         if asset.image and "," in asset.image.data_url)
        with TemporaryDirectory(prefix="miniconstruct-story-longform-stress-") as temporary:
            sensitive.append(temporary)
            store = DeterministicStoryProjectStore(Path(temporary), case.id)
            snapshot = materialize_story_project(store, case, loaded)
            initial_memory = snapshot.generations[-1].story_memory_after
            identities = _stress_identity_ids(initial_memory)
            base["initialState"] = {
                "canonicalGenerationCount": len(snapshot.generations),
                "generationIds": [generation.generation_id for generation in snapshot.generations],
                "storyMemory": initial_memory.model_dump(mode="json", by_alias=True),
                "trackedMemoryIds": identities,
            }
            base["canonicalProjectSnapshot"] = {
                "projectId": snapshot.manifest.project_id,
                "createdAt": snapshot.manifest.created_at,
                "updatedAt": snapshot.manifest.updated_at,
                "baseSequenceFingerprint": snapshot.fingerprint,
            }
            base["workspace"] = {"schemaVersion": 1, "sources": [
                {"path": generation.workspace_path,
                 "sourceSha256": hashlib.sha256((case_dir / generation.workspace_path).read_bytes()).hexdigest()}
                for generation in case.story.generations
            ]}
            image_mappings = {}
            for generation, (scoped_case, workspace) in zip(snapshot.generations, loaded):
                for entry in _reference_inputs(scoped_case, workspace, case_dir, runner.source_root):
                    base["referenceInputs"].append({"sourceGenerationId": generation.generation_id,
                                                     "sourceGeneration": generation.ordinal, **entry})
                for digest, mappings in _workspace_image_mappings(scoped_case, workspace, h3_derivative=settings.story_vision_enabled).items():
                    image_mappings.setdefault(digest, []).extend({"sourceGenerationId": generation.generation_id, **mapping}
                                                                  for mapping in mappings)

            factory = SingleAttemptClient if runner.client_factory is OpenAICompatibleClient else runner.client_factory

            def recording_factory(llm):
                client = RecordingClient(factory(llm), raw_outputs, llm)
                captured.append(client)
                return client

            async def apply_phase(name, operation, request, operation_snapshot, *, optional_review=False):
                nonlocal failed_phase_evidence
                before = store.read_story_snapshot(snapshot.manifest.project_id)
                raw_start = len(raw_outputs)
                capture_start = len(captured)
                try:
                    if operation == "next":
                        plan = await plan_next(store, before.manifest.project_id, request, recording_factory)
                        states = next_memory_states(operation_snapshot, plan)
                        store.apply_story_plan_next(before.manifest.project_id, plan)
                    elif operation == "replan":
                        plan = await plan_replan(store, before.manifest.project_id, request, recording_factory)
                        states = replan_memory_states(operation_snapshot, plan)
                        store.apply_story_plan_replan(before.manifest.project_id, plan)
                    else:
                        plan = await plan_insert(store, before.manifest.project_id, request, recording_factory)
                        states = next_memory_states(operation_snapshot, plan)
                        store.apply_story_plan_insert(before.manifest.project_id, plan)
                except (LLMBackendError, ProjectStoreError, ValueError) as exc:
                    try:
                        client = require_single_recorded_request(captured[capture_start:])
                    except RecordingCaptureError:
                        client = None
                    payload = getattr(client, "payload", None)
                    effective_request = _effective_request_parameters(payload) if payload else None
                    failed_phase_evidence = {
                        "phase": name,
                        "operation": operation,
                        "instruction": request.instruction,
                        "canonicalGenerationCountBefore": len(before.generations),
                        "modelInput": _messages_provenance(payload["messages"], image_mappings) if payload else None,
                        "effectiveRequest": effective_request,
                        "requestedReasoningMode": getattr(
                            getattr(getattr(client, "settings", None), "reasoning_mode", None), "value", None,
                        ),
                        "seed": _seed_provenance(
                            requested_seed=getattr(getattr(client, "settings", None), "seed", None),
                            mode=getattr(getattr(getattr(client, "settings", None), "seed_mode", None), "value", "backend_default"),
                            effective_seed=getattr(client, "effective_seed", None),
                            seed_unsupported=bool(getattr(client, "seed_unsupported", False)),
                            known_unsupported=bool(getattr(client, "seed_known_unsupported", False)),
                            final_request_included_seed=bool(payload and "seed" in payload),
                        ),
                        "rawModelOutput": raw_outputs[-1] if len(raw_outputs) > raw_start else None,
                        "provider": {
                            "usage": getattr(client, "last_usage", None),
                            "requestDurationMs": getattr(client, "last_request_duration_ms", None),
                            "compatibilityFallback": bool(getattr(client, "compatibility_fallback", False)),
                        },
                        "error": {
                            "category": getattr(exc, "category", "production_validation"),
                            "message": str(exc),
                        },
                    }
                    raise
                after = store.read_story_snapshot(before.manifest.project_id)
                client = require_single_recorded_request(captured[capture_start:])
                model_input = _messages_provenance(client.payload["messages"], image_mappings)
                context = _request_context(client.payload)
                raw_output = raw_outputs[-1] if len(raw_outputs) > raw_start else None
                phase = {
                    "phase": name,
                    "operation": operation,
                    "instruction": request.instruction,
                    "canonicalGenerationCountBefore": len(before.generations),
                    "canonicalGenerationCountAfter": len(after.generations),
                    "structuredStoryPlannerOutput": plan.model_dump(mode="json", by_alias=True),
                    "rawModelOutput": raw_output,
                    "modelInput": model_input,
                    "effectiveRequest": _effective_request_parameters(client.payload),
                    "seed": _seed_provenance(
                        requested_seed=getattr(client.settings, "seed", None),
                        mode=getattr(getattr(client.settings, "seed_mode", None), "value", "backend_default"),
                        effective_seed=getattr(client, "effective_seed", None),
                        seed_unsupported=bool(getattr(client, "seed_unsupported", False)),
                        known_unsupported=bool(getattr(client, "seed_known_unsupported", False)),
                        final_request_included_seed=bool("seed" in client.payload),
                    ),
                    "provider": {
                        "usage": getattr(client, "last_usage", None),
                        "requestDurationMs": client.last_request_duration_ms,
                        "compatibilityFallback": bool(getattr(client, "compatibility_fallback", False)),
                    },
                    "metrics": _stress_phase_metrics(profile, before, request, model_input, raw_output, client, context, plan),
                    "storyMemoryBefore": operation_snapshot.context["storyMemory"],
                    "projectedMemoryAfterEachGeneration": [state.model_dump(mode="json", by_alias=True) for state in states],
                    "memoryHygieneMetrics": memory_hygiene_metrics(
                        StoryContinuityMemory.model_validate(operation_snapshot.context["storyMemory"]), states,
                    ),
                    "referenceMetrics": reference_metrics(operation_snapshot, before, plan, model_input, context),
                    "trustTransitions": _stress_trust_transitions(before, after),
                    "postApplyGenerationIds": [generation.generation_id for generation in after.generations],
                }
                if operation == "next":
                    affected = after.generations[-len(states):]
                elif operation == "replan":
                    by_id = {generation.generation_id: generation for generation in after.generations}
                    affected = [by_id[item.existing_generation_id] for item in plan.items]
                else:
                    start = next(index for index, generation in enumerate(after.generations)
                                 if generation.generation_id == plan.authority.upstream_generation_id) + 1
                    affected = after.generations[start:start + len(states)]
                phase["postApplyStoryMemoryAfterEachGeneration"] = [
                    state.story_memory_after.model_dump(mode="json", by_alias=True) for state in affected
                ]
                return plan, states, before, after, context, phase

            captured_targets = {}
            phase_names = ["Phase A", "Phase B", "Phase C", "Phase D", "Phase E", "Phase F"]
            for name, phase_fixture in zip(phase_names, case.story.stress.plan_next):
                before = store.read_story_snapshot(snapshot.manifest.project_id)
                request = StoryNextRequest(anchorGenerationId=before.generations[-1].generation_id,
                                           requestedCount=phase_fixture.requested_count,
                                           instruction=phase_fixture.instruction, llm=settings)
                operation_snapshot = store.read_story_operation_snapshot(before.manifest.project_id,
                                                                          request.anchor_generation_id, request.instruction)
                base["failedPhase"] = name
                _plan, states, _before, after, _context, evidence = await apply_phase(
                    name, "next", request, operation_snapshot, optional_review=name == "Phase F",
                )
                evidence["machineChecks"] = _stress_phase_checks(name, states, identities, _plan)
                if name == "Phase B":
                    captured_targets["insertUpstream"] = after.generations[case.story.stress.insert_upstream_generation - 1].generation_id
                    captured_targets["insertDownstream"] = after.generations[case.story.stress.insert_upstream_generation].generation_id
                if name == "Phase C":
                    captured_targets["replanStart"] = after.generations[case.story.stress.replan_start_generation - 1].generation_id
                    captured_targets["replanEnd"] = after.generations[case.story.stress.replan_end_generation - 1].generation_id
                if name == "Phase E":
                    evidence["carolLatePreReturn"] = {
                        "historicalCandidateOffered": any("carol" in item.get("subjectIds", [])
                                                          for item in evidence["referenceMetrics"]["historicalReferenceCandidates"]),
                        "pixelsTransmitted": evidence["metrics"]["picturesTransmitted"],
                    }
                if name == "Phase F":
                    recommendations = evidence["referenceMetrics"]["recommendedReferencePairs"]
                    carol_candidates = [item for item in evidence["referenceMetrics"]["historicalReferenceCandidates"]
                                        if "carol" in item.get("subjectIds", [])]
                    carol_pairs = {(item["sourceGenerationId"], item["assetId"]) for item in carol_candidates}
                    evidence["carolReturnReference"] = {
                        "historicalCandidateOffered": bool(carol_candidates),
                        "pixelsTransmitted": evidence["metrics"]["picturesTransmitted"],
                        "recommended": any((item["sourceGenerationId"], item["assetId"]) in carol_pairs for item in recommendations),
                        "candidatePairs": [{"sourceGenerationId": source, "assetId": asset} for source, asset in sorted(carol_pairs)],
                    }
                    evidence["machineChecks"].append(_stress_check(
                        "carol-historical-identity-offered", "Carol's historical identity candidate is available when her return makes her relevant.",
                        bool(carol_candidates), candidateCount=len(carol_candidates),
                    ))
                passed = all(check["passed"] for check in evidence["machineChecks"])
                evidence["status"] = "fail" if not passed else "review" if name == "Phase F" else "pass"
                base["phaseEvidence"].append(evidence)
                base["completedPhases"].append(name)
                base["failedPhase"] = None

            pre_replan = store.read_story_snapshot(snapshot.manifest.project_id)
            if len(pre_replan.generations) != 21:
                raise ValueError("story-longform-stress must reach exactly 21 Generations before historical editing")
            pre_replan_ids = [generation.generation_id for generation in pre_replan.generations]
            pre_replan_status = {generation.generation_id: generation.story_memory_status
                                 for generation in pre_replan.generations}
            target_ids = [captured_targets["replanStart"], captured_targets["replanEnd"]]
            target_before = {generation.generation_id: {
                "ordinal": generation.ordinal,
                "assetIds": [asset.id for asset in generation.workspace.assets],
                "memoryStatus": generation.story_memory_status,
            } for generation in pre_replan.generations if generation.generation_id in target_ids}
            request = StoryReplanRequest(startGenerationId=target_ids[0], endGenerationId=target_ids[1],
                                         instruction=case.story.stress.replan_instruction, llm=settings)
            operation_snapshot = store.read_story_replan_snapshot(pre_replan.manifest.project_id, target_ids[0], target_ids[1],
                                                                   request.instruction)
            base["failedPhase"] = "Replan"
            plan, states, _before, after_replan, context, evidence = await apply_phase("Replan", "replan", request, operation_snapshot)
            target_after = {generation.generation_id: generation for generation in after_replan.generations
                            if generation.generation_id in target_ids}
            future_patterns = [r"sealed ledger.{0,80}\b(?:delivered|deliver\w+)\b", r"\b(?:delivered|deliver\w+)\b.{0,80}sealed ledger", r"carol.{0,80}\b(?:returns|returned)\b"]
            historical_output = {"context": context, "proposal": plan.model_dump(mode="json", by_alias=True),
                                 "raw": evidence["rawModelOutput"] or ""}
            evidence["machineChecks"] = [
                _stress_check("replan-target-ids-preserved", "Replan preserves the captured stable target Generation IDs and order.",
                              [item.existing_generation_id for item in plan.items] == target_ids,
                              expectedTargetGenerationIds=target_ids, actualTargetGenerationIds=[item.existing_generation_id for item in plan.items]),
                _stress_check("replan-target-assets-preserved", "Replan preserves target assets and target snapshots become trusted.",
                              all([asset.id for asset in target_after[identifier].workspace.assets] == target_before[identifier]["assetIds"]
                                  and target_after[identifier].story_memory_status == "trusted" for identifier in target_ids),
                              targetBefore=target_before,
                              targetAfter={identifier: {"ordinal": target_after[identifier].ordinal,
                                                        "assetIds": [asset.id for asset in target_after[identifier].workspace.assets],
                                                        "memoryStatus": target_after[identifier].story_memory_status}
                                           for identifier in target_ids}),
                _stress_check("replan-trust-transitions", "Replan preserves order and applies the expected trusted/needs_review transitions.",
                              all(status == "trusted" for status in pre_replan_status.values())
                              and [generation.generation_id for generation in after_replan.generations] == pre_replan_ids
                              and all(after_replan.generations[index].story_memory_status == "trusted" for index in range(10))
                              and all(after_replan.generations[index].story_memory_status == "trusted"
                                      for index in (10, 11))
                              and all(after_replan.generations[index].story_memory_status == "needs_review"
                                      for index in range(12, 21)),
                              beforeStatuses=pre_replan_status,
                              afterStatuses={generation.generation_id: generation.story_memory_status
                                             for generation in after_replan.generations},
                              targetGenerationIds=target_ids),
                _stress_check("replan-has-no-future-state-leakage", "Historical Replan excludes later delivery and Carol-return truth.",
                              _stress_phrase_absent(historical_output, future_patterns), checkedPatterns=future_patterns),
                _stress_check("replan-bridge-is-historical-truth", "Historical Replan receives the already-collapsed bridge under the stable fact ID.",
                              (bridge := _stress_memory_entry(states[-1], identities["bridge"], "durable_facts")) is not None
                              and bool(re.search(r"collapsed|impassable", bridge.text, re.IGNORECASE)),
                              memoryId=identities["bridge"], factText=bridge.text if bridge else None),
                _stress_check("replan-ledger-still-open", "Historical Replan retains the pre-delivery ledger thread.",
                              identities["ledger"] in _stress_memory_ids(states[-1], "open_threads"), memoryId=identities["ledger"]),
                _stress_check("replan-brass-key-id-preserved", "Historical Replan retains the brass-key fact with its stable ID.",
                              identities["key"] in _stress_memory_ids(states[-1], "durable_facts"), memoryId=identities["key"]),
            ]
            evidence["status"] = "pass" if all(check["passed"] for check in evidence["machineChecks"]) else "fail"
            base["phaseEvidence"].append(evidence)
            base["completedPhases"].append("Replan")
            base["failedPhase"] = None

            pre_insert = store.read_story_snapshot(snapshot.manifest.project_id)
            upstream_id = captured_targets["insertUpstream"]
            downstream_id = captured_targets["insertDownstream"]
            old_memories = {generation.generation_id: generation.story_memory_after for generation in pre_insert.generations}
            pre_insert_ids = [generation.generation_id for generation in pre_insert.generations]
            pre_insert_status = {generation.generation_id: generation.story_memory_status
                                 for generation in pre_insert.generations}
            request = StoryInsertRequest(upstreamGenerationId=upstream_id, requestedCount=1,
                                         instruction=case.story.stress.insert_instruction, llm=settings)
            operation_snapshot = store.read_story_insert_snapshot(pre_insert.manifest.project_id, upstream_id, request.instruction)
            base["failedPhase"] = "Insert"
            plan, states, _before, after_insert, context, evidence = await apply_phase("Insert", "insert", request, operation_snapshot)
            original_ids = [generation.generation_id for generation in pre_insert.generations]
            after_old = [generation for generation in after_insert.generations if generation.generation_id in old_memories]
            upstream_memory = StoryContinuityMemory.model_validate(context["storyMemory"])
            historical_output = {"context": context, "proposal": plan.model_dump(mode="json", by_alias=True),
                                 "raw": evidence["rawModelOutput"] or ""}
            insert_future_patterns = future_patterns + [
                r"old stone bridge.{0,80}(?:collapsed|impassable)",
                r"(?:collapsed|impassable).{0,80}old stone bridge",
            ]
            inserted_ids = [generation.generation_id for generation in after_insert.generations
                            if generation.generation_id not in pre_insert_ids]
            after_insert_by_id = {generation.generation_id: generation for generation in after_insert.generations}
            expected_downstream_status = {
                generation_id: ("legacy_unknown" if status == "legacy_unknown" else "needs_review")
                for generation_id, status in pre_insert_status.items()
                if generation_id not in pre_insert_ids[:7]
            }
            evidence["machineChecks"] = [
                _stress_check("insert-boundary-uses-captured-ids", "Insert uses the captured G7 → G8 stable boundary after Replan.",
                              plan.authority.upstream_generation_id == upstream_id and plan.authority.downstream_generation_id == downstream_id,
                              upstreamGenerationId=plan.authority.upstream_generation_id, downstreamGenerationId=plan.authority.downstream_generation_id),
                _stress_check("insert-has-no-future-state-leakage", "Historical Insert excludes later collapse, delivery and Carol-return truth.",
                              _stress_phrase_absent(historical_output, insert_future_patterns),
                              checkedPatterns=insert_future_patterns),
                _stress_check("insert-starts-from-upstream-memory", "Insert starts from G7-owned intact-bridge and unresolved-ledger memory only.",
                              (bridge := _stress_memory_entry(upstream_memory, identities["bridge"], "durable_facts")) is not None
                              and bool(re.search(r"intact|open", bridge.text, re.IGNORECASE))
                              and identities["ledger"] in _stress_memory_ids(upstream_memory, "open_threads"),
                              bridgeFactText=bridge.text if bridge else None, ledgerThreadId=identities["ledger"]),
                _stress_check("insert-brass-key-id-preserved", "Historical Insert retains the brass-key fact with its stable ID.",
                              identities["key"] in _stress_memory_ids(upstream_memory, "durable_facts"), memoryId=identities["key"]),
                _stress_check("insert-preserves-downstream-snapshots", "Insert preserves all old Generation IDs/order and their stored memory snapshots.",
                              [generation.generation_id for generation in after_old] == original_ids
                              and all(generation.story_memory_after == old_memories[generation.generation_id] for generation in after_old),
                              originalGenerationIds=original_ids),
                _stress_check("insert-trust-transitions", "Insert keeps G1–G7 trusted, trusts the new Generation, and reviews the old suffix without promoting it.",
                              inserted_ids and after_insert_by_id[inserted_ids[0]].story_memory_status == "trusted"
                              and all(after_insert_by_id[generation_id].story_memory_status == "trusted"
                                      for generation_id in pre_insert_ids[:7])
                              and all(after_insert_by_id[generation_id].story_memory_status == expected_downstream_status[generation_id]
                                      for generation_id in expected_downstream_status)
                              and all(after_insert_by_id[generation_id].story_memory_status != "trusted"
                                      for generation_id in pre_insert_ids[7:]),
                              beforeStatuses=pre_insert_status,
                              afterStatuses={generation.generation_id: generation.story_memory_status
                                             for generation in after_insert.generations},
                              insertedGenerationIds=inserted_ids),
                _stress_check("insert-adds-one-generation", "Insert adds exactly one Generation for a final canonical count of 22.",
                              len(after_insert.generations) == 22, finalCanonicalGenerationCount=len(after_insert.generations)),
            ]
            evidence["status"] = "pass" if all(check["passed"] for check in evidence["machineChecks"]) else "fail"
            base["phaseEvidence"].append(evidence)
            base["completedPhases"].append("Insert")
            base["failedPhase"] = None
            final = after_insert
            all_checks = [check for phase in base["phaseEvidence"] for check in phase["machineChecks"]]
            hygiene = [phase["memoryHygieneMetrics"] for phase in base["phaseEvidence"]]
            final_memory = final.generations[-1].story_memory_after
            base.update(
                status="completed",
                structuralValid=True,
                finalCanonicalGenerationCount=len(final.generations),
                finalCanonicalGenerationIds=[generation.generation_id for generation in final.generations],
                finalStoryMemory=final_memory.model_dump(mode="json", by_alias=True),
                capturedHistoricalTargets=captured_targets,
                machineChecks={"checks": all_checks, "passed": all(check["passed"] for check in all_checks)},
                expectations={"checks": all_checks, "passed": all(check["passed"] for check in all_checks)},
                memoryHygieneAggregates={
                    "peakDurableFacts": max(metric["after"]["factCount"] for metric in hygiene),
                    "peakOpenThreads": max(metric["after"]["threadCount"] for metric in hygiene),
                    "totalNewFactIds": len({item for metric in hygiene for item in metric["factIdsNew"]}),
                    "totalNewThreadIds": len({item for metric in hygiene for item in metric["threadIdsNew"]}),
                    "factsSurvivingToFinalState": len(final_memory.durable_facts),
                    "threadsSurvivingToFinalState": len(final_memory.open_threads),
                    "suspiciousDuplicateCount": sum(metric["suspiciousDuplicateCount"] for metric in hygiene),
                },
            )
    except (LLMBackendError, ProjectStoreError, ValueError) as exc:
        base.update(
            status="error", structuralValid=False,
            failedPhase=base["failedPhase"] or ("setup" if not base["completedPhases"] else "next pending phase"),
            error={
                "type": "provider_error" if isinstance(exc, LLMBackendError) else "production_rejection" if isinstance(exc, ProjectStoreError) else "structured_output_or_fixture_validation",
                "message": str(exc), "statusCode": getattr(exc, "status_code", None),
                "upstreamStatus": getattr(exc, "upstream_status", None),
            },
        )
        if failed_phase_evidence is not None:
            base["failedPhaseEvidence"] = failed_phase_evidence
    finally:
        base["latencyMs"] = round((time.perf_counter() - started) * 1000, 2)
        base["lastCanonicalGenerationCount"] = (base.get("finalCanonicalGenerationCount")
                                                   or (base["phaseEvidence"][-1]["canonicalGenerationCountAfter"]
                                                       if base["phaseEvidence"] else len(case.story.generations)))
        base["stressSummary"] = {
            "phases": [{
                "phase": phase["phase"], "status": phase.get("status", "review"), "operation": phase["operation"],
                "canonicalSourceGenerationCount": phase["metrics"]["canonicalSourceGenerationCount"],
                "localContextGenerationCount": phase["metrics"]["localContextGenerationCount"],
                "requestedSlots": phase["metrics"]["requestedSlotCount"],
                "returnedSlots": phase["metrics"]["returnedSlotCount"],
                "pictures": phase["metrics"]["picturesTransmitted"],
                "promptTokens": phase["metrics"]["providerPromptTokens"],
                "totalTokens": phase["metrics"]["providerTotalTokens"],
                "promptContextUtilization": phase["metrics"]["promptContextUtilization"],
                "totalContextUtilization": phase["metrics"]["totalContextUtilization"],
                "requestDurationMs": phase["metrics"]["requestDurationMs"],
            } for phase in base["phaseEvidence"]],
            "completedPhases": base["completedPhases"], "failedPhase": base["failedPhase"],
            "finalCanonicalGenerationCount": base.get("finalCanonicalGenerationCount"),
        }
        base["rawModelOutput"] = raw_outputs
    return _sanitize(base, sensitive)


async def run_story_case(runner, profile, case, case_dir):
    settings = runner._story_settings(profile)
    base = runner._planner_skip_result(profile, case, settings=settings)
    base.pop("skip")
    base["settings"].update(repair="never", automaticRetry="never", llmJudge="none")
    base["providerModel"] = {"modelId": profile.model_id, "endpointId": profile.endpoint.id}
    sensitive = [str(runner.source_root)] if runner.source_root else []
    if settings.endpoint.api_key:
        sensitive.append(settings.endpoint.api_key.get_secret_value())
    started = time.perf_counter()
    captured, raw_outputs = [], []
    recorded_client = None
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
                for digest, mappings in _workspace_image_mappings(scoped_case, workspace, h3_derivative=settings.story_vision_enabled).items():
                    image_mappings.setdefault(digest, []).extend({"sourceGenerationId": g.generation_id, **m} for m in mappings)

            factory = SingleAttemptClient if runner.client_factory is OpenAICompatibleClient else runner.client_factory

            def recording_factory(llm):
                client = RecordingClient(factory(llm), raw_outputs, llm)
                captured.append(client)
                return client

            started = time.perf_counter()
            operation_snapshot = None
            memory_states = []
            insert_projection = None
            if case.story.next:
                request = StoryNextRequest(**case.story.next.model_dump(),
                    anchorGenerationId=snapshot.generations[-1].generation_id, llm=settings)
                operation_snapshot = store.read_story_operation_snapshot(
                    snapshot.manifest.project_id, request.anchor_generation_id, request.instruction,
                )
                item_counter = 0
                def next_id():
                    nonlocal item_counter
                    item_counter += 1
                    return str(uuid5(UUID(snapshot.manifest.project_id), f"story-next:{item_counter}"))
                plan = await plan_next(store, snapshot.manifest.project_id, request, recording_factory,
                                       item_id_factory=next_id)
            elif case.story.insert:
                insert = case.story.insert
                request = StoryInsertRequest(
                    upstreamGenerationId=snapshot.generations[insert.upstream_generation - 1].generation_id,
                    requestedCount=insert.requested_count, instruction=insert.instruction, llm=settings,
                )
                operation_snapshot = store.read_story_insert_snapshot(
                    snapshot.manifest.project_id, request.upstream_generation_id, request.instruction,
                )
                item_counter = 0
                def insert_id():
                    nonlocal item_counter
                    item_counter += 1
                    return str(uuid5(UUID(snapshot.manifest.project_id), f"story-insert:{item_counter}"))
                plan = await plan_insert(store, snapshot.manifest.project_id, request, recording_factory,
                                         item_id_factory=insert_id)
            elif case.story.replan:
                replan = case.story.replan
                request = StoryReplanRequest(
                    startGenerationId=snapshot.generations[replan.start_generation - 1].generation_id,
                    endGenerationId=snapshot.generations[replan.end_generation - 1].generation_id,
                    instruction=replan.instruction, llm=settings,
                )
                operation_snapshot = store.read_story_replan_snapshot(
                    snapshot.manifest.project_id, request.start_generation_id, request.end_generation_id,
                    request.instruction,
                )
                item_counter = 0
                def replan_id():
                    nonlocal item_counter
                    item_counter += 1
                    return str(uuid5(UUID(snapshot.manifest.project_id), f"story-replan:{item_counter}"))
                plan = await plan_replan(store, snapshot.manifest.project_id, request, recording_factory,
                                         item_id_factory=replan_id)
            elif case.story.initial_range:
                initial_range = case.story.initial_range
                request = StoryInitialRangeRequest(requestedTotalCount=initial_range.requested_total_count, llm=settings)
                item_counter = 0
                def initial_range_id():
                    nonlocal item_counter
                    item_counter += 1
                    return str(uuid5(UUID(snapshot.manifest.project_id), f"story-initial-range:{item_counter}"))
                plan = await build_initial_range(
                    store, snapshot.manifest.project_id, request, recording_factory,
                    item_id_factory=initial_range_id,
                )
            elif case.story.build:
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
            recorded_client = require_single_recorded_request(captured)
            model_input = _messages_provenance(recorded_client.payload["messages"], image_mappings)
            context = _request_context(recorded_client.payload)
            check_snapshot = operation_snapshot or snapshot
            machine_checks = evaluate_story_checks(
                case.expectations.checks, plan, check_snapshot, request, model_input, raw_outputs[-1],
            )
            machine_checks += evaluate_expectations(case.expectations, plan.model_dump_json(by_alias=True), set())
            base.update(
                status="completed", structuralValid=True,
                structuredStoryPlannerOutput=plan.model_dump(mode="json", by_alias=True),
                machineChecks={"checks": machine_checks, "passed": all(c["passed"] for c in machine_checks)},
            )
            effective_items = getattr(plan, "slots", getattr(plan, "items", []))
            start_ordinal = (
                plan.authority.anchor_ordinal + 1 if isinstance(request, StoryNextRequest)
                else plan.authority.upstream_ordinal + 1 if isinstance(request, StoryInsertRequest)
                else plan.authority.start_ordinal if isinstance(request, StoryReplanRequest)
                else 1
            )
            usage = getattr(recorded_client, "last_usage", None)
            base["storyMetrics"] = _story_metrics(profile, snapshot, request, model_input, raw_outputs[-1], usage)
            slot_operation = isinstance(request, (StoryNextRequest, StoryInsertRequest))
            base["storyMetrics"]["responseItemCount"] = len(plan.slots) if slot_operation else len(plan.items)
            base["storyMetrics"]["responseSlotCount"] = len(plan.slots) if slot_operation else None
            base["storyMetrics"]["temporarySlotCount"] = len(plan.authority.slot_ids) if slot_operation else None
            # responseItemCount historically counts the final API draft, not raw
            # model items. Preserve that meaning for existing baseline comparisons.
            base["storyMetrics"]["responsePatchCount"] = (
                len(service.normalize_story_model_json(raw_outputs[-1])["patches"])
                if isinstance(request, StoryRevisionRequest) else None
            )
            if isinstance(request, StoryInitialRangeRequest):
                before_memory = StoryContinuityMemory()
                memory_states = initial_range_memory_states(snapshot, plan)
                base["storyMetrics"].update(
                    sourceCanonicalGenerationCount=len(snapshot.generations),
                    requestedTotalRange=request.requested_total_count,
                    returnedItemCount=len(plan.items),
                    newGenerationCount=len(plan.items) - 1,
                    picturesTransmitted=model_input.get("imageInputCount", 0),
                    requestDurationMs=getattr(recorded_client, "last_request_duration_ms", None),
                )
                base["storyMemoryBefore"] = before_memory.model_dump(mode="json", by_alias=True)
                base["projectedStoryMemoryAfter"] = [{
                    "ordinal": index, "existingGenerationId": item.existing_generation_id,
                    "storyMemory": state.model_dump(mode="json", by_alias=True),
                } for index, (item, state) in enumerate(zip(plan.items, memory_states), start=1)]
                base["memoryHygieneMetrics"] = memory_hygiene_metrics(before_memory, memory_states)
                base["referenceMetrics"] = {
                    "allowedReferencePairs": [pair.model_dump(mode="json", by_alias=True)
                                               for pair in plan.authority.allowed_reference_pairs],
                    "recommendedReferencePairCount": sum(len(item.reference_recommendations) for item in plan.items),
                }
            if isinstance(request, StoryNextRequest):
                user_content = recorded_client.payload["messages"][1]["content"]
                context_text = user_content[0]["text"] if isinstance(user_content, list) else user_content
                bounded_context = json.loads(context_text)
                historical = bounded_context.get("historicalReferenceContext", [])
                base["storyMetrics"].update(
                    historicalReferenceCount=len(historical),
                    allowedHistoricalReferencePairCount=len({
                        (item.get("sourceGenerationId"), item.get("asset", {}).get("id"))
                        for item in historical if isinstance(item, dict)
                    }),
                    transmittedHistoricalPictureCount=sum(1 for item in (user_content if isinstance(user_content, list) else [])
                                                          if item.get("type") == "text" and str(item.get("text", "")).startswith("historical visual identity input")),
                )
            if operation_snapshot is not None:
                before_memory = StoryContinuityMemory.model_validate(context["storyMemory"])
                memory_states = (next_memory_states(operation_snapshot, plan)
                                 if slot_operation else replan_memory_states(operation_snapshot, plan))
                base["storyMemoryBefore"] = before_memory.model_dump(mode="json", by_alias=True)
                base["projectedStoryMemoryAfter"] = [{
                    "slot": index,
                    **({"targetGenerationId": item.existing_generation_id}
                       if isinstance(request, StoryReplanRequest) else {}),
                    "storyMemory": state.model_dump(mode="json", by_alias=True),
                } for index, (item, state) in enumerate(
                    zip(getattr(plan, "slots", getattr(plan, "items", [])), memory_states), start=1
                )]
                base["memoryHygieneMetrics"] = memory_hygiene_metrics(before_memory, memory_states)
                base["referenceMetrics"] = reference_metrics(operation_snapshot, snapshot, plan, model_input, context)
                if isinstance(request, StoryInsertRequest):
                    insert_projection = _insert_apply_projection(
                        snapshot, case.story.insert, memory_states,
                        store.read_story_snapshot(snapshot.manifest.project_id),
                    )
                    base["insertApplyProjection"] = insert_projection
                base["behavioralChecks"] = collect_behavioral_checks(
                    case.behavioral_checks, plan, context, before_memory, memory_states, operation_snapshot,
                    insert_projection,
                )
            base["expectations"] = base["machineChecks"]
    except RecordingCaptureError as exc:
        base.update(status="error", structuralValid=False, error={
            "type": "benchmark_instrumentation", "message": str(exc),
            "statusCode": None, "upstreamStatus": None,
        })
    except (LLMBackendError, ProjectStoreError, ValueError, OSError) as exc:
        base.update(status="error", structuralValid=False, error={
            "type": "provider_or_validation" if isinstance(exc, LLMBackendError) else "fixture_or_validation",
            "message": str(exc), "statusCode": getattr(exc, "status_code", None),
            "upstreamStatus": getattr(exc, "upstream_status", None),
        })
    finally:
        elapsed = round((time.perf_counter() - started) * 1000, 2)
        base.update(latencyMs=elapsed, rawModelOutput=raw_outputs[-1] if raw_outputs else None)
        if recorded_client is None:
            try:
                recorded_client = require_single_recorded_request(captured)
            except RecordingCaptureError:
                recorded_client = None
        if recorded_client is not None:
            client = recorded_client
            if client.payload:
                base["modelInput"] = _messages_provenance(client.payload["messages"], image_mappings)
                base["effectiveRequest"] = _effective_request_parameters(client.payload)
            settings = getattr(client, "settings", None)
            requested_seed = getattr(settings, "seed", None)
            seed_mode = getattr(getattr(settings, "seed_mode", None), "value", "backend_default")
            known_unsupported = bool(getattr(client, "seed_known_unsupported", False))
            seed_unsupported = bool(getattr(client, "seed_unsupported", False))
            effective_seed = getattr(client, "effective_seed", None)
            final_request_included_seed = bool(client.payload and "seed" in client.payload)
            base["seed"] = _seed_provenance(
                requested_seed=requested_seed, mode=seed_mode, effective_seed=effective_seed,
                seed_unsupported=seed_unsupported, known_unsupported=known_unsupported,
                final_request_included_seed=final_request_included_seed,
            )
            base["provider"] = {"usage": getattr(client, "last_usage", None),
                                "requestDurationMs": client.last_request_duration_ms if client.last_request_duration_ms is not None else elapsed,
                                "compatibilityFallback": bool(getattr(client, "compatibility_fallback", False))}
    return _sanitize(base, sensitive)
