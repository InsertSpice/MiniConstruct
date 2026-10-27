"""Explicit, single-call selected-output observation with bounded local identity evidence."""
import asyncio
import base64
from io import BytesIO
import json
import logging
import re

from pydantic import ValidationError

from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.api import ReasoningMode
from miniconstruct.models.observations import ObservedEndState, TakeObservation
from miniconstruct.models.takes import timestamp
from miniconstruct.take_store import transaction, invalid

logger = logging.getLogger(__name__)

OBSERVATION_POLICY = """Observe the endpoint of the selected rendered video from the supplied endpoint and chronological tail contact sheet.
VIDEO EVIDENCE IS AUTHORITY FOR VISUAL OBSERVATION. Planned continuity is not observed truth.
Planning fields are comparison context only, never proof. If a plan says Alice removes her red jacket but the endpoint shows it on, report jacket on and a conflict. If Alice is unseen, report not_visible or uncertain and leave unsupported state blank. Do not infer unseen wardrobe from identity Pictures.
Identity Pictures and names help map visible people to offered canonical Subject IDs only; never copy their historical outfit, pose or environment as current state. Unmapped people belong in unresolvedVisibleEntities. Never invent Subject IDs or Take IDs.
Describe visible endpoint state, not a synopsis or causal story. Motionless/prone/limp-looking may be visible; do not infer dead, unconscious or injured without literal visible support. Describe ongoing motion at the cut using the tail progression. Do not invent numerical camera parameters.
Return exactly one JSON object matching responseSchema, all fields required, no Markdown or surrounding prose. Include subjectStates only for a canonical Subject that is actually visible or materially uncertain in this endpoint evidence; candidates merely offered for recognition do not need an entry. Use empty strings/lists for unsupported facts and honest visibility/uncertainty. Do not use extended reasoning."""


def _validation_path(location):
    path = ""
    for part in location:
        path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + str(part)
    return path or "response"


def _diagnostic(message):
    message = str(message).replace("\r", " ").replace("\n", " ")[:500]
    message = f"Invalid observation response: {message}"
    logger.warning("Selected Take observation response rejected: %s", message)
    return invalid(message)


def _validation_diagnostic(exc):
    error = exc.errors(include_input=False)[0]
    path = _validation_path(error.get("loc", ()))
    return _diagnostic(f"Observation response field `{path}` failed validation ({error.get('type', 'validation_error')}): {error.get('msg', 'failed validation')}.")


def _normalize_subject_references(payload, candidates):
    if not isinstance(payload.get("subjectStates"), list):
        return payload
    by_id = {candidate["subjectId"]: candidate for candidate in candidates}
    by_label = {candidate["canonicalH3Label"]: candidate for candidate in candidates}
    narrative = {}
    for candidate in candidates:
        name = candidate.get("narrativeName")
        if name:
            narrative.setdefault(name, []).append(candidate)
            narrative.setdefault(name.casefold(), []).append(candidate)
    normalized = json.loads(json.dumps(payload, ensure_ascii=False))
    for index, subject in enumerate(normalized["subjectStates"]):
        if not isinstance(subject, dict) or "subjectId" not in subject:
            continue
        value = subject["subjectId"]
        candidate = by_id.get(value) or by_label.get(value)
        if candidate is None and isinstance(value, str):
            matches = narrative.get(value, []) or narrative.get(value.casefold(), [])
            unique = {item["subjectId"]: item for item in matches}
            if len(unique) == 1:
                candidate = next(iter(unique.values()))
            elif len(unique) > 1:
                raise _diagnostic(f"Subject identity in observation response field `subjectStates[{index}].subjectId` {value!r} is ambiguous among offered Subjects.")
        if candidate is None:
            raise _diagnostic(f"Subject identity in observation response field `subjectStates[{index}].subjectId` {value!r} was not one of the offered Subjects.")
        subject["subjectId"] = candidate["subjectId"]
    return normalized


def _parse_observation_response(raw, candidates, offered):
    if not isinstance(raw, str):
        raise _diagnostic("Observation response was not text.")
    if len(raw) > 80000:
        raise _diagnostic("Observation response exceeded the 80000-character limit.")
    text = raw.strip()
    if text.startswith("```"):
        match = re.fullmatch(r"```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```", text, flags=re.IGNORECASE)
        if not match:
            raise _diagnostic("Observation response used an invalid Markdown JSON fence; expected exactly one JSON object.")
        text = match.group(1).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _diagnostic(f"Observation response was not valid JSON: {exc.msg} at character {exc.pos}.") from exc
    if not isinstance(payload, dict):
        raise _diagnostic("Observation response JSON must be exactly one object.")
    payload = _normalize_subject_references(payload, candidates)
    try:
        state = ObservedEndState.model_validate(payload)
        state.validate_subjects(offered)
    except ValidationError as exc:
        raise _validation_diagnostic(exc) from exc
    except ValueError as exc:
        raise _diagnostic(f"Observation response failed validation: {exc}.") from exc
    return state


def image_part(data):
    return {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(data).decode()}}


def identity_parts(pictures, offered):
    from PIL import Image, ImageOps
    parts, used = [], set()
    for sid, path in pictures:
        if sid not in offered or sid in used:
            continue
        if len(used) >= 4:
            break
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 9 * 1024 * 1024:
                continue
            with Image.open(BytesIO(path.read_bytes())) as source:
                if source.width * source.height > 16_777_216:
                    continue
                image = ImageOps.exif_transpose(source).convert("RGB")
                image.thumbnail((512, 512))
                output = BytesIO(); image.save(output, format="JPEG", quality=80)
            parts.extend([{"type": "text", "text": f"Identity-only reference for subjectId {sid}; NOT endpoint state."}, image_part(output.getvalue())])
            used.add(sid)
        except (ValueError, OSError):
            continue
    return parts


def _recognition_candidates(store, directory, manifest, generations, generation_index):
    """Build analysis-only identity candidates without changing authoring Workspace state."""
    subjects, _ = store._sequence_cast(manifest)
    canonical = {subject.id: subject for subject in subjects}
    ordered: list[str] = []

    def add(ids):
        for subject_id in ids:
            if subject_id in canonical and subject_id not in ordered and len(ordered) < 16:
                ordered.append(subject_id)

    current = generations[generation_index]
    add(record.get("id") for record in current.workspace.get("subjects", []))
    predecessor = generations[generation_index - 1] if generation_index else None
    if predecessor:
        active = next((observation for observation in predecessor.take_observations
                       if predecessor.selected_take_id and observation.take_id == predecessor.selected_take_id), None)
        if active:
            add(subject.subject_id for subject in active.effective_observed_state.subject_states)
        add(record.get("id") for record in predecessor.workspace.get("subjects", []))
    for prior in reversed(generations[:max(0, generation_index - 1)]):
        add(record.get("id") for record in prior.workspace.get("subjects", []))
    # A canonical cast member first materialized by a later Generation is
    # future evidence, even though it now exists in the Sequence registry.
    future_local = {record.get("id") for future in generations[generation_index + 1:]
                    for record in future.workspace.get("subjects", [])}
    add(subject.id for subject in subjects if subject.id not in future_local)

    candidates = [{"subjectId": subject_id, "canonicalH3Label": f'<Subject {canonical[subject_id].number}>',
                   "narrativeName": canonical[subject_id].narrative_name}
                  for subject_id in ordered]
    pictures = []
    for subject_id in ordered:
        for source_generation in [current, *reversed(generations[:generation_index])]:
            entries = {entry.asset_id: entry for entry in source_generation.asset_files}
            assets = sorted(source_generation.workspace.get("assets", []), key=lambda asset: (asset.get("order", 0), asset.get("id", "")))
            asset = next((item for item in assets
                          if item.get("kind") == "image" and item.get("role") == "subject_identity"
                          and item.get("subjectIdentity", {}).get("subjectId") == subject_id
                          and item.get("id") in entries), None)
            if asset is None:
                continue
            path = store._output_path(directory, entries[asset["id"]].path)
            if path.is_file():
                pictures.append((subject_id, path))
                break
        if len(pictures) == 4:
            break
    return candidates, pictures


async def analyze_selected_take(store, project_id, generation_id, request, client_factory=OpenAICompatibleClient):
    if request.llm.supports_vision is not True:
        raise invalid("Select a vision-capable model and enable Supports Vision in LLM settings before analyzing a Take")
    with transaction():
        directory, generation, take, source, previous = store.observation_snapshot(
            project_id, generation_id, request.expected_project_fingerprint)
        if previous and previous.status == "user_edited" and not request.replace_corrections:
            raise invalid("Re-analyze will replace saved corrections; explicitly confirm replacement first")
        # Read only local metadata and at most four optional identity Pictures.
        # Unrelated/missing authoring Audio/Video/Pictures must not gate Take analysis.
        _, manifest, generations, _, _ = store._output_context(project_id, generation_id)
        generation_index = next(index for index, item in enumerate(generations) if item.generation_id == generation_id)
        candidates, pictures = _recognition_candidates(store, directory, manifest, generations, generation_index)
        offered = {candidate["subjectId"] for candidate in candidates}
    metadata, images = await asyncio.to_thread(store.prepare_observation_evidence, directory, take, source)
    identities = await asyncio.to_thread(identity_parts, pictures, offered)
    context = {"incomingState": generation.continuity.incoming_state[:4000],
               "intendedOutgoingState": generation.continuity.intended_outgoing_state[:4000],
               "creativeRequest": generation.workspace.get("creativeRequest", "")[:4000],
               "cameraIntent": generation.planning.camera_intent[:2000], "candidateSubjects": candidates,
               "sampledTimestamps": metadata.sampled_timestamps, "endpointTimestamp": metadata.endpoint_timestamp,
               "responseSchema": ObservedEndState.model_json_schema(by_alias=True)}
    parts = [{"type": "text", "text": json.dumps(context, ensure_ascii=False)},
             {"type": "text", "text": "Endpoint frame (actual last decoded frame):"}, image_part(images["endpoint"]),
             {"type": "text", "text": "Chronological final-tail contact sheet:"}, image_part(images["contact-sheet"]), *identities]
    if len(json.dumps(parts, ensure_ascii=False).encode("utf-8")) > 5 * 1024 * 1024:
        raise invalid("Vision evidence exceeds the 5 MiB payload limit")
    settings = request.llm.model_copy(update={"reasoning_mode": ReasoningMode.OFF, "seed": None,
        "max_tokens": min(request.llm.max_tokens, 6000), "temperature": 0})
    async with client_factory(settings) as client:
        raw = await client.generate([{"role": "system", "content": OBSERVATION_POLICY}, {"role": "user", "content": parts}],
                                    response_instruction=None, allow_retries=False)
    state = _parse_observation_response(raw, candidates, offered)
    observation = TakeObservation(takeId=take.id, takeSha256=take.sha256, analyzedAt=timestamp(),
        analyzerModel=settings.selected_model_id, offeredSubjectIds=sorted(offered), evidence=metadata,
        subjectLabels={c["subjectId"]: f'{c["narrativeName"]} / {c["canonicalH3Label"]}'.strip(" / ") for c in candidates},
        modelObservedState=state, effectiveObservedState=state.model_copy(deep=True))
    try:
        return store.commit_observation(project_id, generation_id, observation, previous, images)
    except Exception as exc:
        logger.warning("Selected Take observation commit rejected: %s", str(exc)[:500])
        raise
