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
the record counts move with it: a query behind a LIMIT short-circuits at a
different point each run, and a plan that collapses to one stage reports fewer
per-stage reads for exactly the same work. Where a stability flag is false, the
corresponding identity claim is measuring noise and must not be made.
"""
import json
import math
import statistics
from collections import Counter, OrderedDict, defaultdict

from .stats import (
    stratified_median_ratio,
    stratified_median_ratio_ci,
    van_elteren,
)

#: Per-variant keys every consumer of a summary depends on. A summary written
#: by an older version will be missing some; say so instead of raising KeyError
#: three frames deep.
REQUIRED_VARIANT_KEYS = frozenset({
    "n", "slot_ms_median", "ratio_vs_base", "p_vs_base", "ratio_ci",
    "bytes_billed", "records_read", "stages",
    "elapsed_ms_median", "elapsed_ratio_vs_base", "strata",
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

    # The run is blocked by pass: a comparison measured twice on different days
    # carries a level shift common to both its variants. Compare within a pass
    # so that shift divides out instead of being read as a difference.
    passes = _passes(variants[names[0]])
    elapsed_passes = _passes(variants[names[0]], "elapsed_ms")
    plans = {n: _modal_plan(variants[n]) for n in names}
    plan_stable = {n: len({tuple(r["plan_steps"]) for r in variants[n]}) == 1
                   for n in names}
    work_stable = {n: len({_work_of(r) for r in variants[n]}) == 1 for n in names}

    rows = OrderedDict(
        (n, _variant(variants[n], plans[n],
                     plan_stable=plan_stable[n], work_stable=work_stable[n],
                     n_passes=len(passes),
                     strata=None if n == names[0] else _strata(
                         passes, _passes(variants[n])),
                     elapsed_strata=None if n == names[0] else _strata(
                         elapsed_passes, _passes(variants[n], "elapsed_ms"))))
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


def _modal_plan(runs):
    """The stage sequence this variant produced most often.

    Not the first repetition's. BigQuery varies the plan run to run, so any
    single draw is a lottery: on the published data, picking the first record
    rather than the modal one flips the plan-identity verdict in four of the
    nineteen comparisons. The mode is still only a summary of an unstable
    quantity - `plan_stable_across_reps` says whether it meant anything.
    """
    counts = Counter(tuple(r["plan_steps"]) for r in runs)
    return list(counts.most_common(1)[0][0])


def _passes(runs, field="slot_ms"):
    """One metric grouped by the pass that produced it, in a stable order."""
    by_pass = OrderedDict()
    for r in sorted(runs, key=lambda x: (x.get("run_id", ""), x.get("rep", 0))):
        by_pass.setdefault(r.get("run_id", ""), []).append(r[field])
    return by_pass


def _strata(baseline_passes, other_passes):
    """[(baseline, other), ...] for every pass that measured both variants.

    A pass holding only one of the two cannot say anything about their ratio,
    so it is dropped rather than pooled in. On this data no such pass exists -
    every comparison is balanced - and the check is here so that a future
    partial re-run degrades honestly instead of silently.
    """
    return [(baseline_passes[p], other_passes[p])
            for p in baseline_passes if p in other_passes]


def _variant(runs, plan, *, plan_stable, work_stable,
             strata, elapsed_strata, n_passes):
    slots = sorted(r["slot_ms"] for r in runs)
    median = statistics.median(slots)
    elapsed = statistics.median(r["elapsed_ms"] for r in runs)
    read, written, shuffled = _work(runs)
    return {
        "n": len(runs),
        "slot_ms_median": median,
        "slot_ms_min": slots[0],
        "slot_ms_max": slots[-1],
        # Wall clock. Slot-ms is aggregate slot consumption across parallel
        # workers, which is what capacity pricing bills and what a "slower"
        # claim is usually NOT about. These two disagree often enough that
        # publishing only the first would misdescribe several results.
        "elapsed_ms_median": elapsed,
        # Wall clock is blocked by pass for the same reason slot time is: a
        # busy pass lengthens both variants alike.
        "elapsed_ratio_vs_base": (1.0 if elapsed_strata is None
                                  else _round(stratified_median_ratio(elapsed_strata))),
        "bytes_processed": _one_or_all(r["bytes_processed"] for r in runs),
        "bytes_billed": _one_or_all(r["bytes_billed"] for r in runs),
        "records_read": read,
        "records_written": written,
        "shuffle_bytes": shuffled,
        "stages": len(plan),
        "plan_stable_across_reps": plan_stable,
        "work_stable_across_reps": work_stable,
        "ratio_vs_base": (1.0 if strata is None
                          else _round(stratified_median_ratio(strata))),
        "p_vs_base": None if strata is None else round(van_elteren(strata) or 1.0, 4),
        # The baseline has no ratio, but it sat in the same passes, so the
        # column reads the same down the whole comparison.
        "strata": len(strata) if strata is not None else n_passes,
        # How large a difference could have hidden here? The p-value does not
        # say, and at n = 6..18 the answer is often "quite a lot".
        "ratio_ci": _ci(strata),
    }


def _ratio(value, baseline):
    """Three decimal places, or four significant figures, whichever keeps more.

    `LIMIT` collapses slot time by three orders of magnitude; at 3 dp that ratio
    prints as 0.0, which tells the reader nothing. Three significant figures is
    not enough either - it rounds to 0.000297, which inverts to 3,367x rather
    than the 3,362x the prose quotes, and a reader who checks will find the
    table and the text disagreeing. Ratios near 1 keep their three decimals.
    """
    if not baseline:
        return None
    ratio = value / baseline
    if not ratio:
        return 0.0
    return round(ratio, max(3, 4 - math.ceil(math.log10(abs(ratio)))))


def _round(ratio):
    """A stratified ratio, at the same precision as a pooled one."""
    return None if ratio is None else _ratio(ratio, 1.0)


def _ci(strata):
    if strata is None:
        return None
    low, high = stratified_median_ratio_ci(strata)
    return None if low is None else [_round(low), _round(high)]


def _work_of(record):
    """(records read, records written) summed over plan stages.

    Shuffle bytes are deliberately excluded. `work_identical` compares reads and
    writes, so including shuffle here made `work_stable` strictly harder to
    satisfy than the verdict it gates - a comparison could be marked unstable
    over shuffle jitter alone and lose a record-identity claim its record counts
    supported. Shuffle is still recorded per stage in `plan_records`.
    """
    plan = record.get("plan_records") or []
    return (sum(s[1] for s in plan), sum(s[2] for s in plan))


def _work(runs):
    """Median work across repetitions.

    These do not move with slot availability, which is what makes them better
    evidence than timing - but they are not constant either. A query that
    short-circuits reads a different number of records each run, so the median
    is taken for the same reason it is taken for slot time, and
    `work_stable_across_reps` says whether it was needed.
    """
    per_run = [(*_work_of(r), sum(s[3] for s in (r.get("plan_records") or [])))
               for r in runs]
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
