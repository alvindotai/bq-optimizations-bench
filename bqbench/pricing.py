"""BigQuery list prices, and the one calculation that uses them.

The denormalisation break-even is reported by two commands. It lives here so
they cannot disagree.
"""
import json
import statistics

ON_DEMAND_USD_PER_TIB = 6.25          # US multi-region
STORAGE_USD_PER_GIB_MONTH = 0.020     # US multi-region, active logical

#: The tables a star join reads, against which `denorm_wide` is priced.
NORMALISED_TABLES = ("n_posts", "n_users", "n_badges")


def _billed(variant):
    """Billed bytes for a variant, as one number.

    `analysis._one_or_all` hands back a list when a variant's billed bytes moved
    between repetitions. Arithmetic on that would raise three frames down, so
    take the median and let the caller's own stability reporting carry the
    caveat.
    """
    value = variant["bytes_billed"]
    return statistics.median(value) if isinstance(value, list) else value


def denormalisation_breakeven(summary, rebuilds, sizes,
                              usd_per_tib=ON_DEMAND_USD_PER_TIB):
    """How many queries a rebuild has to serve before denormalising pays.

    "Denormalise for sub-second latency" is sound on read cost and silent on
    both sides of the trade: the storage (nearly free, because columnar
    compression absorbs the duplication) and the refresh (which is what
    actually binds).
    """
    variants = summary["m6_denormalisation"]["variants"]
    star = variants["star_join_3_tables"]
    flat = variants["denormalised_1_table"]

    rebuild_slot = statistics.median(r["slot_ms"] for r in rebuilds)
    rebuild_bytes = statistics.median(r["bytes_billed"] for r in rebuilds)
    saved_slot = star["slot_ms_median"] - flat["slot_ms_median"]
    saved_bytes = _billed(star) - _billed(flat)

    wide = sizes["denorm_wide"]["bytes"]
    normalised = sum(sizes[t]["bytes"] for t in NORMALISED_TABLES)
    # Two different questions, and the advice's storage warning only answers the
    # first. REPLACING the three tables with the wide one costs the duplication
    # (`storage_overhead_pct`). But you cannot replace them: the rebuild reads
    # them, so they are retained, and what you actually add is the whole wide
    # table (`storage_usd_month`).
    storage_usd_month = wide / 2**30 * STORAGE_USD_PER_GIB_MONTH
    nightly_usd_month = rebuild_bytes / 2**40 * usd_per_tib * 30

    return {
        "rebuilds": len(rebuilds),
        "saved_slot_ms": saved_slot,
        "saved_bytes": saved_bytes,
        "rebuild_slot_ms": rebuild_slot,
        "rebuild_bytes": rebuild_bytes,
        "breakeven_slot": rebuild_slot / saved_slot,
        "breakeven_bytes": rebuild_bytes / saved_bytes,
        "wide_bytes": wide,
        "normalised_bytes": normalised,
        "storage_overhead_pct": 100 * (wide - normalised) / normalised,
        "storage_usd_month": storage_usd_month,
        "nightly_usd_month": nightly_usd_month,
        "nightly_vs_storage": nightly_usd_month / storage_usd_month,
    }


def total_cost(results_files, rebuilds, usd_per_tib=ON_DEMAND_USD_PER_TIB):
    """Jobs, bytes and dollars for a whole run."""
    billed = jobs = 0
    for path in results_files:
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    billed += json.loads(line)["bytes_billed"]
                    jobs += 1
    billed += sum(r["bytes_billed"] for r in rebuilds)
    jobs += len(rebuilds)
    return {"jobs": jobs, "bytes": billed, "usd": billed / 2**40 * usd_per_tib}
