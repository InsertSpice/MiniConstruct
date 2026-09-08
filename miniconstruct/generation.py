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

from miniconstruct.execution_authoring import execution_authoring_fingerprint
from miniconstruct.h3.builder import AssembledPrompt, assemble_prompt
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.api import GenerationRequest, GeneratedVariation, LLMSettings, SEED_MAX, SeedMode


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


async def generate_workspace_prompts(
    request: GenerationRequest,
    client_factory: Callable[[LLMSettings], OpenAICompatibleClient] = OpenAICompatibleClient,
) -> ProductionGeneration:
    """Run normal assembly, provider generation, and structural validation."""
    assembled = assemble_prompt(request.prompt_workspace, request.llm.supports_vision)
    warnings = list(assembled.warnings)
    results: list[ProductionVariation] = []
    for seed in resolved_generation_seeds(request):
        settings = request.llm.model_copy(update={"seed": seed})
        started = time.perf_counter()
        async with client_factory(settings) as client:
            prompt = await client.generate(deepcopy(assembled.messages))
            elapsed_ms = (time.perf_counter() - started) * 1000
            seed_unsupported = bool(getattr(client, "seed_unsupported", False))
            if seed_unsupported:
                warnings.append("This endpoint does not support the seed parameter; MiniConstruct retried with backend defaults.")
            results.append(ProductionVariation(
                generated=GeneratedVariation(
                    referenceMap=request.reference_map, sourceFingerprint=execution_authoring_fingerprint(request.workspace),
                    prompt=prompt,
                    validation=validate_workspace_prompt(prompt, request.prompt_workspace),
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
