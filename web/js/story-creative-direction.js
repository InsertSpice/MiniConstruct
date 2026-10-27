import { TONE_FIELDS, TONE_LEVELS, PERFORMANCE_STYLE_LEVELS, PERFORMANCE_ENERGY_LEVELS,
  createRangeRow, semanticFromRange, semanticLabel } from "./creative-controls.js";

// Story guidance is deliberately separate from H3's per-shot wording: it shapes planning, not camera prompts.
export const STORY_DIRECTION_GUIDANCE = {
  sensuality: {
    subtle: "Allow a light sensual or attraction undercurrent where it suits the story, expressed through interpersonal behavior, proximity, or atmosphere rather than obligatory sexual behavior.",
    moderate: "Give attraction, sensual atmosphere, and interpersonal chemistry a clear but context-appropriate influence on relevant beats and behavior.",
    strong: "Let sensuality and attraction materially shape compatible interpersonal behavior, proximity, atmosphere, and beat selection without overriding the story's actual authority.",
    intense: "Make sensual atmosphere and attraction a dominant influence in compatible story beats while preserving context, consent, continuity, and the story's actual authority.",
  }, drama: {
    subtle: "Add a light emotional weight to relevant beats through meaningful reactions and modest stakes.",
    moderate: "Give compatible beats clear emotional conflict, consequences, and expressive stakes without forcing melodrama.",
    strong: "Maintain strong emotional conflict and meaningful stakes where compatible, letting them materially shape beat construction without making every moment melodramatic.",
    intense: "Make high emotional stakes and consequential conflict a dominant influence in compatible beats while preserving believable motivation and continuity.",
  }, horror: {
    subtle: "Maintain a lightly unsettling or ominous undercurrent through atmosphere, details, and reactions where compatible.",
    moderate: "Give compatible beats a clear ominous, threatening, or unsettling emphasis without inventing unrelated horror.",
    strong: "Let dread, threat, and unsettling developments materially influence compatible beat construction while respecting the established story.",
    intense: "Make dread, threat, and horror atmosphere a dominant influence in compatible beats without overriding established story facts or forcing unrelated danger.",
  }, tension: {
    subtle: "Maintain a light undercurrent of tension; let unease emerge through small details and restrained reactions rather than overt danger dominating each beat.",
    moderate: "Build clear suspense, uncertainty, or pressure through compatible developments and reactions.",
    strong: "Maintain strong dramatic tension; let uncertainty, pressure, and suspense materially influence beat construction without forcing unrelated danger.",
    intense: "Make escalating suspense, pressure, and uncertainty a dominant influence in compatible beats while preserving continuity and actual story authority.",
  }, romance: {
    subtle: "Allow a light romantic warmth or affection to influence compatible interpersonal moments.",
    moderate: "Give compatible beats clear romantic chemistry, affection, and emotional connection without forcing a relationship into unrelated scenes.",
    strong: "Let romance, intimacy, and interpersonal connection materially influence compatible behavior and beat selection while respecting the established story.",
    intense: "Make romantic connection and intimacy a dominant influence in compatible beats while preserving context, consent, and the story's actual authority.",
  }, whimsy: {
    subtle: "Add a lightly playful, charming, or imaginative quality to compatible moments without disrupting the story's logic.",
    moderate: "Give compatible beats a clear playful, fanciful, or imaginative character while retaining coherent cause and effect.",
    strong: "Let playful imagination, charm, and lightly surreal possibilities materially shape compatible beat construction without becoming random nonsense.",
    intense: "Make playful, imaginative, and lightly surreal character a dominant influence in compatible beats while preserving coherent story logic.",
  }, performanceStyle: {
    restrained: "Keep performances understated and controlled, favoring small expressions, measured movement, and restrained delivery over theatrical display.",
    subtle: "Favor natural, low-amplitude performances with nuanced reactions and believable restraint.",
    expressive: "Use clearly visible facial expression, body language, gesture, and emotional delivery where appropriate without requiring frantic action.",
    exaggerated: "Use large, emphatic, highly animated performances where appropriate to the medium and scene.",
  }, performanceEnergy: {
    calm: "Keep performances calm and composed, with quiet reactions and low-intensity movement; this does not require slow story progression.",
    low: "Keep performances restrained and low-key, favoring subtle reactions, controlled movement, and understated delivery over heightened or explosive expression.",
    energetic: "Favor lively, active, animated performance and movement where appropriate without changing the chosen performance style.",
    intense: "Favor forceful, vigorous, high-momentum performance and action where appropriate without requiring every beat to escalate.",
  }, pacing: {
    very_slow: "Let narrative progression unfold very gradually, allowing developments, atmosphere, and consequences time to settle while preserving meaningful movement.",
    slow: "Keep narrative progression measured, allowing developments and reactions room to unfold before the next meaningful turn.",
    brisk: "Keep narrative progression brisk. Move regularly toward the next meaningful development, using concise transitions while preserving continuity.",
    fast: "Move narrative progression quickly through meaningful developments and decisive transitions while preserving clarity and continuity.",
  }, density: {
    very_sparse: "Keep beat density very sparse. Center each Generation on one primary development and leave generous room for atmosphere, reactions, and transitions.",
    sparse: "Keep beat density sparse. Center each Generation on one primary development and leave room for reactions, atmosphere, and transitions to breathe.",
    dense: "Keep beat density dense, packing several connected meaningful developments or pieces of information into each Generation without sacrificing clarity.",
    very_dense: "Keep beat density very dense, packing many tightly connected meaningful developments into each Generation while maintaining comprehensible continuity.",
  },
};

export const DIRECTION_GROUPS = [
  ["toneMood", "Tone / Mood", TONE_FIELDS.map(([key, label]) => [key, label, TONE_LEVELS])],
  ["performanceEnergy", "Performance / Energy", [["performanceStyle", "Performance style", PERFORMANCE_STYLE_LEVELS], ["performanceEnergy", "Performance energy", PERFORMANCE_ENERGY_LEVELS]]],
  ["pacingRhythm", "Pacing / Rhythm", [["pacing", "Pacing", ["very_slow", "slow", "auto", "brisk", "fast"]]]],
  ["beatDurationDensity", "Beat Duration / Density", [["density", "Beat density", ["very_sparse", "sparse", "auto", "dense", "very_dense"]]]],
];
const controlsFor = field => DIRECTION_GROUPS.find(([key]) => key === field)?.[2] || [];
const semantic = (key, value) => STORY_DIRECTION_GUIDANCE[key]?.[value];
const legacyNoun = key => ({ performanceStyle: "performance", performanceEnergy: "energy", density: "beat density" }[key] || key);

export function encodeDirectionField(field, values) {
  return controlsFor(field).flatMap(([key, , levels]) => {
    const value = values[key] ?? "auto";
    if (!levels.includes(value)) throw new Error(`Unknown ${key} value: ${value}`);
    return value === "auto" ? [] : [semantic(key, value)];
  }).join("\n\n");
}
function legacyShort(field, values) {
  const parts = controlsFor(field).flatMap(([key, , levels]) => values[key] !== "auto" && levels.includes(values[key]) ? [`${values[key].replaceAll("_", " ")} ${legacyNoun(key)}`] : []);
  return parts.length ? `${parts.join("; ")[0].toUpperCase()}${parts.join("; ").slice(1)}.` : "";
}
// Decode only exact MiniConstruct formats: current semantic text and the original v11 short spelling.
export function decodeDirectionField(field, text) {
  const controls = controlsFor(field), defaults = Object.fromEntries(controls.map(([key]) => [key, "auto"]));
  if (text === "") return defaults;
  const semanticValues = { ...defaults };
  for (const [key, , levels] of controls) {
    const match = levels.find(value => value !== "auto" && text.split("\n\n").includes(semantic(key, value)));
    if (match) semanticValues[key] = match;
  }
  if (encodeDirectionField(field, semanticValues) === text) return semanticValues;
  if (!text.endsWith(".")) return null;
  const legacy = { ...defaults };
  for (const part of text.slice(0, -1).toLowerCase().split("; ")) {
    const match = controls.flatMap(([key, , levels]) => levels.filter(value => value !== "auto" && part === `${value.replaceAll("_", " ")} ${legacyNoun(key)}`).map(value => [key, value]))[0];
    if (!match || legacy[match[0]] !== "auto") return null;
    legacy[match[0]] = match[1];
  }
  return legacyShort(field, legacy) === text ? legacy : null;
}

export function createStoryCreativeDirection({ root, confirmReplace, onChange }) {
  const doc = root.ownerDocument, groups = [], custom = doc.createElement("details");
  const summary = doc.createElement("summary"); summary.textContent = "Custom guidance"; custom.append(summary);
  const reset = doc.createElement("button"); reset.type = "button"; reset.className = "quiet"; reset.id = "story-reset-creative-direction"; reset.textContent = "Reset Creative Direction";
  const payload = () => Object.fromEntries(groups.map(group => [group.field, group.text]));
  const hasCustom = () => groups.some(group => group.values === null && group.text !== "");
  for (const [field, label, controls] of DIRECTION_GROUPS) {
    const section = doc.createElement("section"); section.className = "story-direction-group";
    const heading = doc.createElement("h3"); heading.textContent = label; const rows = doc.createElement("div"); rows.className = "tone-range-controls";
    const input = doc.createElement("textarea"); input.rows = 2; input.maxLength = 2000; input.id = `story-creative-${field.replace(/[A-Z]/g, letter => `-${letter.toLowerCase()}`)}`; input.setAttribute("aria-label", `${label} custom guidance`);
    const customLabel = doc.createElement("label"); customLabel.textContent = label; customLabel.append(input);
    const makeCustom = doc.createElement("button"); makeCustom.type = "button"; makeCustom.textContent = "Replace with Custom prose";
    const useControls = doc.createElement("button"); useControls.type = "button"; useControls.textContent = "Use structured controls"; useControls.id = `story-structured-${field}`;
    custom.append(customLabel, makeCustom, useControls);
    if (field === "toneMood") { const header = doc.createElement("div"); header.className = "section-heading compact-heading"; header.append(heading, reset); section.append(header); } else if (controls.length > 1) section.append(heading);
    section.append(rows); root.append(section);
    const group = { field, text: "", values: null, input, customLabel, makeCustom, useControls };
    const render = () => {
      const structured = group.values !== null; rows.hidden = !structured; section.hidden = field !== "toneMood" && !structured; customLabel.hidden = structured; useControls.hidden = structured; makeCustom.hidden = !structured;
      rows.replaceChildren();
      if (!structured) { custom.open = true; return; }
      for (const [key, title, levels] of controls) {
        const row = createRangeRow(doc, title, key, levels, group.values[key]), slider = row.querySelector("input"); slider.id = `story-direction-${key}`;
        if (key === "pacing") slider.title = "How rapidly the narrative advances across Generations; independent of beat density.";
        if (key === "density") slider.title = "How much meaningful action or information belongs in each Generation; independent of pacing.";
        if (key === "performanceEnergy") slider.title = "Performance intensity and activity, not whether the story is boring or slow.";
        slider.addEventListener("input", () => { group.values[key] = semanticFromRange(levels, slider.value); const label = semanticLabel(group.values[key]); row.querySelector("output").textContent = label; slider.setAttribute("aria-valuetext", label); group.text = encodeDirectionField(field, group.values); onChange(payload()); }); rows.append(row);
      }
    };
    group.restore = text => { group.text = text; input.value = text; group.values = decodeDirectionField(field, text); render(); };
    input.addEventListener("input", () => { group.text = input.value; group.values = null; render(); onChange(payload()); });
    makeCustom.addEventListener("click", () => { if (reset.disabled) return; group.values = null; group.text = ""; input.value = ""; render(); input.focus(); onChange(payload()); });
    useControls.addEventListener("click", () => { if (reset.disabled) return; if (group.text !== "" && !confirmReplace("Replace this Custom guidance with Auto structured controls?")) return; group.restore(""); custom.open = hasCustom(); onChange(payload()); });
    groups.push(group); group.restore("");
  }
  root.append(custom);
  const restore = (value = {}) => { groups.forEach(group => group.restore(value?.[group.field] ?? "")); custom.open = hasCustom(); };
  reset.addEventListener("click", () => { if (reset.disabled) return; if (hasCustom() && !confirmReplace("Reset Creative Direction and erase Custom guidance?")) return; restore(); onChange(payload()); });
  return { payload, restore, setBusy(value) { reset.disabled = value; groups.forEach(group => { group.useControls.disabled = value; group.makeCustom.disabled = value; }); } };
}
