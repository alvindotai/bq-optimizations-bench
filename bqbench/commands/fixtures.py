"""Build the fixture tables for myths 6 and 7.

Five of the seven myths run entirely on `bigquery-public-data.stackoverflow`.
Two cannot: "denormalise for sub-second latency" needs a wide pre-joined table
to compare a star join against, and "cast string keys to INT64" needs the same
rows keyed both ways. Both derive from the same public data, so nothing here
depends on private data.
"""
import json
import string
import urllib.request

from .. import client, paths
from ..config import DATASET, LOCATION, require_project

HELP = "build the myth 6/7 fixture tables (~$0.01)"

TABLES = ("k_users_int", "k_users_str", "k_posts_int", "k_posts_str",
          "n_posts", "n_users", "n_badges", "denorm_wide")


def add_arguments(p):
    p.add_argument("--skip-sizes", action="store_true",
                   help="do not record fixture storage sizes afterwards")


def main(args):
    project = require_project()
    template = (paths.SQL / "fixtures.sql").read_text()
    sql = string.Template(template).substitute(PROJECT=project, DATASET=DATASET)

    statements = [s.strip() for s in sql.split(";")
                  if s.strip() and not _only_comments(s)]
    print(f"{len(statements)} statements -> {project}.{DATASET} ({LOCATION})")
    for i, stmt in enumerate(statements, 1):
        r = client.run(stmt, project, location=LOCATION,
                       labels={"bq_myth_bench": "fixture"})
        print(f"  [{i}/{len(statements)}] {_target(stmt):14} "
              f"billed={r['bytes_billed']/2**20:8.1f} MiB  slot={r['slot_ms']:>9,} ms")

    if not args.skip_sizes:
        _record_sizes(project)
    print("\nFixtures ready. Next: python3 -m bqbench run --matrix core")
    return 0


def _record_sizes(project):
    """Record each fixture's storage footprint; the economics command prices the
    denormalisation trade-off from this rather than from constants in code."""
    sizes = {"_comment": "Storage footprint of the tables built by "
                         "sql/fixtures.sql, read from tables.get."}
    for table in TABLES:
        url = (f"{client.API}/projects/{project}/datasets/{DATASET}/tables/{table}")
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + client.token())
        with urllib.request.urlopen(req, timeout=60) as resp:
            meta = json.load(resp)
        sizes[table] = {"rows": int(meta["numRows"]), "bytes": int(meta["numBytes"])}
    paths.ensure_results_dir()
    with open(paths.FIXTURE_SIZES, "w") as fh:
        json.dump(sizes, fh, indent=2)
    print(f"recorded fixture sizes -> {paths.FIXTURE_SIZES}")


def _only_comments(chunk):
    return all(not ln.strip() or ln.strip().startswith("--")
               for ln in chunk.splitlines())


def _target(stmt):
    for line in stmt.splitlines():
        if "CREATE OR REPLACE TABLE" in line:
            return line.split("`")[1].split(".")[-1]
    return "?"
