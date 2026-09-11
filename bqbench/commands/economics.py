"""The denormalisation break-even, and what the benchmark itself cost.

Myth 6 is the one claim that is true on read cost and still mispriced, because
the advice quotes neither side of the trade: not the storage (which turns out to
be nearly free) nor the rebuild (which is the binding constraint).
"""
import json
import statistics

from .. import paths

HELP = "denormalisation break-even and total benchmark cost"

ON_DEMAND_USD_PER_TIB = 6.25          # US multi-region list price
STORAGE_USD_PER_GIB_MONTH = 0.020     # US multi-region active logical storage
NORMALISED = ("n_posts", "n_users", "n_badges")

_RUN = "python3 -m bqbench run --matrix all && python3 -m bqbench analyze"


def add_arguments(p):
    p.add_argument("--price", type=float, default=ON_DEMAND_USD_PER_TIB)


def main(args):
    summary = json.load(open(paths.require(paths.SUMMARY, _RUN)))
    rebuilds = json.load(open(paths.require(paths.REBUILD,
                                            "python3 -m bqbench rebuild")))
    sizes = json.load(open(paths.require(paths.FIXTURE_SIZES,
                                         "python3 -m bqbench fixtures")))

    m6 = summary["m6_denormalisation"]["variants"]
    star, flat = m6["star_join_3_tables"], m6["denormalised_1_table"]

    rebuild_slot = statistics.median(r["slot_ms"] for r in rebuilds)
    rebuild_bytes = statistics.median(r["bytes_billed"] for r in rebuilds)
    saved_slot = star["slot_ms_median"] - flat["slot_ms_median"]
    saved_bytes = star["bytes_billed"] - flat["bytes_billed"]

    wide = sizes["denorm_wide"]["bytes"]
    parts = sum(sizes[t]["bytes"] for t in NORMALISED)
    storage_mo = wide / 2**30 * STORAGE_USD_PER_GIB_MONTH
    rebuild_mo = rebuild_bytes / 2**40 * args.price * 30

    rule = "=" * 74
    print(rule)
    print("MYTH 6 - denormalisation: the arithmetic the advice leaves out")
    print(rule)
    print(f"  per-query saving   {saved_slot:>12,.0f} slot_ms   "
          f"{saved_bytes/2**20:>9.1f} MiB billed")
    print(f"  one rebuild costs  {rebuild_slot:>12,.0f} slot_ms   "
          f"{rebuild_bytes/2**20:>9.1f} MiB billed   (median of {len(rebuilds)})")
    print()
    print(f"  BREAK-EVEN, slot time  {rebuild_slot/saved_slot:>6.1f} queries per rebuild")
    print(f"  BREAK-EVEN, on-demand  {rebuild_bytes/saved_bytes:>6.1f} queries per rebuild")
    print()
    print(f"  wide table         {wide/2**30:>7.3f} GiB")
    print(f"  3 normalised       {parts/2**30:>7.3f} GiB   "
          f"-> denormalising costs {100*(wide-parts)/parts:+.1f}% storage")
    print(f"  extra storage      ${storage_mo:>7.3f}/month")
    print(f"  nightly rebuild    ${rebuild_mo:>7.3f}/month   "
          f"({rebuild_mo/storage_mo:.0f}x the storage)")
    print()
    print("  Columnar compression absorbs nearly all the duplication, so the cost")
    print("  everyone warns about is noise. The binding constraint is refresh")
    print("  frequency: below the break-even above, the rebuild costs more than")
    print("  the reads save.")

    print()
    print(rule)
    print("WHAT THIS BENCHMARK COST")
    print(rule)
    billed = n = 0
    for path in sorted(paths.RESULTS.glob("*.jsonl")):
        with open(path) as fh:
            for line in fh:
                if line.strip():
                    billed += json.loads(line)["bytes_billed"]
                    n += 1
    billed += sum(r["bytes_billed"] for r in rebuilds)
    n += len(rebuilds)
    print(f"  jobs            {n:>8,}")
    print(f"  total billed    {billed/2**40:>8.3f} TiB")
    print(f"  on-demand cost  ${billed/2**40*args.price:>7.2f}")
    return 0
