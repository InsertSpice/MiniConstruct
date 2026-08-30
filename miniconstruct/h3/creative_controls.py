from __future__ import annotations

from miniconstruct.models.workspace import (
    AssetKind,
    CameraPreference,
    MusicMode,
    PerformanceEnergy,
    PerformanceStyle,
    ReferenceFidelityLevel,
    SubjectIdentityFocus,
    SubjectIdentityView,
    VisualStylePreset,
    Workspace,
)


CAMERA_LABELS = {
    "zoom": "Zoom", "push_pull": "Push/Pull", "pan": "Pan", "truck": "Truck",
    "tilt": "Tilt", "pedestal": "Pedestal", "arc": "Arc", "tracking": "Tracking",
    "static": "Static", "shake": "Shake", "pov": "POV", "roll": "Roll",
}


STYLE_GUIDANCE = {
    VisualStylePreset.CINEMATIC: "Use a cinematic visual presentation consistent with the Creative Request and references.",
    VisualStylePreset.LIVE_ACTION: "Render as live-action, photoreal filmed imagery rather than animation or CG stylization.",
    VisualStylePreset.ANIMATED_2D: "Use a 2D-animated presentation.",
    VisualStylePreset.ANIMATED_2D_ANIME: "Use a 2D-animated anime presentation.",
    VisualStylePreset.CG_3D: "Use a 3D CG presentation.",
    VisualStylePreset.CG_3D_STYLIZED: "Use a stylized 3D CG presentation.",
    VisualStylePreset.CLAYMATION: "Use a claymation or clay stop-motion aesthetic.",
    VisualStylePreset.WATERCOLOR: "Use a watercolor visual aesthetic.",
    VisualStylePreset.VINTAGE_FILM: "Use a vintage-film visual treatment.",
}


TONE_GUIDANCE = {
    "sensuality": {
        "subtle": "Lightly sensual presentation through tasteful body language, framing, expression, or movement where appropriate.",
        "moderate": "Clearly sensual presentation with confident body language and appealing framing while remaining coherent with the requested scene.",
        "strong": "Strong sensual emphasis through performance, pose, movement, expression, and cinematography where appropriate.",
        "intense": "Make sensual presentation a dominant artistic quality through performance, pose, movement, expression, and cinematographic presentation while preserving requested identity, action, and scene logic.",
    },
    "drama": {
        "subtle": "Add a light sense of emotional weight and expressive staging.",
        "moderate": "Give the sequence clear emotional weight, visual intensity, and dramatic presentation.",
        "strong": "Strongly emphasize emotional weight, expressive staging, and dramatic visual intensity.",
        "intense": "Make dramatic presentation a dominant quality through high emotional weight and striking staging.",
    },
    "horror": {
        "subtle": "Introduce a lightly unsettling or ominous mood where appropriate.",
        "moderate": "Emphasize ominous, unsettling, or frightening mood and performance where appropriate.",
        "strong": "Strongly emphasize unsettling, frightening, or disturbing mood and cinematographic treatment.",
        "intense": "Make an ominous, frightening, or disturbing atmosphere a dominant artistic quality.",
    },
    "tension": {
        "subtle": "Add light suspense, anticipation, or unease.",
        "moderate": "Build clear suspense, anticipation, pressure, and dramatic unease.",
        "strong": "Strongly sustain suspense, pressure, anticipation, and dramatic buildup.",
        "intense": "Make escalating suspense, pressure, and dramatic buildup a dominant quality.",
    },
    "romance": {
        "subtle": "Add a light sense of romantic warmth or affection where appropriate.",
        "moderate": "Emphasize romantic warmth, intimacy, affection, and chemistry where appropriate.",
        "strong": "Strongly emphasize romantic intimacy, chemistry, affection, and emotional connection.",
        "intense": "Make romantic warmth, intimacy, and emotional connection a dominant artistic quality.",
    },
    "whimsy": {
        "subtle": "Add a lightly playful, charming, or fanciful character to staging where appropriate.",
        "moderate": "Emphasize playful, fanciful, charming, or imaginative character and staging.",
        "strong": "Strongly emphasize playful, charming, fanciful, and imaginative character and staging.",
        "intense": "Make playful, fanciful, charming imagination a dominant artistic quality of the sequence.",
    },
}


PERFORMANCE_STYLE_GUIDANCE = {
    PerformanceStyle.RESTRAINED: "Use controlled, understated subject movement and expression; avoid unnecessary theatrical motion.",
    PerformanceStyle.SUBTLE: "Use natural low-amplitude subject expression and movement, slightly more understated than normal.",
    PerformanceStyle.EXPRESSIVE: "Use clear, animated body language, facial expression, gesture, and performance without cartoonish excess unless appropriate.",
    PerformanceStyle.EXAGGERATED: "Use large, emphatic, highly animated gestures, poses, facial expressions, and performance appropriate to the chosen medium and scene.",
}


PERFORMANCE_ENERGY_GUIDANCE = {
    PerformanceEnergy.CALM: "Keep subject action and performance relaxed, composed, and low-energy.",
    PerformanceEnergy.LOW: "Use gentle, measured subject motion and behavior.",
    PerformanceEnergy.ENERGETIC: "Use lively, active, high-energy subject performance and movement.",
    PerformanceEnergy.INTENSE: "Use very forceful, vigorous, high-momentum subject action and performance where appropriate.",
}


def compile_creative_controls(workspace: Workspace, supports_vision: bool = True) -> str:
    """Return concise active workspace preferences, or an empty string for all-Auto."""
    controls = workspace.creative_controls
    lines: list[str] = []
    # Keep this local import so the builder can call this compiler without an import cycle.
    from miniconstruct.h3.builder import numbered_assets

    picture_references = [
        (asset, label) for asset, label in numbered_assets(workspace)
        if asset.kind == AssetKind.IMAGE
    ]
    style_pictures = [(asset, label) for asset, label in picture_references if asset.role == "style_appearance"]
    subject_style_pictures = [(asset, label) for asset, label in picture_references if asset.role == "subject_identity"]
    environment_pictures = [(asset, label) for asset, label in picture_references if asset.role == "environment"]
    if controls.music.mode == MusicMode.OFF:
        lines.extend([
            "Non-diegetic music: forbidden.",
            "Output exactly one non_diegetic_music section and set it to N/A; do not treat diegetic sound as score.",
        ])
    elif controls.music.mode == MusicMode.ON:
        description = f" Follow this music direction: {controls.music.description.strip()}." if controls.music.description.strip() else ""
        lines.append(f"Non-diegetic music: required; do not output N/A for non_diegetic_music.{description}")

    avoid = [CAMERA_LABELS[name] for name, value in controls.camera if value == CameraPreference.AVOID]
    prefer = [CAMERA_LABELS[name] for name, value in controls.camera if value == CameraPreference.PREFER]
    if avoid or prefer:
        camera = ["Camera language:"]
        if avoid:
            camera.append(f"Avoid: {', '.join(avoid)}. Do not use these unless an explicit higher-priority current-output or revision request requires them.")
        if prefer:
            camera.append(f"Prefer: {', '.join(prefer)} where appropriate; do not force every shot.")
        lines.append(" ".join(camera))

    tone_performance = controls.tone_performance
    tone_items = [
        f"{name.title()}: {TONE_GUIDANCE[name][level.value]}"
        for name, level in (
            ("sensuality", tone_performance.sensuality), ("drama", tone_performance.drama),
            ("horror", tone_performance.horror), ("tension", tone_performance.tension),
            ("romance", tone_performance.romance), ("whimsy", tone_performance.whimsy),
        )
        if level.value != "auto"
    ]
    if tone_performance.performance_style != PerformanceStyle.AUTO:
        tone_items.append(f"Subject performance style: {PERFORMANCE_STYLE_GUIDANCE[tone_performance.performance_style]}")
    if tone_performance.performance_energy != PerformanceEnergy.AUTO:
        tone_items.append(f"Subject performance energy: {PERFORMANCE_ENERGY_GUIDANCE[tone_performance.performance_energy]}")
    if tone_items:
        lines.append(
            "Tone / Performance: Blend active dimensions coherently according to their relative strengths and the Creative Request. "
            "These govern subject tone, performance, expression, pose, movement, and staging or presentation, not cut pacing, and must not override explicit Camera Creative Controls or identity-fidelity requirements. "
            "They do not independently change a reference-established wardrobe; when the Creative Request explicitly requests a wardrobe change, follow that requested target. "
            + " ".join(tone_items)
        )

    style = controls.visual_style
    if style.preset in STYLE_GUIDANCE:
        style_text = (
            "Visual Style (authoritative): "
            f"{STYLE_GUIDANCE[style.preset]} Establish this overall style naturally at the beginning of Shot 1. "
            "Preserve the relevant identity, environment, and content from references while applying this target presentation."
        )
        if style_pictures and supports_vision:
            labels = ", ".join(label for _, label in style_pictures)
            style_text += (
                f" Visually inspect {labels} as the primary overall visual-style authority and retain only "
                "the concise, confident visible design or rendering traits that matter under this medium, such as character "
                "proportions, facial or eye treatment, linework, coloring, shading, hair rendering, highlights, or texture. "
                "Use only traits supported by those Pictures, their Notes, or the Creative Request; do not add preset-only genre embellishment."
            )
        elif subject_style_pictures and supports_vision:
            labels = ", ".join(label for _, label in subject_style_pictures)
            style_text += (
                f" With no Style / Appearance Picture, use {labels} only as supporting character-design evidence for facial design, "
                "eye treatment, linework, coloring, shading, or hair rendering where visibly supported; do not treat it as authority for unrelated environment artwork."
            )
        elif style_pictures or subject_style_pictures:
            style_text += (
                " Vision is unavailable, so use only the selected medium plus style facts supplied in Notes or the Creative Request; "
                "do not claim uninspected reference-specific visual traits."
            )
        if environment_pictures:
            labels = ", ".join(label for _, label in environment_pictures)
            style_text += (
                f" {labels} may guide environment rendering and appearance only; it is not a universal global or facial-style authority."
            )
        lines.append(style_text)
    elif style.preset == VisualStylePreset.CUSTOM and style.custom.strip():
        lines.append(
            "Visual Style (authoritative): "
            f"Follow this custom target style: {style.custom.strip()}. "
            "Establish it naturally at the beginning of Shot 1 while preserving relevant reference identity, environment, and content."
        )

    subject_identity_pictures = [
        (asset, label) for asset, label in picture_references
        if asset.kind == AssetKind.IMAGE and asset.role == "subject_identity"
    ]
    fidelity_level = controls.subject_identity_fidelity.level
    if subject_identity_pictures and fidelity_level != ReferenceFidelityLevel.AUTO:
        labels = ", ".join(label for _, label in subject_identity_pictures)
        specialists: list[str] = []
        wardrobe_references: list[str] = []
        outfit_references: list[str] = []
        for asset, label in subject_identity_pictures:
            identity = asset.subject_identity
            if identity.focus == SubjectIdentityFocus.FACE:
                if supports_vision:
                    strength = "strongly prefer" if fidelity_level == ReferenceFidelityLevel.STRICT else "prefer"
                    specialists.append(
                        f"{label} is a facial identity anchor: visually inspect it and {strength} a concise, confident inventory of its most identity-defining visible facial traits. "
                        "Use positive concrete traits actually visible in the Picture, such as face shape or proportions, eye shape, spacing or iris treatment, unusual brows, distinctive eyelash treatment, nose, mouth, jawline, cheek structure, cheek marks / cheek lines, freckles, moles / beauty marks, scars, asymmetry, makeup markings, small line-art facial accents, distinctive hairline or face-framing bangs, skin tone, and other visible facial identifiers where relevant; do not invent unclear features. "
                        f"Use authoritative Notes as supplemental facts, then encode the resulting feature-level anchor naturally in subject_definitions and/or retention_analysis with {label} as provenance, without repeating it in every shot."
                    )
                    if asset.notes.strip():
                        specialists.append(
                            f"{label} has explicit identity Notes: when they identify traits as important, defining, or must-preserve, prioritize that user emphasis over lower-salience automatic visual ranking; retain the fact naturally rather than copying Notes verbatim."
                        )
                else:
                    specialists.append(
                        f"{label} is a facial identity anchor. Vision is unavailable: preserve the structured facial-reference role and any authoritative Notes without claiming a visually inspected feature inventory."
                    )
            elif identity.focus == SubjectIdentityFocus.GENERAL:
                wardrobe_references.append(label)
                if supports_vision:
                    specialists.append(
                        f"{label} supports overall recognizable identity and current appearance; consistent visible wardrobe may contribute to that current appearance."
                    )
                else:
                    specialists.append(
                        f"{label} designates overall recognizable identity and current appearance. Vision is unavailable, so use only its Notes, structured metadata, or the Creative Request for wardrobe facts."
                    )
            elif identity.focus == SubjectIdentityFocus.FULL_BODY:
                wardrobe_references.append(label)
                if supports_vision:
                    specialists.append(
                        f"{label} anchors body proportions, silhouette, current clothing, footwear, accessories, and complete visible character appearance."
                    )
                else:
                    specialists.append(
                        f"{label} designates body proportions, silhouette, and the full-body appearance role. Vision is unavailable, so use only its Notes, structured metadata, or the Creative Request for wardrobe facts."
                    )
            elif identity.focus == SubjectIdentityFocus.OUTFIT:
                wardrobe_references.append(label)
                outfit_references.append(label)
                if supports_vision:
                    specialists.append(
                        f"{label} is the primary wardrobe authority for clothing, footwear, wearable design, fine detail, and disambiguation."
                    )
                else:
                    specialists.append(
                        f"{label} designates primary wardrobe-reference purpose. Vision is unavailable, so use only its Notes, structured metadata, or the Creative Request for wardrobe facts."
                    )
            elif identity.focus == SubjectIdentityFocus.DETAIL:
                specialists.append(f"{label} is a specialist identity-detail reference; use its Notes for the defining detail.")
            if identity.view == SubjectIdentityView.PROFILE:
                specialists.append(f"{label} provides profile appearance information when the subject is seen from the side; it does not require a profile shot.")
            elif identity.view == SubjectIdentityView.REAR:
                specialists.append(f"{label} provides rear-view hair, clothing, and silhouette information when the subject is seen from behind; it does not require a rear shot.")
            elif identity.view == SubjectIdentityView.FRONT:
                specialists.append(f"{label} provides front-facing appearance information when that view is shown.")
            elif identity.view == SubjectIdentityView.THREE_QUARTER:
                specialists.append(f"{label} provides three-quarter appearance information when that view is shown.")
        if wardrobe_references and fidelity_level in {ReferenceFidelityLevel.STRONG, ReferenceFidelityLevel.STRICT}:
            reference_labels = ", ".join(wardrobe_references)
            if supports_vision:
                outfit_authority = (
                    f" Outfit / Clothing primary wardrobe authority: {', '.join(outfit_references)}."
                    if outfit_references else ""
                )
                strict_detail = (
                    " Under strict fidelity, make the established wardrobe an explicit part of appearance retention and encode concise, relevant wardrobe facts in subject_definitions and/or retention_analysis."
                    if fidelity_level == ReferenceFidelityLevel.STRICT else
                    " Under strong fidelity, encourage concise current-wardrobe consistency when it is clearly established."
                )
                specialists.append(
                    "Reference-derived wardrobe policy: explicit Creative Request or authoritative reference-relationship statements that specify wardrobe or a wardrobe change take precedence. "
                    f"Visually compare {reference_labels} for a consistent or compatible current wardrobe; a dedicated Outfit / Clothing Picture is not required. "
                    "When the references establish one and no higher-authority request changes it, preserve it as part of the subject's current appearance using concise, relevant clothing facts rather than a forensic inventory. "
                    "If the references materially conflict, do not flatten them into one wardrobe; use Outfit / Clothing focus, Notes, the Creative Request, or authoritative relationships to resolve intent."
                    f"{outfit_authority}{strict_detail}"
                )
            else:
                specialists.append(
                    "Reference-derived wardrobe policy: explicit Creative Request or authoritative reference-relationship statements that specify wardrobe or a wardrobe change take precedence. "
                    "Vision is unavailable, so do not infer uninspected wardrobe details; use only Notes, structured metadata, or the Creative Request for wardrobe facts."
                )
        fidelity = {
            ReferenceFidelityLevel.BALANCED: "Preserve recognizable character identity and major appearance traits; normal pose, expression, action, staging, and viewpoint changes remain free.",
            ReferenceFidelityLevel.STRONG: "Strongly preserve facial identity and structure, body proportions, hair, clothing or outfit, colors, and distinctive appearance traits. Use the Subject Identity Picture references as appearance anchors; requested action, expression, pose, and camera remain free.",
            ReferenceFidelityLevel.STRICT: "Make close visual identity and appearance fidelity to the Subject Identity Picture references a very high priority across shots. Encode concise feature-level identity anchors where H3 naturally defines identity and retention, especially subject_definitions and retention_analysis, and keep them consistent across shots. Still allow new action, expression, pose, composition, framing, and camera movement.",
        }[fidelity_level]
        specialist_text = f" {' '.join(specialists)}" if specialists and fidelity_level in {ReferenceFidelityLevel.STRONG, ReferenceFidelityLevel.STRICT} else ""
        lines.append(f"Subject Identity Fidelity ({fidelity_level.value}; anchors: {labels}): {fidelity}{specialist_text}")
    if not lines:
        return ""
    return "ACTIVE CREATIVE CONTROLS (authoritative for their governed dimensions; override conflicting freeform Creative Request wording unless an individual control specifies a more specific precedence policy):\n- " + "\n- ".join(lines)
