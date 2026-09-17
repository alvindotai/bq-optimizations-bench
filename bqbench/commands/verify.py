"""Check the benchmark against itself.

Two different objections, two different checks:

  semantics        are the compared queries what the comparison claims? Needs
                   credentials and costs about $0.05.
  reproducibility  does the matrix still generate the committed results,
                   byte-for-byte, and do the documented run conditions hold in
                   the data? Free, no credentials.
"""
import json
import re
import statistics
import urllib.request
from collections import Counter, defaultdict

from .. import client, matrix, paths
from ..config import DATASET, LOCATION, require_project

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
    """Is a query systematically slower because of WHERE IT SAT in the schedule?

    Measured from the order the jobs actually ran in (`ts`), not from the order
    `matrix.py` declares its variants - those coincide only in a run made before
    the runner rotated, and an estimator keyed on declaration order silently
    stops measuring anything once rotation is on.

    Each job is divided by its own variant's median, which removes the effect of
    the SQL and leaves only position. Those normalised values are then averaged
    per slot within the repetition, and the figure reported is the spread across
    slots: 1.0 means position cost nothing.

    Note what this can and cannot see. It is an average over every comparison,
    so a bias confined to one comparison is diluted, and slot-ms is noisy enough
    at these sample sizes that only a systematic effect of a few percent or more
    will surface. It bounds a schedule artefact; it does not rule one out.
    """
    baseline = defaultdict(list)
    for r in records:
        baseline[(r["myth"], r["variant"])].append(r["slot_ms"])
    medians = {k: statistics.median(v) for k, v in baseline.items()}

    groups = defaultdict(list)
    for r in records:
        groups[(r["myth"], r["run_id"], r["rep"])].append(r)

    by_slot = defaultdict(list)
    for members in groups.values():
        if len(members) < 2:
            continue
        for position, r in enumerate(sorted(members, key=lambda x: x["ts"])):
            median = medians[(r["myth"], r["variant"])]
            if median:
                by_slot[position].append(r["slot_ms"] / median)

    means = [statistics.mean(v) for _, v in sorted(by_slot.items()) if v]
    return max(means) / min(means) if len(means) > 1 and min(means) else None


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
    if bias is not None and bias > 1.10:
        failures.append(
            f"a variant's position in the schedule moved its slot time by "
            f"{bias:.3f}x - the schedule may be biasing the measurement")

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

    outstanding = _outstanding(records)
    if outstanding:
        print("\nDECLARED BUT NOT MEASURED - these are defined in matrix.py and\n"
              "absent from this results/ on purpose. Close one with:")
        for m in outstanding:
            which = "followups" if m in matrix.FOLLOWUPS else "core"
            print(f"  - {m['key']}\n"
                  f"      python3 -m bqbench run --matrix {which} "
                  f"--only {m['key']}")
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


#: A fixture reference carries whichever project rendered it, so the same query
#: reads `my-project.bq_myth_bench.n_posts` for you and
#: `example-project.bq_myth_bench.n_posts` in the published records. Comparing
#: those literally reports drift for all five fixture variants the moment
#: BENCH_PROJECT is set - which is every real run.
_FIXTURE_REF = re.compile(r"`[^`.]+\.(" + re.escape(DATASET) + r")\.")


def _comparable(sql):
    """SQL with the billing project neutralised, so only real drift shows."""
    return _FIXTURE_REF.sub(r"`<project>.\1.", sql)


def _check_sql_matches(published, complete):
    """Every recorded query is still generated, unchanged, by the matrix.

    `complete` says whether these records are a full run; only then is a query
    the matrix defines but the results lack a real failure.
    """
    built = {(m["key"], name): sql for m in matrix.ALL for name, sql in m["variants"]
             if m.get("measured", True)}
    failures = [f"in results but not in matrix.py: {k[0]}/{k[1]}"
                for k in sorted(set(published) - set(built))]
    failures += [f"SQL drifted since the run: {k[0]}/{k[1]}"
                 for k in sorted(set(built) & set(published))
                 if _comparable(built[k]) != _comparable(published[k])]
    if complete:
        failures += [f"in matrix.py but never run: {k[0]}/{k[1]}"
                     for k in sorted(set(built) - set(published))]
    return failures


def _outstanding(records):
    """Comparisons this repository defines but has not measured.

    Declared with `measured=False`, so they are a stated gap rather than drift,
    and they are reported with the command that closes them.
    """
    seen = {r["myth"] for r in records}
    return [m for m in matrix.ALL
            if not m.get("measured", True) and m["key"] not in seen]


def _check_run_conditions(records):
    """The conditions the methodology asserts actually hold in the data."""
    failures = []
    for field, expected in (("cache_hit", False), ("reservation", "ON_DEMAND"),
                            ("edition", None)):
        seen = {str(r[field]) for r in records if r[field] != expected}
        if seen:
            failures.append(f"{field} is not always {expected!r}: saw {seen}")
    return failures


#: The repetition range METHODOLOGY.md and RESULTS.md both quote. Asserted, not
#: assumed: a run that silently came out at n = 3 would leave every document
#: claiming a sample size the data does not have.
MIN_REPS, MAX_REPS = 6, 18


def _check_repetitions(records):
    """Enough repetitions, balanced across a comparison, and none of them a
    cache hit in disguise."""
    counts = Counter((r["myth"], r["variant"]) for r in records)
    slots = defaultdict(set)
    for r in records:
        slots[(r["myth"], r["variant"])].add(r["slot_ms"])
    failures = []
    frozen = [k for k, v in slots.items() if len(v) == 1 and counts[k] > 2]
    if frozen:
        failures.append(f"suspiciously constant slot_ms (cached run?): {frozen[:3]}")

    outside = sorted(k for k, n in counts.items() if not MIN_REPS <= n <= MAX_REPS)
    if outside:
        failures.append(
            f"repetition count outside the documented {MIN_REPS}-{MAX_REPS}: "
            + ", ".join(f"{m}/{v} n={counts[(m, v)]}" for m, v in outside[:3]))

    # A comparison whose variants have different n is not a paired measurement;
    # a job that failed and was dropped would show up here and nowhere else.
    for entry in matrix.ALL:
        present = {n: counts[(entry["key"], n)]
                   for n, _ in entry["variants"] if counts[(entry["key"], n)]}
        if len(set(present.values())) > 1:
            failures.append(
                f"{entry['key']} has unequal repetitions per variant: {present}")
    return failures
