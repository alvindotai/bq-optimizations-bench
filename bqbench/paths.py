"""Where things live on disk.

`results/` is generated, not committed - see README. Every command resolves its
paths through here so the layout is stated once.
"""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
SQL = ROOT / "sql"
RESULTS = pathlib.Path(__import__("os").environ.get("BENCH_RESULTS", ROOT / "results"))

ALL_RESULTS = RESULTS / "all_results.jsonl"
SUMMARY = RESULTS / "summary.json"
REBUILD = RESULTS / "rebuild.json"
FIXTURE_SIZES = RESULTS / "fixture_sizes.json"
RENDERED = RESULTS / "RESULTS_RAW.txt"


def ensure_results_dir():
    RESULTS.mkdir(parents=True, exist_ok=True)
    return RESULTS


def require(path, produced_by):
    """Fail with an instruction, not a traceback, when a run has not happened."""
    if not path.exists():
        raise SystemExit(
            f"missing {path}\n"
            f"  results/ is not committed - generate it with:\n"
            f"      {produced_by}")
    return path
