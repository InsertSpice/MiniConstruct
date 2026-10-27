"""Offline storage measurement using real v9 Project persistence. No providers."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import NAMESPACE_URL, uuid5

from miniconstruct.models.story_memory import (
    STORY_SUMMARY_MAX_CHARS, STORY_MEMORY_MAX_ENTRIES, STORY_MEMORY_ENTRY_MAX_CHARS,
    STORY_MEMORY_MAX_SUBJECT_LINKS, StoryContinuityMemory,
)
from miniconstruct.models.workspace import validate_authoring_workspace
from miniconstruct.project_store import ProjectStore


def representative_memory():
    facts = [
        "Alice carries medicine intended for Bob at the railway depot.",
        "The city gates close at curfew, and guards inspect every late traveller.",
        "Bob uses the depot's old signal lamp to mark a safe approach.",
        "The railway underpass offers the shortest route across the district.",
        "Alice promised Carol she would return before the final evening train.",
        "A maintenance crew has reported electrical faults near the underpass.",
        "The station clock runs five minutes slow after a power interruption.",
        "The medicine must stay dry and arrive before the night shift begins.",
        "Carol knows a footpath connecting the depot and the river steps.",
        "Bob injured his ankle and cannot meet Alice outside the depot.",
        "The market shutters close when the first warning bell sounds.",
        "Alice has a brass key that opens the depot's side entrance.",
    ]
    threads = ["Deliver the medicine to Bob before curfew.", "Find out what caused the electrical faults.",
               "Return the brass key to Carol.", "Arrange a safe route home after the delivery."]
    def entries(values):
        return [{"id": str(uuid5(NAMESPACE_URL, text)), "text": text, "relatedSubjectIds": []} for text in values]
    return StoryContinuityMemory(recentSummary=(
        "Alice has crossed the market district and reached the railway underpass. She is carrying medicine "
        "to Bob at the depot before curfew. The station clock is unreliable, the warning bells have begun, "
        "and electrical faults may affect her route. Carol is waiting for Alice to return with the brass key. "
        "Bob's injured ankle prevents him from meeting her outside, so she must reach the side entrance. "
        "The medicine remains dry and intact. Alice has not yet decided which route she will use to return."),
        durableFacts=entries(facts), openThreads=entries(threads))


def compact_bytes(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def measure():
    memory = representative_memory()
    rows = []
    with TemporaryDirectory(prefix="miniconstruct-memory-storage-") as folder:
        store = ProjectStore(Path(folder))
        project = store.create_project(validate_authoring_workspace({"creativeRequest": "Alice approaches the depot."}), story_memory=memory)
        for count in range(1, 101):
            if count > 1:
                project = store.create_generation(project.project_id)
            if count in (12, 30, 60, 100):
                path = Path(folder) / f"project-{project.project_id}" / "project.json"
                raw = json.loads(path.read_text(encoding="utf-8"))
                memory_bytes = sum(compact_bytes(g["storyMemoryAfter"]) for g in raw["sequence"]["generations"])
                rows.append({"generations": count, "storyMemorySerializedBytes": memory_bytes,
                             "projectManifestBytes": path.stat().st_size,
                             "memoryBytesPerGeneration": memory_bytes // count})
    # A conservative JSON byte ceiling: control characters escape to six ASCII
    # bytes each, exceeding four-byte UTF-8 characters. UUIDs remain 36 ASCII.
    controls = [chr(i) for i in range(32) if i not in {8, 9, 10, 12, 13}]
    links = ["\x00" * 126 + controls[i // len(controls)] + controls[i % len(controls)]
             for i in range(STORY_MEMORY_MAX_SUBJECT_LINKS)]
    worst = StoryContinuityMemory(recentSummary="\x00" * STORY_SUMMARY_MAX_CHARS,
        durableFacts=[{"id": str(uuid5(NAMESPACE_URL, f"worst-fact-{i}")), "text": "\x00" * STORY_MEMORY_ENTRY_MAX_CHARS,
                       "relatedSubjectIds": links}
                      for i in range(STORY_MEMORY_MAX_ENTRIES)],
        openThreads=[{"id": str(uuid5(NAMESPACE_URL, f"worst-thread-{i}")), "text": "\x00" * STORY_MEMORY_ENTRY_MAX_CHARS,
                      "relatedSubjectIds": links}
                     for i in range(STORY_MEMORY_MAX_ENTRIES)])
    ceiling = compact_bytes(worst.model_dump(by_alias=True))
    return {"fixture": "~500-character summary, 12 facts, 4 threads; ordinary blank Generation Workspaces",
            "rows": rows, "approximateManifestGrowthBytesPerGeneration":
            (rows[-1]["projectManifestBytes"] - rows[0]["projectManifestBytes"]) // (rows[-1]["generations"] - rows[0]["generations"]),
            "worstCaseMemoryBytesPerGenerationUpperBound": ceiling,
            "worstCaseMemoryBytesByGenerationCount": {str(n): ceiling * n for n in (12, 30, 60, 100)},
            "notes": "Memory bytes are compact UTF-8 JSON values; manifest bytes are actual indented project.json. Worst case is a separate conservative escaped-character ceiling for bounded memory, not a whole-Project bound."}


if __name__ == "__main__":
    print(json.dumps(measure(), indent=2))
