"""Opt-in, human-reviewed matched H3 character-performance benchmark."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
import uuid
import zipfile
from typing import Any

from miniconstruct.benchmark.runner import (
    BenchmarkProfile, _manual_render_inputs, _messages_provenance,
    _workspace_image_mappings, load_profiles,
)
from miniconstruct.benchmark.temporal_directing import (
    TemporalCase, _safe_model_messages, _vision_records, capture_authority,
)
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.api import GenerationRequest, SeedMode
from miniconstruct.models.h3_continuity import H3ContinuityContext
from miniconstruct.models.workspace import validate_authoring_workspace


SUITE_NAME = "h3-character-performance"
PERFORMANCE_POLICY = """BENCHMARK-ONLY CHARACTER PERFORMANCE DIRECTING OVERLAY
The official H3 structure, exact dialogue, current Scene Plan, Character Profiles,
Creative Direction, Subject identity, references, continuity, and user intent retain
their existing authority. Identity Pictures and Reference Sheets establish stable
likeness and appearance, not a frozen expression, pose, gaze, or mouth state.
Subject Identity Fidelity constrains who the Subject is; preserve facial likeness
while allowing transient expression to follow the target scene.

Where that authority supports an emotional response, direct its concrete visible
or audible progression and timing rather than merely naming the emotion. Choose
only useful cues: eyes, brows, mouth, gaze, head angle, breath, shoulders, posture,
hands, hesitation, recoil, relaxation, proximity, voice, and interaction. Let
Character Profiles shape how each Subject responds, including a non-speaking
listener when appropriate. Do not invent emotion, dialogue, or story events.
Stillness, neutrality, numbness, stoicism, awkwardness, and subtle restraint can
be the right performance. Do not animate every face, force melodrama or close-ups,
repeat identity description in each shot, or add detail that does not help the
scene. Preserve action readability and all official H3 reference rules."""
PERFORMANCE_SYSTEM_SECTION = "\n\n## Benchmark-only character performance policy\n\n" + PERFORMANCE_POLICY

REVIEW_QUESTIONS = [
    "Does Experimental describe more concrete, scene-appropriate character performance?",
    "Do expressions and reactions evolve naturally through time instead of staying a static adjective?",
    "Are reactions caused by the supplied dialogue, action, or event?",
    "Does a non-speaking Subject respond when the scene calls for it?",
    "Does the Character Profile shape how the character reacts without being copied mechanically?",
    "With Strict fidelity, are stable likeness and provenance clear while expression can change from the neutral sheet?",
    "Is identity description repeated excessively?",
    "Is Experimental unnecessarily verbose, overacted, or emotionally unsupported?",
    "Does the quiet case remain restrained without needless facial or camera activity?",
    "Does performance stay integrated with physical action in the active case?",
    "Would Experimental plausibly yield a more alive performance than Control?",
]


def load_cases(path: Path) -> list[TemporalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != 1 or raw.get("suite") != SUITE_NAME:
        raise ValueError(f"Expected {SUITE_NAME} schemaVersion 1")
    cases = [TemporalCase.model_validate(item) for item in raw.get("cases", [])]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Character performance cases require unique IDs")
    for case in cases:
        if case.exact_dialogue:
            raise ValueError("Character performance dialogue must be stored in the Workspace fixture")
        workspace = case.model_workspace(path.parent)
        if case.story_authority is not None and case.story_authority["currentGeneration"].get("dialogue", "") != workspace.dialogue:
            raise ValueError("Current Story dialogue must match canonical Workspace dialogue")
    return cases


def _request(case: TemporalCase, workspace, settings) -> GenerationRequest:
    request = GenerationRequest(workspace=workspace, llm=settings, resolvedSeeds=[1],
                                authoringPath=case.authoring_path)
    if case.story_authority is not None:
        request._story_context = deepcopy(case.story_authority)
    if case.continuity:
        request._continuity_context = H3ContinuityContext.model_validate(case.continuity)
    request._narrative_resolved = True
    return request


def _check(name: str, passed: bool, evidence: Any = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "evidence": evidence}


def _finding_codes(validation: dict[str, Any]) -> set[str]:
    return {item.get("code", "") for item in validation.get("findings", [])}


def _user_text(messages: list[dict[str, Any]]) -> str:
    chunks = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            chunks.extend(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
    return "\n".join(chunks)


def _overlay_count(messages: list[dict[str, Any]], section: str = PERFORMANCE_SYSTEM_SECTION) -> int:
    if not messages or messages[0].get("role") != "system" or not isinstance(messages[0].get("content"), str):
        return 0
    return messages[0]["content"].count(section)


def _remove_exact_overlay(messages: list[dict[str, Any]], section: str = PERFORMANCE_SYSTEM_SECTION) -> list[dict[str, Any]] | None:
    """Remove only the one canonical benchmark section from final system input."""
    if _overlay_count(messages, section) != 1:
        return None
    result = deepcopy(messages)
    result[0]["content"] = result[0]["content"].replace(section, "", 1)
    return result


def _overlay_isolation_passes(control: list[dict[str, Any]], experimental: list[dict[str, Any]],
                              section: str = PERFORMANCE_SYSTEM_SECTION) -> bool:
    stripped = _remove_exact_overlay(experimental, section)
    return _overlay_count(control, section) == 0 and stripped == control


def capture_variant(case, workspace, settings, generated):
    """Shared benchmark evidence for a completed production H3 variation."""
    variation = generated.variations[0]
    payload = variation.model_payload or {}
    messages = payload.get("messages", generated.assembled.messages)
    mappings = _workspace_image_mappings(case, workspace, h3_derivative=settings.supports_vision is True)
    images = _vision_records(messages, mappings)
    arm = {
        "modelFacingMessages": _safe_model_messages(messages, images),
        "modelInput": _messages_provenance(messages, mappings),
        "visionInputs": images,
        "exactOutput": variation.generated.prompt,
        "validation": variation.generated.validation.model_dump(mode="json"),
        "requestedSeed": variation.requested_seed,
        "effectiveSeed": variation.effective_seed,
        "seedUnsupported": variation.seed_unsupported,
        "compatibilityFallback": variation.compatibility_fallback,
        "requestDurationMs": variation.request_duration_ms,
        "usage": variation.usage,
    }
    return arm, deepcopy(messages)


async def run_case(case: TemporalCase, settings, client_factory, fixture_root: Path, *,
                   system_section: str = PERFORMANCE_SYSTEM_SECTION,
                   review_questions: list[str] = REVIEW_QUESTIONS,
                   message_capture: dict[str, list[dict[str, Any]]] | None = None,
                   arm_client_factory=None) -> dict[str, Any]:
    workspace = case.model_workspace(fixture_root)
    canonical_dialogue = workspace.dialogue
    if case.authoring_path == "story":
        validate_authoring_workspace(workspace)
    evidence = capture_authority(case, workspace)
    evidence["subjectIdentityFidelity"] = workspace.creative_controls.subject_identity_fidelity.level.value
    evidence["referenceSheetDesignated"] = any(
        asset.role == "subject_identity" and asset.subject_identity.layout.value == "reference_sheet"
        for asset in workspace.assets
    )
    if case.requires_vision and settings.supports_vision is not True:
        return {"caseId": case.id, "title": case.title, "status": "skipped",
                "skip": {"code": "vision_capability_mismatch", "message": "Use a vision-enabled benchmark profile."},
                "authority": evidence, "variants": {}, "reviewQuestions": review_questions}

    variants: dict[str, Any] = {}
    raw_messages: dict[str, list[dict[str, Any]]] = {}
    for name, section in (("control", None), ("experimental", system_section)):
        request = _request(case, workspace, settings)
        if arm_client_factory is not None:
            # Benchmark-owned treatment after ordinary production assembly.
            generated = await generate_workspace_prompts(
                request, lambda settings: arm_client_factory(name, settings))
        elif section is None:
            generated = await generate_workspace_prompts(request, client_factory)
        else:
            generated = await generate_workspace_prompts(request, client_factory,
                                                          benchmark_system_section=section)
        variants[name], raw_messages[name] = capture_variant(case, workspace, settings, generated)
        if message_capture is not None:
            message_capture[name] = deepcopy(raw_messages[name])

    control, experimental = raw_messages["control"], raw_messages["experimental"]
    checks = [
        _check("both_variants_completed", set(variants) == {"control", "experimental"}),
        _check("only_overlay_differs_in_model_messages", _overlay_isolation_passes(control, experimental, system_section)),
        _check("control_has_no_overlay", _overlay_count(control, system_section) == 0),
        _check("experimental_has_overlay", _overlay_count(experimental, system_section) == 1),
        _check("settings_and_seed_matched", variants["control"]["requestedSeed"] == variants["experimental"]["requestedSeed"] == 1
               and variants["control"]["effectiveSeed"] == variants["experimental"]["effectiveSeed"]),
        _check("workspace_authority_matched", True, "One validated Workspace and Story projection supplied to both calls"),
        _check("validator_results_captured", all("valid" in arm["validation"] for arm in variants.values())),
        _check("expected_shot_count_valid", all(not _finding_codes(arm["validation"]) &
               {"missing_shot_1", "shot_sequence", "shot_count"} for arm in variants.values())),
        _check("reference_labels_legal", all(not _finding_codes(arm["validation"]) &
               {"bare_reference_label", "dangling_asset", "dangling_subject", "identity_reference_missing"}
               for arm in variants.values())),
        _check("identity_fidelity_recorded", bool(evidence["subjectIdentityFidelity"]), evidence["subjectIdentityFidelity"]),
        _check("profile_presence_recorded", "storyCharacterProfilesSupplied" in evidence,
               evidence["storyCharacterProfilesSupplied"]),
        _check("dedicated_exact_dialogue_authority", not canonical_dialogue or
               all("EXACT RESOLVED SPOKEN OUTPUT" in _user_text(messages)
                   and canonical_dialogue in _user_text(messages).split("EXACT RESOLVED SPOKEN OUTPUT", 1)[1]
                   for messages in raw_messages.values())),
        _check("exact_dialogue_preserved_in_output", not canonical_dialogue or
               all("dialogue_changed" not in _finding_codes(arm["validation"]) for arm in variants.values())),
        _check("vision_reference_transmitted", not case.requires_vision or
               all(len(arm["visionInputs"]) >= 1 for arm in variants.values())),
    ]
    return {"caseId": case.id, "title": case.title, "status": "completed",
            "authority": evidence, "mode": workspace.mode.value,
            "durationSeconds": case.duration_seconds, "shotCount": case.shot_count,
            "checks": checks, "reviewQuestions": review_questions, "variants": variants}


async def run_suite(root: Path, profile: BenchmarkProfile, client_factory=OpenAICompatibleClient,
                    case_ids: set[str] | None = None) -> dict[str, Any]:
    fixture_root = root / "character-performance"
    cases = load_cases(fixture_root / "cases.json")
    selected = [case for case in cases if case_ids is None or case.id in case_ids]
    if not selected or (case_ids and case_ids - {case.id for case in selected}):
        raise ValueError("Unknown or empty character performance case selection")
    settings = profile.to_settings().model_copy(update={"seed_mode": SeedMode.FIXED, "fixed_seed": 1, "seed": 1})
    return {"schemaVersion": 1, "suite": SUITE_NAME,
            "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "profile": profile.public_dict(),
            "settings": {"modelId": settings.model_id, "supportsVision": settings.supports_vision,
                         "temperature": settings.temperature, "maxTokens": settings.max_tokens,
                         "reasoningMode": settings.reasoning_mode.value,
                         "seedMode": "fixed", "effectiveSeed": 1, "transport": "non_streaming",
                         "llmJudge": "none"},
            "experimentalPolicy": PERFORMANCE_POLICY,
            "cases": [await run_case(case, settings, client_factory, fixture_root) for case in selected]}


def write_run(root: Path, result: dict[str, Any], *, fixture_name: str = "character-performance",
              case_loader=load_cases, arm_case_resolver=None) -> Path:
    directory = root / "runs" / result["suite"] / result["runId"]
    directory.mkdir(parents=True, exist_ok=False)
    fixture_root = root / fixture_name
    by_id = {case.id: case for case in case_loader(fixture_root / "cases.json")}
    lines = [f"# {result['suite']} review", "", "Review exact prompts side by side. Machine checks do not score creative quality.", ""]
    if "renderPriority" in result:
        lines.extend(["Matched render priority: " + ", ".join(result["renderPriority"]) + ".", ""])
    if result.get("renderPriorityNote"):
        lines.extend([result["renderPriorityNote"], ""])
    for case_result in result["cases"]:
        lines.extend([f"## {case_result['title']} (`{case_result['caseId']}`)", ""])
        if case_result["status"] == "skipped":
            lines.extend([f"Skipped: {case_result['skip']['message']}", ""])
            continue
        case = by_id[case_result["caseId"]]
        workspace = case.model_workspace(fixture_root)
        files, manifest = _manual_render_inputs(case, fixture_root, None,
                                                SimpleNamespace(prompt_workspace=workspace), {})
        for name, arm in case_result["variants"].items():
            if arm_case_resolver is not None:
                arm_case = arm_case_resolver(case, name)
                arm_workspace = arm_case.model_workspace(fixture_root)
                files, manifest = _manual_render_inputs(arm_case, fixture_root, None,
                    SimpleNamespace(prompt_workspace=arm_workspace), {})
            bundle = directory / f"{case.id}.{name}.manual-render.zip"
            bundle_manifest = {**deepcopy(manifest), "promptSha256": hashlib.sha256(arm["exactOutput"].encode()).hexdigest(),
                               "durationSeconds": case.duration_seconds, "shotCount": case.shot_count,
                               "aspectRatio": workspace.aspect_ratio,
                               "creativeControls": workspace.creative_controls.model_dump(mode="json", by_alias=True),
                               "subjectIdentityFidelity": workspace.creative_controls.subject_identity_fidelity.level.value}
            if "promptDetail" in arm:
                bundle_manifest.update({
                    "suite": result["suite"], "caseId": case.id, "variant": name,
                    "promptDetail": arm["promptDetail"], "livelinessOverlay": arm["livelinessOverlay"],
                    "profile": result["profile"], "settings": result["settings"],
                    "mode": case_result["mode"],
                })
                bundle_manifest["creativeControls"]["promptDetail"] = arm["promptDetail"]
            bundle_manifest.update(arm.get("renderMetadata", {}))
            if "referenceRoles" in arm:
                for reference in bundle_manifest["references"]:
                    reference["role"] = arm["referenceRoles"][reference["h3Label"]]
            for key in ("livelinessExperimentRevision", "livelinessTreatment"):
                if key in arm:
                    bundle_manifest[key] = arm[key]
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("prompt.txt", arm["exactOutput"])
                archive.writestr("manual-render-manifest.json", json.dumps(bundle_manifest, indent=2) + "\n")
                for path, payload in sorted(files.items()):
                    archive.writestr(path, payload)
            arm["manualRenderBundle"] = bundle.name
            lines.extend([f"### {name.title()}", "", f"Manual render pack: `{bundle.name}`", ""])
            if "renderEligible" in arm:
                eligibility = "yes (no structural ERROR findings)" if arm["renderEligible"] else "no (structural ERROR findings; archive only)"
                lines.extend([f"Structurally render eligible: **{eligibility}**. This is not a creative-quality judgment.", ""])
                lines.extend(f"- {finding['severity']} `{finding['code']}`: {finding['message']}"
                             for finding in arm["validation"]["findings"])
                lines.append("")
            if "treatmentCompliant" in arm:
                compliant = "yes" if arm["treatmentCompliant"] else "no"
                matched = "yes" if arm["matchedRenderEligible"] else "no"
                lines.extend([f"Treatment compliant: **{compliant}**.", "",
                              f"Matched render candidate: **{matched}**.", ""])
                lines.extend(f"- Treatment requirement `{item['requirement']}`: {'met' if item['passed'] else 'not met'}"
                             for item in arm.get("treatmentComplianceChecks", []))
                lines.append("")
            # Use a longer display fence so an archived fenced provider response
            # cannot close the review document's enclosing fence. Output is untouched.
            fence = "`" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"`+", arm["exactOutput"])), default=3))
            lines.extend([fence + "text", arm["exactOutput"], fence, ""])
        lines.extend(["### Human review", "", *[f"- {q}" for q in case_result["reviewQuestions"]], ""])
    (directory / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = directory / "review.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Opt-in matched H3 character performance benchmark")
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profiles", type=Path, default=Path("benchmarks/models.local.json"))
    parser.add_argument("--case", action="append", dest="cases")
    args = parser.parse_args()
    profiles = load_profiles(args.profiles)
    if args.profile not in profiles:
        parser.error(f"Profile {args.profile!r} not found in {args.profiles}")
    result = asyncio.run(run_suite(args.root, profiles[args.profile], case_ids=set(args.cases) if args.cases else None))
    print(write_run(args.root, result))


if __name__ == "__main__":
    main()
