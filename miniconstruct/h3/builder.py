from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from miniconstruct.h3.guide_acquisition import GuideAcquisitionError, guide_setup_message
from miniconstruct.models.workspace import AssetKind, H3Mode, ReferenceAsset, ReferenceLayout, Workspace
from miniconstruct.h3.creative_controls import compile_creative_controls
from miniconstruct.h3.creative_interpretation import creative_interpretation_policy


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
    "seamless_overlap_continuation": "Seamless overlap continuation",
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
    subject_labels = {subject.id: f"Subject {subject.number}" for subject in workspace.subjects}
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


def build_reference_manifest(workspace: Workspace) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
    subject_labels = {subject.id: f"Subject {subject.number}" for subject in workspace.subjects}
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
    return {
        "subjects": [
            {
                "id": subject.id,
                "label": f"Subject {subject.number}",
                "identityPictures": subject_pictures.get(subject.id, []),
            }
            for subject in workspace.subjects
        ],
        "assets": assets,
        "referenceLabels": workspace.reference_labels,
    }


def _reference_semantics(workspace: Workspace, supports_vision: bool | None) -> str:
    """Explain stable grouping and role semantics without claiming unseen pixels."""
    labels = {subject.id: f"Subject {subject.number}" for subject in workspace.subjects}
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
        "Cite identity-only Picture provenance inside its <Subject N> definition; do not create standalone Picture retention entries or mark an identity-only Picture fully_preserved. Separately configured concrete Picture roles remain independent.",
        "retention_analysis lists only shots where the Subject is visibly in-frame; off-screen mentions do not count. Use canonical <Subject N> labels for visibly present referenced Subjects in each shot.",
    ]
    for asset, label in identity_assets:
        identity_groups.setdefault(asset.subject_identity.subject_id, []).append(label)
    for subject_id, pictures in identity_groups.items():
        if pictures:
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
                lines.append(f"<{label}>: manually designated Reference Sheet identity provenance for <{subject}>; primary focus is {identity.focus.value.replace('_', ' ')}.")
            elif identity.layout == ReferenceLayout.AUTO:
                if supports_vision is True:
                    lines.append(f"<{label}> is Auto-layout identity evidence for <{subject}>. If it is clearly a multi-view reference sheet, treat repeated consistent depictions as complementary evidence for that one Subject and do not reproduce sheet layout or panels in the target video.")
                else:
                    lines.append(f"<{label}> has Auto layout with vision unavailable. Do not infer a reference sheet, particular viewpoints, expressions, labels, or other uninspected visual contents; use its explicit metadata and Notes only.")
            else:
                view = identity.view.value.replace("_", " ")
                lines.append(f"<{label}>: single-view identity provenance for <{subject}>; {identity.focus.value.replace('_', ' ')} focus and {view} view metadata. The view documents evidence; it does not require that target camera view.")
        elif asset.role == "character_comparison_scale":
            subjects = " and ".join(f"<{labels[subject_id]}>" for subject_id in asset.comparison_subject_ids)
            lines.append(
                f"<{label}> is a Character Comparison / Scale reference for {subjects}. REQUIRED: include one <{label}> relationship statement in subject_definitions; do not absorb it into Subject identity. In retention_analysis, retain its scale relationship (normally fully_preserved when maintained), never using Subject-style appears in [Shot ...] wording for <{label}>. Preserve its visible relative height, body-size contrast, and broad body-proportion relationship when those Subjects share a spatial plane. It is not target-shot composition authority: do not copy its neutral poses, side-by-side arrangement, staging, blocking, camera, or background. Comparison imagery is not authority for exact heights, exact height differences, centimetres/inches, numeric ratios or percentages, inferred physical dimensions, OCR-read measurement labels, or visually estimated measurements. Text or numbers printed inside the image remain image-derived, even when readable by OCR or vision; they are not user-authored textual input. The REQUIRED <{label}> relationship statement in subject_definitions must remain qualitative unless the exact numeric measurement is explicitly present in an actual user-authored textual workspace field, such as the Creative Request, this reference's Notes, or Reference Labels / explicit relationship text. If an exact number exists only in image pixels, printed labels, OCR, or visual inference, omit it and describe the relative relationship qualitatively instead."
            )
            if supports_vision is not True:
                lines.append(f"Vision is unavailable for <{label}>; use only its role and explicit Notes for scale relationships, never uninspected visual ratios.")
    return "\n".join(lines)


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
    if workspace.mode == H3Mode.I2VA:
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
        if asset.kind == AssetKind.VIDEO and asset.role == "seamless_overlap_continuation":
            interval = (
                f"00:00.000–{_timecode(asset.duration_seconds)}"
                if asset.duration_seconds is not None
                else f"the complete duration of <{label}>"
            )
            lines.append(
                f"<{label}> is a seamless-overlap continuation using official [video continuation] semantics. "
                f"Shot 1 opens by reproducing {interval} as the same physical moments, from the source start through its final frame, then continues monotonically forward. "
                "No replay, rewind, reset, repeated earlier action, pose snap, object/environment-state snap, camera discontinuity, or lost momentum. "
                "The overlap remains within Shot 1. Preserve the outgoing camera across the boundary; where time permits, allow about 0.5–1.0 seconds of new seamless action before an optional editorial cut, unless the user supplied another valid timing."
            )
    return "\n".join(lines)


def instruction_layers(workspace: Workspace, supports_vision: bool | None) -> list[tuple[str, str]]:
    official_path = GUIDES / ("ref-en.txt" if workspace.mode == H3Mode.REF2VA else "base-en.txt")
    if not official_path.is_file():
        raise GuideAcquisitionError(guide_setup_message([official_path]))
    return [
        ("MiniConstruct core operating instructions", _read_text(str(OPERATING))),
        ("Official MiniMax H3 guide (normative)", _read_text(str(official_path))),
        ("Mode and reference guidance", _mode_guidance(workspace)),
        ("Subject and reference semantics", _reference_semantics(workspace, supports_vision)),
        ("Creative Interpretation policy", creative_interpretation_policy(workspace)),
        (
            "Canonical workspace/reference manifest",
            json.dumps(
                {
                    "mode": workspace.mode.value,
                    "durationSeconds": workspace.duration_seconds,
                    "shots": workspace.shots,
                    "aspectRatio": workspace.aspect_ratio,
                    "variations": workspace.variations,
                    **build_reference_manifest(workspace),
                },
                ensure_ascii=False,
                indent=2,
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


def assemble_prompt(workspace: Workspace, supports_vision: bool | None) -> AssembledPrompt:
    layers = instruction_layers(workspace, supports_vision)
    user_parts = [
        f"MAIN CREATIVE REQUEST (authoritative):\n{workspace.creative_request}",
        "USER TERMINOLOGY AUTHORITY:\n" + USER_TERMINOLOGY_AUTHORITY,
    ]
    if workspace.dialogue.strip():
        user_parts.append(
            "EXACT DIALOGUE (preserve wording and punctuation verbatim; map Subjects to speaker IDs by actual speech order):\n"
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
    warnings: list[str] = []
    if image_assets and supports_vision is True:
        content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
        for asset, label in image_assets:
            content.append({"type": "text", "text": f"Visual input for <{label}> ({asset.filename}):"})
            content.append({"type": "image_url", "image_url": {"url": asset.image.data_url}})
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
