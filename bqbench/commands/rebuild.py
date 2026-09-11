"""Measure what it costs to REBUILD the denormalised table.

"Denormalise for sub-second latency" is sound on read cost. The number the
advice never includes is the refresh: every rebuild pays for the join you were
trying to avoid.
"""
import json
import statistics

from .. import client, paths
from ..config import LOCATION, fixture, require_project

HELP = "measure myth 6's refresh cost"
SCRATCH = "denorm_wide_rebuild"


def add_arguments(p):
    p.add_argument("--reps", type=int, default=5)
    p.add_argument("--out", default=None,
                   help="default: results/rebuild.json")
    p.add_argument("--keep", action="store_true",
                   help="keep the scratch table instead of dropping it")


def main(args):
    project = require_project()
    sql = f"""CREATE OR REPLACE TABLE {fixture(SCRATCH)} AS
SELECT p.id, p.owner_user_id, p.tags, p.view_count, p.score, p.creation_date,
       u.display_name, u.reputation, u.location,
       IFNULL(b.n_badges, 0) AS n_badges
FROM {fixture('n_posts')} p
JOIN {fixture('n_users')} u ON p.owner_user_id = u.id
LEFT JOIN {fixture('n_badges')} b ON b.user_id = u.id"""

    runs = []
    try:
        for i in range(args.reps):
            r = client.run(sql, project, location=LOCATION,
                           labels={"bq_myth_bench": "rebuild"})
            runs.append(r)
            print(f"rebuild {i+1}/{args.reps}: slot={r['slot_ms']:>9,} ms  "
                  f"billed={r['bytes_billed']/2**30:.3f} GiB  "
                  f"elapsed={r['elapsed_ms']:,} ms", flush=True)
    finally:
        if runs and not args.keep:
            client.run(f"DROP TABLE IF EXISTS {fixture(SCRATCH)}",
                       project, location=LOCATION)
            print(f"dropped scratch table {SCRATCH}")

    if not runs:
        return 1
    print(f"\nmedian slot_ms : {statistics.median(r['slot_ms'] for r in runs):,.0f}")
    print(f"median billed  : "
          f"{statistics.median(r['bytes_billed'] for r in runs)/2**30:.3f} GiB")
    out = args.out or str(paths.ensure_results_dir() / "rebuild.json")
    with open(out, "w") as fh:
        json.dump([{k: v for k, v in r.items() if k != "plan_records"} for r in runs],
                  fh, indent=2)
    print(f"wrote {out}")
    return 0
