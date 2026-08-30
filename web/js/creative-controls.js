export const CAMERA_FIELDS = [
  ["zoom", "Zoom"], ["pushPull", "Push/Pull"], ["pan", "Pan"], ["truck", "Truck"],
  ["tilt", "Tilt"], ["pedestal", "Pedestal"], ["arc", "Arc"], ["tracking", "Tracking"],
  ["static", "Static"], ["shake", "Shake"], ["pov", "POV"], ["roll", "Roll"],
];

export const VISUAL_STYLE_PRESETS = [
  ["auto", "Auto"], ["cinematic", "Cinematic"], ["live_action", "Live-action"],
  ["animated_2d", "2D animated"], ["animated_2d_anime", "2D animated — anime"],
  ["cg_3d", "3D CG"], ["cg_3d_stylized", "3D CG — stylized"], ["claymation", "Claymation"],
  ["watercolor", "Watercolor"], ["vintage_film", "Vintage film"], ["custom", "Custom"],
];

export const TONE_LEVELS = ["auto", "subtle", "moderate", "strong", "intense"];
export const PERFORMANCE_STYLE_LEVELS = ["restrained", "subtle", "auto", "expressive", "exaggerated"];
export const PERFORMANCE_ENERGY_LEVELS = ["calm", "low", "auto", "energetic", "intense"];
export const TONE_FIELDS = [
  ["sensuality", "Sensuality"], ["drama", "Drama"], ["horror", "Horror"],
  ["tension", "Tension"], ["romance", "Romance"], ["whimsy", "Whimsy"],
];

export const semanticLabel = value => value.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase());

export function semanticFromRange(levels, value) {
  return levels[Number(value)] || levels[Math.floor(levels.length / 2)];
}

export function rangeForSemantic(levels, value) {
  const index = levels.indexOf(value);
  return index < 0 ? Math.floor(levels.length / 2) : index;
}

export function defaultCreativeControls() {
  return {
    music: { mode: "auto", description: "" },
    camera: Object.fromEntries(CAMERA_FIELDS.map(([key]) => [key, "auto"])),
    visualStyle: { preset: "auto", custom: "" },
    subjectIdentityFidelity: { level: "auto" },
    tonePerformance: {
      sensuality: "auto", drama: "auto", horror: "auto", tension: "auto", romance: "auto", whimsy: "auto",
      performanceStyle: "auto", performanceEnergy: "auto",
    },
  };
}

export function normalizeCreativeControls(value = {}) {
  const defaults = defaultCreativeControls();
  const music = value.music || {};
  const camera = value.camera || {};
  const visualStyle = value.visualStyle || {};
  const subjectIdentityFidelity = value.subjectIdentityFidelity || value.referenceFidelity || {};
  const tonePerformance = value.tonePerformance || {};
  return {
    music: {
      mode: ["auto", "off", "on"].includes(music.mode) ? music.mode : "auto",
      description: typeof music.description === "string" ? music.description : "",
    },
    camera: Object.fromEntries(CAMERA_FIELDS.map(([key]) => [
      key, ["auto", "avoid", "prefer"].includes(camera[key]) ? camera[key] : defaults.camera[key],
    ])),
    visualStyle: {
      preset: VISUAL_STYLE_PRESETS.some(([preset]) => preset === visualStyle.preset) ? visualStyle.preset : "auto",
      custom: typeof visualStyle.custom === "string" ? visualStyle.custom : "",
    },
    subjectIdentityFidelity: {
      level: ["auto", "balanced", "strong", "strict"].includes(subjectIdentityFidelity.level)
        ? subjectIdentityFidelity.level : "auto",
    },
    tonePerformance: {
      ...Object.fromEntries(TONE_FIELDS.map(([key]) => [key, TONE_LEVELS.includes(tonePerformance[key]) ? tonePerformance[key] : "auto"])),
      performanceStyle: PERFORMANCE_STYLE_LEVELS.includes(tonePerformance.performanceStyle) ? tonePerformance.performanceStyle : "auto",
      performanceEnergy: PERFORMANCE_ENERGY_LEVELS.includes(tonePerformance.performanceEnergy) ? tonePerformance.performanceEnergy : "auto",
    },
  };
}

export function resetCameraControls(controls) {
  return { ...normalizeCreativeControls(controls), camera: defaultCreativeControls().camera };
}

export function resetCreativeControls(controls) {
  const defaults = defaultCreativeControls();
  const normalized = normalizeCreativeControls(controls);
  return {
    ...defaults,
    music: { ...defaults.music, description: normalized.music.description },
    visualStyle: { ...defaults.visualStyle, custom: normalized.visualStyle.custom },
  };
}

export function resetTonePerformance(controls) {
  return { ...normalizeCreativeControls(controls), tonePerformance: defaultCreativeControls().tonePerformance };
}

export function renderCreativeControls(root, controls, hasSubjectIdentityPicture) {
  const normalized = normalizeCreativeControls(controls);
  const buttonGroup = (values, selected, field, disabled = false) => values.map(([value, label]) =>
    `<button type="button" class="control-choice ${selected === value ? "active" : ""}" data-creative-field="${field}" data-creative-value="${value}"${disabled ? " disabled" : ""}>${label}</button>`
  ).join("");
  root.querySelector("#music-mode-controls").innerHTML = buttonGroup([["auto", "Auto"], ["off", "Off"], ["on", "On"]], normalized.music.mode, "music.mode");
  root.querySelector("#music-description").value = normalized.music.description;
  root.querySelector("#music-description-wrap").classList.toggle("hidden", normalized.music.mode !== "on");
  root.querySelector("#camera-control-rows").innerHTML = CAMERA_FIELDS.map(([key, label]) =>
    `<div class="camera-control-row"><span>${label}</span><div class="segmented">${buttonGroup([["avoid", "Avoid"], ["auto", "Auto"], ["prefer", "Prefer"]], normalized.camera[key], `camera.${key}`)}</div></div>`
  ).join("");
  root.querySelector("#visual-style-preset").innerHTML = VISUAL_STYLE_PRESETS.map(([value, label]) =>
    `<option value="${value}">${label}</option>`
  ).join("");
  root.querySelector("#visual-style-preset").value = normalized.visualStyle.preset;
  root.querySelector("#visual-style-custom").value = normalized.visualStyle.custom;
  root.querySelector("#visual-style-custom-wrap").classList.toggle("hidden", normalized.visualStyle.preset !== "custom");
  root.querySelector("#visual-style-helper").textContent = normalized.visualStyle.preset === "custom" && !normalized.visualStyle.custom.trim()
    ? "Add a custom style description to make this control active."
    : "Non-Auto styles are established naturally at the beginning of Shot 1.";
  root.querySelector("#subject-identity-fidelity-controls").innerHTML = buttonGroup(
    [["auto", "Auto"], ["balanced", "Balanced"], ["strong", "Strong"], ["strict", "Strict"]],
    normalized.subjectIdentityFidelity.level,
    "subjectIdentityFidelity.level",
    !hasSubjectIdentityPicture,
  );
  root.querySelector("#subject-identity-fidelity-controls").classList.toggle("inactive", !hasSubjectIdentityPicture);
  root.querySelector("#subject-identity-fidelity-helper").textContent = hasSubjectIdentityPicture
    ? "Applies only to Subject Identity Picture references; pose and composition remain free."
    : "Inactive until a Picture reference uses the Subject Identity role.";
  const rangeRow = (label, field, levels, value) => {
    const rangeValue = rangeForSemantic(levels, value);
    return `<label class="tone-range-row"><span>${label}</span><div><input type="range" min="0" max="${levels.length - 1}" step="1" value="${rangeValue}" data-tone-field="${field}" aria-label="${label}" aria-valuetext="${semanticLabel(value)}"><output>${semanticLabel(value)}</output></div></label>`;
  };
  root.querySelector("#tone-mood-controls").innerHTML = TONE_FIELDS.map(([key, label]) =>
    rangeRow(label, key, TONE_LEVELS, normalized.tonePerformance[key]),
  ).join("");
  root.querySelector("#performance-controls").innerHTML = [
    rangeRow("Performance style", "performanceStyle", PERFORMANCE_STYLE_LEVELS, normalized.tonePerformance.performanceStyle),
    rangeRow("Performance energy", "performanceEnergy", PERFORMANCE_ENERGY_LEVELS, normalized.tonePerformance.performanceEnergy),
  ].join("");
}
