export const AS_WRITTEN_DIALOGUE_LANGUAGE = "as_written";

export function effectiveDialogueLanguage(projectLanguage, workspaceLanguage) {
  return projectLanguage || workspaceLanguage || AS_WRITTEN_DIALOGUE_LANGUAGE;
}

export function dialogueResolutionIsCurrent({ dialogueLanguage, dialogue, resolution }) {
  return Boolean(
    dialogueLanguage !== AS_WRITTEN_DIALOGUE_LANGUAGE
      && resolution
      && resolution.targetLanguage === dialogueLanguage
      && resolution.sourceText === dialogue
      && ["current", "user_edited"].includes(resolution.status),
  );
}

export async function dialogueResolutionNeedsProvider({ dialogueLanguage, dialogue, resolution }) {
  if (dialogueLanguage === AS_WRITTEN_DIALOGUE_LANGUAGE || !dialogue.trim()) return false;
  if (!dialogueResolutionIsCurrent({ dialogueLanguage, dialogue, resolution }) || resolution.contractVersion !== 1) return true;
  if (!resolution.resolvedText?.trim()) return true;
  if (resolution.status === "user_edited") return false;
  try {
    const payload = `dialogue-resolution-v1\0${dialogueLanguage}\0${dialogue}`;
    const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(payload));
    const fingerprint = [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
    return resolution.sourceFingerprint !== fingerprint;
  } catch { return true; }
}

export function staleDialogueResolution(resolution) {
  return resolution ? { ...resolution, status: "stale" } : null;
}

export function editedDialogueResolution({ dialogueLanguage, dialogue, resolution, resolvedText }) {
  if (dialogueLanguage === AS_WRITTEN_DIALOGUE_LANGUAGE) return resolution;
  return {
    ...(resolution || {}),
    sourceText: dialogue,
    targetLanguage: dialogueLanguage,
    resolvedText,
    status: "user_edited",
    contractVersion: 1,
    sourceFingerprint: resolution?.sourceFingerprint || "0".repeat(64),
  };
}
