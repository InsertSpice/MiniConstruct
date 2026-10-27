"""Shared production generation orchestration.

The HTTP route and benchmark harness use this module. It deliberately does not
repair, regenerate, or reinterpret a model response.
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
import secrets
import time
from typing import Any

from miniconstruct.h3.builder import AssembledPrompt, assemble_prompt
from miniconstruct.context_capacity import preflight_request_capacity
from miniconstruct.llm.compatibility import prepare_messages
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.api import GenerationRequest, GeneratedVariation, LLMSettings, SEED_MAX, SeedMode
from miniconstruct.dialogue_language import resolve_dialogue, workspace_for_h3


@dataclass(frozen=True)
class ProductionVariation:
    generated: GeneratedVariation
    requested_seed: int | None
    effective_seed: int | None
    seed_unsupported: bool
    seed_known_unsupported: bool
    compatibility_fallback: bool
    request_duration_ms: float
    usage: dict[str, int]
    model_payload: dict[str, Any] | None


@dataclass(frozen=True)
class ProductionGeneration:
    assembled: AssembledPrompt
    variations: list[ProductionVariation]
    warnings: list[str]


def resolved_generation_seeds(request: GenerationRequest) -> list[int | None]:
    """Use the browser snapshot when available; retain sane API-call behavior."""
    if request.resolved_seeds:
        return request.resolved_seeds
    if request.llm.seed_mode == SeedMode.FIXED:
        return [request.llm.fixed_seed] * request.workspace.variations
    if request.llm.seed_mode == SeedMode.RANDOM:
        seeds: set[int] = set()
        while len(seeds) < request.workspace.variations:
            seeds.add(secrets.randbelow(SEED_MAX + 1))
        return list(seeds)
    return [None] * request.workspace.variations


def resolve_narrative_request(request, store=None):
    steps = _narrative_request_steps(request, store)
    try:
        project_store, project_id, generation_id, workspace = next(steps)
    except StopIteration as done:
        return done.value
    handoff = project_store.visual_handoff(project_id, generation_id, workspace, materialize=True)
    try:
        steps.send(handoff)
    except StopIteration as done:
        return done.value


async def resolve_narrative_request_async(request, store=None):
    steps = _narrative_request_steps(request, store)
    try:
        project_store, project_id, generation_id, workspace = next(steps)
    except StopIteration as done:
        return done.value
    handoff = await project_store.visual_handoff_async(project_id, generation_id, workspace, materialize=True)
    try:
        steps.send(handoff)
    except StopIteration as done:
        return done.value


def _narrative_request_steps(request, store=None):
    """Share snapshot/finalization; yield only the local handoff boundary.

    Sync utilities and async owners resume the same assembly after materialization,
    so context, fingerprints and the post-extraction identity check stay identical.
    """
    if request._narrative_resolved:
        return request
    resolved = request.model_copy(deep=True)
    if request.filesystem_project_id is not None:
        from miniconstruct.project_store import ProjectStore
        project_store = store or ProjectStore()
        if request.authoring_path == "story":
            if not request.generation_id:
                raise ValueError("Generate from Story requires a saved Generation")
            saved = project_store.read_generation(request.filesystem_project_id, request.generation_id)
            resolved.workspace = saved.workspace
            resolved._story_context = project_store.story_h3_context(request.filesystem_project_id, request.generation_id)
        from miniconstruct.take_store import transaction, invalid
        with transaction():
            # New filesystem operations cannot use historical/browser-only
            # Pictures. Preserve unrelated unsaved one-shot authoring, but fail
            # closed until the dedicated reference boundary has agreed.
            saved = (project_store.read_generation(request.filesystem_project_id, request.generation_id)
                     if request.generation_id else project_store.load_project(request.filesystem_project_id))
            from miniconstruct.execution_authoring import execution_authoring_fingerprint
            def reference_source(workspace):
                required = workspace.structural_subject_ids()
                return saved.workspace.model_copy(update={"assets": workspace.assets,
                    "subjects": [subject for subject in workspace.subjects if subject.id in required]})
            if execution_authoring_fingerprint(reference_source(resolved.workspace)) != execution_authoring_fingerprint(reference_source(saved.workspace)):
                raise invalid("Reference Assets differ from saved authority. Synchronize Reference Assets before authoring.")
            resolved._continuity_context = project_store.h3_continuity_context(
                request.filesystem_project_id, request.generation_id)
            if request.authoring_path == "one_shot":
                from miniconstruct.h3.continuity import one_shot_continuity_context
                resolved._continuity_context = one_shot_continuity_context(resolved._continuity_context)
            resolved._narrative_subjects = project_store.generation_narrative_subjects(
                request.filesystem_project_id, resolved.workspace)
            handoff_identity = project_store.visual_handoff(
                request.filesystem_project_id, request.generation_id, resolved.workspace, strict=False).identity
        resolved._visual_handoff = yield (project_store, request.filesystem_project_id,
                                          request.generation_id, resolved.workspace)
        if resolved._visual_handoff.identity != handoff_identity:
            raise invalid("Endpoint source or Visual Handoff changed during request assembly; retry")
        # Handoff availability is live execution state, not immutable H3 output truth.
        # An explicit request-only value represents the user's unsaved normal
        # generation choice. Without it, the saved Project Sequence remains
        # authoritative for compatibility/API clients.
        language = getattr(request, "sequence_dialogue_language", None)
        if language is None:
            language = project_store.load_project(request.filesystem_project_id).dialogue_language
        resolved.workspace = resolved.workspace.model_copy(update={"dialogue_language": language})
    else:
        resolved._narrative_subjects = []
        resolved._continuity_context = None
    resolved._narrative_resolved = True
    from miniconstruct.models.workspace import Workspace
    try:
        if resolved.authoring_path == "story":
            from miniconstruct.models.workspace import validate_authoring_workspace
            validate_authoring_workspace(resolved.effective_workspace)
        else:
            Workspace.model_validate(resolved.effective_workspace.model_dump(mode="json", by_alias=True),
                                     context={"native_i2va": resolved.effective_workspace._native_i2va})
        # Route labels/routing must describe this complete effective reference
        # set, including a transient Visual Handoff, not the raw Workspace.
        resolved.reference_map
    except ValueError as exc:
        from miniconstruct.take_store import invalid
        from pydantic import ValidationError
        detail = "; ".join(e["msg"] for e in exc.errors(include_input=False)) if isinstance(exc, ValidationError) else str(exc)
        raise invalid("Effective H3 inputs are incomplete for this mode: " + detail) from exc
    return resolved


def revalidate_native_authoring(request, store=None):
    if request.effective_workspace._native_i2va:
        from miniconstruct.project_store import ProjectStore
        from miniconstruct.take_store import invalid
        handoff = (store or ProjectStore()).visual_handoff(request.filesystem_project_id, request.generation_id,
                                                         request.workspace)
        if handoff.identity != request._visual_handoff.identity:
            raise invalid("Native-only I2VA source or successor Profile changed during authoring; generate again.")


async def generate_workspace_prompts(
    request: GenerationRequest,
    client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
    *,
    benchmark_system_section: str | None = None,
) -> ProductionGeneration:
    """Run normal assembly, provider generation, and structural validation.

    The optional section is an explicit opt-in benchmark seam. Production callers
    omit it, so their assembled messages and behavior are unchanged.
    """
    request = await resolve_narrative_request_async(request)
    # Translation is resolved before the normal compiler.  This makes the exact
    # target text inspectable and keeps the H3 model out of the translation role.
    resolution = await resolve_dialogue(request.workspace, request.llm, client_factory)
    authoring_workspace = request.workspace.model_copy(update={"dialogue_resolution": resolution})
    request = request.model_copy(update={"workspace": authoring_workspace})
    prompt_workspace = workspace_for_h3(request.prompt_workspace)
    assembled = assemble_prompt(prompt_workspace, request.llm.supports_vision, request.narrative_subjects, request.continuity_context, request.story_context)
    if benchmark_system_section is not None:
        if not benchmark_system_section or assembled.messages[0].get("role") != "system":
            raise ValueError("Benchmark system section requires a nonempty section and a system message")
        messages = deepcopy(assembled.messages)
        messages[0]["content"] += benchmark_system_section
        assembled = AssembledPrompt(messages=messages, inspector_text=assembled.inspector_text + benchmark_system_section,
                                    warnings=assembled.warnings)
    capacity = preflight_request_capacity(prepare_messages(assembled.messages, request.llm.reasoning_mode), request.llm, purpose="H3 generation")
    warnings = list(assembled.warnings)
    if capacity["completionBudgetWarning"]:
        warnings.append(capacity["completionBudgetWarning"])
    results: list[ProductionVariation] = []
    for seed in resolved_generation_seeds(request):
        revalidate_native_authoring(request)
        settings = request.llm.model_copy(update={"seed": seed})
        started = time.perf_counter()
        async with client_factory(settings) as client:
            prompt = await client.generate(deepcopy(assembled.messages))
            revalidate_native_authoring(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            seed_unsupported = bool(getattr(client, "seed_unsupported", False))
            if seed_unsupported:
                warnings.append("This endpoint does not support the seed parameter; MiniConstruct retried with backend defaults.")
            results.append(ProductionVariation(
                generated=GeneratedVariation(
                    referenceMap=request.reference_map, sourceFingerprint=request.source_fingerprint,
                    authoringPath=request.authoring_path,
                    prompt=prompt,
                    validation=validate_workspace_prompt(prompt, prompt_workspace),
                ),
                requested_seed=seed,
                effective_seed=getattr(client, "effective_seed", seed),
                seed_unsupported=seed_unsupported,
                seed_known_unsupported=bool(getattr(client, "seed_known_unsupported", False)),
                compatibility_fallback=bool(getattr(client, "compatibility_fallback", False)),
                request_duration_ms=round(elapsed_ms, 2),
                usage=dict(getattr(client, "last_usage", {})),
                model_payload=getattr(client, "last_payload", None),
            ))
    return ProductionGeneration(assembled=assembled, variations=results, warnings=list(dict.fromkeys(warnings)))
