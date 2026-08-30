from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from miniconstruct.h3.guide_acquisition import GuideAcquisitionError, guide_setup_message
from miniconstruct.models.workspace import AssetKind, H3Mode, ReferenceAsset, Workspace
from miniconstruct.h3.creative_controls import compile_creative_controls


ROOT = Path(__file__).resolve().parent
GUIDES = ROOT / "guides"
OPERATING = ROOT / "operating" / "miniconstruct.md"


@dataclass(frozen=True)
class AssembledPrompt:
    messages: list[dict[str, Any]]
    inspector_text: str
    warnings: list[str]


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


def _timecode(seconds: float) -> str:
    whole_minutes = int(seconds // 60)
    remainder = seconds - (whole_minutes * 60)
    return f"{whole_minutes:02d}:{remainder:06.3f}"


def build_reference_manifest(workspace: Workspace) -> dict[str, Any]:
    assets: list[dict[str, Any]] = []
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
            if identity.focus.value != "general" or identity.view.value != "unspecified":
                entry["subjectIdentity"] = {
                    "focus": identity.focus.value,
                    "view": identity.view.value,
                }
        assets.append(entry)
    return {"assets": assets, "referenceLabels": workspace.reference_labels}


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


def assemble_prompt(workspace: Workspace, supports_vision: bool | None) -> AssembledPrompt:
    official_path = GUIDES / ("ref-en.txt" if workspace.mode == H3Mode.REF2VA else "base-en.txt")
    if not official_path.is_file():
        raise GuideAcquisitionError(guide_setup_message([official_path]))
    layers: list[tuple[str, str]] = [
        ("MiniConstruct core operating instructions", _read_text(str(OPERATING))),
        ("Official MiniMax H3 guide (normative)", _read_text(str(official_path))),
        ("Mode and reference guidance", _mode_guidance(workspace)),
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
    user_parts = [f"MAIN CREATIVE REQUEST (authoritative):\n{workspace.creative_request}"]
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

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": f"## {title}\n\n{body}"} for title, body in layers
    ]
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

    inspector = "\n\n".join(
        [f"===== {title} =====\n{body}" for title, body in layers]
        + [f"===== User material =====\n{user_text}"]
    )
    return AssembledPrompt(messages=messages, inspector_text=inspector, warnings=warnings)
