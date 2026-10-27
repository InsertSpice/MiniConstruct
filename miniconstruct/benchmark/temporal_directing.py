"""Opt-in human-reviewed H3 temporal directing benchmark.

This benchmark intentionally leaves the ordinary H3 assembly and production
policies alone. Its experimental arm appends one benchmark-only section to the
existing production system message.

The provider-free renderer gate in the same suite writes deterministic final
H3 treatments and human timing-review packs without running a prompt writer.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid
import zipfile
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from miniconstruct.benchmark.runner import (
    BenchmarkProfile, FixtureAsset, _manual_render_inputs, _messages_provenance, _resolve_fixture_assets,
    _workspace_image_mappings, load_profiles,
)
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.h3.builder import assemble_prompt
from miniconstruct.h3.validator import validate_workspace_prompt
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.api import GenerationRequest, SeedMode
from miniconstruct.models.h3_continuity import H3ContinuityContext
from miniconstruct.models.workspace import Workspace, validate_authoring_workspace
from miniconstruct.dialogue_language import workspace_for_h3
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.h3.vision_input import model_vision_stats


SUITE_NAME = "h3-temporal-directing"
SCHEMA_VERSION = 1
STRUCTURAL_PRIORITY_RULE = (
    "Priority: Official H3 structure and reference rules outrank this temporal policy. "
    "Preserve every required <Subject N> and <Picture N> label, identity/reference provenance, "
    "canonical reference syntax, all required sections, exact dialogue, and mode-specific grammar; "
    "never trade them for brevity, temporal detail, action density, or style."
)
TEMPORAL_DIRECTING_POLICY = f"""BENCHMARK-ONLY TEMPORAL DIRECTING OVERLAY
{STRUCTURAL_PRIORITY_RULE}

Within the official H3 structure and task syntax, allocate useful descriptive detail
in this order: (1) observable change through time; (2) causal action, reaction, and
continuation; (3) subject position, orientation, travel direction, and interaction
geometry; (4) continuity of those spatial facts through cuts; (5) camera behavior
that reveals, follows, or responds to action; (6) only necessary environment and
appearance reinforcement; (7) decorative atmosphere last.

Translate verbs and dramatic intent into visible sequences when the supplied
authority supports them. Do not invent major story events, cross the current Scene
Plan or Generation boundary, or force motion into intentionally quiet scenes. Pauses,
stillness, held gazes, silence, and restrained movement remain valid when intentional.
Do not impose fixed beat intervals, action counts, or length targets. Use approximate
timings only when they clarify meaningful progression, synchronization, interaction,
or a cut. Make orientation and spatial transitions explicit where ambiguity risks
continuity. Avoid repeating reference-established appearance details unless identity
clarity requires them. Preserve exact dialogue, reference grammar, mode constraints,
subject identity, continuity authority, and every normative rule in the official H3
guide. This overlay prioritizes directing information; it does not replace that guide."""
TEMPORAL_DIRECTING_SYSTEM_SECTION = (
    "\n\n## Benchmark-only temporal directing policy\n\n" + TEMPORAL_DIRECTING_POLICY
)

HUMAN_REVIEW_FIELDS = [
    "structuralH3Validity", "plotStoryFidelity", "temporalInformationDensity",
    "staticPaddingBurden", "actionProgression", "spatialOrientationClarity",
    "cutContinuity", "cameraUsefulness", "appropriateStillness",
    "inventionDiscipline", "verbosityEfficiency", "overallPreference", "reviewerNote",
]
REVIEW_QUESTIONS = {
    "structuralH3Validity": "Is the output structurally valid H3?",
    "plotStoryFidelity": "Does it remain inside the current Generation's intended story scope?",
    "temporalInformationDensity": "Does it describe meaningful change through the clip?",
    "staticPaddingBurden": "Does it spend excessive text or time on poses, atmosphere, or repeated appearance?",
    "actionProgression": "Do actions have readable development rather than one vague verb?",
    "spatialOrientationClarity": "Are facing, travel direction, and interaction geometry clear where useful?",
    "cutContinuity": "Do later shots preserve or deliberately change those spatial facts?",
    "cameraUsefulness": "Does camera direction support action rather than merely decorate the prompt?",
    "appropriateStillness": "Does it avoid pointless motion in a quiet scene?",
    "inventionDiscipline": "Does it avoid premature plot events or unsupported major developments?",
    "verbosityEfficiency": "Are extra words buying useful temporal information?",
    "overallPreference": "Control, Experimental, or Tie?",
    "reviewerNote": "Free-form reviewer note",
}


class AuthorityMarker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=500)


class TemporalCase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,80}$")
    title: str = Field(min_length=1)
    authoring_path: str = Field(alias="authoringPath")
    workspace: dict[str, Any]
    duration_seconds: float = Field(alias="durationSeconds", ge=4, le=15)
    shot_count: int = Field(alias="shotCount", ge=1, le=12)
    requires_vision: bool = Field(default=False, alias="requiresVision")
    story_authority: dict[str, Any] | None = Field(default=None, alias="storyAuthority")
    continuity: dict[str, Any] = Field(default_factory=dict)
    exact_dialogue: str = Field(default="", alias="exactDialogue")
    fixture_assets: list[FixtureAsset] = Field(default_factory=list, alias="fixtureAssets")
    authority_markers: list[AuthorityMarker] = Field(default_factory=list, alias="authorityMarkers")

    @model_validator(mode="after")
    def path_authority(self):
        if self.authoring_path not in {"story", "one_shot"}:
            raise ValueError("authoringPath must be story or one_shot")
        if (self.authoring_path == "story") != (self.story_authority is not None):
            raise ValueError("Story cases require storyAuthority; one_shot cases must not have it")
        if self.story_authority is not None:
            current = self.story_authority.get("currentGeneration")
            plans = self.story_authority.get("orderedScenePlans")
            if (not isinstance(self.story_authority.get("storyPlanPremise"), str)
                    or not isinstance(current, dict) or not isinstance(current.get("scenePlan"), str)
                    or not isinstance(plans, list) or not plans):
                raise ValueError("Story authority requires a premise, current Scene Plan, and ordered Scene Plans")
            if any(not isinstance(plan, dict) or not isinstance(plan.get("ordinal"), int)
                   or not isinstance(plan.get("scenePlan"), str) or not isinstance(plan.get("current"), bool)
                   for plan in plans):
                raise ValueError("Every ordered Scene Plan requires ordinal, scenePlan, and current fields")
            if sum(plan["current"] for plan in plans) != 1:
                raise ValueError("Story authority must mark exactly one ordered Scene Plan current")
            current_plan = next(plan for plan in plans if plan["current"])
            if current_plan["scenePlan"] != current["scenePlan"]:
                raise ValueError("The current Story Scene Plan must match the ordered current Scene Plan")
        asset_ids = [item.asset_id for item in self.fixture_assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("fixtureAssets must have unique asset IDs")
        if self.authoring_path == "one_shot" and self.authority_markers:
            raise ValueError("authorityMarkers require Story authority")
        return self

    def model_workspace(self, fixture_root: Path) -> Workspace:
        raw = dict(self.workspace)
        raw["durationSeconds"] = self.duration_seconds
        raw["shots"] = self.shot_count
        if self.exact_dialogue:
            raw["dialogue"] = self.exact_dialogue
        workspace = Workspace.model_validate(raw, context={"authoring": True})
        return _resolve_fixture_assets(self, workspace, fixture_root)


def load_cases(path: Path) -> list[TemporalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != SCHEMA_VERSION or raw.get("suite") != SUITE_NAME:
        raise ValueError(f"Expected {SUITE_NAME} schemaVersion {SCHEMA_VERSION}")
    cases = [TemporalCase.model_validate(item) for item in raw.get("cases", [])]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Temporal directing suite must contain at least one case with unique IDs")
    for case in cases:
        case.model_workspace(path.parent)
    return cases


def capture_authority(case: TemporalCase, workspace: Workspace) -> dict[str, Any]:
    """Compact, reviewable, non-secret account of prompt-visible authority."""
    story = case.story_authority or {}
    current = story.get("currentGeneration") or {}
    marker_source = json.dumps(story, ensure_ascii=False).casefold()
    narrative_identities = []
    if case.authoring_path == "story":
        for item in story.get("characterProfiles", []):
            if not isinstance(item, dict):
                continue
            profile = item.get("characterProfile")
            profile_supplied = isinstance(profile, dict) and any(
                isinstance(value, str) and value.strip() for value in profile.values()
            )
            narrative_identities.append({
                "narrativeName": item.get("narrativeName"),
                "canonicalH3Label": item.get("canonicalH3Label"),
                "characterProfileSupplied": profile_supplied,
            })
    active_references = []
    for asset, label in numbered_assets(workspace):
        active_references.append({
            "label": label, "kind": asset.kind.value, "role": asset.role,
            "subjectId": asset.subject_identity.subject_id if asset.role == "subject_identity" else None,
            "filename": asset.filename,
        })
    return {
        "authoringPath": case.authoring_path,
        "storyPlanPremise": story.get("storyPlanPremise"),
        "currentScenePlan": current.get("scenePlan"),
        "neighboringScenePlans": [item for item in story.get("orderedScenePlans", [])
                                   if not item.get("current")],
        "relevantStoryMemoryProjection": story.get("storyMemoryBefore"),
        "continuityContext": {
            "sequenceContext": story.get("sequenceContext"),
            "incomingState": current.get("incomingState"),
            "intendedOutgoingState": current.get("intendedOutgoingState"),
            "relationshipToPrevious": current.get("relationshipToPrevious"),
            "cameraIntent": current.get("cameraIntent"),
            "h3ContinuityProjection": case.continuity,
        },
        "storyNarrativeIdentities": narrative_identities,
        "storyCharacterProfilesSupplied": any(item["characterProfileSupplied"] for item in narrative_identities),
        "exactDialoguePresence": bool(case.exact_dialogue or current.get("dialogue")),
        "exactDialogue": case.exact_dialogue or current.get("dialogue", ""),
        "activeReferenceSummary": active_references,
        "declaredAuthorityMarkers": [
            {"label": marker.label, "text": marker.text,
             "presentInStoryAuthority": marker.text.casefold() in marker_source}
            for marker in case.authority_markers
        ],
    }


def extract_description_body(prompt: str) -> str:
    """Extract the mode-appropriate H3 description section, without its heading."""
    match = re.search(
        r"(?ims)^(?:detailed_description|integrated_multimodal_description)\s*:\s*(.*?)(?=^\w[\w ]*\s*:|\Z)",
        prompt,
    )
    return match.group(1).strip() if match else ""


_H3_TIMESTAMP_RE = re.compile(r"\b\d{2}:\d{2}\.\d{3}\b|\b\d+(?:\.\d+)?\s*(?:seconds?|s)\b", re.I)


def message_metrics(prompt: str, validation: dict[str, Any], duration: float, shots: int) -> dict[str, Any]:
    description = extract_description_body(prompt)
    timestamps = _H3_TIMESTAMP_RE.findall(description)
    appearance_phrases = re.findall(r"\b(?:wearing|dressed in|with (?:a|an) [\w -]{1,40} (?:coat|shirt|dress|jacket|uniform))\b[^,.\n]{0,80}", description, re.I)
    repeated = sorted({phrase.strip().lower() for phrase in appearance_phrases
                       if len(appearance_phrases) > 1 and appearance_phrases.count(phrase) > 1})
    return {
        "totalCharacters": len(prompt), "totalWords": len(prompt.split()),
        "descriptionBodyCharacters": len(description), "descriptionBodyWords": len(description.split()),
        "numberOfShots": shots, "durationSeconds": duration,
        "explicitInternalTimestampCount": len(timestamps),
        "repeatedExactAppearancePhrases": repeated,
        "structuralValid": bool(validation.get("valid")),
    }


def review_sheet(case: TemporalCase) -> dict[str, Any]:
    return {"fields": {key: {"question": REVIEW_QUESTIONS[key], "value": None} for key in HUMAN_REVIEW_FIELDS},
            "variants": ["control", "experimental"]}


def _safe_model_messages(messages: list[dict[str, Any]], image_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep exact message ordering/text while replacing image bytes with provenance."""
    safe = deepcopy(messages)
    image_index = 0
    for message in safe:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_index += 1
            record = image_records[image_index - 1] if image_index <= len(image_records) else {"index": image_index}
            part["image_url"] = {"url": f"[image payload omitted; sha256={record.get('sha256')}]"}
            part["imageProvenance"] = record
    return safe


def _vision_records(messages: list[dict[str, Any]], mappings: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    provenance = _messages_provenance(messages, mappings)
    dimensions = model_vision_stats(messages)
    result = []
    for index, image in enumerate(provenance["images"]):
        item = dict(image)
        if index < len(dimensions):
            item.update(dimensions[index])
        result.append(item)
    return result


def _skipped_case(case: TemporalCase, workspace: Workspace, message: str) -> dict[str, Any]:
    return {
        "caseId": case.id, "title": case.title, "status": "skipped",
        "skip": {"code": "vision_capability_mismatch", "message": message},
        "storyAuthority": capture_authority(case, workspace),
        "durationSeconds": case.duration_seconds, "shotCount": case.shot_count,
        "reviewSheet": review_sheet(case), "variants": {},
    }


async def run_case(case: TemporalCase, settings, client_factory, variants: set[str], fixture_root: Path) -> dict[str, Any]:
    workspace = case.model_workspace(fixture_root)
    if case.authoring_path == "story":
        validate_authoring_workspace(workspace)
    if case.requires_vision and not settings.supports_vision:
        return _skipped_case(case, workspace,
            "This case requires a vision-enabled local profile so its identity Pictures reach the H3 writer. Select a profile with supportsVision=true.")
    authority = case.story_authority
    continuity = H3ContinuityContext.model_validate(case.continuity) if case.continuity else None
    assembled = assemble_prompt(workspace_for_h3(workspace), settings.supports_vision,
                                continuity_context=continuity, story_context=authority)
    outputs: dict[str, Any] = {}
    for variant in ("control", "experimental"):
        if variant not in variants:
            continue
        messages = deepcopy(assembled.messages)
        if variant == "experimental":
            if not messages or messages[0].get("role") != "system" or not isinstance(messages[0].get("content"), str):
                raise ValueError("Production H3 assembly must begin with a text system message")
            messages[0]["content"] += TEMPORAL_DIRECTING_SYSTEM_SECTION
        if variant == "control":
            request = GenerationRequest(workspace=workspace, llm=settings, resolvedSeeds=[1],
                                        authoringPath=case.authoring_path)
            # Story context is explicit benchmark authority; for this benchmark the same production
            # compiler and generation orchestrator are invoked with the fixture's saved projection.
            if authority is not None:
                request._story_context = authority
            if continuity is not None:
                request._continuity_context = continuity
            request._narrative_resolved = True
            generated = await generate_workspace_prompts(request, client_factory)
            prompt = generated.variations[0].generated.prompt
            validation = generated.variations[0].generated.validation.model_dump(mode="json")
            actual_payload = generated.variations[0].model_payload
            actual_messages = (actual_payload.get("messages", generated.assembled.messages)
                               if actual_payload else generated.assembled.messages)
        else:
            async with client_factory(settings.model_copy(update={"seed": 1})) as client:
                prompt = await client.generate(messages)
                actual_payload = getattr(client, "last_payload", None)
                submitted_messages = getattr(client, "messages", messages)
            validation = validate_workspace_prompt(prompt, workspace_for_h3(workspace)).model_dump(mode="json")
            actual_messages = (actual_payload.get("messages", submitted_messages)
                               if actual_payload else submitted_messages)
        image_mappings = _workspace_image_mappings(case, workspace, h3_derivative=settings.supports_vision)
        model_input_summary = _messages_provenance(actual_messages, image_mappings)
        image_records = _vision_records(actual_messages, image_mappings)
        outputs[variant] = {
            "modelFacingMessages": _safe_model_messages(actual_messages, image_records),
            "modelInput": model_input_summary,
            "visionInputs": image_records,
            "exactOutput": prompt,
            "validation": validation,
            "metrics": message_metrics(prompt, validation, case.duration_seconds, case.shot_count),
            "authoringPath": case.authoring_path,
        }
    return {"caseId": case.id, "title": case.title, "status": "completed",
            "storyAuthority": capture_authority(case, workspace),
            "durationSeconds": case.duration_seconds, "shotCount": case.shot_count,
            "reviewSheet": review_sheet(case), "variants": outputs}


async def run_suite(root: Path, profile: BenchmarkProfile, client_factory=OpenAICompatibleClient,
                    case_ids: set[str] | None = None, variants: set[str] | None = None) -> dict[str, Any]:
    cases = load_cases(root / "temporal-directing" / "cases.json")
    selected = [case for case in cases if case_ids is None or case.id in case_ids]
    if not selected:
        raise ValueError("No matching temporal-directing fixtures selected")
    if case_ids and case_ids - {case.id for case in selected}:
        raise ValueError(f"Unknown case IDs: {', '.join(sorted(case_ids - {case.id for case in selected}))}")
    variants = variants or {"control", "experimental"}
    fixture_root = root / "temporal-directing"
    settings = profile.to_settings().model_copy(update={
        "seed_mode": SeedMode.FIXED, "fixed_seed": 1, "seed": 1,
    })
    return {
        "schemaVersion": 1, "suite": SUITE_NAME,
        "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(), "profile": profile.public_dict(),
        "settings": {
            "source": "benchmark_profile_and_temporal_suite_harness",
            "modelId": settings.model_id,
            "temperature": settings.temperature,
            "maxTokens": settings.max_tokens,
            "contextWindow": settings.context_window,
            "reasoningMode": settings.reasoning_mode.value,
            "supportsVision": settings.supports_vision,
            "seedMode": settings.seed_mode.value,
            "effectiveSeed": settings.seed,
            "seedPolicy": "fixed seed 1 for temporal A/B; overrides source profile seed settings",
            "sourceProfileSeedMode": profile.seed_mode.value,
            "sourceProfileFixedSeed": profile.fixed_seed,
            "transport": "non_streaming",
            "llmJudge": "none",
        },
        "experimentalPolicy": TEMPORAL_DIRECTING_POLICY,
        "cases": [await run_case(case, settings, client_factory, variants, fixture_root) for case in selected],
    }


def write_run(root: Path, result: dict[str, Any]) -> Path:
    directory = root / "runs" / SUITE_NAME / result["runId"]
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [f"# {SUITE_NAME} review", "", f"Profile: {result['profile']['displayName']}", "",
             "Human review only; automated fields are descriptive diagnostics, not creative scores.", ""]
    for case in result["cases"]:
        lines.extend([f"## {case['title']} (`{case['caseId']}`)", "",
                      "### Authority supplied", "", "```json",
                      json.dumps(case["storyAuthority"], ensure_ascii=False, indent=2), "```", ""])
        if case.get("status") == "skipped":
            lines.extend([f"**Skipped:** {case['skip']['message']}", ""])
            continue
        for variant, value in case["variants"].items():
            lines.extend([f"### {variant.title()}", "", "#### Model input (images shown by identity and digest)", "", "```json",
                          json.dumps({"modelInput": value["modelInput"], "visionInputs": value["visionInputs"]}, ensure_ascii=False, indent=2),
                          "```", "", "#### Exact H3 output", "", "```text", value["exactOutput"], "```", "",
                          f"Diagnostics: {json.dumps(value['metrics'], ensure_ascii=False)}", ""])
        lines.extend(["### Review", "", "| Criterion | Control | Experimental |", "| --- | --- | --- |"])
        for key, value in case["reviewSheet"]["fields"].items():
            if key == "reviewerNote":
                continue
            lines.append(f"| {value['question']} |  |  |")
        lines.extend(["", "Reviewer note:", "", "", "Overall preference: Control / Experimental / Tie", ""])
    report = directory / "review.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


TIMING_REVIEW_QUESTIONS = [
    "Were all three events present?", "Were they in the intended order?",
    "Were events temporally separated rather than collapsed together?",
    "Did timestamped treatments place events closer to 2/4/6 seconds?",
    "Did exact wording outperform approximate wording?",
    "Did either timestamp treatment introduce unnatural waiting or freezing?",
    "Were there accidental cuts or transitions?", "Was there discontinuous teleporting?",
    "Was there abrupt pose snapping?", "Did H3 rush early events to make later landmarks?",
    "Was the final seated state reached?",
]


def renderer_fixture(root: Path) -> tuple[TemporalCase, dict[str, Any]]:
    raw = json.loads((root / "temporal-directing" / "renderer-case.json").read_text(encoding="utf-8"))
    if raw["schemaVersion"] != SCHEMA_VERSION or raw["suite"] != SUITE_NAME:
        raise ValueError("Expected temporal-directing renderer fixture schema 1")
    return TemporalCase.model_validate(raw["case"]), raw


def prepare_renderer_gate(root: Path) -> dict[str, Any]:
    """Deterministic final prompts: no provider, writer assembly, or rendering."""
    case, fixture = renderer_fixture(root)
    workspace = case.model_workspace(root / "temporal-directing")
    variants = {}
    for treatment, prefixes in fixture["treatments"].items():
        prompt = fixture["promptTemplate"].format(**dict(zip(
            ("event1", "event2", "event3"), prefixes, strict=True)))
        validation = validate_workspace_prompt(prompt, workspace_for_h3(workspace)).model_dump(mode="json")
        if not validation["valid"]:
            raise ValueError(f"Invalid renderer fixture {treatment}: {validation}")
        events = [{**event, "targetSeconds": None if treatment == "chronological-control" else event["targetSeconds"],
                   "observedOnsetSeconds": None, "observedCompletionSeconds": None,
                   "onsetDeltaSeconds": None, "completionDeltaSeconds": None, "notes": ""}
                  for event in fixture["events"]]
        variants[treatment] = {
            "exactOutput": prompt, "validation": validation,
            "renderMetadata": {
                "suite": SUITE_NAME, "caseId": case.id, "treatmentId": treatment,
                "durationSeconds": case.duration_seconds, "shotCount": case.shot_count,
                "aspectRatio": workspace.aspect_ratio,
                "oneShotContract": "one continuous shot; no cuts; no transitions",
                "rendererSettings": dict.fromkeys([
                    "seed", "workflowName", "workflowDigest", "model", "sampler", "steps",
                    "guidance", "width", "height", "fps", "otherSettings", "videoArtifact", "reviewer"]),
                "timingReview": {"events": events, "qualitativeNotes": "",
                                 "questions": [{"question": q, "answer": None} for q in TIMING_REVIEW_QUESTIONS],
                                 "scoring": "Human review only; no tolerance, pass threshold, or aggregate quality score. Record signed observed-minus-target deltas only for timestamped arms; onset and completion remain separate."},
            },
        }
    return {
        "schemaVersion": SCHEMA_VERSION, "suite": SUITE_NAME,
        "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-renderer-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "experiment": "sparse-intra-shot-renderer-isolation",
        "promptSource": "deterministic benchmark-authored final H3 fixture",
        "providerUsed": False, "renderPerformed": False, "llmJudge": "none",
        "cases": [{"caseId": case.id, "title": case.title, "variants": variants}],
    }


def write_renderer_gate(root: Path, result: dict[str, Any]) -> Path:
    """Use existing suite run paths and manual-render ZIP/manifest convention."""
    case, _ = renderer_fixture(root)
    workspace = case.model_workspace(root / "temporal-directing")
    files, manifest = _manual_render_inputs(case, root / "temporal-directing", None,
                                           SimpleNamespace(prompt_workspace=workspace), {})
    directory = root / "runs" / SUITE_NAME / result["runId"]
    directory.mkdir(parents=True, exist_ok=False)
    lines = [f"# {SUITE_NAME}: renderer timing review", "",
             "Render all three arms with one fixed renderer seed and identical workflow/settings.",
             "Stop if no meaningful difference. Only after plausible benefit, use two additional matched seeds (maximum nine videos).",
             "No LLM judge, automatic tolerance or aggregate quality score. Identity quality is not evaluated.", ""]
    for treatment, arm in result["cases"][0]["variants"].items():
        metadata = {**deepcopy(manifest), **arm["renderMetadata"],
                    "promptSha256": hashlib.sha256(arm["exactOutput"].encode("utf-8")).hexdigest()}
        name = f"{case.id}.{treatment}.manual-render.zip"
        review = [f"## {treatment}", "", f"Pack: `{name}`", "", "```text", arm["exactOutput"], "```", "",
                 "Record approximate first occurrence; missing/unobservable events stay blank with notes.", "",
                 "| Event | Target s | Onset s | Clear state s | Onset delta s | Clear state delta s | Notes |",
                 "| --- | --- | --- | --- | --- | --- | --- |"]
        for event in metadata["timingReview"]["events"]:
            target = event["targetSeconds"] if event["targetSeconds"] is not None else "none (control)"
            review.append(f"| {event['onset']} / {event['completion']} | {target} | | | | | |")
        review.extend(["", "Deltas are observed minus target, separately for onset and clear state; no target-time score for control.", "",
                       "Record renderer seed, workflow name/digest, model, sampler, steps, guidance, dimensions, fps, other settings, video artifact and reviewer in the manifest.", ""])
        review.extend(f"- {q} Answer: ___" for q in TIMING_REVIEW_QUESTIONS)
        review.extend(["", "Qualitative notes: ___", ""])
        with zipfile.ZipFile(directory / name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("prompt.txt", arm["exactOutput"])
            archive.writestr("manual-render-manifest.json", json.dumps(metadata, indent=2) + "\n")
            archive.writestr("review.md", "\n".join(review))
            for path, payload in sorted(files.items()):
                archive.writestr(path, payload)
        arm["manualRenderBundle"] = name
        lines.extend(review)
    (directory / "results.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    report = directory / "review.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in H3 temporal directing benchmark (human reviewed)")
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    parser.add_argument("--profile")
    parser.add_argument("--renderer-gate", action="store_true", help="Prepare provider-free final-prompt manual render packs")
    parser.add_argument("--profiles", type=Path, default=Path("benchmarks/models.local.json"))
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--variant", choices=["control", "experimental", "both"], default="both")
    args = parser.parse_args()
    if args.renderer_gate:
        if args.profile or args.cases or args.variant != "both":
            parser.error("--renderer-gate prepares the complete matched fixture; do not combine with writer options")
        print(write_renderer_gate(args.root, prepare_renderer_gate(args.root)))
        return
    if not args.profile:
        parser.error("--profile is required for the historical writer experiment")
    profiles = load_profiles(args.profiles)
    if args.profile not in profiles:
        parser.error(f"Profile {args.profile!r} not found in {args.profiles}")
    variants = {"control", "experimental"} if args.variant == "both" else {args.variant}
    result = asyncio.run(run_suite(args.root, profiles[args.profile], case_ids=set(args.cases) if args.cases else None,
                                   variants=variants))
    print(write_run(args.root, result))


if __name__ == "__main__":
    main()
