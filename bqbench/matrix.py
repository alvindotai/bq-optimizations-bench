# -*- coding: utf-8 -*-
"""The experiment matrix.

Each entry is one folklore claim expressed as two or more spellings of the same
question. Where a pair touches identical columns the bytes are identical by
construction, so any cost difference has to come from the spelling rather than
from the work.

Two matrices:

  CORE       the seven claims, plus the controls that price what the article
             states as fact (LIMIT, SELECT *, REGEXP_CONTAINS).
  FOLLOWUPS  tests the CORE run made necessary - see the comment on m1c.

`ALL` is both, and is what results/all_results.jsonl contains.

Every table is `bigquery-public-data.stackoverflow` except the two fixtures for
myths 6 and 7, which sql/fixtures.sql derives from that same public data.
"""
from .config import fixture as _f

Q = "`bigquery-public-data.stackoverflow.posts_questions`"
U = "`bigquery-public-data.stackoverflow.users`"
B = "`bigquery-public-data.stackoverflow.badges`"

CORE = []
FOLLOWUPS = []
_target = CORE


def myth(key, claim, source, variants, reps=9, note=""):
    """Register one claim. `variants` is [(name, sql), ...]; the first is the
    baseline every other variant is reported against."""
    _target.append({"key": key, "claim": claim, "source": source,
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
     "e6data / Yuki / folklore",
     [
       ("most_eliminating_first",
        _m1_body(Q, " AND ".join(_m1_preds[k] for k in ("tag", "sc", "vc", "ac")))),
       ("least_eliminating_first",
        _m1_body(Q, " AND ".join(_m1_preds[k] for k in ("ac", "vc", "sc", "tag")))),
     ])

# The strongest form of the same claim: an expensive regex next to a
# hyper-selective integer equality. If BigQuery evaluated left-to-right, putting
# the regex first would run it over all 23M rows.
_m1x = ("SELECT COUNT(*) AS n FROM {} WHERE {}").format
_rx = r"REGEXP_CONTAINS(title, r'(?i)\b(bigquery|snowflake|redshift)\b')"
_eq = "id = 4562282"
myth("m1_costly_predicate",
     "Put the cheap/most-selective predicate first or the expensive function runs on every row.",
     "corollary of the same folklore",
     [
       ("expensive_regex_first", _m1x(Q, f"{_rx} AND {_eq}")),
       ("cheap_equality_first",  _m1x(Q, f"{_eq} AND {_rx}")),
     ])

# ── M2 ── "INNER JOIN is faster than LEFT, which is faster than OUTER"
# 2a: badges->users matches 46,135,068 of 46,135,386 rows, so INNER and LEFT
# return effectively the SAME result set (318 rows apart, 0.0007%). Any cost
# difference here is attributable to the keyword alone.
_m2a = ("SELECT COUNT(*) AS n, SUM(u.reputation) AS r "
        f"FROM {B} b {{}} JOIN {U} u ON b.user_id = u.id").format
myth("m2a_jointype_same_rows",
     "INNER JOIN is faster than LEFT JOIN is faster than FULL OUTER JOIN.",
     "e6data / Sheer / folklore",
     [("inner", _m2a("INNER")), ("left", _m2a("LEFT")),
      ("right", _m2a("RIGHT")), ("full_outer", _m2a("FULL OUTER"))],
     reps=7,
     note="Result sets are ~identical (318 rows apart) for inner vs left.")

# 2b: the same join with the right side filtered hard, so LEFT must emit ~23M
# unmatched rows while INNER emits far fewer. Isolates output cardinality as the
# real cost driver.
_m2b = ("SELECT COUNT(*) AS n FROM {q} q {{}} JOIN "
        "(SELECT id, reputation FROM {u} WHERE reputation > 10000) u "
        "ON q.owner_user_id = u.id").format(q=Q, u=U).format
myth("m2b_jointype_diff_rows",
     "Same claim, but where the join types genuinely return different row counts.",
     "control for m2a",
     [("inner", _m2b("INNER")), ("left", _m2b("LEFT")), ("full_outer", _m2b("FULL OUTER"))],
     reps=7)

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
     "Yuki / folklore",
     [("distinct", _m3_hi_d), ("group_by", _m3_hi_g)])
myth("m3_low_cardinality",
     "Prefer DISTINCT over GROUP BY (low cardinality: 127 distinct values).",
     "Yuki / folklore",
     [("distinct", _m3_lo_d), ("group_by", _m3_lo_g)])
myth("m3_multi_column",
     "Prefer DISTINCT over GROUP BY (two columns, ~5M groups).",
     "Yuki / folklore",
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
     "e6data / folklore",
     [("cte",      f"WITH heavy AS ({_heavy}) SELECT COUNT(*) AS users, SUM(c) AS posts, SUM(v) AS views FROM heavy"),
      ("subquery", f"SELECT COUNT(*) AS users, SUM(c) AS posts, SUM(v) AS views FROM ({_heavy})")])

myth("m4b_cte_referenced_thrice",
     "Avoid CTEs, use temp tables - the multiply-referenced case.",
     "e6data / folklore",
     [("cte_3_refs",
       "WITH heavy AS (" + _heavy + ") " + _fanout.format(h="heavy")),
      ("subquery_3_copies",
       _fanout.format(h="(" + _heavy + ")")),
      ("temp_table_3_refs",
       "CREATE TEMP TABLE heavy AS " + _heavy + ";\n" + _fanout.format(h="heavy") + ";")],
     reps=7,
     note="BigQuery does not materialize CTEs; a 3x-referenced CTE is a 3x re-read.")

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
     "e6data / folklore",
     [("largest_first", _m5_2t_big), ("smallest_first", _m5_2t_sml)])
myth("m5_join_order_3t",
     "Start your joins with the largest table (3 tables).",
     "e6data / folklore",
     [("largest_first", _m5_3t_big), ("smallest_first", _m5_3t_sml)],
     reps=7)

# ── Bonus: claims Article 1 states as fact in §3.3 / §3.4 and should back ──
myth("b1_limit_bytes",
     "LIMIT reduces bytes scanned.",
     "stated FALSE in outline 3.3 - verify",
     [("no_limit", f"SELECT title, tags FROM {Q}"),
      ("limit_10", f"SELECT title, tags FROM {Q} LIMIT 10")],
     reps=5)

myth("b2_predicate_cost",
     "REGEXP_CONTAINS starts a regex engine per row to answer what = answers for free.",
     "stated in outline 3.4 - quantify",
     [("equality",  f"SELECT COUNT(*) AS n FROM {Q} WHERE tags = 'python'"),
      ("like_exact", f"SELECT COUNT(*) AS n FROM {Q} WHERE tags LIKE 'python'"),
      ("regexp",    f"SELECT COUNT(*) AS n FROM {Q} WHERE REGEXP_CONTAINS(tags, r'^python$')")])

# COUNT(*) over a subquery gets folded to a metadata read, so force a real
# column scan: LIMIT keeps the result set small while the scan stays full-width.
myth("b3_select_star",
     "SELECT * costs more than naming the columns you need.",
     "stated in outline 3.3 - quantify",
     [("two_columns", f"SELECT id, tags FROM {Q} LIMIT 10"),
      ("select_star", f"SELECT * FROM {Q} LIMIT 10")],
     reps=3)

# ── M6 / M7 ── the two myths that need fixtures (same StackOverflow rows,
# materialised into $BENCH_PROJECT.$BENCH_DATASET; see sql/fixtures.sql).
# `_f` is imported at the top of this module.

myth("m7_key_types",
     "Cast string keys to INT64 for faster joins.",
     "e6data / folklore",
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
     note="Same 22.6M x 18.7M rows in all three; only the key's storage type differs.")

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
     "e6data / folklore (storage + freshness cost unpriced)",
     [("star_join_3_tables", _m6_star), ("denormalised_1_table", _m6_flat)],
     reps=9,
     note="Identical result set; denorm_wide is the same rows pre-joined.")


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
     "the strong form of the WHERE-order folklore",
     [("expensive_regex_first", _body.format(f"{_rx} AND {_mod}")),
      ("cheap_filter_first",    _body.format(f"{_mod} AND {_rx}"))],
     reps=11,
     note="MOD() defeats block pruning, so both predicates see all 23M rows.")

# Control: two CHEAP predicates in both orders over the same full scan. If
# ordering only matters when one side is expensive, this pair should be flat.
_cheap_a = "score > 0"
_cheap_b = _mod
myth("m1d_shortcircuit_both_cheap",
     "Filter order matters when both predicates are cheap.",
     "control for m1c",
     [("selective_first", _body.format(f"{_cheap_b} AND {_cheap_a}")),
      ("unselective_first", _body.format(f"{_cheap_a} AND {_cheap_b}"))],
     reps=11)

# And the practical question the article should actually answer: does hoisting
# the expensive function out of the filter beat reordering it?
myth("m1e_regex_vs_cheaper_rewrite",
     "The fix that actually pays: replace the regex, don't reorder it.",
     "constructive counterpart",
     [("regex_over_full_scan", _body.format(_rx)),
      ("like_over_full_scan",
       _body.format("(LOWER(title) LIKE '%bigquery%' OR LOWER(title) LIKE '%snowflake%' "
                    "OR LOWER(title) LIKE '%redshift%')"))],
     reps=11)


ALL = CORE + FOLLOWUPS


MATRICES = {"core": CORE, "followups": FOLLOWUPS, "all": ALL}


def select(name):
    """Look up a matrix by name, with a usable error for a typo."""
    try:
        return MATRICES[name]
    except KeyError:
        raise SystemExit(f"unknown matrix {name!r}; choose from "
                         f"{', '.join(MATRICES)}")
