"""Four-arm production natural liveliness ablation experiment using the performance harness."""
from __future__ import annotations

from copy import deepcopy
import re
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

from miniconstruct.benchmark import character_performance as harness
from miniconstruct.benchmark.media import decode_data_image
from miniconstruct.benchmark.runner import BenchmarkProfile, load_profiles
from miniconstruct.h3.builder import numbered_assets
from miniconstruct.h3.natural_liveliness import NATURAL_LIVELINESS_POLICY, NATURAL_LIVELINESS_TITLE, natural_liveliness_layer
from miniconstruct.benchmark.temporal_directing import TemporalCase
from miniconstruct.h3.prompt_detail import h3_prompt_detail_policy
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.models.api import SeedMode
from miniconstruct.models.workspace import H3PromptDetail

SUITE_NAME = "h3-natural-liveliness"
FIXTURE_NAME = "natural-liveliness"
LIVELINESS_POLICY = NATURAL_LIVELINESS_POLICY
LIVELINESS_SYSTEM_SECTION = "\n\n## %s\n\n%s" % natural_liveliness_layer()
EXPERIMENT_REVISION = 2


def ablate_natural_liveliness(messages):
    """Remove exactly one whole canonical system section, or reject setup."""
    matches = []
    for index, message in enumerate(messages):
        if message.get("role") != "system" or not isinstance(message.get("content"), str):
            continue
        content = message["content"]
        for match in re.finditer(r"(?:^|\n\n)## " + re.escape(NATURAL_LIVELINESS_TITLE) + r"\n\n(.*?)(?=\n\n## |\Z)", content, re.S):
            matches.append((index, match))
    if len(matches) != 1 or matches[0][1].group(1) != NATURAL_LIVELINESS_POLICY:
        raise ValueError("Expected exactly one canonical Natural Liveliness system section")
    result = deepcopy(messages)
    index, match = matches[0]
    content = result[index]["content"]
    result[index]["content"] = content[:match.start()] + content[match.end():]
    return result


class _TreatmentClient:
    """Benchmark-only adapter; preserves the actual client's effective payload."""
    def __init__(self, client, treatment):
        self.client = client
        self.treatment = treatment
        self.last_payload = None

    def __getattr__(self, name):
        return getattr(self.client, name)

    async def __aenter__(self):
        await self.client.__aenter__()
        return self

    async def __aexit__(self, *args):
        return await self.client.__aexit__(*args)

    async def generate(self, messages):
        control = ablate_natural_liveliness(messages)
        effective = control if self.treatment == "control" else deepcopy(messages)
        output = await self.client.generate(effective)
        self.last_payload = deepcopy(getattr(self.client, "last_payload", None) or {"messages": effective})
        return output


DETAIL_SYSTEM_SECTION = "\n\n## H3 Prompt Detail policy\n\n" + h3_prompt_detail_policy(H3PromptDetail.DETAILED)
ARMS = ("standard-control", "detailed-control", "standard-experimental", "detailed-experimental")
REVIEW_QUESTIONS = [
    "Did Standard Control already feel sufficiently alive?",
    "Did Detailed Control improve articulation without adding unsupported plot?",
    "Did Experimental add useful natural micro-performance beyond each matching Control?",
    "Did Detailed + Experimental become redundant or over-directed?",
    "Were facial cues subtle and context-appropriate rather than theatrical?",
    "Was any character over-animated or fidgety?",
    "Were new emotions or dialogue invented (including speech for the silent listener)?",
    "Was deliberate stillness and exact performance direction preserved?",
    "Did a blink reopen immediately?",
    "Did gaze return to or remain on the same attention target?",
    "Did Experimental create an unsupported emotional change?",
    "Did deliberate stillness remain genuinely still?",
    "Were closed eyes and sleeping respected without added blinking?",
    "For multiple characters, did the prompt avoid synchronized mechanical behavior?",
    "For anime, were visual conventions described naturally rather than as ASCII text, with symbolic marks justified?",
    "After actual matched H3 renders, did H3 follow the direction usefully? Better wording alone is not proof.",
]
CASE_REVIEW_QUESTIONS = {
    "anime-neutral-fixed-frame": [
        "Does Control remain unnaturally frozen?",
        "Does Experimental add a brief natural blink?",
        "Does the blink reopen immediately?",
        "Does gaze remain on or return to the same target?",
        "Does expression return to the baseline state?",
        "Does Experimental invent an emotional change or larger gesture?",
        "Does Detailed alone alter the result materially?",
        "Does Detailed + Experimental become over-directed?",
    ],
    "anime-deliberate-stillness-fixed-frame": [
        "Does the held gaze remain stable?",
        "If a blink occurs, is it brief and does the gaze immediately return?",
        "Does Experimental lower or redirect the eyes?",
        "Does Experimental sustain eye closure?",
        "Does Experimental alter emotional expression?",
        "Does Experimental introduce a gesture?",
        "Is subtle breathing compatible with the requested stillness?",
        "Is Experimental actually better than Control?",
    ],
}


def load_cases(path: Path) -> list[TemporalCase]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != 1 or raw.get("suite") != SUITE_NAME:
        raise ValueError(f"Expected {SUITE_NAME} schemaVersion 1")
    cases = [TemporalCase.model_validate(item) for item in raw.get("cases", [])]
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Natural liveliness cases require unique IDs")
    for case in cases:
        if case.authoring_path != "one_shot" or case.exact_dialogue:
            raise ValueError("Use one-shot cases with canonical Workspace dialogue only")
        workspace = case.model_workspace(path.parent)
        if workspace.variations != 1 or workspace.creative_controls.prompt_detail != H3PromptDetail.STANDARD:
            raise ValueError("Fixtures require one variation and baseline Standard Prompt Detail")
    return cases


def _prompt_detail_isolation_passes(standard, detailed) -> bool:
    # Reuse exact-section removal and compare every remaining message field.
    return harness._overlay_isolation_passes(standard, detailed, DETAIL_SYSTEM_SECTION)


async def run_case(case, settings, client_factory, fixture_root):
    variants = {}
    checks = []
    pairs = {}
    workspaces = []
    raw_messages = {}
    for detail in ("standard", "detailed"):
        arm_case = case.model_copy(deep=True)
        arm_case.workspace.setdefault("creativeControls", {})["promptDetail"] = detail
        workspace = arm_case.model_workspace(fixture_root).model_dump(mode="json", by_alias=True)
        observed_detail = workspace["creativeControls"].pop("promptDetail", None)
        if observed_detail != detail:
            raise ValueError(f"Workspace Prompt Detail changed unexpectedly: expected {detail!r}, got {observed_detail!r}")
        workspaces.append(workspace)
        captured = {}
        case_questions = REVIEW_QUESTIONS + CASE_REVIEW_QUESTIONS.get(case.id, [])
        pair = await harness.run_case(arm_case, settings, client_factory, fixture_root,
                                      system_section=LIVELINESS_SYSTEM_SECTION,
                                      review_questions=case_questions, message_capture=captured,
                                      arm_client_factory=lambda name, settings: _TreatmentClient(client_factory(settings), name))
        if pair["status"] == "skipped":
            return {**pair, "livelinessExperimentRevision": EXPERIMENT_REVISION}
        picture_inputs = []
        fixtures = {fixture.asset_id: fixture for fixture in arm_case.fixture_assets}
        for asset, label in numbered_assets(arm_case.model_workspace(fixture_root)):
            if asset.kind.value != "image" or asset.image is None:
                continue
            decoded = decode_data_image(asset.image.data_url)
            if decoded is None:
                raise ValueError(f"Could not decode canonical image input for {asset.id}")
            fixture = fixtures.get(asset.id)
            picture_inputs.append({
                "h3Label": label,
                "assetId": asset.id,
                "role": asset.role,
                "authority": "required_first_frame" if asset.role == "first_frame_anchor" else "reference",
                "fixturePath": fixture.path if fixture else None,
                "fixtureSource": fixture.source if fixture else None,
                "fixtureSha256": hashlib.sha256(decoded[0]).hexdigest(),
            })
        pairs[detail] = pair
        raw_messages.update({f"{detail}-{overlay}": messages for overlay, messages in captured.items()})
        checks.extend({**check, "name": f"{detail}.{check['name']}"} for check in pair["checks"])
        for overlay, arm in pair["variants"].items():
            variants[f"{detail}-{overlay}"] = {**arm, "promptDetail": detail,
                                               "livelinessOverlay": overlay,
                                               "livelinessExperimentRevision": EXPERIMENT_REVISION,
                                               "livelinessTreatment": "ablation_control" if overlay == "control" else "production",
                                               "pictureInputs": picture_inputs}
    checks.append(harness._check("all_four_arms_completed", set(variants) == set(ARMS)))
    checks.append(harness._check("only_prompt_detail_differs_in_workspace", workspaces[0] == workspaces[1]))
    for overlay in ("control", "experimental"):
        checks.append(harness._check(f"{overlay}.only_prompt_detail_policy_differs_in_model_messages",
            _prompt_detail_isolation_passes(raw_messages[f"standard-{overlay}"],
                                           raw_messages[f"detailed-{overlay}"])))
    for name, arm in variants.items():
        messages = arm["modelFacingMessages"]
        checks.append(harness._check(f"{name}.correct_policy_selection",
            harness._overlay_count(messages, DETAIL_SYSTEM_SECTION) == (arm["promptDetail"] == "detailed")
            and harness._overlay_count(messages, LIVELINESS_SYSTEM_SECTION) == (arm["livelinessOverlay"] == "experimental")))
    checks.append(harness._check("all_outputs_structurally_valid",
                                all(arm["validation"]["valid"] for arm in variants.values())))
    checks.append(harness._check("all_four_seeds_and_references_matched",
        all(arm["requestedSeed"] == 1 and arm["effectiveSeed"] == variants[ARMS[0]]["effectiveSeed"]
            and arm["visionInputs"] == variants[ARMS[0]]["visionInputs"] for arm in variants.values())))
    checks.append(harness._check("picture_bytes_and_transmitted_image_inputs_match",
        all(arm["pictureInputs"] == variants[ARMS[0]]["pictureInputs"]
            and arm["modelInput"]["images"] == variants[ARMS[0]]["modelInput"]["images"]
            for arm in variants.values())))
    if case.model_workspace(fixture_root).mode.value == "I2VA":
        checks.append(harness._check("i2va_first_frame_picture_1_alignment_valid",
            all(arm["validation"]["valid"] and "first_frame_alignment" not in harness._finding_codes(arm["validation"])
                and len(arm["pictureInputs"]) == 1
                and arm["pictureInputs"][0]["h3Label"] == "Picture 1"
                and arm["pictureInputs"][0]["role"] == "first_frame_anchor"
                and arm["pictureInputs"][0]["authority"] == "required_first_frame"
                for arm in variants.values())))
    return {**pairs["standard"], "livelinessExperimentRevision": EXPERIMENT_REVISION, "variants": {name: variants[name] for name in ARMS}, "checks": checks}


async def run_suite(root: Path, profile: BenchmarkProfile, client_factory=OpenAICompatibleClient,
                    case_ids: set[str] | None = None):
    fixture_root = root / FIXTURE_NAME
    cases = load_cases(fixture_root / "cases.json")
    selected = [case for case in cases if case_ids is None or case.id in case_ids]
    if not selected or (case_ids and case_ids - {case.id for case in selected}):
        raise ValueError("Unknown or empty natural liveliness case selection")
    settings = profile.to_settings().model_copy(update={"seed_mode": SeedMode.FIXED, "fixed_seed": 1, "seed": 1})
    return {"schemaVersion": 1, "suite": SUITE_NAME,
            "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
            "timestamp": datetime.now(timezone.utc).isoformat(), "profile": profile.public_dict(),
            "settings": {"modelId": settings.model_id, "supportsVision": settings.supports_vision,
                         "temperature": settings.temperature, "maxTokens": settings.max_tokens,
                         "reasoningMode": settings.reasoning_mode.value,
                         "seedMode": "fixed", "effectiveSeed": 1, "transport": "non_streaming", "llmJudge": "none"},
            "experimentalPolicy": LIVELINESS_POLICY,
            "livelinessExperimentRevision": EXPERIMENT_REVISION,
            "cases": [await run_case(case, settings, client_factory, fixture_root) for case in selected]}


def write_run(root, result):
    return harness.write_run(root, result, fixture_name=FIXTURE_NAME, case_loader=load_cases)


def main():
    parser = argparse.ArgumentParser(description="Opt-in four-arm H3 natural liveliness benchmark")
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
