"""The denormalisation break-even, and what the benchmark itself cost."""
from .. import analysis, paths, pricing

HELP = "denormalisation break-even and total benchmark cost"

_ANALYZE = "python3 -m bqbench run --matrix all && python3 -m bqbench analyze"
RULE = "=" * 74


def add_arguments(p):
    p.add_argument("--price", type=float, default=pricing.ON_DEMAND_USD_PER_TIB,
                   help=f"USD per TiB (default {pricing.ON_DEMAND_USD_PER_TIB})")


def main(args):
    summary = analysis.load_summary(paths.require(paths.SUMMARY, _ANALYZE))
    rebuilds = paths.read_json(paths.REBUILD, "python3 -m bqbench rebuild")
    sizes = paths.read_json(paths.FIXTURE_SIZES, "python3 -m bqbench fixtures")

    b = pricing.denormalisation_breakeven(summary, rebuilds, sizes, args.price)
    print(RULE)
    print("MYTH 6 - denormalisation: the arithmetic the advice leaves out")
    print(RULE)
    print(f"  per-query saving   {b['saved_slot_ms']:>12,.0f} slot_ms   "
          f"{b['saved_bytes']/2**20:>9.1f} MiB billed")
    print(f"  one rebuild costs  {b['rebuild_slot_ms']:>12,.0f} slot_ms   "
          f"{b['rebuild_bytes']/2**20:>9.1f} MiB billed   "
          f"(median of {b['rebuilds']})")
    print()
    print(f"  BREAK-EVEN, slot time  {b['breakeven_slot']:>6.1f} queries per rebuild")
    print(f"  BREAK-EVEN, on-demand  {b['breakeven_bytes']:>6.1f} queries per rebuild")
    print()
    print(f"  wide table         {b['wide_bytes']/2**30:>7.3f} GiB")
    print(f"  3 normalised       {b['normalised_bytes']/2**30:>7.3f} GiB   "
          f"-> denormalising costs {b['storage_overhead_pct']:+.1f}% storage")
    print(f"  extra storage      ${b['storage_usd_month']:>7.3f}/month")
    print(f"  nightly rebuild    ${b['nightly_usd_month']:>7.3f}/month   "
          f"({b['nightly_vs_storage']:.0f}x the storage)")
    print()
    print("  Columnar compression absorbs nearly all the duplication, so the cost")
    print("  everyone warns about is noise. The binding constraint is refresh")
    print("  frequency: below the break-even above, the rebuild costs more than")
    print("  the reads save.")

    total = pricing.total_cost(sorted(paths.RESULTS.glob("*.jsonl")),
                               rebuilds, args.price)
    print()
    print(RULE)
    print("WHAT THIS BENCHMARK COST")
    print(RULE)
    print(f"  jobs            {total['jobs']:>8,}")
    print(f"  total billed    {total['bytes']/2**40:>8.3f} TiB")
    print(f"  on-demand cost  ${total['usd']:>7.2f}")
    return 0
