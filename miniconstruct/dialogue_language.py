"""Resolution of authoring dialogue into MiniMax H3's documented spoken languages.

This module intentionally sits before H3 assembly.  It never edits source dialogue
and it does not attempt language detection: provenance and the selected enum are the
only authority.
"""

from __future__ import annotations

import hashlib
import re

from miniconstruct.llm.client import LLMBackendError, OpenAICompatibleClient
from miniconstruct.models.api import LLMSettings
from miniconstruct.models.workspace import DialogueLanguage, DialogueResolution, Workspace, DIALOGUE_LANGUAGE_LABELS


TRANSLATION_CONTRACT_VERSION = 1
_DIALOGUE_PREFIX = re.compile(r"^(\s*(?:\[[^\]\r\n]+\]\s*)*[^:\r\n]+:\s*)(.*)$")


def dialogue_fingerprint(source_text: str, target_language: DialogueLanguage) -> str:
    payload = f"dialogue-resolution-v{TRANSLATION_CONTRACT_VERSION}\0{target_language.value}\0{source_text}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolution_is_current(workspace: Workspace) -> bool:
    if workspace.dialogue_language == DialogueLanguage.AS_WRITTEN:
        return True
    resolution = workspace.dialogue_resolution
    return bool(resolution and resolution.target_language == workspace.dialogue_language
                and resolution.source_text == workspace.dialogue
                and (not workspace.dialogue.strip() or resolution.resolved_text.strip())
                and resolution.contract_version == TRANSLATION_CONTRACT_VERSION
                and resolution.status in {"current", "user_edited"}
                # An explicit user override is its own authority while the exact
                # source/target pair remains unchanged. Refresh writes a normal
                # deterministic cache fingerprint again.
                and (resolution.status == "user_edited" or resolution.source_fingerprint == dialogue_fingerprint(workspace.dialogue, workspace.dialogue_language)))


def exact_dialogue(workspace: Workspace) -> str:
    """The sole text passed to H3 validation and prompt compilation."""
    if workspace.dialogue_language == DialogueLanguage.AS_WRITTEN:
        return workspace.dialogue
    if not resolution_is_current(workspace):
        raise LLMBackendError(
            "Dialogue translation is stale or missing.", 409, category="dialogue_resolution",
            safe_message="Translate or refresh Spoken Output before generating an H3 prompt.",
        )
    return workspace.dialogue_resolution.resolved_text if workspace.dialogue_resolution else ""


def workspace_for_h3(workspace: Workspace) -> Workspace:
    """A transient compiler projection; persisted source remains intact."""
    return workspace.model_copy(update={"dialogue": exact_dialogue(workspace)})


def story_dialogue_policy(language: DialogueLanguage) -> str:
    speaker_policy = (" When authoring more than one distinct vocal event or speaker turn, use the existing canonical Name: or Subject N: textual speaker-label syntax on separate lines. A single utterance containing a pause or ellipsis remains one exact utterance; do not split it across Subjects by punctuation or order.")
    if language == DialogueLanguage.AS_WRITTEN:
        return "Dialogue Language is As Written: author exact dialogue in its natural authored wording." + speaker_policy
    return (f"Sequence Dialogue Language is {DIALOGUE_LANGUAGE_LABELS[language]} ({language.value}). "
            "For every NEW or explicitly dialogue-scoped revised Exact Dialogue / Lyrics value, author the spoken "
            "or sung words directly in that language. Do not author English then add an instruction to translate it. "
            "The language name is metadata, not spoken text: never put [Japanese], (Japanese), Japanese:, TRANSLATION:, or equivalent language/translation/pronunciation annotations in the canonical dialogue field unless those characters are literally intended to be spoken. Preserve existing protected user-authored dialogue exactly. "
            "Do not translate Creative Request, Story Memory, profiles, state, or other planning prose." + speaker_policy)


def identity_dialogue_resolution(dialogue: str, language: DialogueLanguage) -> DialogueResolution | None:
    """Record Story-authored target-language text without a translation call."""
    if language == DialogueLanguage.AS_WRITTEN:
        return None
    return DialogueResolution(
        sourceText=dialogue,
        targetLanguage=language,
        resolvedText=dialogue,
        sourceFingerprint=dialogue_fingerprint(dialogue, language),
        status="current",
        contractVersion=TRANSLATION_CONTRACT_VERSION,
    )


def validate_translation_structure(source: str, translated: str) -> None:
    """Keep the existing line-oriented speaker/marker syntax out of LLM discretion."""
    source_lines = [line for line in source.splitlines() if line.strip()]
    target_lines = [line for line in translated.splitlines() if line.strip()]
    if len(source_lines) != len(target_lines):
        raise LLMBackendError("Dialogue translation changed the number of spoken lines.", 502, category="dialogue_resolution",
                              safe_message="Translation changed dialogue line structure. Refresh with a compatible model.")
    for index, (source_line, target_line) in enumerate(zip(source_lines, target_lines), 1):
        source_match, target_match = _DIALOGUE_PREFIX.match(source_line), _DIALOGUE_PREFIX.match(target_line)
        if bool(source_match) != bool(target_match) or (source_match and source_match.group(1) != target_match.group(1)):
            raise LLMBackendError("Dialogue translation changed a speaker label or structural marker.", 502, category="dialogue_resolution",
                                  safe_message=f"Translation changed the speaker or timestamp structure on dialogue line {index}.")
        payload = target_match.group(2) if target_match else target_line
        if not payload.strip():
            raise LLMBackendError("Dialogue translation removed spoken content.", 502, category="dialogue_resolution",
                                  safe_message=f"Translation removed spoken content on dialogue line {index}.")


async def resolve_dialogue(
    workspace: Workspace,
    settings: LLMSettings,
    client_factory=None,
) -> DialogueResolution | None:
    """Return a cached resolution or make one narrow, low-variance provider call."""
    if workspace.dialogue_language == DialogueLanguage.AS_WRITTEN:
        return None
    if resolution_is_current(workspace):
        return workspace.dialogue_resolution
    if not workspace.dialogue.strip():
        return DialogueResolution(sourceText="", targetLanguage=workspace.dialogue_language, resolvedText="",
                                  sourceFingerprint=dialogue_fingerprint("", workspace.dialogue_language))
    label = DIALOGUE_LANGUAGE_LABELS[workspace.dialogue_language]
    system = (
        "You are MiniConstruct's exact-dialogue translator. Return ONLY the translated dialogue text, with no "
        "Markdown, explanation, quotes, headings, or JSON. Preserve meaning exactly. Do not add, remove, censor, "
        "embellish, summarize, or reorder content. Preserve speaker labels, Subject IDs, timestamps, structural "
        "markers, line ordering, and intentional line breaks exactly; translate only spoken or sung content. "
        "Preserve proper names unless normal target-language transliteration is natural. If content is already "
        "naturally written in the target language, preserve its wording."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": f"Target language: {label}\n\n{workspace.dialogue}"}]
    low_variance = settings.model_copy(update={"temperature": 0, "reasoning_mode": "off"})
    client_factory = client_factory or OpenAICompatibleClient
    async with client_factory(low_variance) as client:
        translated = await client.generate(messages, response_instruction="Return only the exact translated dialogue text.")
    translated = translated.strip("\ufeff")
    if not translated.strip():
        raise LLMBackendError("Dialogue translator returned blank output.", 502, category="dialogue_resolution",
                              safe_message="Translation returned no spoken output. Check the model and try Refresh.")
    validate_translation_structure(workspace.dialogue, translated)
    return DialogueResolution(sourceText=workspace.dialogue, targetLanguage=workspace.dialogue_language,
                              resolvedText=translated, sourceFingerprint=dialogue_fingerprint(workspace.dialogue, workspace.dialogue_language))
