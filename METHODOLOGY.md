# Methodology

## The design

Each claim is expressed as two or more **spellings of the same question** — SQL
that differs only in the thing the folklore says matters. Where a pair is
byte-identical in the columns it touches, that is by construction, and it means
any difference in cost has to come from the spelling rather than from the work.

A handful of comparisons deliberately break that symmetry as controls: the
join-type test is also run where the join types genuinely return different row
counts, and `LIMIT` / `SELECT *` are measured precisely because they *do* change
what is read.

## How it was run

- **622 query jobs** total: 617 in the measured matrix, plus 5 repetitions of
  the denormalised-table rebuild used for the break-even arithmetic.
- **19 comparisons.** 14 had two variants; 5 had three or four (join types had
  four; CTE/subquery/temp-table, `=`/`LIKE`/`REGEXP_CONTAINS`, INT64/STRING/CAST
  and the row-count control had three each).
- **n = 6 to 18 repetitions per variant.** Most are 14 or 18; the follow-ups are
  11; myths 6 and 7 are 9; the `SELECT *` comparison is 6, because each rep of it
  bills 37 GiB.
- **Repetitions are interleaved** — rep 1 of every variant, then rep 2, and so
  on — so drift in the shared slot pool is spread across variants instead of
  being confounded with them.
- **Result cache disabled on every job.** Verified in the records: `cache_hit`
  is `false` for all 617.
- **On-demand throughout.** Verified in the records: `reservation` is
  `ON_DEMAND` and `edition` is null for all 617. Billed bytes are therefore
  literally the bill.
- **US multi-region**, colocated with `bigquery-public-data`.
- Reported figure is the **median**, with `[min..max]` shown alongside.

## Why slot time alone is not enough

On-demand slot time is noisy. The same query ran between 42,254 and 140,546
slot-ms across its own repetitions. Any single-run comparison on this metric is
worthless, and even medians move by a few percent between passes.

So every comparison also carries the **work metrics** in the query plan: the
stage sequence, records read, records written, and shuffle bytes. These do not
move with slot availability, which is what makes them better evidence than
timing. When two spellings produce the same stages and the same record counts,
they are executing the same query — that is the citable evidence, and slot time
is corroboration.

They are not constant, however, and the analysis does not pretend otherwise. A
query that short-circuits reads a different number of records each run: the
`SELECT id, tags ... LIMIT 10` variant ranged from 408,986 to 23,020,127 records
read across its six repetitions. Each variant therefore carries
`work_stable_across_reps`, the reported figure is the **median** (for the same
reason slot time is), and a comparison whose counts were unstable is marked as
such in RESULTS.md rather than having an identity claim rest on it. Every record
count quoted in this document comes from a comparison that was stable.

Two summary counts from the 19 comparisons:

- **Identical plans *and* identical record counts: 12 of 19.**
- **Identical billed bytes: 15 of 19.** The four exceptions are the temp table,
  `SELECT *`, the key-type change and denormalisation — every one of which
  changes what is *stored* or *which columns are read*. No predicate or join
  rewrite moved a single billed byte.

That second count is the finding underneath the other findings: on on-demand
pricing, rewriting predicates and joins has an arithmetically bounded payoff of
zero, because bytes are the meter and bytes did not move.

## Statistics

p-values are a **two-sided Mann-Whitney U** test on the slot-ms distributions,
computed with a normal approximation and a tie correction (`bqbench/stats.py`). At
n = 6 to 18 these are **indicative, not definitive** — they are reported to
distinguish "this gap survives resampling" from "this gap is the slot pool", not
to make a formal statistical claim. Where a result is called real, the plan and
record-count evidence agrees with the p-value; where they disagree, the text
says so.

## What "identical" means, precisely

Three different things are checked, and they are not interchangeable:

- **Identical plans** — the variants compiled to the same stage sequence. Only
  claimed where each variant's plan was also *stable across its own
  repetitions* (see below).
- **Identical record counts** — the same records read and written across plan
  stages. Note these are the counts flowing through the *stages*, which for an
  aggregate query are dominated by the input scan. In myth 2 all four join types
  read 129,695,296 records and write 64,847,699 — those are the two input tables
  (46,135,386 + 18,712,212), not the join output. The join outputs genuinely
  differ, and `bqbench verify semantics` prints them: 46,135,068 / 46,135,386 /
  56,180,984 / 56,181,302. So myth 2's finding is that FULL OUTER emits ten
  million more rows for the same cost, which is a stronger result than "the
  keyword does nothing" — but it is not a claim that the four queries are
  equivalent.
- **Identical billed bytes** — the on-demand meter. True in 15 of 19
  comparisons.

## Multiple comparisons

The matrix runs roughly two dozen pairwise tests and applies **no correction for
multiple comparisons**. At α = 0.05 that means about one false positive is
expected across the set. This is why no result rests on a p-value alone:

- The findings reported as real (myths 1b, 4's temp table, 6, 7, and the
  `LIMIT` and `SELECT *` controls) all sit at p ≤ 0.005 *and* are corroborated
  by plan or byte evidence that does not depend on timing at all.
- The weakest surviving claim is `REGEXP_CONTAINS` at p = 0.005 and a 1.17×
  effect. Under a Bonferroni correction across 24 tests (α = 0.002) it would not
  survive, which is why it is reported as "real but small" rather than as a
  recommendation.
- The `SELECT *` slot-time difference (p = 0.045) is **not** relied on at all;
  that myth's finding rests entirely on billed bytes, which differ by 48.8×.

## Caveats

**One dataset, one region, one pricing model.** Everything ran against
`bigquery-public-data.stackoverflow` (plus two fixtures derived from it) in the
US multi-region on on-demand pricing. On a reservation with fixed slots the
work metrics would be identical and the slot-time behaviour would differ under
contention.

**Query plans are not fully stable across repetitions.** In **7 of the 19
comparisons**, at least one variant produced more than one distinct stage
sequence across its own repetitions. Two mechanisms account for all of it, and
both are BigQuery runtime adaptivity rather than anything in the SQL:

1. *Dynamic repartitioning.* The high-cardinality `DISTINCT`/`GROUP BY`
   comparison produced 5-, 6- and 7-stage plans for the same SQL depending on
   the run; the three-table join order produced 7 or 8.
2. *Single-stage collapse.* Fast queries sometimes execute as one `S00: Output`
   stage instead of `Input → Aggregate → Output`. This affected both variants
   roughly symmetrically wherever it occurred (16/2 vs 16/2, 16/2 vs 17/1).

The affected comparisons are: high-cardinality DISTINCT, multi-column DISTINCT,
CTE-vs-subquery, three-table join order, the predicate-cost trio,
`SELECT *`, and denormalisation. **No plan-identity claim is made for those.**
In several of them the *record counts* are still identical across variants,
which is the stronger evidence and is unaffected by stage jitter — where that is
the case, the results text says so explicitly rather than leaning on the plan.

**The schedule was checked for positional bias.** Variant order inside a
comparison was fixed in the original run, which could in principle have made
whichever query ran second look systematically faster. It did not: across the
two-variant comparisons that measured no effect, the second variant's median is
1.011x the first — inside the noise. `bqbench verify reproducibility` reports
that figure and fails outside 0.95–1.05, and the runner now rotates variant
order per repetition so later runs are robust by construction rather than by
inspection.

**Three-table join order is directional only.** The 0.968 ratio runs opposite
the folklore, but at p = 0.24 it is not a significant result. It is reported as
"not the direction the advice predicts", not as a measured win.

**The temp-table storage charge was not measured.** BigQuery bills temporary
tables for storage; what was measured here is only the query cost, which was
already the worst of the three options.

## Reproducing

`python3 -m bqbench estimate` dry-runs every variant and prints the bill before
anything is billed.

`results/` is generated by a run, not committed — see the README. A run writes
one JSON record per job with the complete query plan, which is what every number
above is computed from. `python3 -m bqbench verify reproducibility` checks a
`results/` directory against the matrix that claims to describe it.
