from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .runner import BenchmarkRunner, benchmark_root, compare_runs, list_definitions, load_profiles, load_suite, write_run


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MiniConstruct behavioural benchmarks")
    parser.add_argument("--root", type=Path, default=None, help="Benchmark directory (default: ./benchmarks)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List benchmark cases and suites")
    run = subparsers.add_parser("run", help="Run a suite or one case")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--suite")
    target.add_argument("--case")
    run.add_argument("--profile", required=True)
    run.add_argument("--profiles", type=Path, default=None, help="Profile JSON (default: models.local.json)")
    run.add_argument(
        "--source-root", type=Path, default=None,
        help="External benchmark asset source root (or set MINICONSTRUCT_BENCHMARK_SOURCE_ROOT)",
    )
    compare = subparsers.add_parser("compare", help="Compare two completed run directories")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    args = parser.parse_args()
    root = benchmark_root(args.root)
    if args.command == "list":
        definitions = list_definitions(root)
        print("Suites:")
        print(*[f"  {item}" for item in definitions["suites"]], sep="\n")
        print("Cases:")
        print(*[f"  {item}" for item in definitions["cases"]], sep="\n")
        return
    if args.command == "compare":
        print(compare_runs(args.left, args.right), end="")
        return
    profile_path = args.profiles or root / "models.local.json"
    profiles = load_profiles(profile_path)
    if args.profile not in profiles:
        parser.error(f"Profile {args.profile!r} not found in {profile_path}")
    suite_id = args.suite
    case_ids = load_suite(root, suite_id).cases if suite_id else [args.case]
    result = asyncio.run(BenchmarkRunner(root, source_root=args.source_root).run(profiles[args.profile], case_ids, suite_id))
    print(write_run(root, result))


if __name__ == "__main__":
    main()
