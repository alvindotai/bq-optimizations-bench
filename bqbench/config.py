"""Where the benchmark runs. Everything comes from the environment.

    BENCH_PROJECT    GCP project billed for the jobs (required to bill anything)
    BENCH_DATASET    dataset for the myth 6/7 fixtures  (default bq_myth_bench)
    BENCH_LOCATION   must colocate with bigquery-public-data (default US)
    BENCH_RESULTS    where a run writes                 (default ./results)

Auth is whatever `gcloud auth print-access-token` returns.
"""
import os
import re
import sys

#: Stands in for the billing project when BENCH_PROJECT is unset, so the matrix
#: can be rendered and verified with no credentials. The published records were
#: scrubbed to this same name, so offline verification matches.
PLACEHOLDER_PROJECT = "example-project"

_PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,127}$", re.IGNORECASE)
_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_LOCATION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


def _validated(name, value, pattern):
    if value and not pattern.match(value):
        sys.exit(f"invalid {name}: {value!r}")
    return value


PROJECT = _validated("BENCH_PROJECT", os.environ.get("BENCH_PROJECT", ""), _PROJECT_RE)
DATASET = _validated("BENCH_DATASET", os.environ.get("BENCH_DATASET", "bq_myth_bench"), _DATASET_RE)
LOCATION = _validated("BENCH_LOCATION", os.environ.get("BENCH_LOCATION", "US"), _LOCATION_RE)


def sql_project():
    """Project name for rendering SQL. Never raises."""
    return PROJECT or PLACEHOLDER_PROJECT


def require_project():
    """Project name for work that costs money. Raises if it was never set."""
    if not PROJECT:
        sys.exit("BENCH_PROJECT is not set - this command bills BigQuery.\n"
                 "    export BENCH_PROJECT=my-gcp-project")
    return _validated("BENCH_PROJECT", PROJECT, _PROJECT_RE)


def fixture(table):
    """Fully-qualified name of a fixture table (myths 6 and 7)."""
    _validated("table", table, _DATASET_RE)
    return f"`{sql_project()}.{DATASET}.{table}`"

