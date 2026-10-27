"""Canonical Sequence narrative identity; never Workspace participation metadata."""
import re
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .workspace import SubjectRecord


def narrative_key(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def validate_narrative_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Narrative names must be text")
    if any(unicodedata.category(char).startswith("C") or char in "\u2028\u2029" for char in value):
        raise ValueError("Narrative names cannot contain control or newline characters")
    value = value.strip()
    if not value or len(value) > 80:
        raise ValueError("Narrative names must contain 1–80 characters")
    if re.fullmatch(r"<?subject\s+\d+>?", narrative_key(value)):
        raise ValueError("Subject N labels are reserved for technical identity")
    return value


class CharacterProfile(BaseModel):
    """Small, stable, author-written characterization for one canonical Subject."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    role_background: str = Field(default="", alias="roleBackground", max_length=4000)
    personality: str = Field(default="", max_length=4000)
    appearance: str = Field(default="", max_length=4000)
    speech_mannerisms: str = Field(default="", alias="speechMannerisms", max_length=4000)
    notes: str = Field(default="", max_length=4000)

    def is_blank(self) -> bool:
        return not any((
            self.role_background.strip(), self.personality.strip(), self.appearance.strip(),
            self.speech_mannerisms.strip(), self.notes.strip(),
        ))


class SequenceSubject(SubjectRecord):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    narrative_name: str | None = Field(default=None, alias="narrativeName")
    narrative_aliases: list[str] = Field(default_factory=list, alias="narrativeAliases", max_length=100)
    character_profile: CharacterProfile = Field(default_factory=CharacterProfile, alias="characterProfile")

    @field_validator("narrative_name")
    @classmethod
    def validate_name(cls, value):
        return validate_narrative_name(value) if value is not None else None

    @field_validator("narrative_aliases")
    @classmethod
    def validate_aliases(cls, values):
        return [validate_narrative_name(value) for value in values]

    @model_validator(mode="after")
    def unique_tokens(self):
        tokens = ([self.narrative_name] if self.narrative_name else []) + self.narrative_aliases
        if len({narrative_key(value) for value in tokens}) != len(tokens):
            raise ValueError("A Subject's narrative name and aliases must be distinct")
        return self

    @property
    def display_label(self):
        technical = f"Subject {self.number}"
        return f"{self.narrative_name} ({technical})" if self.narrative_name else technical


def validate_cast_names(subjects):
    owners = {}
    for subject in subjects:
        for value in ([subject.narrative_name] if subject.narrative_name else []) + subject.narrative_aliases:
            key = narrative_key(value)
            if key in owners and owners[key] != subject.id:
                raise ValueError(f"Narrative name or alias {value!r} is already reserved by another Subject")
            owners[key] = subject.id


def local_narrative_subjects(workspace, subjects):
    """Exact stable-ID intersection; narrative availability never adds participation."""
    local = {subject.id: subject.number for subject in workspace.subjects}
    result = []
    for subject in subjects:
        if subject.id not in local:
            continue
        if local[subject.id] != subject.number:
            raise ValueError("Subject conflicts with Sequence identity; reload the Project")
        if subject.narrative_name or subject.narrative_aliases:
            result.append(subject.model_copy(deep=True))
    return sorted(result, key=lambda subject: (subject.number, subject.id))


def narrative_mapping(subjects, *, include_character_profiles: bool = False):
    """Current-name-only model-facing identity mapping.

    H3 uses the default projection.  Character Profiles deliberately remain
    Story Planner context rather than becoming hidden H3 prompt prose. Retained
    aliases are recognition-only compatibility metadata and are never projected.
    """
    result = []
    for subject in subjects:
        item = {
            "subjectId": subject.id,
            "canonicalH3Label": f"<Subject {subject.number}>",
            "narrativeName": subject.narrative_name,
        }
        if include_character_profiles and not subject.character_profile.is_blank():
            item["characterProfile"] = subject.character_profile.model_dump(mode="json", by_alias=True)
        result.append(item)
    return result


CHARACTER_PROFILE_POLICY = """
Character Profiles are stable author-provided Subject guidance for characterization,
appearance, mannerisms, voice, and background. They never establish current plot state.
They cannot override structural or signed operation authority, an explicit operation
instruction, protected or exact Generation authoring, current or historically valid
Story Memory, or exact dialogue. When a stable Profile conflicts with newer Story
Memory, honor Story Memory for the current state; do not rewrite the Profile.
"""


def character_profile_policy_for(subjects) -> str:
    """Return Profile guidance only when the supplied cast contains a Profile."""
    for subject in subjects:
        if isinstance(subject, dict):
            profile = subject.get("characterProfile")
            if isinstance(profile, dict) and any(str(value).strip() for value in profile.values()):
                return CHARACTER_PROFILE_POLICY
            continue
        profile = getattr(subject, "character_profile", None)
        if profile is not None and not profile.is_blank():
            return CHARACTER_PROFILE_POLICY
    return ""
