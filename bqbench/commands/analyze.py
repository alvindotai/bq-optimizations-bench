"""Summarise a run: medians, spread, plan identity, plan stability, p-values."""
import json

from .. import analysis, matrix, paths

HELP = "summarise job records into per-comparison tables"


def add_arguments(p):
    p.add_argument("results", nargs="*",
                   help="JSONL files (default: every results/*.jsonl)")
    p.add_argument("--matrix", default="all", choices=sorted(matrix.MATRICES))
    p.add_argument("--json", dest="json_out", default=None,
                   help="where to write the machine-readable summary "
                        "(default: results/summary.json)")


def main(args):
    files = args.results or sorted(str(p) for p in paths.RESULTS.glob("*.jsonl"))
    if not files:
        raise SystemExit(
            f"no JSONL files in {paths.RESULTS}\n"
            "  results/ is not committed - generate it with:\n"
            "      python3 -m bqbench run --matrix all")

    records = analysis.load(files)
    meta = {m["key"]: m for m in matrix.select(args.matrix)}
    summary = analysis.summarise(records, meta)
    analysis.render(summary)

    out = args.json_out or str(paths.ensure_results_dir() / "summary.json")
    with open(out, "w") as fh:
        json.dump(summary, fh, indent=2, default=str)
    print(f"\nread {len(files)} file(s); wrote {out}")
    return 0
