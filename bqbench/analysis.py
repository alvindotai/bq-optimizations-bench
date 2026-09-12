"""Job records in, per-comparison summary out.

Separate from the CLI so `analyze` and `results-doc` report the same structure.

Three properties matter more than the timings:

  plans_identical   do the variants compile to the same stage sequence?
  work_identical    do they read and write the same number of records?
  plans_stable      does each variant produce ONE stage sequence across its own
                    repetitions?
  work_stable       does each variant read and write the SAME counts across its
                    own repetitions?

The two stability flags exist because neither property can be assumed. BigQuery
varies the plan run to run (dynamic repartitioning, single-stage collapse), and
a query that short-circuits - anything with a LIMIT - reads a different number
of records each time. Where a stability flag is false, the corresponding
identity claim is measuring noise and must not be made.
"""
import json
import statistics
from collections import OrderedDict, defaultdict

from .stats import mann_whitney_u, median_ratio_ci

#: Per-variant keys every consumer of a summary depends on. A summary written
#: by an older version will be missing some; say so instead of raising KeyError
#: three frames deep.
REQUIRED_VARIANT_KEYS = frozenset({
    "n", "slot_ms_median", "ratio_vs_base", "p_vs_base", "ratio_ci",
    "bytes_billed", "records_read", "stages",
})
REQUIRED_BLOCK_KEYS = frozenset({
    "claim", "plans_identical", "work_identical", "bytes_identical",
    "plans_stable", "work_stable", "variants",
})


def load_summary(path, regenerate_with="python3 -m bqbench analyze"):
    """Read a summary.json, refusing one this version cannot read.

    A summary is derived, not source: it can always be rebuilt from the job
    records beside it, which is what the error says to do.
    """
    with open(path) as fh:
        summary = json.load(fh)
    for key, block in summary.items():
        missing = (REQUIRED_BLOCK_KEYS - set(block)) | (
            REQUIRED_VARIANT_KEYS - set(next(iter(block.get("variants", {}).values()), {})))
        if missing:
            raise SystemExit(
                f"{path} was written by an older version of bqbench\n"
                f"  (comparison {key!r} is missing {sorted(missing)})\n"
                f"  Regenerate it with:\n"
                f"      {regenerate_with}")
    return summary


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
    plan_stable = {n: len({tuple(r["plan_steps"]) for r in variants[n]}) == 1
                   for n in names}
    work_stable = {n: len({_work_of(r) for r in variants[n]}) == 1 for n in names}

    rows = OrderedDict(
        (n, _variant(variants[n], plans[n],
                     plan_stable=plan_stable[n], work_stable=work_stable[n],
                     baseline_median=baseline_median,
                     baseline_slots=None if n == names[0] else baseline_slots))
        for n in names)

    return {
        "claim": info.get("claim", ""),
        "note": info.get("note", ""),
        "plans_identical": len({tuple(p) for p in plans.values()}) == 1,
        "work_identical": len({(r["records_read"], r["records_written"])
                               for r in rows.values()}) == 1,
        "bytes_identical": len({str(r["bytes_billed"]) for r in rows.values()}) == 1,
        "plans_stable": all(plan_stable.values()),
        "work_stable": all(work_stable.values()),
        "variants": rows,
    }


def _variant(runs, plan, *, plan_stable, work_stable,
             baseline_median, baseline_slots):
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
        "work_stable_across_reps": work_stable,
        "ratio_vs_base": round(median / baseline_median, 3) if baseline_median else None,
        "p_vs_base": None if baseline_slots is None else round(
            mann_whitney_u(baseline_slots, slots) or 1.0, 4),
        # How large a difference could have hidden here? The p-value does not
        # say, and at n = 6..18 the answer is often "quite a lot".
        "ratio_ci": _ci(baseline_slots, slots),
    }


def _ci(baseline_slots, slots):
    if baseline_slots is None:
        return None
    low, high = median_ratio_ci(baseline_slots, slots)
    return None if low is None else [round(low, 3), round(high, 3)]


def _work_of(record):
    """(records read, records written, shuffle bytes) summed over plan stages."""
    plan = record.get("plan_records") or []
    return (sum(s[1] for s in plan), sum(s[2] for s in plan),
            sum(s[3] for s in plan))


def _work(runs):
    """Median work across repetitions.

    These do not move with slot availability, which is what makes them better
    evidence than timing - but they are not constant either. A query that
    short-circuits reads a different number of records each run, so the median
    is taken for the same reason it is taken for slot time, and
    `work_stable_across_reps` says whether it was needed.
    """
    per_run = [_work_of(r) for r in runs]
    return tuple(round(statistics.median(v[i] for v in per_run)) for i in range(3))


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
        for what, ok in (("plan", block["plans_stable"]),
                         ("record counts", block["work_stable"])):
            if not ok:
                print(f"  !! {what} varied across repetitions"
                      f" - no identity claim for this row")
        print(f"  {'variant':24} {'n':>2} {'slot_ms med':>12} {'ratio':>7} "
              f"{'95% CI':>15} {'p':>7} {'stg':>4} {'recs_read':>14} {'billed':>12}")
        for name, s in block["variants"].items():
            p = "" if s["p_vs_base"] is None else f"{s['p_vs_base']:.3f}"
            stages = "n/a" if s["stages"] == 0 else s["stages"]
            ci = ("" if not s["ratio_ci"]
                  else f"[{s['ratio_ci'][0]:.2f}, {s['ratio_ci'][1]:.2f}]")
            print(f"  {name:24} {s['n']:>2} {s['slot_ms_median']:>12,.0f} "
                  f"{s['ratio_vs_base']:>7} {ci:>15} {p:>7} {stages:>4} "
                  f"{s['records_read']:>14,} {format_bytes(s['bytes_billed']):>12}")
