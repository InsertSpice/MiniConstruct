"""Bounded Generation-owned continuity snapshots and per-slot operation deltas."""
from uuid import UUID, uuid4
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

STORY_SUMMARY_MAX_CHARS = 6000
STORY_MEMORY_MAX_ENTRIES = 64  # Per collection, never a chronological transcript.
STORY_MEMORY_ENTRY_MAX_CHARS = 500
STORY_MEMORY_MAX_SUBJECT_LINKS = 32
STORY_MEMORY_NEW_SLOTS = 16  # Per collection per Story operation.


class MemoryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class StoryMemoryEntry(MemoryModel):
    id: str
    text: str = Field(min_length=1, max_length=STORY_MEMORY_ENTRY_MAX_CHARS)
    related_subject_ids: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(default_factory=list, alias="relatedSubjectIds",
                                          max_length=STORY_MEMORY_MAX_SUBJECT_LINKS)

    @field_validator("id")
    @classmethod
    def opaque_id(cls, value):
        return str(UUID(value))

    @model_validator(mode="after")
    def valid_entry(self):
        if not self.text.strip() or len(set(self.related_subject_ids)) != len(self.related_subject_ids):
            raise ValueError("Memory text must be nonblank and Subject links unique")
        return self


class StoryContinuityMemory(MemoryModel):
    recent_summary: str = Field(default="", alias="recentSummary", max_length=STORY_SUMMARY_MAX_CHARS)
    durable_facts: list[StoryMemoryEntry] = Field(default_factory=list, alias="durableFacts", max_length=STORY_MEMORY_MAX_ENTRIES)
    open_threads: list[StoryMemoryEntry] = Field(default_factory=list, alias="openThreads", max_length=STORY_MEMORY_MAX_ENTRIES)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [entry.id for entry in self.durable_facts + self.open_threads]
        if len(ids) != len(set(ids)):
            raise ValueError("Memory entry IDs must be unique")
        return self

    def validate_subjects(self, subject_ids):
        authority = set(subject_ids)
        if any(not set(entry.related_subject_ids) <= authority
               for entry in self.durable_facts + self.open_threads):
            raise ValueError("Memory references an unknown Sequence Subject")


class StoryMemoryPatch(MemoryModel):
    recent_summary: str | None = Field(default=None, alias="recentSummary", max_length=STORY_SUMMARY_MAX_CHARS)
    fact_upserts: list[StoryMemoryEntry] = Field(default_factory=list, alias="factUpserts", max_length=STORY_MEMORY_MAX_ENTRIES)
    resolved_fact_ids: list[str] = Field(default_factory=list, alias="resolvedFactIds", max_length=STORY_MEMORY_MAX_ENTRIES)
    thread_upserts: list[StoryMemoryEntry] = Field(default_factory=list, alias="threadUpserts", max_length=STORY_MEMORY_MAX_ENTRIES)
    resolved_thread_ids: list[str] = Field(default_factory=list, alias="resolvedThreadIds", max_length=STORY_MEMORY_MAX_ENTRIES)


def apply_memory_patch(memory, patch, fact_slots, thread_slots, subject_ids, *, allocate=False,
                       consumed_fact_ids=None, consumed_thread_ids=None):
    """Validate before allocating canonical IDs. Omitted sections preserve authority."""
    all_ids = [e.id for e in patch.fact_upserts + patch.thread_upserts] + patch.resolved_fact_ids + patch.resolved_thread_ids
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("Duplicate or conflicting memory patch IDs")

    def collection(entries, upserts, resolved, slots, consumed):
        current = {e.id: e.model_copy(deep=True) for e in entries}
        if not set(resolved) <= current.keys():
            raise ValueError("Unknown existing memory ID")
        for key in resolved:
            del current[key]
        for entry in upserts:
            if entry.id not in current and entry.id in slots and consumed is not None and entry.id in consumed:
                raise ValueError("Consumed temporary memory ID cannot be reused")
            if entry.id not in current and entry.id not in slots:
                raise ValueError("Memory upsert must target an existing ID or issued temporary slot")
            if entry.id in slots and consumed is not None:
                consumed.add(entry.id)
            current[entry.id] = entry.model_copy(deep=True)
        return list(current.values())

    result = StoryContinuityMemory(
        recentSummary=memory.recent_summary if patch.recent_summary is None else patch.recent_summary,
        durableFacts=collection(memory.durable_facts, patch.fact_upserts, patch.resolved_fact_ids,
                                fact_slots, consumed_fact_ids),
        openThreads=collection(memory.open_threads, patch.thread_upserts, patch.resolved_thread_ids,
                               thread_slots, consumed_thread_ids),
    )
    result.validate_subjects(subject_ids)
    if allocate:
        temporary = set(fact_slots) | set(thread_slots)
        for entry in result.durable_facts + result.open_threads:
            if entry.id in temporary:
                entry.id = str(uuid4())
    return result
