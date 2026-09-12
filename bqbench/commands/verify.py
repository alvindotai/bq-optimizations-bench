"""Check the benchmark against itself.

Two different objections, two different checks:

  semantics        are the compared queries what the comparison claims? Needs
                   credentials and costs about $0.05.
  reproducibility  does the matrix still generate the committed results,
                   byte-for-byte, and do the documented run conditions hold in
                   the data? Free, no credentials.
"""
import json
import statistics
import urllib.request
from collections import Counter, defaultdict

from .. import client, matrix, paths
from ..config import LOCATION, require_project

HELP = "verify query semantics, or that the code still matches the results"

Q, U, B = matrix.Q, matrix.U, matrix.B

SEMANTIC_CHECKS = [
    ("join types return DIFFERENT results (myth 2 is a real comparison)", f"""
     SELECT
       (SELECT COUNT(*) FROM {B} b INNER      JOIN {U} u ON b.user_id=u.id) AS inner_rows,
       (SELECT COUNT(*) FROM {B} b LEFT       JOIN {U} u ON b.user_id=u.id) AS left_rows,
       (SELECT COUNT(*) FROM {B} b RIGHT      JOIN {U} u ON b.user_id=u.id) AS right_rows,
       (SELECT COUNT(*) FROM {B} b FULL OUTER JOIN {U} u ON b.user_id=u.id) AS full_rows
     """),
    ("filter order returns the SAME answer (myth 1 is semantics-preserving)", f"""
     SELECT
       (SELECT COUNT(*) FROM {Q} WHERE tags LIKE '%google-bigquery%' AND score > 0
          AND view_count > 100 AND answer_count >= 1) AS selective_first,
       (SELECT COUNT(*) FROM {Q} WHERE answer_count >= 1 AND view_count > 100
          AND score > 0 AND tags LIKE '%google-bigquery%') AS selective_last
     """),
    ("DISTINCT and GROUP BY return the SAME answer (myth 3)", f"""
     SELECT
       (SELECT COUNT(*) FROM (SELECT DISTINCT tags FROM {Q})) AS via_distinct,
       (SELECT COUNT(*) FROM (SELECT tags FROM {Q} GROUP BY tags)) AS via_group_by
     """),
    ("CTE and subquery return the SAME answer (myth 4)", f"""
     WITH heavy AS (SELECT owner_user_id, COUNT(*) AS c FROM {Q}
                    WHERE owner_user_id IS NOT NULL GROUP BY owner_user_id)
     SELECT (SELECT SUM(c) FROM heavy) AS via_cte,
            (SELECT SUM(c) FROM (SELECT owner_user_id, COUNT(*) AS c FROM {Q}
              WHERE owner_user_id IS NOT NULL GROUP BY owner_user_id)) AS via_subquery
     """),
    ("predicate selectivities myth 1 relies on", f"""
     SELECT COUNT(*) AS total,
            COUNTIF(tags LIKE '%google-bigquery%') AS most_eliminating,
            COUNTIF(score > 0) AS score_gt_0,
            COUNTIF(view_count > 100) AS views_gt_100,
            COUNTIF(answer_count >= 1) AS least_eliminating
     FROM {Q}
     """),
]


def add_arguments(p):
    p.add_argument("what", choices=("semantics", "reproducibility"),
                   help="which check to run")


def main(args):
    return (_semantics if args.what == "semantics" else _reproducibility)()


def _semantics():
    project = require_project()
    for title, sql in SEMANTIC_CHECKS:
        print("=" * 78)
        print(title)
        for name, value in _scalar_row(sql, project).items():
            print(f"  {name:20} {value:>15,}")
    print("=" * 78)
    print("Read these against README.md: the join counts must differ, the other\n"
          "pairs must match, and the selectivity spread must be ~892x.")
    return 0


def _scalar_row(sql, project):
    job = client.run(sql, project, location=LOCATION,
                     labels={"bq_myth_bench": "verify"})
    url = (f"{client.API}/projects/{project}/queries/{job['job_id']}"
           f"?location={LOCATION}&maxResults=1")
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + client.token())
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.load(resp)
    fields = [f["name"] for f in body["schema"]["fields"]]
    return dict(zip(fields, (int(c["v"]) for c in body["rows"][0]["f"])))


def _read_records(paths_):
    records = []
    for path in paths_:
        with open(path) as fh:
            records.extend(json.loads(line) for line in fh if line.strip())
    return records


def _positional_bias(records):
    """Are variants measured second systematically faster or slower?

    The schedule rotates variant order per repetition, so this should sit at
    1.0. It is computed over the two-variant comparisons that found no effect,
    since those are the ones where a positional artefact would show up
    undisguised.
    """
    by_variant = defaultdict(list)
    for r in records:
        by_variant[(r["myth"], r["variant"])].append(r["slot_ms"])

    ratios = []
    for entry in matrix.ALL:
        names = [n for n, _ in entry["variants"]]
        if len(names) != 2:
            continue
        try:
            first, second = (statistics.median(by_variant[(entry["key"], n)])
                             for n in names)
        except statistics.StatisticsError:
            continue
        if first and 0.9 < second / first < 1.1:      # comparisons finding ~no effect
            ratios.append(second / first)
    return statistics.mean(ratios) if ratios else None


def _reproducibility():
    """A benchmark repository has a failure mode ordinary tests miss: the code
    drifts, the recorded results stay, and the two quietly stop describing each
    other. Each check below is one way that can happen."""
    records = _read_records(_result_files())
    published = {}
    for r in records:
        published.setdefault((r["myth"], r["variant"]), r["sql"])

    complete = paths.ALL_RESULTS.exists()
    failures = (_check_sql_matches(published, complete)
                + _check_run_conditions(records)
                + _check_repetitions(records))
    bias = _positional_bias(records)
    if bias is not None and not 0.95 <= bias <= 1.05:
        failures.append(
            f"variants measured second are {bias:.3f}x the first on comparisons "
            "that found no effect - the schedule may be biasing the measurement")

    counts = Counter((r["myth"], r["variant"]) for r in records)
    print(f"records          {len(records):,}")
    print(f"comparisons      {len({r['myth'] for r in records})}")
    print(f"variants         {len(published)}")
    print(f"reps per variant {min(counts.values())}..{max(counts.values())}")
    if bias is not None:
        print(f"positional bias  {bias:.3f}  (1.000 = none)")
    if failures:
        print("\nFAILED:")
        for failure in failures:
            print("  -", failure)
        return 1
    print("\nOK - matrix.py reproduces every recorded query; run conditions hold.")
    return 0


def _result_files():
    if paths.ALL_RESULTS.exists():
        return [paths.ALL_RESULTS]
    candidates = sorted(paths.RESULTS.glob("*.jsonl"))
    if not candidates:
        raise SystemExit(
            f"no results to verify in {paths.RESULTS}\n"
            "  results/ is generated, not committed. Produce it with:\n"
            "      python3 -m bqbench run --matrix all "
            "--out results/all_results.jsonl")
    return candidates


def _check_sql_matches(published, complete):
    """Every recorded query is still generated, unchanged, by the matrix.

    `complete` says whether these records are a full run; only then is a query
    the matrix defines but the results lack a real failure.
    """
    built = {(m["key"], name): sql for m in matrix.ALL for name, sql in m["variants"]}
    failures = [f"in results but not in matrix.py: {k[0]}/{k[1]}"
                for k in sorted(set(published) - set(built))]
    failures += [f"SQL drifted since the run: {k[0]}/{k[1]}"
                 for k in sorted(set(built) & set(published))
                 if built[k] != published[k]]
    if complete:
        failures += [f"in matrix.py but never run: {k[0]}/{k[1]}"
                     for k in sorted(set(built) - set(published))]
    return failures


def _check_run_conditions(records):
    """The conditions the methodology asserts actually hold in the data."""
    failures = []
    for field, expected in (("cache_hit", False), ("reservation", "ON_DEMAND"),
                            ("edition", None)):
        seen = {str(r[field]) for r in records if r[field] != expected}
        if seen:
            failures.append(f"{field} is not always {expected!r}: saw {seen}")
    return failures


def _check_repetitions(records):
    """Enough repetitions, and none of them a cache hit in disguise."""
    counts = Counter((r["myth"], r["variant"]) for r in records)
    slots = defaultdict(set)
    for r in records:
        slots[(r["myth"], r["variant"])].add(r["slot_ms"])
    failures = []
    frozen = [k for k, v in slots.items() if len(v) == 1 and counts[k] > 2]
    if frozen:
        failures.append(f"suspiciously constant slot_ms (cached run?): {frozen[:3]}")
    return failures


def _read_records(paths_):
    records = []
    for path in paths_:
        with open(path) as fh:
            records.extend(json.loads(line) for line in fh if line.strip())
    return records


def _positional_bias(records):
    """Are variants measured second systematically faster or slower?

    The schedule rotates variant order per repetition, so this should sit at
    1.0. It is computed over the two-variant comparisons that found no effect,
    since those are the ones where a positional artefact would show up
    undisguised.
    """
    by_variant = defaultdict(list)
    for r in records:
        by_variant[(r["myth"], r["variant"])].append(r["slot_ms"])

    ratios = []
    for entry in matrix.ALL:
        names = [n for n, _ in entry["variants"]]
        if len(names) != 2:
            continue
        try:
            first, second = (statistics.median(by_variant[(entry["key"], n)])
                             for n in names)
        except statistics.StatisticsError:
            continue
        if first and 0.9 < second / first < 1.1:      # comparisons finding ~no effect
            ratios.append(second / first)
    return statistics.mean(ratios) if ratios else None
