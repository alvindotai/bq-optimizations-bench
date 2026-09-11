"""Where the benchmark runs.

Nothing here is specific to the original run: point BENCH_PROJECT at any GCP
project you can bill BigQuery jobs to, in a location that can read
`bigquery-public-data` (US, unless you have copied it elsewhere).

    export BENCH_PROJECT=my-gcp-project      # required to bill anything
    export BENCH_DATASET=bq_myth_bench       # optional, created by `bqbench fixtures`
    export BENCH_LOCATION=US                 # optional
    export BENCH_RESULTS=/path/to/results    # optional, defaults to ./results

Auth is whatever `gcloud auth print-access-token` returns, so run
`gcloud auth login` (or configure a service account) first.

Two levels of strictness, deliberately:

  sql_project()     renders SQL. Falls back to a placeholder so the matrix can
                    be inspected, diffed and verified with no credentials at
                    all - which is what makes `verify` and `check-leaks` free.
  require_project() bills BigQuery. Hard-fails if BENCH_PROJECT is unset.
"""
import os
import sys

#: Stands in for the billing project when BENCH_PROJECT is unset. The recorded
#: results were scrubbed to this same name, so offline verification matches.
PLACEHOLDER_PROJECT = "example-project"

PROJECT = os.environ.get("BENCH_PROJECT", "")
DATASET = os.environ.get("BENCH_DATASET", "bq_myth_bench")
LOCATION = os.environ.get("BENCH_LOCATION", "US")


def sql_project():
    """Project name for rendering SQL. Never raises."""
    return PROJECT or PLACEHOLDER_PROJECT


def require_project():
    """Project name for billing work. Raises if it was never set."""
    if not PROJECT:
        sys.exit("BENCH_PROJECT is not set - this command bills BigQuery.\n"
                 "    export BENCH_PROJECT=my-gcp-project")
    return PROJECT


def fixture(table):
    """Fully-qualified name of a fixture table (myths 6 and 7)."""
    return f"`{sql_project()}.{DATASET}.{table}`"
