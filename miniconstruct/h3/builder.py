from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from miniconstruct.h3.guide_acquisition import GuideAcquisitionError, guide_setup_message

from miniconstruct.models.subject_numbers import subject_labels as technical_subject_labels
from miniconstruct.models.sequence_subject import (
    local_narrative_subjects, narrative_mapping,
)
from miniconstruct.models.workspace import (
    DIALOGUE_LANGUAGE_LABELS, AssetKind, H3Mode, ReferenceAsset,
    ReferenceLayout, Workspace,
)
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.creative_interpretation import creative_interpretation_policy
from miniconstruct.h3.prompt_detail import h3_prompt_detail_policy
from miniconstruct.h3.natural_liveliness import natural_liveliness_layer
from miniconstruct.h3.continuity import continuity_layers, continuity_warnings
from miniconstruct.h3.vision_input import prompt_writer_image
from miniconstruct.h3.subject_identity import H3_SUBJECT_IDENTITY_POLICY


ROOT = Path(__file__).resolve().parent
GUIDES = ROOT / "guides"
OPERATING = ROOT / "operating" / "miniconstruct.md"


@dataclass(frozen=True)
class AssembledPrompt:
    messages: list[dict[str, Any]]
    inspector_text: str
    warnings: list[str]


USER_TERMINOLOGY_AUTHORITY = (
    "When the user explicitly supplies a term for an action, role, relationship, dynamic, style, or other named "
    "concept, retain that user-supplied term verbatim at least once in the H3 prompt. Do not replace the term entirely "
    "with a synonym, euphemism, generic category, or only its observable description. Supporting visual description may "
    "expand or clarify the term but must not substitute for it."
)

REF2VA_TERMINOLOGY_IDENTITY_RULE = (
    " Narrative Names used as structural Subject references follow the H3 Subject identity policy: "
    "emit the mapped <Subject N> token. Literal dialogue and lyrics remain exact."
)


@lru_cache(maxsize=3)
def _read_text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def numbered_assets(workspace: Workspace) -> list[tuple[ReferenceAsset, str]]:
    result: list[tuple[ReferenceAsset, str]] = []
    names = {AssetKind.IMAGE: "Picture", AssetKind.VIDEO: "Video", AssetKind.AUDIO: "Audio"}
    if workspace.mode == H3Mode.T2VA:
        active = []
    elif workspace.mode == H3Mode.I2VA:
        active = [asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE and asset.role == "first_frame_anchor"]
    elif workspace.mode == H3Mode.FL2VA:
        role_order = {"first_frame_anchor": 0, "last_frame_anchor": 1}
        active = sorted(
            (asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE and asset.role in role_order),
            key=lambda asset: (role_order[asset.role], asset.order, asset.id),
        )
    elif workspace.mode == H3Mode.L2VA:
        active = [asset for asset in workspace.assets if asset.kind == AssetKind.IMAGE and asset.role == "last_frame_anchor"]
    else:
        active = list(workspace.assets)
    for kind in (AssetKind.IMAGE, AssetKind.VIDEO, AssetKind.AUDIO):
        items = sorted(
            (asset for asset in active if asset.kind == kind),
            key=lambda asset: (asset.order, asset.id),
        )
        if workspace.mode == H3Mode.FL2VA and kind == AssetKind.IMAGE:
            items = sorted(items, key=lambda asset: ({"first_frame_anchor": 0, "last_frame_anchor": 1}[asset.role], asset.order, asset.id))
        result.extend((asset, f"{names[kind]} {index}") for index, asset in enumerate(items, 1))
    return result


def media_reference_allowlist(workspace: Workspace) -> str:
    """Project the canonical, mode-active numbered manifest into writer instructions."""
    labels = [label for _, label in numbered_assets(workspace)]
    lines = ["Media references are closed-world: use only the exact labels below from the current numbered Reference Manifest. Do not infer future labels or treat dialogue, voice, sound effects, or music as an Audio reference. Existing H3 text cannot authorize a label absent from this manifest."]
    for kind in ("Picture", "Video", "Audio"):
        allowed = [f"<{label}>" for label in labels if label.startswith(kind + " ")]
        lines.append(f"Allowed {kind} references: {', '.join(allowed) if allowed else 'none'}.")
        if not allowed:
            lines.append(f"There are no {kind} references in this Generation. Do not write any <{kind} N> reference.")
    return "\n".join(lines)


_IDENTITY_FOCUS_LABELS = {
    "general": "General",
    "face": "Facial",
    "full_body": "Full-body",
    "outfit": "Outfit",
    "detail": "Detail",
}
_IDENTITY_VIEW_LABELS = {
    "front": "Front",
    "three_quarter": "Three-quarter",
    "profile": "Profile",
    "rear": "Rear",
}
_ROLE_LABELS = {
    "environment": "Environment",
    "style_appearance": "Style / appearance",
    "continuity_state": "Continuity state",
    "first_frame_anchor": "First-frame anchor",
    "keyframe_anchor": "Keyframe anchor",
    "last_frame_anchor": "Last-frame anchor",
    "storyboard_composition": "Storyboard / composition",
    "general_visual": "General",
    "continuation_source": "Continuation source",
    "tail_reference_continuation": "Tail continuation reference",
    "editing_source": "Editing source",
    "motion_action": "Motion / action",
    "camera_movement": "Camera movement",
    "cut_pacing_rhythm": "Cut / pacing / rhythm",
    "general_video": "General",
    "full_reuse": "Full audio reuse / copy 1:1",
    "partial_reuse": "Partial audio reuse",
    "music_beat_rhythm": "Music / beat / rhythm",
    "voice_timbre": "Voice timbre",
    "dialogue_spoken_content": "Dialogue / spoken-content",
    "sound_audio_style": "Sound / audio-style",
    "general_audio": "General",
}


def build_h3_input_map(workspace: Workspace) -> list[dict[str, Any]]:
    """Build the read-only physical H3 input projection from canonical labels."""
    subject_labels = technical_subject_labels(workspace.subjects)
    entries: list[dict[str, Any]] = []
    for asset, label in numbered_assets(workspace):
        metadata: list[str] = []
        if asset.kind == AssetKind.IMAGE and asset.role == "subject_identity":
            identity = asset.subject_identity
            metadata = [subject_labels.get(identity.subject_id, "Subject"), "Identity"]
            metadata.append(_IDENTITY_FOCUS_LABELS.get(identity.focus.value, identity.focus.value))
            if identity.layout == ReferenceLayout.REFERENCE_SHEET:
                metadata.append("Reference sheet")
            elif identity.layout == ReferenceLayout.SINGLE_VIEW:
                metadata.append("Single view")
            if identity.view.value != "unspecified":
                metadata.append(_IDENTITY_VIEW_LABELS.get(identity.view.value, identity.view.value))
        elif asset.kind == AssetKind.IMAGE and asset.role == "character_comparison_scale":
            metadata = [
                f"Subjects {', '.join(str(next((subject.number for subject in workspace.subjects if subject.id == subject_id), 'Subject')) for subject_id in asset.comparison_subject_ids)}",
                "Character Comparison / Scale",
            ]
        else:
            metadata = [_ROLE_LABELS.get(asset.role, asset.role.replace("_", " ").title())]
        if getattr(asset, "options", {}).get("executionEmbeddedSoundtrack"):
            metadata = ["Embedded video soundtrack"]
        if getattr(asset, "options", {}).get("visualHandoff"):
            metadata = [asset.filename]
        entries.append({
            "id": asset.id,
            "kind": asset.kind.value,
            "h3Label": label,
            "filename": asset.filename,
            "metadata": metadata,
        })
    return entries


def _timecode(seconds: float) -> str:
    whole_minutes = int(seconds // 60)
    remainder = seconds - (whole_minutes * 60)
    return f"{whole_minutes:02d}:{remainder:06.3f}"


def build_reference_manifest(workspace: Workspace, narrative_subjects=()) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    subject_labels = technical_subject_labels(workspace.subjects)
    subject_pictures: dict[str, list[str]] = {subject.id: [] for subject in workspace.subjects}
    for asset, label in numbered_assets(workspace):
        entry: dict[str, Any] = {
            "kind": asset.kind.value,
            "h3Label": label,
            "filename": asset.filename,
            "mimeType": asset.mime_type,
            "durationSeconds": asset.duration_seconds,
            "role": asset.role,
            "notes": asset.notes,
            "options": asset.options,
        }
        if asset.kind == AssetKind.IMAGE and asset.image:
            entry["visionInputAttached"] = True
            entry["processedDimensions"] = {
                "width": asset.image.width,
                "height": asset.image.height,
            }
        else:
            entry["visionInputAttached"] = False
        if asset.kind == AssetKind.IMAGE and asset.role == "subject_identity":
            identity = asset.subject_identity
            subject_pictures.setdefault(identity.subject_id, []).append(label)
            entry["subjectIdentity"] = {
                "subjectId": identity.subject_id,
                "subjectLabel": subject_labels[identity.subject_id],
                "focus": identity.focus.value,
                "layout": identity.layout.value,
                "view": identity.view.value,
                "viewActive": identity.layout != ReferenceLayout.REFERENCE_SHEET,
            }
        elif asset.kind == AssetKind.IMAGE and asset.role == "character_comparison_scale":
            entry["comparisonSubjects"] = [
                {"id": subject_id, "label": subject_labels[subject_id]}
                for subject_id in asset.comparison_subject_ids
            ]
        assets.append(entry)
    mapping = {entry["subjectId"]: entry for entry in narrative_mapping(local_narrative_subjects(workspace, narrative_subjects))}
    manifest_subjects = []
    for subject in workspace.subjects:
        entry = {"id": subject.id}
        if workspace.mode == H3Mode.REF2VA:
            entry.update({
                "label": subject_labels[subject.id],
                "identityPictures": subject_pictures.get(subject.id, []),
            })
        if subject.id in mapping:
            entry["narrativeName"] = mapping[subject.id]["narrativeName"]
        manifest_subjects.append(entry)
    return {
        "subjects": manifest_subjects,
        "assets": assets,
        "referenceLabels": workspace.reference_labels,
    }


def _reference_semantics(workspace: Workspace, supports_vision: bool | None, narrative_subjects=()) -> str:
    """Explain stable grouping and role semantics without claiming unseen pixels."""
    labels = technical_subject_labels(workspace.subjects)
    identity_groups: dict[str, list[str]] = {subject.id: [] for subject in workspace.subjects}
    identity_assets = [
        (asset, label) for asset, label in numbered_assets(workspace)
        if asset.kind == AssetKind.IMAGE and asset.role == "subject_identity"
    ]
    sheet_assets = [pair for pair in identity_assets if pair[0].subject_identity.layout == ReferenceLayout.REFERENCE_SHEET]
    facial_assets = [pair for pair in identity_assets if pair[0].subject_identity.focus.value == "face"]
    lines = [
        "Subject identity is semantic and stable: Subject numbers are not Picture numbers and must never be reassigned because Pictures are reordered or absent.",
        "Dedicated Subject / Identity Pictures define stable identity; Comparison / Scale Pictures only support relative height, body size, and broad proportions.",
        "Expression, emotion, gaze, pose, mouth state, expression-induced eye/brow state, blush, and other transient performance belong to the target video, never identity anchors; authoritative Notes may establish genuinely permanent morphology.",
    ]
    if workspace.mode == H3Mode.REF2VA:
        assigned = ", ".join(f"<{label}>" for label in labels.values()) or "none"
        lines.append(f"Assigned current Subject labels: {assigned}. Use exactly these assigned numbers for the mapped Subjects; never compact or renumber them by list position, even when only one Subject remains. Generic guide examples do not override this authority. Independently defined environments/objects must use other unused numbers.")
        example_pair = next((
            (labels[asset.subject_identity.subject_id], picture_label)
            for asset, picture_label in identity_assets
            if asset.subject_identity.subject_id in labels
        ), None)
        example_subject, example_picture = example_pair or ("Subject N", "Picture N")
        lines.extend([
            f"Ref2VA identity-only Picture syntax: put each mapped <Picture N> token in the SAME <Subject N> definition sentence that starts with '<Subject N> is'. GOOD: '<{example_subject}> is Alice, the woman shown in <{example_picture}>, with ...'. BAD: '<{example_subject}> is Alice...' followed by '<{example_picture}> is the identity reference for <{example_subject}>.' Do not create a standalone <Picture N> definition or retention_analysis line just because the Picture supplies identity; identity-only Pictures get no retention line. A standalone Picture record and retention line are allowed when the canonical Reference Asset role independently tracks a frame, composition, or storyboard target. Do not preserve a Reference Sheet's grid, panels, labels, or layout unless that independent role requests it. This provenance rule does not determine wardrobe; follow the configured role, fidelity, wardrobe, Notes, and Creative Request authority.",
            "retention_analysis lists only shots where the Subject is visibly in-frame; off-screen mentions do not count. Use canonical <Subject N> labels for visibly present referenced Subjects in each shot.",
        ])
    mapping = narrative_mapping(local_narrative_subjects(workspace, narrative_subjects))
    if mapping:
        if workspace.mode == H3Mode.REF2VA:
            lines.append("Each current Narrative Name below resolves to one existing canonical Subject. This bounded mapping is input interpretation context, not permission to substitute names for technical H3 tokens. Preserve supplied authoring prose in storage; canonicalize structural references in final H3 according to the H3 Subject identity policy.")
        else:
            lines.append("Current Narrative Names below orient interpretation of human-authored Story and continuity prose. They are input context only; follow the active base Video guide's ordinary prose and do not introduce Ref2VA Subject labels or sections.")
        for entry in mapping:
            name = json.dumps(entry["narrativeName"], ensure_ascii=False) if entry["narrativeName"] else None
            if workspace.mode == H3Mode.REF2VA:
                lines.append((f"{name} == " if name else "") + entry["canonicalH3Label"] + ". " + json.dumps(entry, ensure_ascii=False))
            else:
                lines.append(json.dumps({
                    "subjectId": entry["subjectId"],
                    "narrativeName": entry["narrativeName"],
                }, ensure_ascii=False))
    for asset, label in identity_assets:
        identity_groups.setdefault(asset.subject_identity.subject_id, []).append(label)
    for subject_id, pictures in identity_groups.items():
        if pictures and workspace.mode == H3Mode.REF2VA:
            lines.append(f"<{labels[subject_id]}> is defined by {', '.join(f'<{picture}>' for picture in pictures)}.")

    if facial_assets:
        lines.append(
            "Facial Identity semantics: extract stable facial morphology and discriminative traits, not a reference's smile, frown, scowl, emotion, gaze, mouth openness, expression-induced eye/brow pose, head pose, or blush. Notes may establish permanent morphology (for example naturally upturned mouth corners), not that the Subject is smiling in this reference."
        )

    if sheet_assets:
        lines.append(
            "Reference Sheet semantics: repeated consistent depictions are complementary evidence for one assigned Subject, not separate people or an expression sequence. Use sheets as identity evidence only; never reproduce panels, grids, sheet backgrounds, annotations, labels, repeated copies, or side-by-side layout as target composition. Expression examples show deformation only, never retained traits."
        )
        if supports_vision is True:
            lines.extend([
                "Visually synthesize clearly visible complementary depictions into one coherent Subject identity; use only visible traits and do not enumerate panels.",
                "For requested profile/side, rear/back, or three-quarter views, selectively use matching visible sheet evidence in that shot; do not invent traits or reproduce the sheet.",
                "Focus is primary, not exclusive: General supports broad multi-view evidence and a large facial panel may help; Full-body prioritizes proportions, silhouette, cross-view hair, clothing, footwear, and accessories while a large facial panel may help; Facial prioritizes facial/head evidence.",
            ])
        else:
            lines.append("Vision is unavailable for manually designated sheets. Use only their sheet designation and explicit Notes; do not invent their views, expressions, labels, panels, props, or details.")

    for asset, label in numbered_assets(workspace):
        if asset.kind != AssetKind.IMAGE:
            continue
        if asset.role == "subject_identity":
            identity = asset.subject_identity
            subject = labels[identity.subject_id]
            if identity.layout == ReferenceLayout.REFERENCE_SHEET:
                if workspace.mode == H3Mode.REF2VA:
                    lines.append(f"<{label}>: manually designated Reference Sheet identity provenance for <{subject}>; primary focus is {identity.focus.value.replace('_', ' ')}.")
                else:
                    lines.append(f"<{label}>: manually designated character identity Reference Sheet; primary focus is {identity.focus.value.replace('_', ' ')}. Use as identity evidence only, not as target composition.")
            elif identity.layout == ReferenceLayout.AUTO:
                if supports_vision is True:
                    if workspace.mode == H3Mode.REF2VA:
                        lines.append(f"<{label}> is Auto-layout identity evidence for <{subject}>. If it is clearly a multi-view reference sheet, treat repeated consistent depictions as complementary evidence for that one Subject and do not reproduce sheet layout or panels in the target video.")
                    else:
                        lines.append(f"<{label}> is Auto-layout identity evidence for a participating character. If it is clearly a multi-view reference sheet, synthesize consistent traits as one character; do not reproduce sheet layout or panels in the target video.")
                else:
                    lines.append(f"<{label}> has Auto layout with vision unavailable. Do not infer a reference sheet, particular viewpoints, expressions, labels, or other uninspected visual contents; use its explicit metadata and Notes only.")
            else:
                view = identity.view.value.replace("_", " ")
                if workspace.mode == H3Mode.REF2VA:
                    lines.append(f"<{label}>: single-view identity provenance for <{subject}>; {identity.focus.value.replace('_', ' ')} focus and {view} view metadata. The view documents evidence; it does not require that target camera view.")
                else:
                    lines.append(f"<{label}>: single-view character identity evidence; {identity.focus.value.replace('_', ' ')} focus and {view} view metadata. The source view does not require that camera view in the target video.")
        elif asset.role == "character_comparison_scale":
            subjects = " and ".join(f"<{labels[subject_id]}>" for subject_id in asset.comparison_subject_ids)
            if workspace.mode == H3Mode.REF2VA:
                lines.append(
                f"<{label}> is a Character Comparison / Scale reference for {subjects}. REQUIRED: include one <{label}> relationship statement in subject_definitions; do not absorb it into Subject identity. In retention_analysis, retain its scale relationship (normally fully_preserved when maintained), never using Subject-style appears in [Shot ...] wording for <{label}>. Preserve its visible relative height, body-size contrast, and broad body-proportion relationship when those Subjects share a spatial plane. It is not target-shot composition authority: do not copy its neutral poses, side-by-side arrangement, staging, blocking, camera, or background. Comparison imagery is not authority for exact heights, exact height differences, centimetres/inches, numeric ratios or percentages, inferred physical dimensions, OCR-read measurement labels, or visually estimated measurements. Text or numbers printed inside the image remain image-derived, even when readable by OCR or vision; they are not user-authored textual input. The REQUIRED <{label}> relationship statement in subject_definitions must remain qualitative unless the exact numeric measurement is explicitly present in an actual user-authored textual workspace field, such as the Creative Request, this reference's Notes, or Reference Labels / explicit relationship text. If an exact number exists only in image pixels, printed labels, OCR, or visual inference, omit it and describe the relative relationship qualitatively instead."
                )
            else:
                lines.append(f"<{label}> provides relative-scale guidance for the configured characters. Preserve their visible relative height and broad body-proportion relationship when they share a spatial plane; do not copy the reference pose, staging, camera, background, or printed measurements as numeric facts.")
            if supports_vision is not True:
                lines.append(f"Vision is unavailable for <{label}>; use only its role and explicit Notes for scale relationships, never uninspected visual ratios.")
    environment_labels = [f"<{label}>" for asset, label in numbered_assets(workspace)
                          if asset.kind == AssetKind.IMAGE and asset.role == "environment"]
    if environment_labels:
        if workspace.mode == H3Mode.REF2VA:
            lines.append(ENVIRONMENT_REFERENCE_POLICY.format(pictures=", ".join(environment_labels)))
        else:
            lines.append(
                f"{', '.join(environment_labels)} provide environment appearance and location context for natural scene prose. "
                "Use visible details only when vision is enabled and otherwise rely on Notes. Follow the requested target camera and composition. "
                "Do not add Ref2VA Subject definitions, retention analysis, or technical Subject labels in this base Video mode."
            )
    return "\n".join(lines)


# Canonical writer policy, shared by Main Prompt, Story, Revision and Repair.
# Viewpoint differences are assessed semantically by the writer, never parsed here.
ENVIRONMENT_SUBJECT_DEFINITION_TEMPLATE = """<Subject N> is the environment from <Picture M>. Use <Picture M> only as a reference for <Subject N>'s environment identity, design, furniture, materials, colors, lighting, and visual style. Do NOT preserve <Picture M>'s camera angle, framing, perspective, or composition. Reconstruct <Subject N> from the camera viewpoint requested in each shot."""

ENVIRONMENT_REFERENCE_POLICY = """{pictures}: Environment-only evidence for stable environment identity and appearance.
For each ordinary Picture whose role is exactly environment, REQUIRED: emit the canonical minimal, viewpoint-neutral Environment Subject definition below in the FINAL H3 subject_definitions. Replace N and M with the real allocated Subject number and source Picture number; never emit placeholder labels. The explicit identity-only, anti-composition and requested-view reconstruction relationship must be in that definition, not only in retention_analysis or Shot 1.
Canonical definition:
""" + ENVIRONMENT_SUBJECT_DEFINITION_TEMPLATE + """
Do NOT add MODEL-INFERRED visual object inventory to subject_definitions: no 'featuring a white bed, desk, lamp, bookshelf, window' prose transcription of the Picture. The Picture supplies visual details. Keep the Environment definition minimal; preserve stable design, architecture, furniture/object identity, materials, colors, lighting character, visual style and overall spatial identity through the Picture.
An Environment Picture does NOT define target first-frame composition, camera position/height, lens/framing, perspective, required 2D projection, source-screen coordinates or Subject placement. Do not preserve its camera angle, framing, perspective or composition merely because it supplies the environment.
Never promote MODEL-INFERRED screen-space observations into persistent environment identity: bookshelf on the left, desk on the right, window in the center of Picture, bed receding diagonally from the source camera's foreground edge, frame-left/frame-right objects or furniture at the image edge describe one source projection. Do not assume H3 reliably transforms source-frame left/right into world-space geometry. Do not manufacture an exhaustive textual floor plan or speculative world topology from image pixels, such as fixed object axes or blanket prohibitions on rotating, relocating, resizing or rearranging every object. Prefer less textual reconstruction; use topology only when stronger authored/continuity authority supplies it or it is genuinely necessary for the request. When vision is unavailable, use explicit metadata and Notes without inventing visual observations.
Preserve explicit user-authored spatial relationships and blocking, including left/right text, headboard beneath the window, desk beside the bed, pink duvet or cabinet against the north wall. Notes, Main Prompt, Scene Plan, Incoming State, Sequence Context, Story continuity, Camera / Shot Intent and stronger canonical fields retain their existing precedence. Preserve those facts in appropriate shot/state prose; include them in the definition only when H3 structure genuinely benefits. Image-derived OCR/vision observations are never user-authored facts. This restricts unnecessary visual inference, not authored spatial authority.
An Environment Subject is normally LOCATION / SETTING context, not a camera target. Do NOT apply 'close-up of', 'medium close-up of' or 'portrait of' to the Environment Subject unless the user explicitly requests an environment-only establishing shot. Bind it locatively when visibly relevant, for example 'Inside <Subject N>, a close-up frames <Subject 1> in profile'; no exact locative phrase is required. If Main Prompt or Camera / Shot Intent requests Shot 1 as a close-up of Subject 1, keep <Subject 1> as the framing target; never substitute the Environment Subject. Preserve explicit current instructions not to use an Environment Picture as first-frame composition authority. Ordinary Environment evidence never overrides explicit current Main Prompt, Camera / Shot Intent, Scene Plan camera requirements or harder mode/frame anchors. Story uses Scene Plan for what happens, Camera / Shot Intent for planned camera authority, and Environment Pictures for location identity evidence under this same contract.
retention_analysis shot membership is visual: list only shots where the Subject is visibly in-frame. If an Environment Subject lists [Shot 1], [Shot 2], each listed shot MUST explicitly reference its canonical <Subject N> label in detailed_description as setting/location context, without replacing the requested camera target. Do not claim the Environment appears in every shot automatically merely because the scene is physically inside it. For an extreme close-up with no visibly identifiable environment content, omit that shot from Environment retention membership; recognizable room content behind a close-up may justify membership and locative binding.
Express requested camera geometry clearly and directly in the relevant [Shot N], rather than in the persistent Environment Subject definition. Retain useful authored character/object blocking and target composition (such as lying lengthwise, full body head-to-toe horizontally across the frame); do not invent extra blocking to force camera compliance.
When current authoring explicitly requests a substantially different viewpoint/perspective, normally use partially_preserved for the Environment Subject in retention_analysis: preserve stable environment and furniture/object identity, materials, colors, lighting character and visual style while freely changing camera viewpoint and perspective. Partial grants projection/viewpoint reconstruction freedom, not permission to deliberately redesign the room. With no meaningful viewpoint change, choose the appropriate official retention relationship.
Explicit storyboard_composition, first_frame_anchor, keyframe_anchor, last_frame_anchor, composition relationships and required H3 I2VA/FL2VA/L2VA mode frame anchors retain their existing stronger authority. A separately configured composition/anchor use of the same Picture may constrain framing. Do not apply Environment viewpoint freedom or normally-partial retention to an authoritative composition/frame role, required mode frame anchor, or current authoring that explicitly reproduces the source composition."""


def _mode_guidance(workspace: Workspace) -> str:
    duration = f"{workspace.duration_seconds:.2f}"
    final_shot = workspace.shots or "N (the actual final shot)"
    lines = [
        f"Selected mode: {workspace.mode.value}.",
        f"Target/effective duration: {duration} seconds.",
        f"Shot count: {workspace.shots if workspace.shots is not None else 'auto; choose a positive count suited to the request'}.",
        f"Aspect ratio guidance: {workspace.aspect_ratio}.",
        "Shot 1 has no timestamp. Every later shot begins at a strictly increasing timestamp within the target duration.",
    ]
    if workspace.mode == H3Mode.I2VA and workspace._native_i2va:
        lines.append(
            "Native-only I2VA successor: there is no new first-frame Picture. Use the base three-section "
            "H3 grammar, beginning directly with integrated_multimodal_description:. Omit the ordinary I2VA "
            "first-frame alignment line. Do not invent Picture 1, an attached last frame, or a Video continuation "
            "reference. The accepted predecessor native context governs the physical opening. Continue its "
            "Subject identity, scene, motion, time progression and camera momentum before the requested action; "
            "preserve authored audio and exact dialogue. Saved preceding scene context is semantic guidance, "
            "not proof of an observed final pose. Do not expose native execution identifiers or settings."
        )
    elif workspace.mode == H3Mode.I2VA:
        lines.append(
            "The first output line must be exactly: For the target video, at 0.00 seconds into the target video, <Picture 1> (from [Shot 1]) is fully referenced."
        )
    elif workspace.mode == H3Mode.FL2VA:
        lines.append(
            "The first output line must use the official alignment grammar, with Picture 1 at 0.00 seconds and "
            f"Picture 2 from Shot {final_shot} at {duration} seconds."
        )
    elif workspace.mode == H3Mode.L2VA:
        lines.append(
            "The first output line must use the official last-frame alignment grammar, with <Picture 1> from "
            f"[Shot {final_shot}] at {duration} seconds."
        )
    elif workspace.mode == H3Mode.REF2VA:
        lines.append("Use only the official six-section full-reference grammar and official task-type prefixes.")

    for asset, label in numbered_assets(workspace):
        if asset.kind == AssetKind.VIDEO and asset.role == "continuation_source":
            lines.append(
                f"{label} is a normal continuation source: use official [video continuation] semantics and proceed from its ending state without forcing a replay."
            )
        if asset.kind == AssetKind.VIDEO and asset.role == "tail_reference_continuation":
            lines.append(
                f"<{label}> is the actual final segment of the predecessor and uses official [video continuation] semantics. "
                "Begin the target action from the END of this Video; preserve compatible visible state and motion, without replaying or restarting an earlier action stage. "
                "It is reference conditioning, not a demand that target frames duplicate the source Video."
            )
        if asset.kind == AssetKind.IMAGE and asset.options.get("visualHandoff", {}).get("strategy") in {"endpoint_frame", "endpoint_then_immediate_cut"}:
            lines.append(f"<{label}> is a concrete predecessor endpoint Picture anchor: use official [keyframe completion] task semantics, not video continuation unless a Video is also genuinely being continued.")
    return "\n".join(lines)


def instruction_layers(workspace: Workspace, supports_vision: bool | None, narrative_subjects=(), continuity_context=None) -> list[tuple[str, str]]:
    from miniconstruct.visual_handoff import handoff_layers
    recognized_subjects = local_narrative_subjects(workspace, narrative_subjects)
    narrative_subjects = recognized_subjects
    official_path = GUIDES / ("ref-en.txt" if workspace.mode == H3Mode.REF2VA else "base-en.txt")
    if not official_path.is_file():
        raise GuideAcquisitionError(guide_setup_message([official_path]))
    manifest = build_reference_manifest(workspace, narrative_subjects)
    # Reference Labels already appear once in authoritative user material.
    manifest.pop("referenceLabels")
    return [
        ("MiniConstruct core operating instructions", _read_text(str(OPERATING))),
        ("Official MiniMax H3 guide (normative)", _read_text(str(official_path))),
        ("Mode and reference guidance", _mode_guidance(workspace)),
        *(([("Accepted preceding scene authoring", "Historical H3 authoring for the exact accepted preceding execution follows. "
              "Use its relevant Subject identity, scene, action, camera and audio context. Its planned endpoint is not observed truth; "
              "native context governs the physical opening. Historical media labels are not current inputs. Do not repeat old "
              "frame-alignment instructions, invent references or restart the previous action. Current authoring controls what happens next.\n"
              + workspace._native_i2va_context)] if workspace._native_i2va else [])),
        ("Allowed media references", media_reference_allowlist(workspace)),
        ("Subject and reference semantics", _reference_semantics(workspace, supports_vision, narrative_subjects)),
        *(([("H3 Subject identity policy", H3_SUBJECT_IDENTITY_POLICY)]
           if workspace.mode == H3Mode.REF2VA else [])),
        ("Creative Interpretation policy", creative_interpretation_policy(workspace)),
        *continuity_layers(continuity_context),
        *handoff_layers(workspace),
        (
            "Canonical workspace/reference manifest",
            json.dumps(
                {
                    "mode": workspace.mode.value,
                    "durationSeconds": workspace.duration_seconds,
                    "shots": workspace.shots,
                    "aspectRatio": workspace.aspect_ratio,
                    "variations": workspace.variations,
                    **manifest,
                },
                ensure_ascii=False,
                separators=(",", ": "),
            ),
        ),
        (
            "Generation policy",
            f"Generate one clean H3 prompt in this response. The application will make {workspace.variations} independent request(s); variations are alternate prompt rewrites, never H3 shot count.",
        ),
    ]


def system_message_content(layers: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"## {title}\n\n{body}" for title, body in layers)


def inspector_text(layers: list[tuple[str, str]], user_text: str) -> str:
    return "\n\n".join(
        [f"===== {title} =====\n{body}" for title, body in layers]
        + [f"===== User material =====\n{user_text}"]
    )


STORY_DIRECTING_POLICY = (
    "Translate the current Scene Plan into concrete temporal performance across this Generation. "
    "Use the ordered Scene Plans, Story Plan / Premise, Story Memory, continuity, and Character Profiles "
    "as authority. Across several seconds, normally show how action, posture, expression, interaction, "
    "camera, or attention develops rather than naming a final pose and filling time with mood adjectives. "
    "Use successive observable developments when the Scene Plan contains them. Convey emotional intent "
    "through visible or audible performance cues; let camera direction respond to action when useful. "
    "Sustained stillness, silence, a held gaze, pause, or prolonged gesture is valid when it is the "
    "intended dramatic event. Do not add motion for activity, impose a beat count, or change something "
    "on a fixed schedule. Avoid unintentional empty time. Obey official H3 grammar, exact dialogue, "
    "references, and continuity without contradicting stronger saved authority. "
    "The Story shotCount and cameraIntent describe planned ordered coverage; current H3 Setup "
    "Workspace.shots is final shot-count authority. Output exactly that count when set. "
    "If the counts differ, combine or redistribute planned coverage intelligently. "
    "Keep Incoming State at the opening of the first H3 shot and Intended Outgoing State "
    "at the end of the final H3 shot. For continue_previous_shot, first continue the "
    "predecessor ending composition before any later cut or reframing. Set exact H3 "
    "timestamps and duration coverage here, not in Story planning."
)


def assemble_prompt(workspace: Workspace, supports_vision: bool | None, narrative_subjects=(), continuity_context=None,
                    story_context=None) -> AssembledPrompt:
    recognized_subjects = local_narrative_subjects(workspace, narrative_subjects)
    layers = instruction_layers(workspace, supports_vision, recognized_subjects, continuity_context)
    # Fresh authoring only: Revision and Repair reuse instruction_layers directly.
    layers.append(natural_liveliness_layer())
    if story_context is None:
        prompt_detail_policy = h3_prompt_detail_policy(workspace.creative_controls.prompt_detail)
        if prompt_detail_policy:
            layers.append(("H3 Prompt Detail policy", prompt_detail_policy))
        user_parts = [f"MAIN CREATIVE REQUEST (authoritative):\n{workspace.creative_request}"]
    else:
        layers.append(("Story-aware directing policy", STORY_DIRECTING_POLICY))
        user_parts = ["CANONICAL STORY H3 AUTHORITY (current Generation is marked current):\n"
                      + json.dumps({**story_context, "currentH3SetupShots": workspace.shots},
                                   ensure_ascii=False, separators=(",", ":")),
                      "CURRENT GENERATION SCENE PLAN — PRIMARY DIRECTING TASK:\n"
                      + story_context["currentGeneration"]["scenePlan"]]
    terminology_authority = USER_TERMINOLOGY_AUTHORITY
    if workspace.mode == H3Mode.REF2VA:
        terminology_authority += REF2VA_TERMINOLOGY_IDENTITY_RULE
    user_parts.append("USER TERMINOLOGY AUTHORITY:\n" + terminology_authority)
    if workspace.dialogue.strip():
        dialogue_language = DIALOGUE_LANGUAGE_LABELS[workspace.dialogue_language]
        language_authority = (
            f"EFFECTIVE SEQUENCE DIALOGUE LANGUAGE: {dialogue_language}. In this Sequence, prefix every H3 dialogue block with [{dialogue_language}]."
            if workspace.dialogue_language.value != "as_written"
            else "EFFECTIVE SEQUENCE DIALOGUE LANGUAGE: As Written. Choose one supported official H3 language tag appropriate to the supplied spoken text; MiniConstruct does not detect or validate language identity."
        )
        user_parts.append(
            "EXACT RESOLVED SPOKEN OUTPUT (literal audible text only; preserve each utterance and every character of its spoken payload exactly):\n"
            + language_authority + "\n"
            "Write each actual spoken utterance as <d>[Language] exact spoken payload</d>. The leading [Language] is required H3 format metadata, not audible speech; exclude it from the exact payload comparison. "
            "Do not add language labels as speech, translation or pronunciation notes, commentary, emotion tags, or other bracketed annotations. Do not add quotation marks unless they are part of the supplied spoken payload. "
            "Use existing Name: or Subject N: speaker labels as attribution, not spoken payload. Assign speaker IDs by actual speech order; do not split one utterance or infer a new speaker from punctuation or pauses. "
            "Timing, performance and reactions may be described outside <d>...</d>.\n"
            + workspace.dialogue
        )
    if workspace.reference_labels.strip():
        user_parts.append("REFERENCE LABEL RELATIONSHIPS (authoritative):\n" + workspace.reference_labels)
    creative_controls = compile_creative_controls(workspace, supports_vision is True)
    if creative_controls:
        user_parts.append(creative_controls)
    user_parts.append("Return only the clean H3 prompt as plain text. Do not use Markdown fences or commentary.")
    user_text = "\n\n".join(user_parts)

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_message_content(layers)}]
    image_assets = [pair for pair in numbered_assets(workspace) if pair[0].kind == AssetKind.IMAGE]
    warnings: list[str] = continuity_warnings(continuity_context, numbered_assets(workspace))
    if workspace._native_i2va:
        warnings = [warning for warning in warnings if not warning.startswith("Same-scene continuity has no usable visual continuity reference")]
    if image_assets and supports_vision is True:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for asset, label in image_assets:
            if asset.image is None:
                raise ValueError(f"{label}: Picture payload {asset.payload_availability or 'unavailable'}; Replace Picture before vision generation")
            content.append({"type": "text", "text": f"Visual input for <{label}> ({asset.filename}):"})
            content.append({"type": "image_url", "image_url": {"url": prompt_writer_image(asset.image.data_url)[0]}})
        messages.append({"role": "user", "content": content})
    else:
        messages.append({"role": "user", "content": user_text})
        if image_assets:
            state = "unknown" if supports_vision is None else "disabled"
            warnings.append(
                f"Vision support is {state}; {len(image_assets)} image reference(s) were not sent as visual inputs. Their metadata, roles, and notes remain in the manifest."
            )

    inspector = inspector_text(layers, user_text)
    return AssembledPrompt(messages=messages, inspector_text=inspector, warnings=warnings)
