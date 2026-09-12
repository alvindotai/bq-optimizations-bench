"""Job records in, per-comparison summary out.

Separate from the CLI so `analyze` and `results-doc` report the same structure.

Three properties matter more than the timings:

  plans_identical   do the variants compile to the same stage sequence?
  work_identical    do they read and write the same number of records?
  plans_stable      does each variant produce ONE stage sequence across its own
                    repetitions? Where this is false, BigQuery's runtime
                    adaptivity varied the plan run to run, and no plan-identity
                    claim should be made for that comparison.
"""
import json
import statistics
from collections import OrderedDict, defaultdict

from .stats import mann_whitney_u


def load(paths):
    """Group job records by myth, then by variant."""
    grouped = defaultdict(lambda: defaultdict(list))
    for path in paths:
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    record = json.loads(line)
                    grouped[record["myth"]][record["variant"]].append(record)
    return grouped


def summarise(grouped, meta):
    """{myth: {claim, plans_identical, ..., variants: {name: stats}}}."""
    ordered = ([k for k in meta if k in grouped]
               + [k for k in grouped if k not in meta])
    summary = OrderedDict()
    for key in ordered:
        block = _compare(grouped[key], meta.get(key, {}))
        if block:
            summary[key] = block
    return summary


def _compare(variants, info):
    names = [n for n, _ in info.get("variants", [])] or list(variants)
    names = [n for n in names if n in variants]
    if not names:
        return None

    baseline_slots = [r["slot_ms"] for r in variants[names[0]]]
    baseline_median = statistics.median(baseline_slots)
    plans = {n: variants[n][0]["plan_steps"] for n in names}
    stable = {n: len({tuple(r["plan_steps"]) for r in variants[n]}) == 1
              for n in names}

    rows = OrderedDict(
        (n, _variant(variants[n], plans[n], stable[n], baseline_median,
                     None if n == names[0] else baseline_slots))
        for n in names)

    return {
        "claim": info.get("claim", ""),
        "note": info.get("note", ""),
        "plans_identical": len({tuple(p) for p in plans.values()}) == 1,
        "work_identical": len({(r["records_read"], r["records_written"])
                               for r in rows.values()}) == 1,
        "bytes_identical": len({str(r["bytes_billed"]) for r in rows.values()}) == 1,
        "plans_stable": all(stable.values()),
        "variants": rows,
    }


def _variant(runs, plan, plan_stable, baseline_median, baseline_slots):
    slots = sorted(r["slot_ms"] for r in runs)
    median = statistics.median(slots)
    read, written, shuffled = _work(runs)
    return {
        "n": len(runs),
        "slot_ms_median": median,
        "slot_ms_min": slots[0],
        "slot_ms_max": slots[-1],
        "bytes_processed": _one_or_all(r["bytes_processed"] for r in runs),
        "bytes_billed": _one_or_all(r["bytes_billed"] for r in runs),
        "records_read": read,
        "records_written": written,
        "shuffle_bytes": shuffled,
        "stages": len(plan),
        "plan_stable_across_reps": plan_stable,
        "ratio_vs_base": round(median / baseline_median, 3) if baseline_median else None,
        "p_vs_base": None if baseline_slots is None else round(
            mann_whitney_u(baseline_slots, slots) or 1.0, 4),
    }


def _work(runs):
    """Records read, records written and shuffle bytes across plan stages.

    Unlike slot time these do not move with slot availability, so they separate
    the query from the weather. Identical across repetitions by construction;
    the minimum is taken so a truncated plan cannot inflate the figure.
    """
    totals = {(sum(s[1] for s in plan), sum(s[2] for s in plan), sum(s[3] for s in plan))
              for plan in ((r.get("plan_records") or []) for r in runs)}
    return min(totals)


def _one_or_all(values):
    """The single value when a variant is constant, else every value seen."""
    distinct = sorted(set(values))
    return distinct[0] if len(distinct) == 1 else distinct


def format_bytes(value):
    if isinstance(value, list):
        return " / ".join(format_bytes(v) for v in value)
    return (f"{value/2**30:.2f} GiB" if value >= 2**30
            else f"{value/2**20:,.0f} MiB")


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
            print("  !! plan varied across repetitions"
                  " - no plan-identity claim for this row")
        print(f"  {'variant':24} {'n':>2} {'slot_ms med':>12} {'[min..max]':>21} "
              f"{'ratio':>7} {'p':>7} {'stg':>4} {'recs_read':>14} {'billed':>12}")
        for name, s in block["variants"].items():
            p = "" if s["p_vs_base"] is None else f"{s['p_vs_base']:.3f}"
            stages = "n/a" if s["stages"] == 0 else s["stages"]
            print(f"  {name:24} {s['n']:>2} {s['slot_ms_median']:>12,.0f} "
                  f"[{s['slot_ms_min']:>9,}..{s['slot_ms_max']:>9,}] "
                  f"{s['ratio_vs_base']:>7} {p:>7} {stages:>4} "
                  f"{s['records_read']:>14,} {format_bytes(s['bytes_billed']):>12}")
