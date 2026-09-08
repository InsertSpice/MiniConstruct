from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Callable

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import (
    AssistedGenerationPlan,
    PlanningInferenceRequest,
    PlanningRevisionRequest,
    PlanningRevisionScope,
)
from miniconstruct.models.planning import GenerationPlanning, RecommendationSource
from miniconstruct.models.workspace import AssetKind, Workspace
from miniconstruct.project_store import GenerationDocument


PLANNER_MEDIA_POLICY = """Never fabricate facts about media that was not content-analysed. Video and Audio are
metadata-only in this request: do not claim to have watched or heard them, transcribe
them, classify them, or inspect waveforms.

"Metadata-only" Video and Audio may be recommended for intended use based on their
role, filename, notes, attachment state, and explicit textual Generation or Sequence
context. Do not state or imply that the media itself contains specific visual content,
motion, camera movement, spoken content, voice characteristics, music, sound, ambience,
environmental audio, or other media-observed facts unless those facts are explicitly
provided in asset metadata or contentAnalysis. When content is unknown, phrase the
recommendation as intended use. A declared role such as voice_timbre or camera_movement
may support recommending that reference for the corresponding guidance, but does not
supply an unrecorded voice quality or a specific trajectory, speed, or direction.
Pictures with actual vision input are different: visual observations from supplied
Picture data are allowed.
"""

BASE_PLANNER_SYSTEM = """You are MiniConstruct's optional one-Generation planning assistant.
Return exactly one JSON object, with no Markdown fences or commentary, using these keys:
creativeRequest, incomingState, intendedOutgoingState, relationshipToPrevious,
cameraIntent, referenceRecommendations.
The first five values must be strings except relationshipToPrevious, which is either
null or one of: continue_previous_shot, new_shot_preserve_continuity,
match_previous_ending_frame, independent. referenceRecommendations is an array of
objects with source, assetId, and recommendation. source must be either
previous_generation or current_generation.

""" + PLANNER_MEDIA_POLICY

INFERENCE_AUTHORITY = """Inference authority:
Non-empty current user-authored fields are authoritative; fill only unspecified or
missing areas. Sequence Context is authoritative. A supplied current relationship or
camera intent is authoritative. Recommendations may mention only source/asset pairs
present in the corresponding previous or current context.
"""

REVISION_AUTHORITY = """Revision authority:
The explicit revision instruction is authoritative for fields in editableScopes,
including non-empty fields. Fields outside editableScopes are authoritative and must
remain exactly unchanged. Sequence Context is read-only. Recommendations may mention
only current-generation source/asset pairs present in context.
"""

RELATIONSHIP_SEMANTICS = """Relationship semantics:
continue_previous_shot: same ongoing shot/action; favor temporal, action, and camera continuity.
new_shot_preserve_continuity: deliberate new camera setup/cut while preserving characters,
appearance, environment, object/story state, and important audiovisual continuity; do not
assume the previous final frame becomes the new first frame.
match_previous_ending_frame: intended to begin from the preceding endpoint/composition;
the actual final frame is unavailable unless supplied as an ordinary Picture.
independent: do not infer direct continuity from the predecessor beyond explicit shared
Sequence Context.
"""

# Kept as a public compatibility name for callers that used the original planner text.
PLANNER_SYSTEM = BASE_PLANNER_SYSTEM + INFERENCE_AUTHORITY + RELATIONSHIP_SEMANTICS


@dataclass(frozen=True)
class PlanningAssembly:
    messages: list[dict[str, Any]]
    inspector_text: str
    allowed_asset_refs: frozenset[tuple[str, str]]


def _asset_metadata(workspace: Workspace) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for asset in workspace.ordered_assets():
        entry = asset.model_dump(mode="json", by_alias=True, exclude={"image", "media_payload"})
        entry["imageAvailableForVision"] = asset.kind == AssetKind.IMAGE and asset.image is not None
        if asset.kind in {AssetKind.VIDEO, AssetKind.AUDIO}:
            entry["contentAnalysis"] = None
        entries.append(entry)
    return entries


def _workspace_context(workspace: Workspace, *, planning: GenerationPlanning, incoming: str, outgoing: str) -> dict[str, Any]:
    return {
        "mode": workspace.mode,
        "durationSeconds": workspace.duration_seconds,
        "shots": workspace.shots,
        "subjects": [subject.model_dump(mode="json") for subject in workspace.subjects],
        "creativeControls": workspace.creative_controls.model_dump(mode="json", by_alias=True),
        "creativeRequest": workspace.creative_request,
        "dialogue": workspace.dialogue,
        "referenceLabels": workspace.reference_labels,
        "assets": _asset_metadata(workspace),
        "incomingState": incoming,
        "intendedOutgoingState": outgoing,
        "planning": planning.model_dump(mode="json", by_alias=True),
    }


def _image_parts(workspace: Workspace, label: str) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for asset in workspace.ordered_assets():
        if asset.kind != AssetKind.IMAGE or asset.image is None:
            continue
        parts.extend([
            {"type": "text", "text": f"Visual input for {label} asset {asset.id} ({asset.filename}), role {asset.role}:"},
            {"type": "image_url", "image_url": {"url": asset.image.data_url}},
        ])
    return parts


def assemble_inference_prompt(
    previous: GenerationDocument,
    request: PlanningInferenceRequest,
    *,
    supports_vision: bool | None = None,
) -> PlanningAssembly:
    current = _workspace_context(
        request.workspace,
        planning=request.planning,
        incoming=request.continuity.incoming_state or "",
        outgoing=request.continuity.intended_outgoing_state or "",
    )
    predecessor = _workspace_context(
        previous.workspace,
        planning=previous.planning,
        incoming=previous.incoming_state,
        outgoing=previous.intended_outgoing_state,
    )
    context = {
        "sequence": {"sequenceContext": request.continuity.sequence_context or ""},
        "previousGeneration": {"label": f"Generation {previous.ordinal}", **predecessor},
        "currentGenerationDraft": current,
        "mediaPolicy": {
            "pictures": "Actual Picture data may be supplied below when vision is enabled.",
            "video": "Metadata only; no video bytes or content analysis supplied.",
            "audio": "Metadata only; no audio bytes, transcript, or content analysis supplied.",
        },
    }
    text = "Prepare a proposal for the CURRENT generation from this context. Do not change explicit non-empty current values.\n" + json.dumps(context, ensure_ascii=False, indent=2, default=str)
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if supports_vision is True:
        content.extend(_image_parts(previous.workspace, "previous-generation") )
        content.extend(_image_parts(request.workspace, "current-generation") )
    messages = [{"role": "system", "content": PLANNER_SYSTEM}, {"role": "user", "content": content if supports_vision is True else text}]
    allowed = {
        (RecommendationSource.PREVIOUS_GENERATION.value, asset.id) for asset in previous.workspace.assets
    } | {
        (RecommendationSource.CURRENT_GENERATION.value, asset.id) for asset in request.workspace.assets
    }
    return PlanningAssembly(messages, f"{BASE_PLANNER_SYSTEM}\n{INFERENCE_AUTHORITY}\n{RELATIONSHIP_SEMANTICS}\n{text}", frozenset(allowed))


def assemble_revision_plan_prompt(request: PlanningRevisionRequest) -> PlanningAssembly:
    current = _workspace_context(
        request.workspace,
        planning=request.planning,
        incoming=request.continuity.incoming_state or "",
        outgoing=request.continuity.intended_outgoing_state or "",
    )
    context = {
        "sequenceContext": request.continuity.sequence_context or "",
        "currentGenerationDraft": current,
        "editableScopes": [scope.value for scope in request.scopes],
        "revisionInstruction": request.instruction,
    }
    text = "Revise only the selected planning scopes. Preserve all other values exactly. Do not revise Sequence Context.\n" + json.dumps(context, ensure_ascii=False, indent=2, default=str)
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    if request.llm.supports_vision is True:
        content.extend(_image_parts(request.workspace, "current-generation"))
    messages = [{"role": "system", "content": BASE_PLANNER_SYSTEM + REVISION_AUTHORITY + RELATIONSHIP_SEMANTICS}, {"role": "user", "content": content if request.llm.supports_vision is True else text}]
    allowed = frozenset((RecommendationSource.CURRENT_GENERATION.value, asset.id) for asset in request.workspace.assets)
    return PlanningAssembly(messages, f"{BASE_PLANNER_SYSTEM}\n{REVISION_AUTHORITY}\n{RELATIONSHIP_SEMANTICS}\n{text}", allowed)


def _parse_plan(raw: str, allowed_asset_refs: frozenset[tuple[str, str]]) -> AssistedGenerationPlan:
    try:
        value = json.loads(raw)
        plan = AssistedGenerationPlan.model_validate(value)
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LLMBackendError("The planning model returned malformed structured JSON.", 502) from exc
    filtered = [
        recommendation for recommendation in plan.reference_recommendations
        if (recommendation.source.value, recommendation.asset_id) in allowed_asset_refs
    ]
    return plan.model_copy(update={"reference_recommendations": filtered})


async def _generate_plan(
    assembly: PlanningAssembly,
    settings,
    client_factory: Callable[[Any], Any] = OpenAICompatibleClient,
) -> AssistedGenerationPlan:
    async with client_factory(settings) as client:
        raw = await client.generate(assembly.messages, response_instruction=None)
    return _parse_plan(raw, assembly.allowed_asset_refs)


async def infer_plan(
    previous: GenerationDocument,
    request: PlanningInferenceRequest,
    client_factory: Callable[[Any], Any] = OpenAICompatibleClient,
) -> AssistedGenerationPlan:
    return await _generate_plan(
        assemble_inference_prompt(previous, request, supports_vision=request.llm.supports_vision),
        request.llm,
        client_factory,
    )


def validate_revision_result(
    request: PlanningRevisionRequest,
    plan: AssistedGenerationPlan,
) -> None:
    selected = set(request.scopes)
    current = {
        PlanningRevisionScope.MAIN_PROMPT: request.workspace.creative_request,
        PlanningRevisionScope.INCOMING_STATE: request.continuity.incoming_state or "",
        PlanningRevisionScope.INTENDED_OUTGOING_STATE: request.continuity.intended_outgoing_state or "",
        PlanningRevisionScope.RELATIONSHIP_TO_PREVIOUS: request.planning.relationship_to_previous,
        PlanningRevisionScope.CAMERA_INTENT: request.planning.camera_intent,
    }
    proposed = {
        PlanningRevisionScope.MAIN_PROMPT: plan.creative_request,
        PlanningRevisionScope.INCOMING_STATE: plan.incoming_state,
        PlanningRevisionScope.INTENDED_OUTGOING_STATE: plan.intended_outgoing_state,
        PlanningRevisionScope.RELATIONSHIP_TO_PREVIOUS: plan.relationship_to_previous,
        PlanningRevisionScope.CAMERA_INTENT: plan.camera_intent,
    }
    for scope, value in current.items():
        if scope not in selected and proposed[scope] != value:
            raise LLMBackendError(f"The planning model changed unselected field {scope.value!r}.", 502)


async def revise_plan(
    request: PlanningRevisionRequest,
    client_factory: Callable[[Any], Any] = OpenAICompatibleClient,
) -> AssistedGenerationPlan:
    assembly = assemble_revision_plan_prompt(request)
    plan = await _generate_plan(assembly, request.llm, client_factory)
    validate_revision_result(request, plan)
    return plan
