"""Price a matrix by dry-run, before billing a single byte."""
from .. import client, matrix, pricing
from ..config import LOCATION, require_project

HELP = "dry-run a matrix and print what it would cost"


def add_arguments(p):
    p.add_argument("--matrix", default="all", choices=sorted(matrix.MATRICES))
    p.add_argument("--price", type=float, default=pricing.ON_DEMAND_USD_PER_TIB,
                   help=f"USD per TiB (default {pricing.ON_DEMAND_USD_PER_TIB})")


def main(args):
    project = require_project()
    total = 0.0
    unpriced = []

    for m in matrix.select(args.matrix):
        for name, sql in m["variants"]:
            if sql.lstrip().upper().startswith("CREATE TEMP"):
                # Multi-statement scripts cannot be dry-run as a unit. This one
                # reads the same source table as its CTE counterpart plus a
                # temp-table write, so it is bounded by that variant's cost.
                unpriced.append((m["key"], name, m["reps"]))
                continue
            try:
                gib = client.dry_run(sql, project, LOCATION)["bytes_processed"] / 2**30
            except Exception as exc:  # noqa: BLE001 - a table you cannot see should not stop the estimate
                print(f"{m['key']:30} {name:24} DRY-RUN FAILED: {str(exc)[:80]}")
                continue
            cost = gib * (args.price / 1024) * m["reps"]
            total += cost
            print(f"{m['key']:30} {name:24} {gib:9.2f} GiB  x{m['reps']:<3} = ${cost:7.3f}")

    for key, name, reps in unpriced:
        print(f"{key:30} {name:24} {'not dry-runnable (script)':>25}  x{reps}")

    print(f"\nESTIMATED TOTAL at ${args.price}/TiB: ${total:.2f}"
          f"{'  (excludes the script variants above)' if unpriced else ''}")
    print("Billing applies a 10 MB per-table minimum, so the real total is "
          "marginally higher.")
    return 0
