export const HANDOFF_OPTIONS = [
  ["auto", "Auto (recommended)"], ["none", "None"], ["endpoint_frame", "Endpoint frame"],
  ["endpoint_then_immediate_cut", "Endpoint → immediate new shot"], ["tail_reference_continuation", "Tail continuation reference"],
  ["native_i2va_continuation", "Native-only I2VA continuation (no new Picture)"],
];
export const freshVisualHandoff = () => ({ strategy: "auto", tailSeconds: 1.5, immediateCutMilliseconds: 100 });
export function resolveVisualHandoff(policy, relationship) {
  return policy.strategy !== "auto" ? policy.strategy : ({
    continue_previous_shot: "tail_reference_continuation", new_shot_preserve_continuity: "endpoint_then_immediate_cut",
    match_previous_ending_frame: "endpoint_frame",
  }[relationship] || "none");
}

export function visiblePromptWarnings(warnings) {
  return warnings.filter(warning => !warning.startsWith("Visual handoff omitted:")
    && !warning.startsWith("Native H3 continuation unavailable:"));
}

export function renderVisualHandoff(root, { policy, relationship, summary, previewUrl, dirty, onChange }) {
  if (!root) return;
  const doc = root.ownerDocument;
  const make = (tag, text) => { const el = doc.createElement(tag); if (text) el.textContent = text; return el; };
  root.replaceChildren();
  const label = make("label", "Visual handoff");
  const select = make("select"); select.setAttribute("aria-label", "Visual handoff");
  for (const [value, text] of HANDOFF_OPTIONS) { const option = make("option", text); option.value = value; select.append(option); }
  if (summary?.native?.supported || policy.strategy === "native_h3_continuation") {
    const option = make("option", "Native H3 continuation"); option.value = "native_h3_continuation";
    option.disabled = !summary?.native?.supported; select.append(option);
  }
  select.value = policy.strategy;
  select.addEventListener("change", () => onChange({ ...policy, strategy: select.value }));
  label.append(select); root.append(label);
  const strategy = policy.strategy === "native_i2va_continuation" ? policy.strategy
    : !dirty && summary?.native?.consume ? "native_h3_continuation" : resolveVisualHandoff(policy, relationship);
  const resolved = strategy === "native_h3_continuation" ? "Native H3 continuation" : summary?.status === "textual_fallback" ? "Textual continuity" : strategy === "tail_reference_continuation" ? `previous ${policy.tailSeconds} s tail continuation reference` :
    strategy === "endpoint_then_immediate_cut" ? "endpoint frame → immediate new shot" : strategy.replaceAll("_", " ");
  const description = make("p", `${policy.strategy === "auto" ? "Auto: " : ""}${resolved}`); description.className = "helper";
  root.append(description);
  if (strategy === "native_i2va_continuation") {
    const native = summary?.native;
    root.append(make("p", "Choose I2VA and Continue previous shot. No new first-frame Picture is expected. Apply a separate picture-free successor Workflow Profile in ComfyUI Execution before generating the prompt."));
    root.append(make("p", dirty ? "Save to verify native-only continuation."
      : native?.consume && native?.pictureFreeI2va ? `Native-only ready · ${native.profileName} · accepted Generation ${native.sourceGenerationNumber}`
        : `Native-only blocked · ${native?.reason || "No verified accepted native source or successor Profile."}`));
  }
  if (["tail_reference_continuation", "endpoint_then_immediate_cut"].includes(strategy)) {
    const tail = strategy === "tail_reference_continuation";
    const valueLabel = make("label", tail ? "Tail duration (seconds)" : "Cut after opening anchor (ms, requested timing)");
    const input = make("input"); input.type = "number"; input.min = tail ? "0.5" : "0";
    input.max = tail ? "2" : "500"; input.step = tail ? "0.1" : "1";
    input.value = String(tail ? policy.tailSeconds : policy.immediateCutMilliseconds);
    input.addEventListener("change", () => {
      const value = Number(input.value);
      if (!Number.isFinite(value) || value < Number(input.min) || value > Number(input.max) || (!tail && !Number.isInteger(value))) {
        input.value = String(tail ? policy.tailSeconds : policy.immediateCutMilliseconds); return;
      }
      onChange({ ...policy, [tail ? "tailSeconds" : "immediateCutMilliseconds"]: value });
    });
    valueLabel.append(input); root.append(valueLabel);
  }
  const state = make("p"); state.className = "helper"; state.setAttribute("role", "status");
  const sourceLabel = summary?.sourceAuthority === "accepted_render" ? "previous accepted render" :
    summary?.sourceAuthority === "selected_take" ? "previous Selected Take" : "previous rendered media";
  state.textContent = strategy === "none" ? "No predecessor visual evidence." : dirty ? "Save to resolve previous rendered media and H3 mode compatibility." :
    !summary ? "Save a filesystem Project to resolve previous rendered media." :
    `Source: ${sourceLabel} · ${{ available: "Available", missing: "Missing", no_selected_take: "No endpoint source", unavailable: "Unavailable" }[summary.status] || "None"} · ${summary.compatible ? "Compatible with current H3 mode" : "Incompatible with current H3 mode"}${summary.warnings?.length ? `. ${summary.warnings.join(" ")}` : ""}`;
  root.append(state);
  if (summary?.native?.supported) {
    const native = summary.native;
    state.textContent = dirty ? "Save to resolve continuation." : strategy === "native_h3_continuation"
      ? `Source: Generation ${native.sourceGenerationNumber ?? "—"} · ${native.profileName} · Context: ${native.contextFrames} frames · Audio continuity: ${native.audioEnabled ? "enabled" : "disabled/unsupported"}${native.consume ? "" : `. ${native.reason}`}`
      : `${state.textContent} Native: ${native.reason}`;
  }
  if (!dirty && summary?.status === "native_available" && summary.native?.consume && !summary.native.requiresTailReference)
    state.textContent = `Native continuation will use the accepted predecessor execution. Source: Generation ${summary.native.sourceGenerationNumber ?? "—"} · ${summary.native.profileName} · Context: ${summary.native.contextFrames} frames · Audio continuity: ${summary.native.audioEnabled ? "enabled" : "disabled/unsupported"}.`;
  if (!dirty && ["endpoint_frame", "endpoint_then_immediate_cut", "tail_reference_continuation"].includes(strategy) && summary?.status === "available" && summary.compatible && previewUrl) {
    const button = make("button", "Preview handoff"); button.type = "button";
    button.addEventListener("click", () => {
      const media = make(strategy === "tail_reference_continuation" ? "video" : "img");
      media.className = "handoff-preview";
      if (strategy === "tail_reference_continuation") { media.controls = true; media.preload = "none"; }
      else media.alt = "Previous rendered endpoint";
      media.src = previewUrl; button.disabled = true; root.append(media);
    });
    root.append(button);
  }
}
