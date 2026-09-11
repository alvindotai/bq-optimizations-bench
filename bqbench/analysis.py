"""Turning raw job records into a comparison.

Kept separate from the CLI so the summary can be reused - `bqbench results-doc`
renders RESULTS.md from exactly the same structure the analyzer prints.

Three properties matter more than the timings and are computed per comparison:

  plans_identical   do the variants compile to the same stage sequence?
  work_identical    do they read and write the same number of records?
  plans_stable      does each variant produce ONE stage sequence across its own
                    repetitions? Where this is false, BigQuery runtime
                    adaptivity varied the plan run to run and no plan-identity
                    claim should be made for that comparison.
"""
import json
import statistics
from collections import OrderedDict, defaultdict

from .stats import mann_whitney_u, quantile


def load(paths):
    """Group job records by myth and variant."""
    by_myth = defaultdict(lambda: defaultdict(list))
    for path in paths:
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    r = json.loads(line)
                    by_myth[r["myth"]][r["variant"]].append(r)
    return by_myth


def _work(record):
    """Deterministic 'work done': records read, records written, shuffle bytes
    summed over plan stages. Unlike slot time these do not move with slot
    availability, so they isolate the query from the weather."""
    plan = record.get("plan_records") or []
    return (sum(s[1] for s in plan),
            sum(s[2] for s in plan),
            sum(s[3] for s in plan))


def summarise(by_myth, meta):
    out = OrderedDict()
    ordered = [k for k in meta if k in by_myth] + [k for k in by_myth if k not in meta]

    for key in ordered:
        variants = by_myth[key]
        info = meta.get(key, {})
        names = [n for n, _ in info.get("variants", [])] or list(variants)
        names = [n for n in names if n in variants]
        if not names:
            continue
        baseline = names[0]
        baseline_slots = [r["slot_ms"] for r in variants[baseline]]
        baseline_median = statistics.median(baseline_slots)

        plans = {n: variants[n][0]["plan_steps"] for n in names}
        stable = {n: len({tuple(r["plan_steps"]) for r in variants[n]}) == 1
                  for n in names}

        rows = OrderedDict()
        for name in names:
            runs = variants[name]
            slots = sorted(r["slot_ms"] for r in runs)
            elapsed = sorted(r["elapsed_ms"] for r in runs)
            processed = {r["bytes_processed"] for r in runs}
            billed = {r["bytes_billed"] for r in runs}
            work = sorted({_work(r) for r in runs})[0]
            median = statistics.median(slots)
            rows[name] = {
                "n": len(runs),
                "slot_ms_median": median,
                "slot_ms_min": slots[0],
                "slot_ms_max": slots[-1],
                "slot_ms_p25": quantile(slots, 0.25),
                "slot_ms_p75": quantile(slots, 0.75),
                "elapsed_ms_median": statistics.median(elapsed),
                "bytes_processed": _single(processed),
                "bytes_billed": _single(billed),
                "bytes_constant": len(processed) == 1,
                "records_read": work[0],
                "records_written": work[1],
                "shuffle_bytes": work[2],
                "stages": len(plans[name]),
                "plan_stable_across_reps": stable[name],
                "ratio_vs_base": round(median / baseline_median, 3) if baseline_median else None,
                "p_vs_base": None if name == baseline else round(
                    mann_whitney_u(baseline_slots, [r["slot_ms"] for r in runs]) or 1.0, 4),
            }

        out[key] = {
            "claim": info.get("claim", ""),
            "source": info.get("source", ""),
            "note": info.get("note", ""),
            "plans_identical": len({tuple(p) for p in plans.values()}) == 1,
            "work_identical": len({(r["records_read"], r["records_written"])
                                   for r in rows.values()}) == 1,
            "plans_stable": all(stable.values()),
            "bytes_identical": len({str(r["bytes_billed"]) for r in rows.values()}) == 1,
            "variants": rows,
        }
    return out


def _single(values):
    return sorted(values)[0] if len(values) == 1 else sorted(values)




def render(summary):
    for key, block in summary.items():
        print("=" * 118)
        print(key)
        print(f'  CLAIM : "{block["claim"]}"')
        print(f"  plans identical: {block['plans_identical']}"
              f" | records identical: {block['work_identical']}"
              f" | billed bytes identical: {block['bytes_identical']}"
              f" | plans stable across reps: {block['plans_stable']}")
        if block["note"]:
            print(f"  note  : {block['note']}")
        if not block["plans_stable"]:
            print("  !! plan varied across repetitions - no plan-identity claim for this row")
        print(f"  {'variant':24} {'n':>2} {'slot_ms med':>12} {'[min..max]':>21} "
              f"{'ratio':>7} {'p':>7} {'stg':>4} {'recs_read':>14} {'billed':>12}")
        for name, s in block["variants"].items():
            p = "" if s["p_vs_base"] is None else f"{s['p_vs_base']:.3f}"
            stages = "n/a" if s["stages"] == 0 else s["stages"]
            print(f"  {name:24} {s['n']:>2} {s['slot_ms_median']:>12,.0f} "
                  f"[{s['slot_ms_min']:>9,}..{s['slot_ms_max']:>9,}] "
                  f"{s['ratio_vs_base']:>7} {p:>7} {stages:>4} "
                  f"{s['records_read']:>14,} {format_bytes(s['bytes_billed']):>12}")


def format_bytes(b):
    if isinstance(b, list):
        return " / ".join(format_bytes(x) for x in b)
    return f"{b/2**30:.2f} GiB" if b >= 2**30 else f"{b/2**20:,.0f} MiB"
