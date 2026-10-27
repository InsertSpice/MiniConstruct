"""Provider-free final-prompt isolation and existing archive compatibility."""
import json
from pathlib import Path
import re
import zipfile

from miniconstruct.benchmark.temporal_directing import (
    load_cases, prepare_renderer_gate, renderer_fixture, write_renderer_gate,
)

ROOT = Path(__file__).resolve().parents[1] / "benchmarks"


def test_historical_fixtures_and_archive_shape_remain_readable(tmp_path):
    cases = load_cases(ROOT / "temporal-directing" / "cases.json")
    assert len(cases) == 5
    assert {c.id for c in cases} == {
        "kent-gold-key-search", "quiet-platform-dialogue", "cup-object-handoff",
        "market-crossing-paths", "rhythmic-street-performance",
    }
    # Re-serialize the historical shape with its original writer/report path.
    # Local archives are optional (ignored by Git), and are only read.
    from miniconstruct.benchmark.temporal_directing import write_run
    archived = list((ROOT / "runs" / "h3-temporal-directing").glob("*/results.json"))
    historical = [json.loads(p.read_text(encoding="utf-8")) for p in archived
                  if "renderer-" not in p.parent.name]
    legacy = {"schemaVersion": 1, "suite": "h3-temporal-directing", "runId": "legacy-shape",
              "profile": {"displayName": "Historical fixture"},
              "cases": [{"caseId": "quiet-platform-dialogue", "title": "Quiet",
                         "storyAuthority": {}, "status": "skipped", "skip": {"message": "fixture"}}]}
    for index, result in enumerate(historical or [legacy]):
        original = json.dumps(result, sort_keys=True)
        copied = json.loads(original)
        copied["runId"] = f"historical-compatibility-{index}"
        report = write_run(tmp_path, copied)
        assert report.is_file()
        assert json.loads((report.parent / "results.json").read_text(encoding="utf-8")) == copied
        assert json.dumps(result, sort_keys=True) == original


def test_exact_temporal_transformation_and_complete_shared_contract():
    case, fixture = renderer_fixture(ROOT)
    assert case.id == "miku-intra-shot-actions"
    workspace = case.model_workspace(ROOT / "temporal-directing")
    assert workspace.mode.value == "T2VA"
    assert (workspace.duration_seconds, workspace.shots, workspace.aspect_ratio) == (8, 1, "16:9")
    assert workspace.assets == [] and not workspace.dialogue
    result = prepare_renderer_gate(ROOT)
    assert result["providerUsed"] is False and result["renderPerformed"] is False
    arms = result["cases"][0]["variants"]
    assert set(arms) == {"chronological-control", "approximate-landmarks", "exact-landmarks"}
    control = arms["chronological-control"]["exactOutput"]
    actions = ["she raises her right hand and performs one clear wave.",
               "she lowers the hand and turns her whole body around to face the chair.",
               "she sits down on the chair."]
    for name, arm in arms.items():
        prompt = arm["exactOutput"]
        assert arm["validation"]["valid"]
        assert re.findall(r"\[Shot \d+\]", prompt) == ["[Shot 1]"]
        assert not re.search(r"<(?:Picture|Video|Audio) \d+>", prompt)
        assert "one continuous shot; no cuts; no transitions" in prompt
        assert [prompt.index(action) for action in actions] == sorted(prompt.index(action) for action in actions)
        assert all(prompt.count(action) == 1 for action in actions)
        # Explicitly reverse exactly the intended prefixes. Equality proves all
        # other prose/sections/constraints/content are identical, not just hashes.
        normalized = prompt
        for prefix, original in zip(fixture["treatments"][name], fixture["treatments"]["chronological-control"], strict=True):
            assert normalized.count(prefix) == 1
            normalized = normalized.replace(prefix, original, 1)
        assert normalized == control
        timestamps = re.findall(r"\d{2}:\d{2}\.\d{3}", prompt)
        if name == "chronological-control":
            assert timestamps == []
        else:
            assert timestamps == ["00:02.000", "00:04.000", "00:06.000"]
            word = "Around" if name == "approximate-landmarks" else "At"
            assert re.findall(rf"{word} (\d{{2}}:\d{{2}}\.\d{{3}}),", prompt) == timestamps
            assert ("Around " in prompt) == (word == "Around")
        targets = [e["targetSeconds"] for e in arm["renderMetadata"]["timingReview"]["events"]]
        assert targets == ([None] * 3 if name == "chronological-control" else [2, 4, 6])
    assert arms["exact-landmarks"]["exactOutput"] == arms["approximate-landmarks"]["exactOutput"].replace("Around ", "At ")


def test_manual_packs_preserve_exact_prompts_and_review_metadata(tmp_path):
    # Fixture inputs are read from the repository; generated archives stay local.
    import shutil
    shutil.copytree(ROOT / "temporal-directing", tmp_path / "temporal-directing")
    result = prepare_renderer_gate(tmp_path)
    report = write_renderer_gate(tmp_path, result)
    saved = json.loads((report.parent / "results.json").read_text(encoding="utf-8"))
    assert saved == result
    assert len(list(report.parent.glob("*.manual-render.zip"))) == 3
    for name, arm in saved["cases"][0]["variants"].items():
        with zipfile.ZipFile(report.parent / arm["manualRenderBundle"]) as archive:
            assert set(archive.namelist()) == {"prompt.txt", "manual-render-manifest.json", "review.md"}
            assert archive.read("prompt.txt") == arm["exactOutput"].encode("utf-8")
            manifest = json.loads(archive.read("manual-render-manifest.json"))
            assert manifest["references"] == []
            assert manifest["h3Mode"] == "T2VA" and manifest["treatmentId"] == name
            assert (manifest["durationSeconds"], manifest["shotCount"], manifest["aspectRatio"]) == (8, 1, "16:9")
            assert all(value is None for value in manifest["rendererSettings"].values())
            assert manifest["timingReview"] == arm["renderMetadata"]["timingReview"]
            assert all(e["observedOnsetSeconds"] is None and e["observedCompletionSeconds"] is None
                       for e in manifest["timingReview"]["events"])
            review = archive.read("review.md").decode()
            assert "accidental cuts" in review and "teleporting" in review and "pose snapping" in review
