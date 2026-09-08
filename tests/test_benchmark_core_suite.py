from pathlib import Path

from miniconstruct.benchmark.runner import load_suite


ROOT = Path(__file__).resolve().parents[1] / "benchmarks"


def test_core_v1_suite_has_the_stable_nine_case_order():
    assert load_suite(ROOT, "core").cases == [
        "ref2va-height-comparison",
        "ref2va-reference-sheet-identity",
        "ref2va-offscreen-retention",
        "ref2va-transient-expression-identity",
        "ref2va-wardrobe-authority",
        "t2va-laterality-baseline",
        "ref2va-creative-inference-baseline",
        "ref2va-continuation-state",
        "ref2va-camera-multishot-controls",
    ]
