"""Opt-in case-specific environment identity/viewpoint experiments; no render calls."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import re
from pathlib import Path
import uuid

from PIL import Image

from miniconstruct.benchmark import character_performance as harness
from miniconstruct.benchmark.media import decode_data_image
from miniconstruct.benchmark.recording import RecordingClient
from miniconstruct.benchmark.runner import _git_info, _seed_provenance, load_profiles
from miniconstruct.benchmark.temporal_directing import TemporalCase, capture_authority
from miniconstruct.generation import generate_workspace_prompts
from miniconstruct.h3.builder import assemble_prompt, build_h3_input_map, numbered_assets
from miniconstruct.llm.client import OpenAICompatibleClient
from miniconstruct.llm.compatibility import DIRECT_OUTPUT_INSTRUCTION, build_generation_payload
from miniconstruct.models.api import SeedMode

SUITE_NAME = "h3-environment-consistency"
FIXTURE_NAME = "environment-consistency"
FIXTURE_PATH = "assets/anime-bedroom.png"
EXPERIMENT_REVISION = 3
ANTI_COMPOSITION_RELATIONSHIP = (
    "{picture} supplies environment identity and layout evidence for {subject}; "
    "it is not target composition authority, so do not preserve {picture}'s camera "
    "position, framing, lens, perspective, or composition unless independently requested."
)
COMPOSITION_RELATIONSHIP = (
    "{picture} is the concrete composition and framing anchor for [Shot 1]; "
    "preserve its camera position, framing, perspective, and broad spatial presentation "
    "as target-shot authority."
)
RENDER_PRIORITY_NOTE = (
    "Prefer A (production-control) / C / D when A is a matched render candidate. "
    "If A is structurally ineligible solely for a provider-formatting defect and human review "
    "finds B materially equivalent for the environment semantics under test, B / C / D may be "
    "used instead. Record that substitution and its reason in human review; B remains diagnostic, "
    "not automatically production. Do not automate semantic equivalence or render all four."
)
SUBJECT_SECTION = """\n\n## Benchmark-only environment Subject treatment

Define one reusable Environment Subject in subject_definitions, sourced from the
supplied Environment Picture using its real numbered Reference Manifest label.
The Picture is source evidence for that Subject. Refer to the Environment Subject
as the room in the target shot. Preserve the visibly evidenced architecture,
furniture inventory, bed/desk/bookshelf/window relationships, dimensions and
proportions, materials, visible color identity and stable room layout. Do not
invent observations when prompt-writer vision is disabled; use explicit metadata
and user notes only. Preserve ordinary H3 structure and reference rules.
If retention_analysis says the Environment Subject appears in [Shot 1], the
[Shot 1] detailed_description must literally include that same <Subject N> label
as the room/location reference. Do not define a fake person or extra Subject."""
NO_COMPOSITION_SECTION = """\n\n## Benchmark-only no composition transfer treatment

In the FINAL H3 subject_definitions, include this exact relationship sentence
verbatim, exactly once, within the Environment Subject definition:
{relationship}
Use these real numbered labels. The complete sentence, including its explicit
denial of composition authority, MUST reach the final H3 prompt, not merely your
internal reasoning. Do not omit, paraphrase or alter it, or place it elsewhere.
Do not create a standalone Picture anchor entry for identity-only evidence or
repeat this warning throughout other sections. Preserve the room while obeying
the requested new viewpoint. Do not move furniture or alter layout to support it.
In retention_analysis, retain {subject} as fully_preserved for its evidenced
architecture, furniture inventory, relative layout, materials, visible colors
and persistent fixtures. Camera change alone does not make the Environment
Subject partially_preserved: camera composition is not environment identity.
Do not require preservation of unseen geometry."""
COMPOSITION_SECTION = """\n\n## Benchmark-only concrete composition control

This is a deliberate negative control. In the FINAL H3 subject_definitions,
include this exact relationship sentence verbatim, exactly once, as a standalone
Picture definition:
{relationship}
Do not omit, paraphrase or alter it, or place it elsewhere. Surrounding prose may
vary. Explicitly reference {picture} in [Shot 1] and retain its concrete composition
role as fully_preserved in retention_analysis, with [Shot 1] and composition or
framing in its role qualifier (for example, ([Shot 1] composition anchor)).
This intentionally conflicts
with the shared creative request's new viewpoint: the concrete Picture's source
composition takes precedence in this control. Do not rewrite that shared request.
Use canonical Ref2VA summary task grammar: include keyframe completion in the
square-bracketed task prefix, for example [keyframe completion], or combine it
with another genuinely applicable task type using + without duplicate types.
This Picture is a composition anchor, not a forced literal first frame at 0.00;
keep Ref2VA mode. Any defined Subject claimed to appear in [Shot 1] in retention
must also be literally referenced by its same <Subject N> label in that shot.
Make the concrete Picture relationship explicit in the final H3 prompt, not
merely in internal reasoning. Preserve ordinary H3 structure and reference rules."""
OUTPUT_CONTRACT = """\n\nReturn the H3 prompt directly. Do not use Markdown code fences, Markdown headings,
or explanatory preamble. Keep the required canonical H3 section labels."""
TREATMENTS = {
    "production-control": {"role": "environment", "section": None,
        "description": "Current ordinary production Environment Picture behavior, including its existing no-composition guidance; no overlay."},
    "environment-subject": {"role": "environment", "section": SUBJECT_SECTION,
        "description": "Diagnostic abstraction arm: deliberately request an Environment Subject sourced from the Picture; production may already do essentially the same thing."},
    "environment-subject-no-composition": {"role": "environment", "section": SUBJECT_SECTION + NO_COMPOSITION_SECTION,
        "description": "Candidate stronger final-H3 identity/layout-not-composition relationship, with the evidenced Environment Subject fully_preserved despite camera change."},
    "composition-anchor": {"role": "storyboard_composition", "section": COMPOSITION_SECTION,
        "description": "Deliberate negative control: existing storyboard_composition role plus explicit final-H3 concrete Picture composition authority and keyframe completion; intentionally conflicts with the shared new viewpoint."},
}


SIDE_CASE = "bedroom-side-view-miku"
PANORAMA_PATH = "assets/anime-bedroom-panorama.png"
MINIMAL_RELATIONSHIP = (
    "{subject} is the bedroom from {picture}. Use {picture} to establish the room's design, "
    "architecture, furniture identity, materials, colors, lighting character, anime style, "
    "and overall spatial identity. Reconstruct the same room from the camera viewpoint "
    "requested in the target shot. Source-frame screen placement is viewpoint-dependent "
    "rather than canonical target composition."
)
SCREEN_RELATIONSHIP = (
    "{subject} is the anime bedroom environment in {picture}, featuring a single bed "
    "with a white duvet and wooden headboard, tall bookshelves on the left, a desk with "
    "a chair and lamp on the right, a window with tied green curtains, and warm sunlight "
    "falling across a wooden floor."
)
PANORAMA_RELATIONSHIP = (
    " The panoramic source represents one coherent bedroom and supplies spatial/environment "
    "evidence, not a target-video projection. Do not reproduce its panoramic distortion, "
    "stitched layout, or any arbitrary source crop as required framing."
)
SIDE_SECTION = """\n\n## Benchmark-only viewpoint reconstruction treatment
In FINAL H3 subject_definitions include this exact relationship once, within the
sole Environment Subject definition, without paraphrase or alteration:
{relationship}
In retention_analysis use exactly:
{subject} (appears in [Shot 1]): {retention} - stable room identity/content;
viewpoint/perspective reconstruction is allowed.
Use {subject} literally as the location in [Shot 1]. Camera change is permitted
in both retention treatments. Do not add other retention markers for this Subject.
Hatsune Miku is ordinary named scene content only: do not define another Subject
or invent any character Picture, reference asset or identity sheet.
{wording_rule}
Keep the shared target camera geometry and scene intent. Only the supplied
Picture exists. Do not invent unseen room observations when vision is disabled.
"""
SIDE_TREATMENTS = {
    "minimal-partial-original": {"role": "environment", "retention": "partially_preserved", "path": FIXTURE_PATH,
        "relationship": MINIMAL_RELATIONSHIP, "description": "Viewpoint-neutral sparse room identity, partial retention, original perspective."},
    "minimal-full-original": {"role": "environment", "retention": "fully_preserved", "path": FIXTURE_PATH,
        "relationship": MINIMAL_RELATIONSHIP, "description": "Same sparse room identity and original; only retention changes to full."},
    "screen-space-partial-original": {"role": "environment", "retention": "partially_preserved", "path": FIXTURE_PATH,
        "relationship": SCREEN_RELATIONSHIP, "description": "Plausible detailed source-screen room description, partial retention, original."},
    "minimal-partial-panorama": {"role": "environment", "retention": "partially_preserved", "path": PANORAMA_PATH,
        "relationship": MINIMAL_RELATIONSHIP + PANORAMA_RELATIONSHIP, "description": "Same sparse partial semantics; one coherent room represented panoramically."},
}
SIDE_PROMPT_REVIEW = [
    "Did the prompt preserve a viewpoint-neutral room identity?",
    "Did it accidentally reintroduce source-image left/right placement?",
    "Did the target shot strongly establish a true lateral mattress-height bed view?",
    "Is Miku's body placement used to constrain camera geometry rather than dominate the prompt?",
    "Did partially_preserved vs fully_preserved materially change the writer's reconstruction freedom?",
    "Did panorama describe one coherent room rather than a target frame?",
    "Did the panorama prompt accidentally ask H3 to reproduce panoramic distortion?",
]
SIDE_RENDER_REVIEW = [
    "OPENING VIEWPOINT: Does frame zero / opening ~0.5 s originate beside the long side of the bed?",
    "DOORWAY ANCHORING: Does it start near the original elevated doorway composition?",
    "BED GEOMETRY: Is the long edge broadly across the image in a lateral view?",
    "CHARACTER GEOMETRIC CONSTRAINT: Is Miku lying lengthwise consistently with the side view?",
    "ROOM IDENTITY: Is this recognizably the same bedroom?",
    "FURNITURE / ARCHITECTURE DRIFT: Does escaping the source camera destroy or relocate major features?",
    "REFERENCE-PROJECTION ARTIFACT: Does panorama produce obvious distortion or stitching?",
    "VIEWPOINT FREEDOM: Is camera geometry reconstructed rather than crop/zoom/pan around the source?",
]
SIDE_RENDER_PLAN = (
    "Stage 1: one reliable Gemma4 writer profile, all four arms; inspect structural and treatment compliance. "
    "Qwen3.8 has a known Markdown-fence tendency in this environment. Stage 2: one identical fixed "
    "renderer seed across all four matched-eligible arms. Stage 3: additional matched seeds only if "
    "the first suggests meaningful differences. Record comparative notes per dimension, no aggregate score."
)


GATE_CASE = "bedroom-production-environment-gate"
GATE_TREATMENTS = {
    "production-gate": {"role": "environment", "section": None,
        "description": "Current production assembly only: sparse viewpoint-neutral Environment semantics; no benchmark overlay."},
}
GATE_PROMPT_REVIEW = [
    "Is the Picture-sourced Environment Subject concise and viewpoint-neutral, with composition freedom?",
    "Does partial retention preserve room identity while permitting the requested new perspective, without redesign?",
    "Did the writer avoid an inferred source-screen furniture inventory or exhaustive floor plan?",
    "Does Shot 1 directly state the lateral mattress-height bedside camera and authored head-to-toe blocking?",
]
GATE_RENDER_REVIEW = SIDE_RENDER_REVIEW[:6] + [SIDE_RENDER_REVIEW[-1]]
GATE_PLAN = "Reusable regression gate for current production Environment semantics. Any configured writer profile may run it; inspect structural validity and treatment checks separately, then review matched H3 renders manually. The gate does not automatically render."


def case_revision(case):
    return 5 if case.id == GATE_CASE else 4 if case.id == SIDE_CASE else 3


def case_treatments(case):
    if case.id == GATE_CASE:
        return GATE_TREATMENTS
    return SIDE_TREATMENTS if case.id == SIDE_CASE else TREATMENTS


def treatment_case(case, name):
    result = case.model_copy(deep=True)
    if name in SIDE_TREATMENTS:
        path = SIDE_TREATMENTS[name]["path"]
        result.fixture_assets[0].path = path
        result.workspace["assets"][0]["filename"] = Path(path).name
    return result


def neutral_definition(text):
    return not re.search(r"\b(?:left|right|frame-left|frame-right|foreground|background)\b|center of (?:the )?image", text, re.I)


def treatment_labels(workspace):
    picture = next(label for asset, label in numbered_assets(workspace) if asset.kind.value == "image")
    # H3 content definitions can introduce the room Subject without a canonical
    # Environment entity. Reuse an ordinary Subject number, or its next free number.
    number = workspace.subjects[0].number if workspace.subjects else workspace.next_subject_number
    return {"picture": f"<{picture}>", "subject": f"<Subject {number}>"}


def canonical_relationship(workspace, name):
    if name in SIDE_TREATMENTS:
        return SIDE_TREATMENTS[name]["relationship"].format(**treatment_labels(workspace))
    template = {"environment-subject-no-composition": ANTI_COMPOSITION_RELATIONSHIP,
                "composition-anchor": COMPOSITION_RELATIONSHIP}.get(name)
    return template.format(**treatment_labels(workspace)) if template else None


def treatment_section(workspace, name):
    if name in GATE_TREATMENTS:
        return None
    if name in SIDE_TREATMENTS:
        treatment = SIDE_TREATMENTS[name]
        return SIDE_SECTION.format(**treatment_labels(workspace), relationship=canonical_relationship(workspace, name),
            retention=treatment["retention"], wording_rule=("Keep this deliberately source-screen description." if name.startswith("screen-space") else
            "Keep the persistent Environment definition viewpoint-neutral; do not add source-screen coordinates or a prose floor plan.")) + OUTPUT_CONTRACT
    template = TREATMENTS[name]["section"]
    if template is None:
        return None
    return template.format(**treatment_labels(workspace), relationship=canonical_relationship(workspace, name)) + OUTPUT_CONTRACT


def render_eligible(validation):
    """Eligibility is structural only; warnings remain available for human review."""
    return not any(finding["severity"] == "ERROR" for finding in validation["findings"])


def _sections(output):
    """Read canonical sections without normalizing or rewriting provider output."""
    names = ("subject_definitions", "summary", "retention_analysis", "detailed_description",
             "overall_soundscape", "non_diegetic_music")
    matches = list(re.finditer(r"(?m)^(" + "|".join(names) + r"):[ \t]*", output))
    sections = {}
    for index, match in enumerate(matches):
        # Duplicate section headers cannot establish an unambiguous contract.
        if match[1] in sections:
            return {}
        sections[match[1]] = output[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(output)]
    return sections


def _subject_entries(definitions):
    return list(re.finditer(r"(?ms)^(<Subject \d+>)\s+is\b.*?(?=^<(?:Subject|Picture) \d+>\s|\Z)", definitions))


def _shot_one(description):
    match = re.search(r"(?s)\[Shot 1\](.*?)(?=\[Shot \d+\]|\Z)", description)
    return match[1] if match else ""


def _subject_retention(retention, label, *, fully_preserved=False):
    marker = "fully_preserved" if fully_preserved else "(?:fully_preserved|partially_preserved|attribute_transfer|weak_reference)"
    return bool(re.search(r"(?m)^" + re.escape(label) + r"\s*\(appears in \[Shot 1\]\):\s*" + marker + r"\b", retention))


def treatment_compliance(workspace, name, output):
    """Boolean experimental contracts only; no prose quality or equivalence score."""
    if name == "production-control":
        return {"compliant": True, "checks": []}
    sections = _sections(output)
    definitions = sections.get("subject_definitions", "")
    retention = sections.get("retention_analysis", "")
    shot = _shot_one(sections.get("detailed_description", ""))
    labels = treatment_labels(workspace)
    picture, subject = labels["picture"], labels["subject"]
    checks = []
    def check(requirement, passed):
        checks.append({"requirement": requirement, "passed": bool(passed)})
    if name in GATE_TREATMENTS:
        # Diagnostic checks for this explicit novel-viewpoint fixture only. These
        # do not choose retention or parse viewpoint differences in production.
        entries = [entry for entry in _subject_entries(definitions) if picture in entry[0]
                   and re.search(r"\b(?:bedroom|room|environment)\b", entry[0], re.I)]
        entry = entries[0] if len(entries) == 1 else None
        room = entry[1] if entry else ""
        definition = entry[0] if entry else ""
        check("picture_sourced_environment_subject", entry is not None)
        check("no_standalone_picture_composition_anchor", not re.search(r"(?m)^<Picture \d+>\s", definitions)
              and "keyframe completion" not in sections.get("summary", ""))
        stable_identity = bool(re.search(
            r"\b(?:same|stable|preserv\w*|retain\w*)\b.{0,90}"
            r"\b(?:room|bedroom|environment|identity)\b|"
            r"\b(?:room|bedroom|environment)\b.{0,90}"
            r"\b(?:identity|same|stable|retain\w*|preserv\w*)\b",
            retention, re.I | re.S)) and not bool(re.search(
                r"\b(?:discard|lose|remove|replace|abandon)\w*\b.{0,50}"
                r"\b(?:room|bedroom|environment)\s+identity\b",
                retention, re.I | re.S))
        stated_viewpoint_change = bool(re.search(
            r"(?:viewpoint|perspective|camera(?:\s+(?:view|angle|position))?|composition)"
            r".{0,90}\b(?:shift\w*|chang\w*|reconstruct\w*|differ\w*|new|freely|free to|allow\w*)\b|"
            r"\b(?:shift\w*|chang\w*|reconstruct\w*|differ\w*|new|freely|free to|allow\w*)\b"
            r".{0,90}(?:viewpoint|perspective|camera(?:\s+(?:view|angle|position))?|composition)",
            definition + "\n" + retention, re.I | re.S))
        user_requested_novel_view = bool(re.search(
            r"\b(?:new|different|substantially different|completely new)\b.{0,50}"
            r"\b(?:camera|viewpoint|perspective|view)\b|"
            r"\b(?:camera|viewpoint|perspective|view)\b.{0,50}\b(?:new|different)\b",
            workspace.creative_request, re.I | re.S))
        shot_states_target_view = bool(re.search(r"lateral|side view", shot, re.I))
        freedom_with_requested_view = stable_identity and (stated_viewpoint_change or
            (user_requested_novel_view and shot_states_target_view))
        check("viewpoint_reconstruction_freedom", freedom_with_requested_view)
        check("viewpoint_neutral_definition", bool(definition) and neutral_definition(definition))
        check("no_inferred_floor_plan", not re.search(
            r"physical layout remains fixed|long axis extends|do not (?:rotate|relocate|resize|rearrange)|headboard must appear", definition, re.I))
        check("environment_in_shot_one", bool(room) and room in shot)
        check("partial_environment_retention", bool(room) and bool(re.search(
            r"(?m)^" + re.escape(room) + r"\s*\(appears in \[Shot 1\]\):\s*partially_preserved\b", retention)))
        check("requested_lateral_camera_in_shot_one", bool(re.search(r"lateral|side view", shot, re.I))
              and bool(re.search(r"mattress.{0,15}height", shot, re.I))
              and bool(re.search(r"beside.{0,30}(?:long|bed)", shot, re.I)))
        check("authored_body_composition_in_shot_one", "Hatsune Miku" in shot
              and "lengthwise" in shot and "head-to-toe" in shot and bool(re.search(r"horizontal", shot, re.I)))
        check("only_supplied_picture", set(re.findall(r"<Picture \d+>", output)) == {picture})
    elif name in SIDE_TREATMENTS:
        entries = _subject_entries(definitions)
        sentence = canonical_relationship(workspace, name)
        entry = entries[0] if len(entries) == 1 else None
        check("sole_environment_subject_exact_relationship", entry is not None and entry[1] == subject
              and sentence in entry[0] and output.count(sentence) == 1)
        check("relationship_in_subject_definitions_once", definitions.count(sentence) == 1)
        markers = re.findall(r"(?m)^" + re.escape(subject) + r"\s*\([^\n]*\):\s*(\w+)", retention)
        check("exact_retention_marker", markers == [SIDE_TREATMENTS[name]["retention"]]
              and _subject_retention(retention, subject))
        check("environment_in_shot_one", subject in shot)
        check("no_invented_reference", set(re.findall(r"<Subject \d+>", output)) == {subject}
              and set(re.findall(r"<Picture \d+>", output)) == {picture}
              and len(workspace.assets) == 1 and not workspace.subjects)
        check("exact_reference_representation", workspace.assets[0].filename == Path(SIDE_TREATMENTS[name]["path"]).name)
        if not name.startswith("screen-space"):
            check("viewpoint_neutral_environment_definition", neutral_definition(definitions))
        check("direct_h3_output", output.startswith("subject_definitions:") and "```" not in output)
    elif name in {"environment-subject", "environment-subject-no-composition"}:
        entries = _subject_entries(definitions)
        # B's existing abstraction contract permits the writer's own Subject number.
        check("picture_sourced_subject_defined_retained_and_in_shot", any(
            picture in entry[0] and entry[1] in shot and _subject_retention(retention, entry[1])
            and (name == "environment-subject" or entry[1] == subject)
            for entry in entries))
        if name == "environment-subject-no-composition":
            sentence = canonical_relationship(workspace, name)
            check("exact_relationship_once_in_subject_definitions", output.count(sentence) == 1
                  and definitions.count(sentence) == 1)
            check("evidenced_environment_fully_preserved", _subject_retention(retention, subject, fully_preserved=True))
    elif name == "composition-anchor":
        sentence = canonical_relationship(workspace, name)
        check("exact_relationship_once_in_picture_definition", output.count(sentence) == 1
              and bool(re.search(r"(?m)^" + re.escape(sentence), definitions)))
        task = re.match(r"\s*\[([^\]]+)\]", sections.get("summary", ""))
        types = [part.strip() for part in task[1].split("+")] if task else []
        check("canonical_keyframe_completion_task", "keyframe completion" in types and len(types) == len(set(types)))
        check("picture_explicitly_in_shot_one", picture in shot)
        entry = re.search(r"(?m)^" + re.escape(picture) + r"\s*\(([^\n)]*)\):\s*fully_preserved\b", retention)
        check("picture_concrete_composition_retention", entry is not None and "[Shot 1]" in entry[1]
              and bool(re.search(r"\b(?:composition|framing)\b", entry[1])))
    else:
        raise ValueError(f"Unknown treatment {name!r}")
    return {"compliant": all(item["passed"] for item in checks), "checks": checks}


PROMPT_REVIEW = [
    "Did the prompt preserve a coherent bedroom identity?",
    "Did it treat the room as reusable environment content rather than a frozen backdrop?",
    "Did it preserve bed/desk/bookshelf/window/furniture relationships?",
    "Did it accidentally turn the Environment Picture into a first-frame anchor?",
    "Did it reproduce the source doorway composition despite the requested camera change?",
    "Did it obey the requested lower/closer new camera viewpoint?",
    "Did it invent major unsupported room architecture?",
    "Did the Environment Subject treatment omit important concrete room details?",
    "Did explicit no-composition treatment add anything useful beyond Subject abstraction alone?",
    "Does the composition-anchor control clearly encourage source-view reuse?",
    "Are A and diagnostic B materially different, or does production already provide the same room abstraction?",
    "Does C's final subject_definitions explicitly establish Picture identity/layout evidence without target composition authority?",
    "Does C retain evidenced room content as fully_preserved despite the new camera, without requiring unseen geometry?",
    "Does D's final prompt explicitly preserve concrete Picture composition authority, including the canonical keyframe completion task relationship?",
]
RENDER_REVIEW = [
    "ROOM IDENTITY: Does this still look recognizably like the same bedroom?",
    "BED: Same broad size, style, location and orientation?",
    "DESK: Same side of room and relationship to bed/window?",
    "BOOKSHELVES: Retained and spatially plausible?",
    "WINDOW / CURTAINS: Retained in coherent relation to headboard/desk?",
    "MATERIAL / COLOR IDENTITY: Floor, furniture, bedding and curtains broadly preserved?",
    "CAMERA FREEDOM: Is the target genuinely a different composition?",
    "SOURCE-COMPOSITION BIAS: Did H3 simply reconstruct the doorway-wide source view?",
    "GEOMETRIC DRIFT: Major furniture relocation, room resizing, bed scaling or missing fixtures?",
]


def fixture_info(root, path=FIXTURE_PATH):
    payload = (root / path).read_bytes()
    with Image.open(BytesIO(payload)) as image:
        image.load()
        if image.format != "PNG":
            raise ValueError("Canonical bedroom fixture must decode as PNG")
        width, height = image.size
    return payload, {"path": path, "source": "case", "sha256": hashlib.sha256(payload).hexdigest(),
                     "width": width, "height": height, "format": "PNG", "byteSize": len(payload)}


def load_cases(path):
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schemaVersion") != 1 or raw.get("suite") != SUITE_NAME:
        raise ValueError(f"Expected {SUITE_NAME} schemaVersion 1")
    cases = [TemporalCase.model_validate(item) for item in raw.get("cases", [])]
    ids = [case.id for case in cases]
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= {"bedroom-new-camera", SIDE_CASE, GATE_CASE}:
        raise ValueError("Expected historical bedroom, side-view or production-gate cases")
    payload, _ = fixture_info(path.parent)
    if any(case.id == SIDE_CASE for case in cases):
        fixture_info(path.parent, PANORAMA_PATH)
    for case in cases:
        workspace = case.model_workspace(path.parent)
        aspect = "4:3" if case.id == GATE_CASE else "16:9"
        if (workspace.mode.value, workspace.duration_seconds, workspace.shots, workspace.aspect_ratio, workspace.variations) != ("Ref2VA", 8, 1, aspect, 1):
            raise ValueError(f"Pilot requires Ref2VA, 8 seconds, 1 shot, {aspect}, 1 variation")
        if case.requires_vision or case.authoring_path != "one_shot" or case.continuity:
            raise ValueError("Pilot uses ordinary optional vision and no continuity evidence")
        if len(case.fixture_assets) != 1 or case.fixture_assets[0].path != FIXTURE_PATH or case.fixture_assets[0].source != "case":
            raise ValueError("Pilot requires exactly the canonical case-local fixture")
        if workspace.subjects:
            raise ValueError("No reference Subjects are supplied")
        if len(workspace.assets) != 1 or workspace.assets[0].role != "environment":
            raise ValueError("Pilot requires one Environment Picture")
        if decode_data_image(workspace.assets[0].image.data_url)[0] != payload:
            raise ValueError("Workspace fixture bytes differ from canonical image")
    return cases


def treatment_workspace(workspace, name):
    result = workspace.model_copy(deep=True)
    result.assets[0].role = (GATE_TREATMENTS if name in GATE_TREATMENTS else SIDE_TREATMENTS if name in SIDE_TREATMENTS else TREATMENTS)[name]["role"]
    return result


def isolation_passes(base_workspace, workspace, name, expected_messages, actual_messages, canonical_bytes):
    """Compare full, unsanitized final messages and exact bytes, not wording scores."""
    if decode_data_image(workspace.assets[0].image.data_url)[0] != canonical_bytes:
        return False
    if workspace != treatment_workspace(base_workspace, name):
        return False
    section = treatment_section(workspace, name)
    if section is None:
        return actual_messages == expected_messages
    return harness._overlay_isolation_passes(expected_messages, actual_messages, section)


class _CapturedClient(RecordingClient):
    async def generate(self, messages, *, response_instruction=DIRECT_OUTPUT_INSTRUCTION, response_format=None):
        return await super().generate(messages, response_instruction=response_instruction, response_format=response_format)

    @property
    def last_payload(self):
        return self.payload


async def run_case(case, settings, client_factory, fixture_root):
    canonical_bytes, info = fixture_info(fixture_root)
    treatments = case_treatments(case)
    base = case.model_workspace(fixture_root)
    variants, checks = {}, []
    workspaces, messages_by_arm, parameters = {}, {}, {}
    for name, treatment in treatments.items():
        arm_case = treatment_case(case, name)
        arm_base = arm_case.model_workspace(fixture_root)
        canonical_bytes, info = fixture_info(fixture_root, arm_case.fixture_assets[0].path)
        workspace = treatment_workspace(arm_base, name)
        workspaces[name] = workspace
        # Each request owns its Workspace copy and overlay; no global/client mutation.
        recorders = []
        def factory(effective_settings):
            recorder = _CapturedClient(client_factory(effective_settings), [], effective_settings)
            recorders.append(recorder)
            return recorder
        generated = await generate_workspace_prompts(
            harness._request(case, workspace, settings), factory,
            benchmark_system_section=treatment_section(workspace, name))
        arm, messages = harness.capture_variant(case, workspace, settings, generated)
        messages_by_arm[name] = messages
        expected = build_generation_payload(settings, assemble_prompt(workspace, settings.supports_vision).messages, stream=False)[0]
        checks.append(harness._check(f"{name}.exact_treatment_isolation", isolation_passes(
            arm_base, workspace, name, expected["messages"], messages, canonical_bytes)))
        payload = recorders[-1].payload
        parameters[name] = {key: value for key, value in payload.items() if key != "messages"}
        variation = generated.variations[0]
        compliance = treatment_compliance(workspace, name, arm["exactOutput"])
        structural_eligible = render_eligible(arm["validation"])
        arm["seed"] = _seed_provenance(requested_seed=variation.requested_seed, mode="fixed",
            effective_seed=variation.effective_seed, seed_unsupported=variation.seed_unsupported,
            known_unsupported=variation.seed_known_unsupported, final_request_included_seed="seed" in payload)
        arm.update({"treatment": name, "treatmentDescription": treatment["description"],
                    "semanticNegativeControl": name == "composition-anchor",
                    "renderEligible": structural_eligible,
                    "treatmentCompliant": compliance["compliant"],
                    "treatmentComplianceChecks": compliance["checks"],
                    "matchedRenderEligible": structural_eligible and compliance["compliant"],
                    "treatmentSection": treatment_section(workspace, name),
                    "canonicalRelationship": canonical_relationship(workspace, name),
                    "fixture": info, "experimentRevision": case_revision(case),
                    "referenceRepresentation": "panorama" if name == "minimal-partial-panorama" else "perspective", "visionEnabled": settings.supports_vision is True,
                    "effectiveRequest": parameters[name], "referenceMap": build_h3_input_map(workspace),
                    "referenceRoles": {label: asset.role for asset, label in numbered_assets(workspace)},
                    "reviewNotes": "", "renderReviewNotes": ""})
        variants[name] = arm
    if case.id == GATE_CASE:
        checks.extend([
            harness._check("single_production_treatment", list(variants) == list(GATE_TREATMENTS)),
            harness._check("no_benchmark_overlay", variants["production-gate"]["treatmentSection"] is None),
            harness._check("original_picture_bytes_exact", decode_data_image(base.assets[0].image.data_url)[0] == canonical_bytes),
            harness._check("all_outputs_structurally_valid", all(a["validation"]["valid"] for a in variants.values())),
        ])
    elif case.id == SIDE_CASE:
        checks.extend([
            harness._check("all_four_treatments_completed", set(variants) == set(treatments)),
            harness._check("same_effective_provider_parameters", all(p == next(iter(parameters.values())) for p in parameters.values())),
            harness._check("all_outputs_structurally_valid", all(a["validation"]["valid"] for a in variants.values())),
            harness._check("original_picture_bytes_identical", all(decode_data_image(workspaces[n].assets[0].image.data_url)[0] == fixture_info(fixture_root)[0] for n in treatments if n != "minimal-partial-panorama")),
            harness._check("panorama_picture_bytes_exact", decode_data_image(workspaces["minimal-partial-panorama"].assets[0].image.data_url)[0] == fixture_info(fixture_root, PANORAMA_PATH)[0]),
        ])
    else:
        checks.extend([
            harness._check("all_four_treatments_completed", set(variants) == set(TREATMENTS)),
            harness._check("same_base_workspace_abc", workspaces["production-control"] == workspaces["environment-subject"] == workspaces["environment-subject-no-composition"] == base),
            harness._check("same_canonical_picture_bytes_all_arms", all(decode_data_image(w.assets[0].image.data_url)[0] == canonical_bytes for w in workspaces.values())),
            harness._check("same_effective_provider_parameters", all(p == parameters["production-control"] for p in parameters.values())),
            harness._check("same_writer_image_inputs", all(a["visionInputs"] == variants["production-control"]["visionInputs"] for a in variants.values())),
            harness._check("ordinary_vision_behavior", all(len(a["visionInputs"]) == int(settings.supports_vision is True) for a in variants.values())),
            harness._check("single_picture_1_reference", all([entry["h3Label"] for entry in a["referenceMap"]] == ["Picture 1"] for a in variants.values())),
            harness._check("all_outputs_structurally_valid", all(a["validation"]["valid"] for a in variants.values())),
        ])
        # Compare complete raw messages across A/B/C as well as against ordinary assembly.
        for name in ("environment-subject", "environment-subject-no-composition"):
            checks.append(harness._check(f"{name}.only_overlay_differs_from_control", harness._overlay_isolation_passes(
                messages_by_arm["production-control"], messages_by_arm[name], treatment_section(workspaces[name], name))))
    prompt_review = GATE_PROMPT_REVIEW if case.id == GATE_CASE else SIDE_PROMPT_REVIEW if case.id == SIDE_CASE else PROMPT_REVIEW
    render_review = GATE_RENDER_REVIEW if case.id == GATE_CASE else SIDE_RENDER_REVIEW if case.id == SIDE_CASE else RENDER_REVIEW
    return {"caseId": case.id, "treatments": deepcopy(treatments),
            "renderPriorityNote": GATE_PLAN if case.id == GATE_CASE else SIDE_RENDER_PLAN if case.id == SIDE_CASE else RENDER_PRIORITY_NOTE, "title": case.title, "status": "completed", "mode": "Ref2VA",
            "durationSeconds": 8, "shotCount": 1, "aspectRatio": base.aspect_ratio, "fixture": fixture_info(fixture_root)[1],
            "fixtures": [fixture_info(fixture_root, path)[1] for path in ([FIXTURE_PATH, PANORAMA_PATH] if case.id == SIDE_CASE else [FIXTURE_PATH])],
            "experimentRevision": case_revision(case),
            "targetRequest": base.creative_request, "authority": capture_authority(case, base),
            "checks": checks, "variants": variants,
            "reviewQuestions": prompt_review + render_review,
            "promptReviewQuestions": prompt_review,
            "renderReviewQuestions": render_review,
            "renderComparisonStatus": "pending", "reviewNotes": ""}


async def run_suite(root, profile, client_factory=OpenAICompatibleClient, case_ids=None):
    fixture_root = root / FIXTURE_NAME
    cases = load_cases(fixture_root / "cases.json")
    if case_ids is not None and (not case_ids or not case_ids <= {c.id for c in cases}):
        raise ValueError("Unknown or empty environment consistency case selection")
    cases = [c for c in cases if c.id in (case_ids if case_ids is not None else {"bedroom-new-camera"})]
    settings = profile.to_settings().model_copy(update={"seed_mode": SeedMode.FIXED, "fixed_seed": 1, "seed": 1})
    result = {"schemaVersion": 1, "suite": SUITE_NAME,
        "runId": f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}",
        "experimentRevision": max(case_revision(c) for c in cases),
        "renderPriority": ["production-control", "environment-subject-no-composition", "composition-anchor"],
        "renderPriorityNote": RENDER_PRIORITY_NOTE,
        "timestamp": datetime.now(timezone.utc).isoformat(), "git": _git_info(root.parent),
        "profile": profile.public_dict(), "settings": {"modelId": settings.model_id,
            "supportsVision": settings.supports_vision, "temperature": settings.temperature,
            "maxTokens": settings.max_tokens, "reasoningMode": settings.reasoning_mode.value,
            "seedMode": "fixed", "effectiveSeed": 1, "transport": "non_streaming", "llmJudge": "none"},
        "treatments": deepcopy(case_treatments(cases[0])),
        "caseTreatments": {c.id: deepcopy(case_treatments(c)) for c in cases},
        "cases": [await run_case(case, settings, client_factory, fixture_root) for case in cases]}
    if any(c.id == SIDE_CASE for c in cases):
        result["renderPriority"] = list(SIDE_TREATMENTS)
        result["renderPriorityNote"] = SIDE_RENDER_PLAN
    if len(cases) == 1 and cases[0].id == GATE_CASE:
        result["renderPriority"] = list(GATE_TREATMENTS)
        result["renderPriorityNote"] = GATE_PLAN
    for case in result["cases"]:
        for name, arm in case["variants"].items():
            arm["renderMetadata"] = {"suite": SUITE_NAME, "caseId": case["caseId"], "treatment": name,
                "treatmentDescription": arm["treatmentDescription"], "semanticNegativeControl": arm["semanticNegativeControl"],
                "experimentRevision": arm["experimentRevision"], "referenceRepresentation": arm["referenceRepresentation"], "renderEligible": arm["renderEligible"],
                "treatmentCompliant": arm["treatmentCompliant"], "matchedRenderEligible": arm["matchedRenderEligible"],
                "treatmentComplianceChecks": arm["treatmentComplianceChecks"],
                "canonicalRelationship": arm["canonicalRelationship"],
                "profile": result["profile"], "settings": result["settings"], "seed": arm["seed"],
                "effectiveRequest": arm["effectiveRequest"], "visionEnabled": arm["visionEnabled"],
                "fixture": arm["fixture"], "referenceMap": arm["referenceMap"]}
    return result


def write_run(root, result):
    # Recheck fixture before packaging: a changed file must never silently replace run input.
    if any(info != fixture_info(root / FIXTURE_NAME, info["path"])[1]
           for case in result["cases"] for info in case.get("fixtures", [case["fixture"]])):
        raise ValueError("Canonical fixture changed since generation")
    if any(arm["fixture"] != fixture_info(root / FIXTURE_NAME, arm["fixture"]["path"])[1]
           for case in result["cases"] for arm in case["variants"].values()):
        raise ValueError("Canonical fixture changed since generation")
    return harness.write_run(root, result, fixture_name=FIXTURE_NAME, case_loader=load_cases, arm_case_resolver=treatment_case)


def main():
    parser = argparse.ArgumentParser(description="Opt-in single-reference H3 environment benchmark and production-policy gate")
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
