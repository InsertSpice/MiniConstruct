"""Opt-in matched writer research; timing numbers are evidence, never quality scores."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid
import zipfile

from pydantic import BaseModel, Field, model_validator

from miniconstruct.benchmark.character_performance import capture_variant, _overlay_isolation_passes
from miniconstruct.benchmark.recording import RecordingClient
from miniconstruct.benchmark.runner import (
    _effective_request_parameters, _git_info, _messages_provenance, _seed_provenance, load_profiles,
)
from miniconstruct.benchmark.temporal_directing import TemporalCase
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.h3.prompt_text import mask_literal_text
from miniconstruct.h3.validator import FIELD_RE, SHOT_RE
from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.llm.compatibility import DIRECT_OUTPUT_INSTRUCTION
from miniconstruct.models.api import GenerationRequest, SeedMode

SUITE_NAME = "h3-shot-timing"
TIMING_POLICY = """BENCHMARK-ONLY SHOT TIMING POLICY
Choose shot boundaries from meaningful scene, action, dialogue and reaction beats
and the scene's pacing. Do not default to equal mathematical subdivision of total
duration by shot count. Unequal shot lengths are valid. Equal or near-equal spacing
remains valid when the scene genuinely calls for it. Never add arbitrary timing
jitter merely to appear irregular. Explicit user timing remains authoritative.
Preserve all existing official H3 structure, reference rules and other writer
policy; this section changes only the choice of shot boundaries."""
TIMING_SYSTEM_SECTION = "\n\n## Benchmark-only shot timing policy\n\n" + TIMING_POLICY
APPROXIMATE_TOLERANCE_MS = 100
REVIEW_QUESTIONS = [
    "Do the cut times correspond to meaningful scene/action/dialogue beats?",
    "Does Control appear to use equal subdivision mechanically?",
    "Does Experimental produce timing that better fits the described pacing?",
    "Does Experimental introduce arbitrary or gratuitous irregularity?",
    "Are individual shots given enough time for their assigned action?",
    "Are any shots implausibly short or unnecessarily long?",
    "In the regular-rhythm case, does Experimental allow sensible near-equal timing?",
    "Is explicit user timing preserved exactly where required, including the event at the cut?",
    "Is the scene content otherwise materially comparable between arms?",
]


class TimingRecordingClient(RecordingClient):
    """Retain the ordinary H3 client's default response instruction when recording."""

    async def generate(self, messages):
        return await super().generate(messages, response_instruction=DIRECT_OUTPUT_INSTRUCTION)


class ExplicitCut(BaseModel):
    model_config = {"extra": "forbid", "populate_by_name": True}
    shot: int = Field(ge=2)
    start_ms: int = Field(alias="startMs", ge=1)
    requirement: str = Field(min_length=1)


class TimingCase(TemporalCase):
    explicit_cuts: list[ExplicitCut] = Field(default_factory=list, alias="explicitCuts")

    @model_validator(mode="after")
    def timing_contract(self):
        if (self.authoring_path != "one_shot" or self.workspace.get("mode") != "T2VA"
                or self.shot_count < 2 or self.exact_dialogue or self.fixture_assets
                or self.workspace.get("assets") or self.continuity):
            raise ValueError("Shot timing fixtures require isolated multi-shot one_shot T2VA")
        for key, expected in (("durationSeconds", self.duration_seconds), ("shots", self.shot_count)):
            if key in self.workspace and self.workspace[key] != expected:
                raise ValueError(f"Conflicting Workspace {key}")
        if self.workspace.get("variations", 1) != 1:
            raise ValueError("Timing benchmark requires one variation per arm")
        if len({cut.shot for cut in self.explicit_cuts}) != len(self.explicit_cuts):
            raise ValueError("Duplicate explicit shot requirement")
        cuts = sorted(self.explicit_cuts, key=lambda cut: cut.shot)
        if any(cut.shot > self.shot_count or cut.start_ms >= self.duration_seconds * 1000
               or cut.requirement not in self.workspace.get("creativeRequest", "") for cut in cuts):
            raise ValueError("Explicit cut must be in range and have canonical user authority")
        if any(a.start_ms >= b.start_ms for a, b in zip(cuts, cuts[1:])):
            raise ValueError("Explicit cuts must increase")
        return self


def load_cases(path: Path) -> list[TimingCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != 1 or raw.get("suite") != SUITE_NAME:
        raise ValueError(f"Expected {SUITE_NAME} schemaVersion 1")
    cases = [TimingCase.model_validate(item) for item in raw.get("cases", [])]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Timing cases require unique IDs")
    for case in cases:
        case.model_workspace(path.parent)
    return cases


def timing_evidence(prompt: str, case: TimingCase, structurally_eligible: bool) -> dict:
    # Match the validator's masked, mode-specific description scope and grammar.
    text = mask_literal_text(prompt)
    fields = list(FIELD_RE.finditer(text))
    body = ""
    for index, field in enumerate(fields):
        if field.group("name") == "integrated_multimodal_description":
            body = text[field.end():fields[index + 1].start() if index + 1 < len(fields) else len(text)]
            break
    matches = list(SHOT_RE.finditer(body))
    numbers = [int(match.group(1)) for match in matches]
    starts = []
    emitted = []
    for match in matches:
        stamp = None if match.group(2) is None else (
            int(match.group(2)) * 60000 + int(match.group(3)) * 1000 + int(match.group(4)))
        emitted.append(stamp)
        starts.append(0 if int(match.group(1)) == 1 and stamp is None else stamp)
    duration_ms = round(case.duration_seconds * 1000)
    complete = (numbers == list(range(1, case.shot_count + 1)) and starts[0] == 0
                and all(value is not None for value in starts)
                and all(a < b for a, b in zip(starts, starts[1:]))
                and starts[-1] < duration_ms)
    eligible = structurally_eligible and complete
    durations = [b - a for a, b in zip(starts, starts[1:] + [duration_ms])] if eligible else None
    # Integer arithmetic avoids floating-point equality and includes the final hold.
    exact = all(value * case.shot_count == duration_ms for value in durations) if eligible else None
    approximate = all(abs(value * case.shot_count - duration_ms) <=
                      APPROXIMATE_TOLERANCE_MS * case.shot_count for value in durations) if eligible else None
    explicit = []
    for cut in case.explicit_cuts:
        actual = starts[cut.shot - 1] if complete else None
        explicit.append({"shot": cut.shot, "requirement": cut.requirement,
                         "requiredStartMs": cut.start_ms, "observedStartMs": actual,
                         "timestampPreservedExactly": actual == cut.start_ms if eligible else None,
                         "eventAtCutHumanReview": ""})
    return {"requestedDurationSeconds": case.duration_seconds, "requestedShotCount": case.shot_count,
            "observedShotNumbers": numbers, "exactShotCountCompliance": len(matches) == case.shot_count,
            "emittedStartTimestampMs": emitted, "shotStartsMs": starts,
            "openingStartInferred": bool(matches and numbers[0] == 1 and emitted[0] is None),
            "timestampSequenceComplete": complete, "eligibleForTimingDescription": eligible,
            "derivedShotDurationsMs": durations, "exactlyEquallySpaced": exact,
            "approximatelyEquallySpaced": approximate, "approximateToleranceMs": APPROXIMATE_TOLERANCE_MS,
            "explicitTiming": explicit,
            "explicitTimestampAuthorityPreserved": all(item["timestampPreservedExactly"] for item in explicit)
                if eligible and explicit else None,
            "explicitEventAuthorityHumanReview": ""}


async def run_case(case, settings, client_factory, fixture_root, seed=1):
    workspace = case.model_workspace(fixture_root)
    variants, payloads = {}, {}
    for name, section in (("control", None), ("experimental", TIMING_SYSTEM_SECTION)):
        recorders = []

        def factory(effective_settings):
            recorder = TimingRecordingClient(client_factory(effective_settings), [], effective_settings)
            recorders.append(recorder)
            return recorder

        request = GenerationRequest(workspace=workspace.model_copy(deep=True), llm=settings,
                                    resolvedSeeds=[seed], authoringPath="one_shot")
        request._narrative_resolved = True
        try:
            generated = await generate_workspace_prompts(request, factory, benchmark_system_section=section)
            arm, _ = capture_variant(case, workspace, settings, generated)
            arm.update({"providerRequestSuccess": True, "structuralEligibility": arm["validation"]["valid"]})
            variation = generated.variations[0]
        except LLMBackendError as exc:
            # No raw exception/provider body: these can contain credentials.
            arm = {"providerRequestSuccess": False, "structuralEligibility": None,
                   "error": {"category": exc.category, "message": "Provider request failed; no regeneration attempted."}}
            variation = None
        if len(recorders) != 1 or recorders[0].payload is None:
            raise ValueError("Timing benchmark requires exactly one captured generation request per arm")
        payload = deepcopy(recorders[0].payload)
        payloads[name] = payload
        arm["modelInput"] = _messages_provenance(payload["messages"], {})
        arm["modelFacingMessages"] = payload["messages"]  # T2VA fixture contract excludes media.
        arm["effectiveRequest"] = _effective_request_parameters(payload)
        arm["requestedSeed"] = seed
        if variation is not None:
            arm["seedProvenance"] = _seed_provenance(
                requested_seed=seed, mode="fixed", effective_seed=variation.effective_seed,
                seed_unsupported=variation.seed_unsupported, known_unsupported=variation.seed_known_unsupported,
                final_request_included_seed="seed" in payload)
            arm["timingEvidence"] = timing_evidence(arm["exactOutput"], case, arm["structuralEligibility"])
        else:
            arm["timingEvidence"] = None
        variants[name] = arm
    control, experimental = payloads["control"], payloads["experimental"]
    stripped = deepcopy(experimental)
    stripped["messages"] = deepcopy(control["messages"])
    isolation = (_overlay_isolation_passes(control["messages"], experimental["messages"], TIMING_SYSTEM_SECTION)
                 and stripped == control)
    return {"caseId": case.id, "title": case.title, "repeatSeed": seed,
            "durationSeconds": case.duration_seconds, "shotCount": case.shot_count, "mode": "T2VA",
            "workspace": workspace.model_dump(mode="json", by_alias=True),
            "matchedRequestIsolation": isolation, "variants": variants,
            "reviewQuestions": REVIEW_QUESTIONS, "humanReview": {"reviewer": "", "notes": "", "preference": ""}}


async def run_suite(root, profile, client_factory=OpenAICompatibleClient, case_ids=None, repeats=1):
    if repeats not in (1, 2, 3):
        raise ValueError("Use one to three matched seeds; review the first seed before expanding")
    fixture_root = root / "shot-timing"
    cases = load_cases(fixture_root / "cases.json")
    selected = [case for case in cases if case_ids is None or case.id in case_ids]
    if not selected or (case_ids and case_ids - {case.id for case in selected}):
        raise ValueError("Unknown or empty shot timing case selection")
    settings = profile.to_settings().model_copy(update={"seed_mode": SeedMode.FIXED, "fixed_seed": 1, "seed": 1})
    return {"schemaVersion": 1, "suite": SUITE_NAME,
            "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            "git": _git_info(root.parent), "profile": profile.public_dict(),
            "settings": _effective_request_parameters({"model": settings.selected_model_id,
                "temperature": settings.temperature, "max_tokens": settings.max_tokens}),
            "reasoningMode": settings.reasoning_mode.value, "generationTransport": "non_streaming",
            "experimentalPolicy": TIMING_POLICY, "experimentalSystemSection": TIMING_SYSTEM_SECTION,
            "repeats": repeats, "writerSeeds": list(range(1, repeats + 1)), "llmJudge": "none",
            "cases": [await run_case(case, settings.model_copy(update={"seed": seed, "fixed_seed": seed}),
                                     client_factory, fixture_root, seed)
                      for seed in range(1, repeats + 1) for case in selected]}


def write_run(root: Path, result: dict) -> Path:
    directory = root / "runs" / SUITE_NAME / result["runId"]
    directory.mkdir(parents=True, exist_ok=False)
    lines = [f"# {SUITE_NAME} human review", "", "Timing numbers are descriptive evidence, not a quality score.", ""]
    for case in result["cases"]:
        lines.extend([f"## {case['title']} — writer seed {case['repeatSeed']}", "",
                      f"Matched request isolation: {case['matchedRequestIsolation']}", ""])
        for name, arm in case["variants"].items():
            lines.extend([f"### {name.title()}", "", f"Provider success: {arm['providerRequestSuccess']}",
                          f"Structural eligibility: {arm['structuralEligibility']}", ""])
            if not arm["providerRequestSuccess"]:
                lines.extend([arm["error"]["message"], ""])
                continue
            manifest = {"schemaVersion": 1, "suite": SUITE_NAME, "caseId": case["caseId"], "variant": name,
                        "mode": case["mode"], "durationSeconds": case["durationSeconds"], "shotCount": case["shotCount"],
                        "aspectRatio": case["workspace"]["aspectRatio"], "writerSeed": case["repeatSeed"],
                        "profile": result["profile"], "effectiveRequest": arm["effectiveRequest"],
                        "seedProvenance": arm["seedProvenance"], "reasoningMode": result["reasoningMode"],
                        "structuralEligibility": arm["structuralEligibility"], "validation": arm["validation"],
                        "timingEvidence": arm["timingEvidence"], "references": [],
                        "promptSha256": hashlib.sha256(arm["exactOutput"].encode()).hexdigest(),
                        "rendererSeed": None, "rendererSettings": {}, "videoArtifact": "",
                        "reviewQuestions": case["reviewQuestions"], "humanReview": case["humanReview"]}
            bundle = directory / f"{case['caseId']}.seed-{case['repeatSeed']}.{name}.manual-render.zip"
            with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("prompt.txt", arm["exactOutput"])
                archive.writestr("manual-render-manifest.json", json.dumps(manifest, indent=2) + "\n")
                archive.writestr("review.md", "\n".join("- " + q for q in case["reviewQuestions"]) + "\n")
            arm["manualRenderBundle"] = bundle.name
            fence = "`" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"`+", arm["exactOutput"])), default=3))
            lines.extend([f"Manual render pack: `{bundle.name}` (invalid output is archive evidence only).", "",
                          "Timing evidence:", "", "```json", json.dumps(arm["timingEvidence"], indent=2), "```", "",
                          fence + "text", arm["exactOutput"], fence, ""])
        lines.extend(["### Human review", "", *["- " + q for q in case["reviewQuestions"]], "", "Reviewer / preference / notes:", ""])
    (directory / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = directory / "review.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="Opt-in matched H3 shot timing writer benchmark (live provider calls)")
    parser.add_argument("--root", type=Path, default=Path("benchmarks"))
    parser.add_argument("--profile", required=True)
    parser.add_argument("--profiles", type=Path, default=Path("benchmarks/models.local.json"))
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--repeats", type=int, choices=(1, 2, 3), default=1)
    args = parser.parse_args()
    profiles = load_profiles(args.profiles)
    if args.profile not in profiles:
        parser.error(f"Profile {args.profile!r} not found in {args.profiles}")
    result = asyncio.run(run_suite(args.root, profiles[args.profile],
        case_ids=set(args.cases) if args.cases else None, repeats=args.repeats))
    print(write_run(args.root, result))


if __name__ == "__main__":
    main()
