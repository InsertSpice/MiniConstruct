const SECTION_NAMES = new Set([
  "integrated_multimodal_description", "subject_definitions", "summary",
  "retention_analysis", "detailed_description", "overall_soundscape", "non_diegetic_music",
]);
const RETENTION_MARKERS = [
  "partially_preserved", "fully_preserved", "attribute_transfer", "weak_reference",
  "partially_copy", "fully_copy", "reference",
];
const TASK_TYPES = "video continuation|video editing|reference generation|audio reuse|audio reference";
const TOKEN_RE = new RegExp(
  `^(?:${[...SECTION_NAMES].join("|")}):|\\[Shot\\s+[1-9]\\d*(?:\\]\\s+At\\s+\\d{2}:\\d{2}\\.\\d{3},|\\])|` +
  `<(?:Subject|Picture|Video|Audio)\\s+[1-9]\\d*>|\\(S[1-9]\\d*\\)|<\\/?d>|` +
  `\\[(?:${TASK_TYPES})(?:\\s*\\+\\s*(?:${TASK_TYPES}))*\\]|` +
  `\\b(?:${RETENTION_MARKERS.join("|")})\\b`,
  "gim",
);

function tokenType(value) {
  if (value.endsWith(":")) return "section";
  if (value.startsWith("[Shot")) return "shot";
  if (["<Subject", "<Picture", "<Video", "<Audio"].some(prefix => value.startsWith(prefix))) return "reference";
  if (value.startsWith("(S")) return "speaker";
  if (value === "<d>" || value === "</d>") return "dialogue-tag";
  if (value.startsWith("[")) return "task";
  return "retention";
}

export function tokenizePrompt(text) {
  const source = String(text ?? "");
  const tokens = [];
  let cursor = 0;
  TOKEN_RE.lastIndex = 0;
  let match;
  while ((match = TOKEN_RE.exec(source))) {
    if (match.index > cursor) tokens.push({ type: "text", text: source.slice(cursor, match.index) });
    tokens.push({ type: tokenType(match[0]), text: match[0] });
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) tokens.push({ type: "text", text: source.slice(cursor) });
  return tokens;
}

export function highlightPrompt(container, text) {
  container.replaceChildren();
  for (const token of tokenizePrompt(text)) {
    if (token.type === "text") container.append(document.createTextNode(token.text));
    else {
      const span = document.createElement("span");
      span.className = `syntax-${token.type}`;
      span.textContent = token.text;
      container.append(span);
    }
  }
}
