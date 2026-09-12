"""The experiment matrix.

Each entry is one folklore claim expressed as two or more spellings of the same
question. Where a pair touches identical columns the bytes are identical by
construction, so any cost difference has to come from the spelling rather than
from the work.

CORE holds the seven claims plus the controls; FOLLOWUPS holds the tests the
first run made necessary (see m1c). `ALL` is both, and `DOC_ORDER` at the bottom
is the order they read in.

Every table is `bigquery-public-data.stackoverflow`, except the two fixtures for
myths 6 and 7 that sql/fixtures.sql derives from that same public data.
"""
from .config import fixture as _f

Q = "`bigquery-public-data.stackoverflow.posts_questions`"
U = "`bigquery-public-data.stackoverflow.users`"
B = "`bigquery-public-data.stackoverflow.badges`"

CORE = []
FOLLOWUPS = []
_target = CORE


def myth(key, claim, title, variants, *, reps=9, note=""):
    """Register one comparison.

    key       stable identifier; appears in every result record
    claim     the folklore, as its proponents state it
    title     heading for RESULTS.md
    variants  [(name, sql), ...]; the first is the baseline the rest are
              reported against
    note      what a reader needs to know to read the numbers honestly
    """
    _target.append({"key": key, "claim": claim, "title": title,
                    "variants": variants, "reps": reps, "note": note})


# ── M1 ── "BigQuery doesn't optimize the WHERE clause; order filters most-eliminating first"
# Four predicates spanning 0.10% -> 86% selectivity (a ~900x range), presented in
# both orders. Identical columns => identical bytes by construction; the only
# question is slot time.
_m1_preds = {
    "tag":  "tags LIKE '%google-bigquery%'",   # 22,081 rows  (0.10%)
    "sc":   "score > 0",                       # 10,890,307   (47.3%)
    "vc":   "view_count > 100",                # 17,148,805   (74.5%)
    "ac":   "answer_count >= 1",               # 19,703,319   (85.6%)
}
_m1_body = "SELECT COUNT(*) AS n, SUM(view_count) AS v FROM {} WHERE {}".format

myth("m1_order",
     "BigQuery doesn't optimize the WHERE clause - order your filters most-eliminating first.",
     "Myth 1 — order your filters most-eliminating first",
     [
       ("most_eliminating_first",
        _m1_body(Q, " AND ".join(_m1_preds[k] for k in ("tag", "sc", "vc", "ac")))),
       ("least_eliminating_first",
        _m1_body(Q, " AND ".join(_m1_preds[k] for k in ("ac", "vc", "sc", "tag")))),
     ],
     note="Four predicates spanning an 892x selectivity range, in both orders."
          " `bqbench verify semantics` confirms both orderings return the same"
          " 7,922 rows.")

# The strongest form of the same claim: an expensive regex next to a
# hyper-selective integer equality. If BigQuery evaluated left-to-right, putting
# the regex first would run it over all 23M rows.
_m1x = ("SELECT COUNT(*) AS n FROM {} WHERE {}").format
_rx = r"REGEXP_CONTAINS(title, r'(?i)\b(bigquery|snowflake|redshift)\b')"
_eq = "id = 4562282"
myth("m1_costly_predicate",
     "Put the cheap/most-selective predicate first or the expensive function runs on every row.",
     "Myth 1b — first attempt, defeated by block pruning",
     [
       ("expensive_regex_first", _m1x(Q, f"{_rx} AND {_eq}")),
       ("cheap_equality_first",  _m1x(Q, f"{_eq} AND {_rx}")),
     ],
     note="First attempt at the same question, and a useful failure: BigQuery"
          " pruned storage blocks on `id`, so both variants read only 221,081 of"
          " 23M records and the regex never ran at scale. m1c defeats that pruning"
          " and is the measurement to cite.")

# ── M2 ── "INNER JOIN is faster than LEFT, which is faster than OUTER"
# 2a: badges->users matches 46,135,068 of 46,135,386 rows, so INNER and LEFT
# return effectively the SAME result set (318 rows apart, 0.0007%). Any cost
# difference here is attributable to the keyword alone.
_m2a = ("SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
        f"FROM {B} b {{}} JOIN {U} u ON b.user_id = u.id").format
myth("m2a_jointype_same_rows",
     "INNER JOIN is faster than LEFT JOIN is faster than FULL OUTER JOIN.",
     "Myth 2 — INNER beats LEFT beats OUTER",
     [("inner", _m2a("INNER")), ("left", _m2a("LEFT")),
      ("right", _m2a("RIGHT")), ("full_outer", _m2a("FULL OUTER"))],
     reps=7,
     note="All four join types on the same join. The record counts below are the"
          " **input** scan (46,135,386 badges + 18,712,212 users); the join outputs"
          " genuinely differ - 46,135,068 / 46,135,386 / 56,180,984 / 56,181,302."
          " FULL OUTER emits ten million more rows for the same cost.")

# 2b: the same join with the right side filtered hard, so LEFT must emit ~23M
# unmatched rows while INNER emits far fewer. Isolates output cardinality as the
# real cost driver.
_m2b = (f"SELECT COUNT(*) AS n FROM {Q} q {{}} JOIN "
        f"(SELECT id, reputation FROM {U} WHERE reputation > 10000) u "
        "ON q.owner_user_id = u.id").format
myth("m2b_jointype_diff_rows",
     "Same claim, but where the join types genuinely return different row counts.",
     "Myth 2 control — result sets differ by construction",
     [("inner", _m2b("INNER")), ("left", _m2b("LEFT")), ("full_outer", _m2b("FULL OUTER"))],
     reps=7,
     note="The right side filtered hard, so LEFT must emit ~23M unmatched rows.")

# ── M3 ── "Prefer DISTINCT over GROUP BY"
_m3_hi_d = f"SELECT COUNT(*) AS n FROM (SELECT DISTINCT tags FROM {Q})"
_m3_hi_g = f"SELECT COUNT(*) AS n FROM (SELECT tags FROM {Q} GROUP BY tags)"
_m3_lo_d = f"SELECT COUNT(*) AS n FROM (SELECT DISTINCT answer_count FROM {Q})"
_m3_lo_g = f"SELECT COUNT(*) AS n FROM (SELECT answer_count FROM {Q} GROUP BY answer_count)"
_m3_mc_d = f"SELECT COUNT(*) AS n FROM (SELECT DISTINCT owner_user_id, answer_count FROM {Q})"
_m3_mc_g = (f"SELECT COUNT(*) AS n FROM (SELECT owner_user_id, answer_count FROM {Q} "
            "GROUP BY owner_user_id, answer_count)")
myth("m3_high_cardinality",
     "Prefer DISTINCT over GROUP BY (high cardinality: 8.5M distinct tags).",
     "Myth 3 — 8.5M distinct tags",
     [("distinct", _m3_hi_d), ("group_by", _m3_hi_g)],
     note="Both spellings return 8,448,317 rows.")
myth("m3_low_cardinality",
     "Prefer DISTINCT over GROUP BY (low cardinality: 127 distinct values).",
     "Myth 3 — prefer DISTINCT over GROUP BY (127 groups)",
     [("distinct", _m3_lo_d), ("group_by", _m3_lo_g)])
myth("m3_multi_column",
     "Prefer DISTINCT over GROUP BY (two columns, ~5M groups).",
     "Myth 3 — two columns, ~5M groups",
     [("distinct", _m3_mc_d), ("group_by", _m3_mc_g)])

# ── M4 ── "Avoid CTEs, use temp tables"
_heavy = (f"SELECT owner_user_id, COUNT(*) AS c, SUM(view_count) AS v "
          f"FROM {Q} WHERE owner_user_id IS NOT NULL GROUP BY owner_user_id")
_fanout = ("SELECT t1.a, t2.b, t3.c FROM "
           "(SELECT SUM(c) AS a FROM {h}) t1, "
           "(SELECT SUM(v) AS b FROM {h}) t2, "
           "(SELECT COUNT(*) AS c FROM {h} WHERE c > 1) t3")

myth("m4a_cte_referenced_once",
     "Avoid CTEs - a CTE referenced once is slower than the equivalent subquery.",
     "Myth 4 — CTE vs subquery, referenced once",
     [("cte",      f"WITH heavy AS ({_heavy}) SELECT COUNT(*) AS users, SUM(c) AS posts, SUM(v) AS views FROM heavy"),
      ("subquery", f"SELECT COUNT(*) AS users, SUM(c) AS posts, SUM(v) AS views FROM ({_heavy})")])

myth("m4b_cte_referenced_thrice",
     "Avoid CTEs, use temp tables - the multiply-referenced case.",
     "Myth 4 — referenced three times",
     [("cte_3_refs",
       "WITH heavy AS (" + _heavy + ") " + _fanout.format(h="heavy")),
      ("subquery_3_copies",
       _fanout.format(h="(" + _heavy + ")")),
      ("temp_table_3_refs",
       "CREATE TEMP TABLE heavy AS " + _heavy + ";\n" + _fanout.format(h="heavy") + ";")],
     reps=7,
     note="The case the folklore is actually about. Both spellings re-read the CTE"
          " (74,257,782 records, ~3x the 23M base) and cost the same; the temp"
          " table is worse on both slot time and bytes. Temp-table stages are n/a:"
          " a script's work lives in child jobs, which carry no parent-level plan.")

# ── M5 ── "Start your joins with the largest table"
_m5_2t_big = ("SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
              f"FROM {Q} q JOIN {U} u ON q.owner_user_id = u.id")
_m5_2t_sml = ("SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
              f"FROM {U} u JOIN {Q} q ON u.id = q.owner_user_id")
_bcount = f"(SELECT user_id, COUNT(*) AS nb FROM {B} GROUP BY user_id)"
_m5_3t_big = ("SELECT COUNT(*) AS n, SUM(b.nb) AS nb "
              f"FROM {Q} q JOIN {U} u ON q.owner_user_id = u.id "
              f"JOIN {_bcount} b ON b.user_id = u.id")
_m5_3t_sml = ("SELECT COUNT(*) AS n, SUM(b.nb) AS nb "
              f"FROM {_bcount} b JOIN {U} u ON b.user_id = u.id "
              f"JOIN {Q} q ON q.owner_user_id = u.id")
myth("m5_join_order_2t",
     "Start your joins with the largest table (2 tables: 39.9 GB vs 3.4 GB).",
     "Myth 5 — start joins with the largest table",
     [("largest_first", _m5_2t_big), ("smallest_first", _m5_2t_sml)],
     note="39.9 GB against 3.4 GB.")
myth("m5_join_order_3t",
     "Start your joins with the largest table (3 tables).",
     "Myth 5 — three tables",
     [("largest_first", _m5_3t_big), ("smallest_first", _m5_3t_sml)],
     reps=7,
     note="Runs opposite the advice, but at p = 0.24 this is directional only, not"
          " a measured win.")

# ── Bonus: claims Article 1 states as fact in §3.3 / §3.4 and should back ──
myth("b1_limit_bytes",
     "LIMIT reduces bytes scanned.",
     "Control — LIMIT reduces bytes scanned",
     [("no_limit", f"SELECT title, tags FROM {Q}"),
      ("limit_10", f"SELECT title, tags FROM {Q} LIMIT 10")],
     reps=5,
     note="The single most useful measurement here: the bill does not move, the"
          " slot time collapses.")

myth("b2_predicate_cost",
     "REGEXP_CONTAINS starts a regex engine per row to answer what = answers for free.",
     "Control — REGEXP_CONTAINS vs = vs LIKE",
     [("equality",  f"SELECT COUNT(*) AS n FROM {Q} WHERE tags = 'python'"),
      ("like_exact", f"SELECT COUNT(*) AS n FROM {Q} WHERE tags LIKE 'python'"),
      ("regexp",    f"SELECT COUNT(*) AS n FROM {Q} WHERE REGEXP_CONTAINS(tags, r'^python$')")])

# COUNT(*) over a subquery gets folded to a metadata read, so force a real
# column scan: LIMIT keeps the result set small while the scan stays full-width.
myth("b3_select_star",
     "SELECT * costs more than naming the columns you need.",
     "Control — SELECT *",
     [("two_columns", f"SELECT id, tags FROM {Q} LIMIT 10"),
      ("select_star", f"SELECT * FROM {Q} LIMIT 10")],
     reps=3,
     note="This myth's finding rests on billed bytes alone. The slot-time column"
          " is not evidence: `LIMIT` short-circuits the two variants differently,"
          " and at n = 6 with p = 0.045 it is not relied on.")

# ── M6 / M7 ── the two myths that need fixtures (same StackOverflow rows,
# materialised into $BENCH_PROJECT.$BENCH_DATASET; see sql/fixtures.sql).
# `_f` is imported at the top of this module.

myth("m7_key_types",
     "Cast string keys to INT64 for faster joins.",
     "Myth 7 — cast string keys to INT64",
     [("int_join_int",
       f"SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
       f"FROM {_f('k_posts_int')} p JOIN {_f('k_users_int')} u ON p.owner_user_id = u.id"),
      ("string_join_string",
       f"SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
       f"FROM {_f('k_posts_str')} p JOIN {_f('k_users_str')} u ON p.owner_user_id = u.id"),
      ("cast_inside_the_join",
       f"SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
       f"FROM {_f('k_posts_str')} p JOIN {_f('k_users_int')} u "
       f"ON CAST(p.owner_user_id AS INT64) = u.id")],
     reps=9,
     note="Same 22.6M x 18.7M rows; only the key's storage type differs.")

_m6_star = (
    "SELECT u.location, COUNT(*) AS posts, SUM(p.view_count) AS views, "
    "AVG(u.reputation) AS rep, SUM(b.n_badges) AS badges "
    f"FROM {_f('n_posts')} p "
    f"JOIN {_f('n_users')} u ON p.owner_user_id = u.id "
    f"LEFT JOIN {_f('n_badges')} b ON b.user_id = u.id "
    "WHERE p.creation_date >= TIMESTAMP '2018-01-01' "
    "GROUP BY u.location ORDER BY views DESC LIMIT 20")
_m6_flat = (
    "SELECT location, COUNT(*) AS posts, SUM(view_count) AS views, "
    "AVG(reputation) AS rep, SUM(n_badges) AS badges "
    f"FROM {_f('denorm_wide')} "
    "WHERE creation_date >= TIMESTAMP '2018-01-01' "
    "GROUP BY location ORDER BY views DESC LIMIT 20")
myth("m6_denormalisation",
     "Denormalise five to eight tables for sub-second latency.",
     "Myth 6 — denormalise for sub-second latency",
     [("star_join_3_tables", _m6_star), ("denormalised_1_table", _m6_flat)],
     reps=9,
     note="A three-table star join against the same rows pre-joined. See the"
          " break-even below.")


# ============================================================================
# Follow-ups. Registered into FOLLOWUPS rather than CORE.
# ============================================================================
_target = FOLLOWUPS

# MOD(id, 7919) = 0 is ~0.013% selective but block-unprunable: every row must be
# read and evaluated. Now the only difference between the two variants is which
# predicate the engine evaluates first, over the full 23M rows.
_rx = r"REGEXP_CONTAINS(title, r'(?i)\b(bigquery|snowflake|redshift)\b')"
_mod = "MOD(id, 7919) = 0"
_body = f"SELECT COUNT(*) AS n FROM {Q} WHERE {{}}"

myth("m1c_shortcircuit_full_scan",
     "Filter order matters (expensive function vs cheap filter, no block pruning possible).",
     "Myth 1b — ...or the expensive function runs on every row",
     [("expensive_regex_first", _body.format(f"{_rx} AND {_mod}")),
      ("cheap_filter_first",    _body.format(f"{_mod} AND {_rx}"))],
     reps=11,
     note="An expensive regex beside a cheap filter, with block pruning defeated"
          " by `MOD()` so both predicates see all 23M rows.")

# Control: two CHEAP predicates in both orders over the same full scan. If
# ordering only matters when one side is expensive, this pair should be flat.
_cheap_a = "score > 0"
_cheap_b = _mod
myth("m1d_shortcircuit_both_cheap",
     "Filter order matters when both predicates are cheap.",
     "Myth 1b control — both predicates cheap",
     [("selective_first", _body.format(f"{_cheap_b} AND {_cheap_a}")),
      ("unselective_first", _body.format(f"{_cheap_a} AND {_cheap_b}"))],
     reps=11,
     note="The same reordering where neither predicate is expensive. If the effect"
          " were about selectivity this would show it; it does not.")

# And the practical question the article should actually answer: does hoisting
# the expensive function out of the filter beat reordering it?
myth("m1e_regex_vs_cheaper_rewrite",
     "The fix that actually pays: replace the regex, don't reorder it.",
     "Myth 1b follow-up — replace the regex instead of reordering it",
     [("regex_over_full_scan", _body.format(_rx)),
      ("like_over_full_scan",
       _body.format("(LOWER(title) LIKE '%bigquery%' OR LOWER(title) LIKE '%snowflake%' "
                    "OR LOWER(title) LIKE '%redshift%')"))],
     reps=11,
     note="Three `LOWER() LIKE` clauses against the single regex they replace. The"
          " obvious fix is worse than the thing it fixes.")


ALL = CORE + FOLLOWUPS


MATRICES = {"core": CORE, "followups": FOLLOWUPS, "all": ALL}


def select(name):
    """Look up a matrix by name, with a usable error for a typo."""
    try:
        return MATRICES[name]
    except KeyError:
        raise SystemExit(f"unknown matrix {name!r}; choose from "
                         f"{', '.join(MATRICES)}") from None


#: Reading order for RESULTS.md. Definition order groups comparisons by when
#: they were written; this groups them by the argument they belong to, so a
#: follow-up sits with the claim it refines rather than in an appendix.
DOC_ORDER = [
    "m1_order",
    "m1_costly_predicate",
    "m1c_shortcircuit_full_scan",
    "m1d_shortcircuit_both_cheap",
    "m1e_regex_vs_cheaper_rewrite",
    "m2a_jointype_same_rows",
    "m2b_jointype_diff_rows",
    "m3_low_cardinality",
    "m3_multi_column",
    "m3_high_cardinality",
    "m4a_cte_referenced_once",
    "m4b_cte_referenced_thrice",
    "m5_join_order_2t",
    "m5_join_order_3t",
    "m6_denormalisation",
    "m7_key_types",
    "b1_limit_bytes",
    "b3_select_star",
    "b2_predicate_cost",
]


def in_doc_order():
    """Every comparison, ordered for reading.

    Raises if DOC_ORDER and the matrix have drifted, so a newly added myth
    cannot be silently left out of the document.
    """
    by_key = {m["key"]: m for m in ALL}
    unlisted = sorted(set(by_key) - set(DOC_ORDER))
    unknown = sorted(set(DOC_ORDER) - set(by_key))
    if unlisted or unknown:
        raise SystemExit(
            "DOC_ORDER is out of sync with the matrix"
            + (f"; not listed: {unlisted}" if unlisted else "")
            + (f"; no such myth: {unknown}" if unknown else ""))
    return [by_key[k] for k in DOC_ORDER]
