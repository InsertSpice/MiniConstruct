"""Opt-in, human-reviewed Story Scene Plan directability benchmark."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from miniconstruct.benchmark.runner import BenchmarkProfile, load_profiles
from miniconstruct.benchmark.temporal_directing import message_metrics
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.models.api import GenerationRequest, SeedMode
from miniconstruct.models.story_planning import StoryBuildRequest
from miniconstruct.models.workspace import Workspace, validate_authoring_workspace
from miniconstruct.story_planning import (
    _call, add_incomplete_build_warnings, assemble_story_prompt, initial_story_draft, project_build,
)


SUITE_NAME = "story-scene-directability"
SCHEMA_VERSION = 1
DIRECTABILITY_POLICY = """BENCHMARK-ONLY SCENE-PLAN DIRECTABILITY POLICY
A Scene Plan is a compact account of dramatic intent and observable development during this Generation, not merely a plot synopsis or static end-state. Where Story authority supports it, prefer concrete progression (for example, begins walking and scans several storefronts) over a static pose that only says a character is ready to act. Preserve useful action development, attention changes, interactions, reactions, spatial relationships, meaningful pauses, state changes, and beginning-to-outgoing-state progression.
Stay within this Generation's Story scope. Do not invent major events or consume later Generation events. Do not force motion: stillness, pauses, and quiet reactions remain valid when intentional. Keep the Scene Plan compact and useful as planning authority, not execution prose. Do not write H3 syntax, timestamps, subject/picture reference syntax, shot construction, camera instructions unless separately requested by the existing Planner contract, detailed soundscape, render timing, or animation micromanagement. Improve directability, not verbosity."""
POLICY_SECTION = "\n\n## Benchmark-only Scene Plan directability policy\n\n" + DIRECTABILITY_POLICY

SCENE_REVIEW = {
    "storyFidelity": "Does the Scene Plan stay faithful to Story intent?",
    "generationBoundary": "Does it respect this Generation's boundary and avoid taking later events?",
    "directability": "Does it tell us what visibly develops rather than merely naming a state?",
    "temporalProgression": "Is there a useful beginning-to-development-to-outgoing progression where supported?",
    "observableDevelopment": "Are attention, interaction, reaction, posture, and spatial relationships clear where relevant?",
    "appropriateStillness": "Does it preserve intentional stillness without adding needless motion?",
    "inventionDiscipline": "Does it avoid unsupported major events?",
    "downstreamUsefulness": "Does it give production H3 useful dramatic and action authority?",
    "abstractionQuality": "Does it remain planning authority rather than becoming shot-by-shot execution prose?",
    "verbosityEfficiency": "Are the words efficient for the information they carry?",
    "reviewerNote": "Free-form reviewer note",
    "overallPreference": "Control, Experimental, or Tie?",
}
H3_REVIEW = {
    "structuralValidity": "Is the H3 output structurally valid?",
    "storyFidelity": "Does it remain faithful to Story and the current Generation?",
    "temporalInformationDensity": "Does it describe meaningful change through time?",
    "staticPaddingBurden": "Does it spend excessive time or text on static setup or repetition?",
    "actionProgression": "Do actions develop clearly rather than stopping at one vague verb?",
    "spatialOrientationClarity": "Are orientation and spatial relationships clear where useful?",
    "cameraUsefulness": "Does camera direction support the action?",
    "appropriateStillness": "Does it preserve quiet and still scenes without pointless motion?",
    "inventionDiscipline": "Does it avoid premature or unsupported events?",
    "verbosityEfficiency": "Do extra words provide useful direction?",
    "overallPreference": "Control, Experimental, or Tie?",
    "reviewerNote": "Free-form reviewer note",
}


class SceneCase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,80}$")
    title: str
    project_name: str = Field(alias="projectName")
    workspace: dict[str, Any]
    story_idea: str = Field(alias="storyIdea", min_length=1)
    sequence_context: str = Field(alias="sequenceContext", min_length=1)
    desired_generation_count: int = Field(alias="desiredGenerationCount", ge=2, le=12)
    planner_seed: int = Field(default=1, alias="plannerSeed", ge=0, le=2147483647)
    h3_seed: int = Field(default=2718, alias="h3Seed", ge=0, le=2147483647)
    h3_temperature: float | None = Field(default=None, alias="h3Temperature", ge=0, le=2)

    def model_workspace(self) -> Workspace:
        return validate_authoring_workspace(Workspace.model_validate(self.workspace, context={"authoring": True}))


def load_cases(path: Path) -> list[SceneCase]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != SCHEMA_VERSION or data.get("suite") != SUITE_NAME:
        raise ValueError(f"Expected {SUITE_NAME} schemaVersion {SCHEMA_VERSION}")
    cases = [SceneCase.model_validate(item) for item in data.get("cases", [])]
    if len(cases) != 3 or len({case.id for case in cases}) != len(cases):
        raise ValueError("Story Scene Directability requires exactly three unique fixture cases")
    for case in cases:
        case.model_workspace()
    return cases


def policy_messages(production_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages = deepcopy(production_messages)
    if not messages or messages[0].get("role") != "system" or not isinstance(messages[0].get("content"), str):
        raise ValueError("Production Story Planner assembly must begin with a text system message")
    messages[0]["content"] += POLICY_SECTION
    return messages


def stable_digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def planner_metrics(items, proposed_items=None, effective_durations=None) -> dict[str, Any]:
    current = items[0]
    proposed_items = proposed_items or items
    return {
        "scenePlanWords": len(current.scene_plan.split()),
        "scenePlanCharacters": len(current.scene_plan),
        "incomingState": current.incoming_state,
        "intendedOutgoingState": current.intended_outgoing_state,
        "relationshipToPrevious": current.relationship_to_previous.value if current.relationship_to_previous else None,
        "cameraIntent": current.camera_intent,
        "exactDialogue": current.dialogue,
        "proposedDurationsSeconds": [getattr(item, "duration_seconds", None) for item in proposed_items],
        "effectiveGenerationDurationsSeconds": list(effective_durations or []),
    }


class _CapturingFactory:
    def __init__(self, inner_factory, destination: dict[str, Any], raw_reply: str | None = None):
        self.inner_factory = inner_factory
        self.destination = destination
        self.raw_reply = raw_reply

    def __call__(self, settings):
        outer = self
        class Client:
            async def __aenter__(self):
                if outer.raw_reply is None:
                    self.client = outer.inner_factory(settings)
                    await self.client.__aenter__()
                    return self
                return self
            async def __aexit__(self, *args):
                if getattr(self, "client", None) is not None:
                    return await self.client.__aexit__(*args)
            async def generate(self, messages, **kwargs):
                outer.destination["messages"] = deepcopy(messages)
                if outer.raw_reply is not None:
                    outer.destination["raw"] = outer.raw_reply
                    return outer.raw_reply
                result = await self.client.generate(messages, **kwargs)
                outer.destination["raw"] = result
                outer.destination["messages"] = deepcopy(getattr(self.client, "messages", messages))
                outer.destination["payload"] = getattr(self.client, "last_payload", None)
                outer.destination["usage"] = getattr(self.client, "last_usage", {})
                return result
            async def generate_streamed(self, messages, **kwargs):
                return await self.generate(messages, **kwargs)
        return Client()


def _human_review() -> dict[str, Any]:
    return {"scenePlan": {key: {"question": question, "control": None, "experimental": None}
                          for key, question in SCENE_REVIEW.items()},
            "downstreamH3": {key: {"question": question, "control": None, "experimental": None}
                             for key, question in H3_REVIEW.items()}}


async def _run_planner_arm(case: SceneCase, project_root: Path, profile: BenchmarkProfile,
                           arm: str, client_factory) -> dict[str, Any]:
    from miniconstruct.benchmark.story import DeterministicStoryProjectStore
    workspace = case.model_workspace()
    store = DeterministicStoryProjectStore(project_root, case.id)
    project = store.create_project(workspace, sequence_context=case.sequence_context,
                                   story_idea=case.story_idea)
    snapshot = store.read_story_snapshot(project.project_id)
    slot_index = 0
    def slot_id():
        nonlocal slot_index
        slot_index += 1
        return str(uuid.uuid5(uuid.UUID(project.project_id), f"directability-slot:{slot_index}"))
    draft = initial_story_draft(snapshot, case.desired_generation_count, item_id_factory=slot_id)
    planner_settings = profile.to_settings().model_copy(update={
        "seed_mode": SeedMode.FIXED, "fixed_seed": case.planner_seed, "seed": case.planner_seed,
    })
    request = StoryBuildRequest(idea=case.story_idea, desiredGenerationCount=case.desired_generation_count,
                                llm=planner_settings)
    base_messages = assemble_story_prompt(snapshot, draft, request.llm, idea=request.idea)
    model_messages = base_messages if arm == "control" else policy_messages(base_messages)
    planner_capture: dict[str, Any] = {}
    planner_factory = _CapturingFactory(client_factory, planner_capture)
    proposals = await _call(model_messages, request.llm, planner_factory, build_initial=draft)
    projected = add_incomplete_build_warnings(project_build(snapshot, draft, proposals))
    return {
        "store": store,
        "projectId": project.project_id,
        "initialDraft": draft,
        "projectedDraft": projected,
        "proposals": proposals,
        "modelFacingRequest": _safe_messages(planner_capture.get("messages", model_messages), workspace),
        "rawReply": planner_capture.get("raw", ""),
        "workspace": workspace,
        "settings": planner_settings,
    }


def _replace_scene_plans(control_draft, experimental_draft):
    """Clone Control authority and replace only Scene Plans by stable item ID."""
    control_ids = [item.plan_item_id for item in control_draft.items]
    experimental_by_id = {item.plan_item_id: item for item in experimental_draft.items}
    if len(experimental_by_id) != len(experimental_draft.items) or set(control_ids) != set(experimental_by_id):
        raise ValueError("Planner arms returned different item identities or ordering")
    normalized = control_draft.model_copy(deep=True)
    for item in normalized.items:
        item.scene_plan = experimental_by_id[item.plan_item_id].scene_plan
    for control_item, normalized_item in zip(control_draft.items, normalized.items):
        if control_item.model_dump(exclude={"scene_plan"}) != normalized_item.model_dump(exclude={"scene_plan"}):
            raise ValueError("Experimental downstream normalization changed a non-Scene-Plan field")
    return normalized


def _normalize_h3_scene_plans(control_messages, control_authority, experimental_authority):
    """Swap every ordered/current Scene Plan in a complete safe H3 request."""
    messages = deepcopy(control_messages)
    control_plans = control_authority.get("orderedScenePlans", [])
    experimental_plans = experimental_authority.get("orderedScenePlans", [])
    if len(control_plans) != len(experimental_plans):
        raise ValueError("Downstream H3 Story authorities have different ordered Scene Plan counts")
    by_identity = {}
    for control_plan, experimental_plan in zip(control_plans, experimental_plans):
        identity = (control_plan.get("generationId"), control_plan.get("ordinal"))
        if identity != (experimental_plan.get("generationId"), experimental_plan.get("ordinal")):
            raise ValueError("Downstream H3 Story authorities have different Generation identities")
        by_identity[identity] = experimental_plan["scenePlan"]
    current = control_authority["currentGeneration"]
    exp_current = experimental_authority["currentGeneration"]
    if current.get("generationId") != exp_current.get("generationId"):
        raise ValueError("Downstream H3 Story authorities have different current Generation identities")
    prefix = "CANONICAL STORY H3 AUTHORITY (current Generation is marked current):\n"
    divider = "\n\nCURRENT GENERATION SCENE PLAN — PRIMARY DIRECTING TASK:\n"

    def normalize_text(text):
        if prefix not in text or divider not in text:
            return text
        start = text.index(prefix) + len(prefix)
        end = text.index(divider, start)
        authority = json.loads(text[start:end])
        model_plans = authority.get("orderedScenePlans", [])
        if len(model_plans) != len(control_plans):
            raise ValueError("H3 model-facing request omitted or added ordered Scene Plans")
        expected_control = {(plan["generationId"], plan["ordinal"]): plan["scenePlan"]
                            for plan in control_plans}
        for plan in model_plans:
            identity = (plan.get("generationId"), plan.get("ordinal"))
            if identity not in by_identity or plan.get("scenePlan") != expected_control.get(identity):
                raise ValueError("H3 ordered Scene Plans do not match the applied Control authority")
            plan["scenePlan"] = by_identity[identity]
        if authority.get("currentGeneration", {}).get("scenePlan") != current["scenePlan"]:
            raise ValueError("H3 current Generation Scene Plan does not match the applied Control authority")
        authority["currentGeneration"]["scenePlan"] = exp_current["scenePlan"]
        updated = json.dumps(authority, ensure_ascii=False, separators=(",", ":"))
        text = text[:start] + updated + text[end:]
        primary = divider + current["scenePlan"]
        if primary not in text:
            raise ValueError("Production H3 request did not contain the current Scene Plan task")
        return text.replace(primary, divider + exp_current["scenePlan"], 1)

    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = normalize_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = normalize_text(part["text"])
    return messages


async def _run_downstream(case: SceneCase, prepared: dict[str, Any], downstream_draft,
                          h3_profile: BenchmarkProfile, client_factory) -> dict[str, Any]:
    store = prepared["store"]
    store.apply_story_plan(prepared["projectId"], downstream_draft)
    saved_snapshot = store.read_story_snapshot(prepared["projectId"])
    first_generation = saved_snapshot.generations[0]
    authority = store.story_h3_context(prepared["projectId"], first_generation.generation_id)
    h3_workspace = first_generation.workspace
    effective_durations = [generation.workspace.duration_seconds for generation in saved_snapshot.generations]
    h3_settings = h3_profile.to_settings().model_copy(update={
        "seed_mode": SeedMode.FIXED, "fixed_seed": case.h3_seed, "seed": case.h3_seed,
        **({"temperature": case.h3_temperature} if case.h3_temperature is not None else {}),
    })
    h3_request = GenerationRequest(workspace=h3_workspace, llm=h3_settings,
                                   resolvedSeeds=[case.h3_seed], authoringPath="story")
    h3_request._story_context = authority
    h3_request._continuity_context = store.h3_continuity_context(prepared["projectId"], first_generation.generation_id)
    h3_request._narrative_subjects = store.generation_narrative_subjects(prepared["projectId"], h3_workspace)
    h3_request._narrative_resolved = True
    h3_capture: dict[str, Any] = {}
    generated = await generate_workspace_prompts(h3_request, _CapturingFactory(client_factory, h3_capture))
    variation = generated.variations[0]
    actual_h3_messages = (variation.model_payload or {}).get("messages", generated.assembled.messages)
    actual_h3_messages = _safe_messages(actual_h3_messages, h3_workspace)
    prompt = variation.generated.prompt
    validation = variation.generated.validation.model_dump(mode="json")
    metrics = message_metrics(prompt, validation, h3_workspace.duration_seconds, h3_workspace.shots)
    return {
        "modelFacingRequest": actual_h3_messages,
        "exactOutput": prompt,
        "validation": validation,
        "metrics": metrics,
        "effectiveSettings": {"modelId": h3_settings.model_id, "temperature": h3_settings.temperature,
                              "reasoningMode": h3_settings.reasoning_mode.value,
                              "maxTokens": h3_settings.max_tokens, "contextWindow": h3_settings.context_window,
                              "supportsVision": h3_settings.supports_vision, "seed": case.h3_seed,
                              "durationSeconds": h3_workspace.duration_seconds, "shots": h3_workspace.shots},
        "references": h3_request.reference_map,
        "diagnostics": generated.warnings,
        "storyAuthority": authority,
        "effectiveGenerationDurationsSeconds": effective_durations,
    }


_SPILLOVER_FIELDS = {
    "scenePlan": "scene_plan",
    "durationSeconds": "duration_seconds",
    "dialogue": "dialogue",
    "incomingState": "incoming_state",
    "intendedOutgoingState": "intended_outgoing_state",
    "relationshipToPrevious": "relationship_to_previous",
    "cameraIntent": "camera_intent",
    "referenceRecommendations": "reference_recommendations",
}


def planner_spillover_diff(control_result, experimental_result) -> list[dict[str, Any]]:
    control_by_id = {item.plan_item_id: item for item in control_result.items}
    experimental_by_id = {item.plan_item_id: item for item in experimental_result.items}
    if set(control_by_id) != set(experimental_by_id):
        raise ValueError("Raw Planner result identities differ; cannot summarize spillover")
    result = []
    for ordinal, control_item in enumerate(control_result.items, 1):
        experimental_item = experimental_by_id[control_item.plan_item_id]
        changes = {}
        for public_name, field_name in _SPILLOVER_FIELDS.items():
            left, right = getattr(control_item, field_name), getattr(experimental_item, field_name)
            if hasattr(left, "value"):
                left = left.value
            if hasattr(right, "value"):
                right = right.value
            if field_name == "reference_recommendations":
                left = [item.model_dump(mode="json", by_alias=True) for item in left]
                right = [item.model_dump(mode="json", by_alias=True) for item in right]
            changes[public_name] = left != right
        result.append({"planItemId": control_item.plan_item_id, "ordinal": ordinal, "changed": changes})
    return result


def _raw_planner_report(prepared: dict[str, Any], source_profile: BenchmarkProfile,
                        effective_durations: list[float] | None = None) -> dict[str, Any]:
    proposals = prepared["proposals"]
    projected = prepared["projectedDraft"]
    settings = prepared["settings"]
    return {
        "modelFacingRequest": prepared["modelFacingRequest"],
        "rawPlannerResult": {
            "reply": prepared["rawReply"],
            "parsed": proposals.model_dump(mode="json", by_alias=True),
            "projected": projected.model_dump(mode="json", by_alias=True),
        },
        "parsedScenePlans": [item.model_dump(mode="json", by_alias=True) for item in projected.items],
        "validation": {"valid": True, "schema": "LegacyStoryBuildResponse", "projectedThrough": "project_build"},
        "metrics": planner_metrics(projected.items, proposals.items, effective_durations),
        "effectiveSettings": {
            "modelId": settings.model_id, "temperature": settings.temperature,
            "reasoningMode": settings.reasoning_mode.value, "maxTokens": settings.max_tokens,
            "contextWindow": settings.context_window, "supportsVision": settings.supports_vision,
            "seedMode": settings.seed_mode.value, "effectiveSeed": settings.seed,
            "sourceProfileSeedMode": source_profile.seed_mode.value,
            "sourceProfileFixedSeed": source_profile.fixed_seed,
        },
    }


async def run_suite(root: Path, planner_profile: BenchmarkProfile, h3_profile: BenchmarkProfile,
                    client_factory=None, case_ids: set[str] | None = None) -> dict[str, Any]:
    from miniconstruct.llm.client import OpenAICompatibleClient
    client_factory = client_factory or OpenAICompatibleClient
    cases = load_cases(root / "story-scene-directability" / "cases.json")
    selected = [case for case in cases if case_ids is None or case.id in case_ids]
    if not selected or (case_ids and case_ids != {case.id for case in selected}):
        raise ValueError("No matching Story Scene Directability fixtures selected")
    planner_settings = planner_profile.to_settings()
    h3_settings = h3_profile.to_settings()
    outputs = []
    with tempfile.TemporaryDirectory(prefix="miniconstruct-scene-directability-") as directory:
        for case in selected:
            control_prepared = await _run_planner_arm(case, Path(directory) / case.id / "control",
                                                      planner_profile, "control", client_factory)
            experimental_prepared = await _run_planner_arm(case, Path(directory) / case.id / "experimental",
                                                           planner_profile, "experimental", client_factory)
            control_request = control_prepared["modelFacingRequest"]
            experimental_request = deepcopy(experimental_prepared["modelFacingRequest"])
            experimental_request[0]["content"] = experimental_request[0]["content"].removesuffix(POLICY_SECTION)
            identical = control_request == experimental_request
            if not identical:
                raise ValueError(f"Planner A/B request parity failed for {case.id}")
            control_downstream_draft = control_prepared["projectedDraft"]
            experimental_downstream_draft = _replace_scene_plans(
                control_downstream_draft, experimental_prepared["projectedDraft"],
            )
            control_h3 = await _run_downstream(case, control_prepared, control_downstream_draft,
                                               h3_profile, client_factory)
            experimental_h3 = await _run_downstream(case, experimental_prepared, experimental_downstream_draft,
                                                    h3_profile, client_factory)
            normalized_control_request = _normalize_h3_scene_plans(
                control_h3["modelFacingRequest"], control_h3["storyAuthority"], experimental_h3["storyAuthority"],
            )
            full_h3_parity = normalized_control_request == experimental_h3["modelFacingRequest"]
            if not full_h3_parity:
                raise ValueError(f"Full downstream H3 request parity failed after Scene Plan normalization for {case.id}")
            if control_h3["effectiveSettings"] != experimental_h3["effectiveSettings"]:
                raise ValueError(f"Downstream H3 settings or duration differ between arms for {case.id}")
            if control_h3["effectiveGenerationDurationsSeconds"] != experimental_h3["effectiveGenerationDurationsSeconds"]:
                raise ValueError(f"Downstream Generation durations differ between arms for {case.id}")
            expected_scene_plans = [item.scene_plan for item in experimental_downstream_draft.items]
            applied_scene_plans = [item["scenePlan"] for item in experimental_h3["storyAuthority"]["orderedScenePlans"]]
            if applied_scene_plans != expected_scene_plans:
                raise ValueError(f"Not all ordered Experimental Scene Plans reached H3 authority for {case.id}")

            control_report = _raw_planner_report(control_prepared, planner_profile,
                control_h3["effectiveGenerationDurationsSeconds"])
            experimental_report = _raw_planner_report(experimental_prepared, planner_profile)
            control_report["downstreamStoryAuthority"] = control_h3["storyAuthority"]
            experimental_report["downstreamNormalizedDraft"] = experimental_downstream_draft.model_dump(
                mode="json", by_alias=True,
            )
            experimental_report["downstreamNormalizedStoryAuthority"] = experimental_h3["storyAuthority"]
            arms = {
                "control": {"planner": control_report, "h3": control_h3},
                "experimental": {"planner": experimental_report, "h3": experimental_h3},
            }
            outputs.append({
                "caseId": case.id, "title": case.title, "status": "completed",
                "plannerSeed": case.planner_seed,
                "plannerInputParity": {"identicalAfterRemovingBenchmarkPolicy": identical,
                                       "controlSha256": stable_digest(control_request),
                                       "experimentalBaseSha256": stable_digest(experimental_request)},
                "plannerSpilloverDiff": planner_spillover_diff(
                    control_prepared["proposals"], experimental_prepared["proposals"]),
                "downstreamNormalization": {
                    "experimentalUsesControlNonScenePlanFields": True,
                    "allOrderedExperimentalScenePlansRetained": True,
                    "onlyExperimentalScenePlanTextChanged": True,
                },
                "downstreamH3Parity": {
                    "completeSafeMessagesMatchAfterAllScenePlanNormalization": full_h3_parity,
                    "effectiveGenerationDurationsMatch": True,
                    "controlNormalizedSha256": stable_digest(normalized_control_request),
                    "experimentalSha256": stable_digest(experimental_h3["modelFacingRequest"]),
                },
                "review": _human_review(), "arms": arms,
            })
    return {
        "schemaVersion": 1, "suite": SUITE_NAME,
        "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "profiles": {"planner": _safe_profile(planner_profile, planner_settings, per_case_seed=True,
                                               seed_policy="case.plannerSeed is fixed and shared by both arms"),
                     "h3": _safe_profile(h3_profile, h3_settings, per_case_seed=True)},
        "experimentalPolicy": DIRECTABILITY_POLICY,
        "storyAuthorityFlow": "Story Plan / Premise -> projected Scene Plans -> ordinary production H3 assembly",
        "llmJudge": "none",
        "cases": outputs,
    }


def _safe_profile(profile: BenchmarkProfile, settings, *, per_case_seed: bool = False,
                  seed_policy: str | None = None) -> dict[str, Any]:
    public_profile = profile.public_dict()
    public_profile.pop("apiKey", None)
    public_profile.pop("api_key", None)
    return {"profile": public_profile, "effective": {
        "modelId": settings.model_id, "temperature": settings.temperature,
        "maxTokens": settings.max_tokens, "contextWindow": settings.context_window,
        "reasoningMode": settings.reasoning_mode.value, "supportsVision": settings.supports_vision,
        "seedMode": "fixed_per_case" if per_case_seed else settings.seed_mode.value,
        "effectiveSeed": None if per_case_seed else settings.seed,
        "effectiveSeedPolicy": seed_policy or ("case.h3Seed is fixed and shared by both arms" if per_case_seed else "profile setting"),
        "sourceProfileSeedMode": profile.seed_mode.value,
        "sourceProfileFixedSeed": profile.fixed_seed,
        "transport": "non_streaming", "reasoningSource": "profile",
    }}


def _safe_messages(messages: list[dict[str, Any]], workspace: Workspace) -> list[dict[str, Any]]:
    safe = deepcopy(messages)
    assets = [asset for asset in workspace.ordered_assets() if asset.image is not None]
    index = 0
    for message in safe:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            index += 1
            asset = assets[index - 1] if index <= len(assets) else None
            digest = hashlib.sha256(asset.image.data_url.encode("utf-8")).hexdigest() if asset else "unknown"
            part["image_url"] = {"url": f"[image payload omitted; sha256={digest}]"}
            part["imageProvenance"] = ({"assetId": asset.id, "filename": asset.filename,
                                        "role": asset.role, "sha256": digest}
                                       if asset else {"sha256": digest})
    return safe


def write_run(root: Path, result: dict[str, Any]) -> Path:
    directory = root / "runs" / SUITE_NAME / result["runId"]
    directory.mkdir(parents=True, exist_ok=False)
    json_path = directory / "results.json"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = [f"# {SUITE_NAME} review", "", "Human review only. Descriptive counts are not creative-quality scores.",
            "", f"Planner profile: {result['profiles']['planner']['profile']['displayName']}",
            f"H3 profile: {result['profiles']['h3']['profile']['displayName']}", ""]
    for case in result["cases"]:
        rows += [f"## {case['title']} (`{case['caseId']}`)", "",
                 "Planner inputs match exactly after removing the benchmark-only policy: **yes**.",
                 f"Planner A/B seed: fixed {case['plannerSeed']} for both arms.",
                 "Downstream H3 holds Control values for every Planner field except all ordered Scene Plans.", "",
                 "### Raw Planner spillover by Generation", "",
                 "| Generation | Changed raw Planner fields |", "| --- | --- |"]
        for diff in case["plannerSpilloverDiff"]:
            changed_fields = [name for name, changed in diff["changed"].items() if changed]
            rows.append(f"| G{diff['ordinal']} | {', '.join(changed_fields) if changed_fields else 'None'} |")
        rows.append("")
        for arm_name in ("control", "experimental"):
            arm = case["arms"][arm_name]
            rows += [f"### {arm_name.title()}", "", "#### Scene Plans", "", "```text",
                     "\n\n".join(item["scenePlan"] for item in arm["planner"]["parsedScenePlans"]), "```", "",
                     "#### Exact raw Planner reply", "", "```text",
                     arm["planner"]["rawPlannerResult"]["reply"], "```", "",
                     f"Planner metrics: `{json.dumps(arm['planner']['metrics'], ensure_ascii=False)}`", "",
                     "#### Production H3 output", "", "```text", arm["h3"]["exactOutput"], "```", "",
                     f"H3 metrics: `{json.dumps(arm['h3']['metrics'], ensure_ascii=False)}`", ""]
            if arm_name == "experimental":
                rows += ["#### Normalized downstream Story authority", "",
                         "This is Control non-Scene-Plan authority plus every Experimental Scene Plan; it is distinct from the raw Planner result above.", "",
                         "```json", json.dumps(arm["planner"]["downstreamNormalizedStoryAuthority"],
                                                ensure_ascii=False, indent=2), "```", ""]
        rows += ["Complete H3 request parity after normalizing every ordered Scene Plan: **yes**.", ""]
        rows += ["### Human review", "", "| Scene Plan criterion | Control | Experimental |", "| --- | --- | --- |"]
        rows += [f"| {question} |  |  |" for question in SCENE_REVIEW.values()]
        rows += ["", "| Downstream H3 criterion | Control | Experimental |", "| --- | --- | --- |"]
        rows += [f"| {question} |  |  |" for question in H3_REVIEW.values()]
        rows += ["", "Reviewer note:", "", ""]
    (directory / "review.md").write_text("\n".join(rows), encoding="utf-8")
    return json_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in Story Scene Plan directability benchmark")
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    parser.add_argument("--planner-profile", required=True)
    parser.add_argument("--h3-profile", required=True)
    parser.add_argument("--profiles", type=Path, default=Path("benchmarks/models.local.json"))
    parser.add_argument("--case", action="append", dest="cases")
    args = parser.parse_args()
    profiles = load_profiles(args.profiles)
    for name in (args.planner_profile, args.h3_profile):
        if name not in profiles:
            parser.error(f"Profile {name!r} not found in {args.profiles}")
    result = asyncio.run(run_suite(args.root, profiles[args.planner_profile], profiles[args.h3_profile],
                                   case_ids=set(args.cases) if args.cases else None))
    print(write_run(args.root, result))


if __name__ == "__main__":
    main()
