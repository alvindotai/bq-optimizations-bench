"""Check the benchmark against itself.

Two different objections, two different checks:

  semantics        are the compared queries what the comparison claims? Needs
                   credentials and costs about $0.05.
  reproducibility  does the matrix still generate the committed results,
                   byte-for-byte, and do the documented run conditions hold in
                   the data? Free, no credentials.
"""
import json
import urllib.request
from collections import defaultdict

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


def _reproducibility():
    """A benchmark repo has a failure mode ordinary tests miss: the code drifts,
    the recorded results stay, and the two quietly stop describing each other."""
    if not paths.ALL_RESULTS.exists():
        candidates = sorted(paths.RESULTS.glob("*.jsonl"))
        if not candidates:
            raise SystemExit(
                f"no results to verify in {paths.RESULTS}\n"
                "  results/ is generated, not committed. Produce it with:\n"
                "      python3 -m bqbench run --matrix all "
                "--out results/all_results.jsonl")
        source = candidates
    else:
        source = [paths.ALL_RESULTS]

    records = [json.loads(l) for f in source for l in open(f) if l.strip()]
    failures = []

    built = {(m["key"], name): sql for m in matrix.ALL for name, sql in m["variants"]}
    published = {}
    for r in records:
        published.setdefault((r["myth"], r["variant"]), r["sql"])

    for key in sorted(set(published) - set(built)):
        failures.append(f"in results but not in matrix.py: {key[0]}/{key[1]}")
    for key in sorted(set(built) & set(published)):
        if built[key] != published[key]:
            failures.append(f"SQL drifted since the run: {key[0]}/{key[1]}")
    missing = sorted(set(built) - set(published))
    if missing and len(source) == 1 and source[0] == paths.ALL_RESULTS:
        for key in missing:
            failures.append(f"in matrix.py but never run: {key[0]}/{key[1]}")

    for field, expected in (("cache_hit", False), ("reservation", "ON_DEMAND"),
                            ("edition", None)):
        seen = {str(r[field]) for r in records if r[field] != expected}
        if seen:
            failures.append(f"{field} is not always {expected!r}: saw {seen}")

    counts = defaultdict(int)
    slots = defaultdict(set)
    for r in records:
        counts[(r["myth"], r["variant"])] += 1
        slots[(r["myth"], r["variant"])].add(r["slot_ms"])
    lo, hi = min(counts.values()), max(counts.values())
    frozen = [k for k, v in slots.items() if len(v) == 1 and counts[k] > 2]
    if frozen:
        failures.append(f"suspiciously constant slot_ms (cached run?): {frozen[:3]}")

    print(f"records          {len(records):,}")
    print(f"comparisons      {len({r['myth'] for r in records})}")
    print(f"variants         {len(published)}")
    print(f"reps per variant {lo}..{hi}")
    if failures:
        print("\nFAILED:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nOK - matrix.py reproduces every recorded query; run conditions hold.")
    return 0
