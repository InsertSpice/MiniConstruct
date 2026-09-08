export const CREATIVE_INTERPRETATION_OPTIONS = [
  ["conservative", "Conservative"],
  ["balanced", "Balanced"],
  ["expansive", "Expansive"],
];

export const DEFAULT_CREATIVE_INTERPRETATION = "balanced";

export function normalizeCreativeInterpretation(value) {
  return CREATIVE_INTERPRETATION_OPTIONS.some(([mode]) => mode === value)
    ? value : DEFAULT_CREATIVE_INTERPRETATION;
}

export function renderCreativeInterpretation(root, selected) {
  const current = normalizeCreativeInterpretation(selected);
  root.querySelector("#creative-interpretation-controls").innerHTML = CREATIVE_INTERPRETATION_OPTIONS.map(([mode, label]) =>
    `<button type="button" class="control-choice ${mode === current ? "active" : ""}" data-creative-interpretation="${mode}" aria-pressed="${mode === current}">${label}</button>`
  ).join("");
}
