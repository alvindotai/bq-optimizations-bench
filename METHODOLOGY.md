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

- **640 query jobs** total: 635 in the measured matrix, plus 5 repetitions of
  the denormalised-table rebuild used for the break-even arithmetic.
- **20 comparisons.** 15 had two variants; 5 had three or four (join types had
  four; CTE/subquery/temp-table, `=`/`LIKE`/`REGEXP_CONTAINS`, INT64/STRING/CAST
  and the row-count control had three each).
- **n = 6 to 18 repetitions per variant.** Most are 14 or 18; the follow-ups are
  11; myths 6 and 7 are 9; the `SELECT *` comparison is 6, because each rep of it
  bills 37 GiB.
- **Repetitions are interleaved** — rep 1 of every variant, then rep 2, and so
  on — so drift in the shared slot pool is spread across variants instead of
  being confounded with them. Only the 18 `m7b` jobs were also *rotated*; see
  the positional-bias caveat below.
- **The published n comes from more than one pass.** The matrix declares 3–11
  repetitions, which is 382 jobs; of the 635 published here, the core matrix was
  run twice and the follow-ups once, pooled. Every variant within a comparison is
  balanced across those passes, so no comparison is confounded by them — but the
  commands in the README reproduce one pass, which is roughly half the n and
  half the cost.
- **Result cache disabled on every job.** Verified in the records: `cache_hit`
  is `false` for all 635.
- **On-demand throughout.** Verified in the records: `reservation` is
  `ON_DEMAND` for all 635. (`edition` is null throughout, but the 617 records
  from the original run read it from the wrong REST object, so treat
  `reservation` as the load-bearing check — the client has since been
  corrected.) Billed bytes are
  therefore the bill, to within the 10 MB per-table minimum that rounds the
  smallest queries up.
- **US multi-region**, colocated with `bigquery-public-data`.
- Reported figure is the **median**, with `[min..max]` shown alongside.

## Three meters, and they disagree

Every comparison is reported against three quantities, because a rewrite can
move one and leave the others flat, and the folklore rarely says which it means:

| | what it is | who pays it |
|---|---|---|
| `slot-ms` | aggregate slot consumption summed across parallel workers | capacity (Editions) pricing |
| `elapsed-ms` | wall clock, job start to job end | the human waiting |
| `billed` | bytes scanned | on-demand pricing — **the meter for this entire run** |

They are not interchangeable, and on this data they disagree often:

| comparison | slot | wall clock | billed bytes |
|---|---:|---:|---:|
| regex predicate first vs last | 1.73x | 1.12x | 1.00x |
| `FULL OUTER` vs `INNER`, filtered join | 0.49x | 0.97x | 1.00x |
| temp table vs CTE | 1.28x | 4.63x | 1.21x |
| star join vs denormalised | 8.92x | 1.61x | 1.18x |
| `STRING` vs `INT64` join key | 1.38x | 1.02x | 1.10x |
| `REGEXP_CONTAINS` vs `=` | 1.17x | 1.35x | 1.00x |
| no `LIMIT` vs `LIMIT 10` | 3,362x | 52x | 1.00x |

So a slot-time ratio must never be read as "faster" or as "cheaper". Three of
those rows are slot-time effects that wall clock cannot detect at all, one
(the temp table) is far *worse* on wall clock than on slots, and every one of
them except two costs exactly the same number of dollars on the pricing model
this benchmark ran under. Each verdict below names its meter.

## Why slot time alone is not enough

On-demand slot time is noisy. The same query ran between 42,254 and 140,546
slot-ms across its own repetitions. Any single-run comparison on this metric is
worthless — and medians are not safe either: across the passes that make up this
run, the same variant's median moved by **11.3% on average and 28.9% at worst**.
That is larger than several of the effects reported here, which is why no
verdict rests on slot time alone.

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

Two summary counts from the 20 comparisons:

- **Identical plans *and* identical record counts: 12 of 20.**
- **Identical billed bytes: 15 of 20.** The five exceptions are the temp table,
  `SELECT *`, both key-type comparisons and denormalisation — every one of which
  changes what is *stored* or *which columns are read*. No predicate or join
  rewrite moved a single billed byte.

**Be careful how you read that second count, because 14 of those 15 are
identical by construction.** Every one of them compares spellings that touch the
same columns of the same tables, and BigQuery's on-demand meter charges for the
columns a query references. So the bytes *cannot* move, and measuring them
confirms only that no variant smuggled in a column.

That is not a weakness of the design — it is the design, and it is what licenses
the strongest claim here. If a predicate or join rewrite is incapable of moving
the meter, then on on-demand pricing its payoff is bounded at zero *a priori*,
before any timing is collected. Every "False" verdict on a predicate or join
rewrite rests on that, not on a confidence interval.

Two of the fifteen are doing more than restating the design:

- **The `LIMIT` control**, where the folklore explicitly predicts fewer bytes.
  Same columns, and the meter still did not move: 1.80 GiB either way.
- **Myth 1b's two attempts**, which are the cleanest demonstration in the whole
  run that rows read and bytes billed are different quantities. Block pruning
  cut the first attempt to 221,081 records read; defeating that pruning in m1c
  put it back to 23,020,279 — **104× the rows, for byte-for-byte the same
  1.39 GiB bill**.

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
- **Identical record counts** — the same records read and written summed across
  plan stages. Read that sum carefully: it is not rows scanned, and it
  double-counts. In myth 2 all four join types report 129,695,296 records read,
  and the four stages behind it are

  ```
  S00: Input   read 18,712,212   (users, scanned)
  S01: Input   read 46,135,386   (badges, scanned)
  S02: Join+   read 64,847,598   (both, read back off the shuffle)
  S03: Output  read        100
  ```

  so every row is counted once where it is scanned and again where the join
  reads it. The rows actually scanned are 64,847,598 — the two input tables,
  46,135,386 + 18,712,212. The figure is still good evidence, because it is the
  same figure for all four variants; it is simply not a row count.

  The join outputs genuinely differ, and `bqbench verify semantics` prints them:
  46,135,068 / 46,135,386 / 56,180,984 / 56,181,302. So myth 2's finding is that
  FULL OUTER emits ten million more rows for the same cost, which is a stronger
  result than "the keyword does nothing" — but it is not a claim that the four
  queries are equivalent.
- **Identical billed bytes** — the on-demand meter. True in 15 of 20
  comparisons.

## "No difference detected" is not "no difference"

This is the easiest way for a benchmark to overstate itself, so it is worth
being explicit. Failing to reject a null hypothesis is not evidence for it. A
comparison reporting p = 0.38 has not shown two spellings are the same; it has
shown that *this* experiment, at *this* sample size, did not separate them.

So every ratio carries a **percentile bootstrap 95% confidence interval**
(`bqbench/stats.py`, seeded and reproducible), and that interval — not the
p-value — is what a comparison actually establishes:

| comparison | ratio | 95% CI | what can honestly be said |
|---|---|---|---|
| filter order | 0.999 | 0.92 – 1.08 | no difference larger than ~8% |
| join type, LEFT vs INNER | 0.991 | 0.92 – 1.08 | no difference larger than ~8% |
| DISTINCT vs GROUP BY, 127 groups | 1.077 | 0.82 – 1.34 | **no difference larger than ~35%** |
| join order, two tables | 1.036 | 0.88 – 1.17 | no difference larger than ~17% |
| `CAST()` in the join clause | 1.037 | 0.89 – 1.18 | no difference larger than ~18% |

A ±35% bound is not "identical", and this document does not claim it is.

**What carries the "False" verdicts is therefore not the timing.** It is the
structural evidence: where two spellings compile to the same plan, read and
write the same number of records, and bill the same bytes, they are the same
query, and no confidence interval is needed to say so. The timing then bounds
whatever residual could remain. Filter order, join type, DISTINCT/GROUP BY at
two of three cardinalities, CTE-vs-subquery and two-table join order all have
that structural identity. Where it is absent — high-cardinality and
multi-column DISTINCT, three-table join order — the plans varied run to run, so
no plan claim is made at all and the verdict rests on billed bytes plus timing.
The interval is shown, and the text says so.

Eleven ratios have a CI excluding 1.0, and every result reported as **real**
is one of them.

## Multiple comparisons

The matrix runs **26 pairwise tests** and applies **no correction for multiple
comparisons**. At α = 0.05 that means about one false positive is expected
across the set. This is why no result rests on a p-value alone:

- The findings reported as real on timing (myths 1b, 4's temp table, 6, 7, the
  `LIMIT` control and myth 2's filtered join) all sit at p ≤ 0.005 *and* are
  corroborated by plan or byte evidence that does not depend on timing at all.
  The `SELECT *` control is the exception and is not in that list: its slot-time
  p is 0.045 and nothing rests on it — see the third bullet.
- The weakest surviving claim is `REGEXP_CONTAINS` at p = 0.005 and a 1.17×
  effect. Under a Bonferroni correction across 26 tests (α = 0.002) it would not
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

**Query plans are not fully stable across repetitions.** In **7 of the 20
comparisons**, at least one variant produced more than one distinct stage
sequence across its own repetitions. Two mechanisms account for all of it, and
both are BigQuery runtime adaptivity rather than anything in the SQL:

1. *Dynamic repartitioning.* The high-cardinality `DISTINCT`/`GROUP BY`
   comparison produced 5-, 6- and 7-stage plans for the same SQL depending on
   the run; the three-table join order produced 7 or 8.
2. *Single-stage collapse.* Fast queries sometimes execute as one `S00: Output`
   stage instead of `Input → Aggregate → Output`. This is usually symmetric
   across the variants, but **not always, and where it is not it biases the
   ratio.** In the predicate-cost trio `=` collapsed to one stage in 14 of 18
   runs and `REGEXP_CONTAINS` in only 8 of 18 — so part of that comparison's
   1.17x is the plan shape rather than the predicate. Comparing only the
   single-stage runs of each gives **1.15x**, which is the figure to trust.

The affected comparisons are: high-cardinality DISTINCT, multi-column DISTINCT,
CTE-vs-subquery, three-table join order, the predicate-cost trio,
`SELECT *`, and denormalisation. **No plan-identity claim is made for those** —
RESULTS.md prints "plans not comparable" rather than a verdict it would then
have to retract.

Where a plan *is* shown for such a comparison it is the variant's **modal** stage
sequence, not its first repetition's. That distinction is not cosmetic: on this
data, reading the plan off an arbitrary repetition instead of the mode flips the
identity verdict in four of the twenty comparisons. The most consequential is
high-cardinality `DISTINCT`, where the first repetitions happened to differ but
the two spellings in fact produce the *same distribution* of plan shapes —
5/6/7 stages in 6/10/2 runs for `DISTINCT` against 6/11/1 for `GROUP BY`. The
plans do not diverge at 8.5M groups; they jitter, identically, for both.

**The schedule was checked for positional bias, and most of the published run
was not rotated.** The runner rotates variant order per repetition, but the
original 617 jobs predate that and ran their variants in declared order every
repetition. Only the 18 `m7b` jobs were measured under rotation. So the check
matters.

`bqbench verify reproducibility` measures it from the order the jobs actually
ran in, dividing each job by its own variant's median so only position is left,
and reports the spread across schedule slots: **1.054**, i.e. where a query sat
moved its slot time by about 6%. It fails above 1.10. That is a real effect and
it is worth knowing, but it is an order of magnitude below the differences this
benchmark calls real, and it applies to every variant in turn rather than to one
consistently.

It is a bound, not an absolution. The estimator averages over every comparison,
so a bias confined to one of them is diluted, and at n = 6–18 only a systematic
effect of a few percent or more will surface at all.

**Three-table join order is directional only.** The 0.968 ratio runs opposite
the folklore, but at p = 0.24 it is not a significant result. It is reported as
"not the direction the advice predicts", not as a measured win.

**The temp-table storage charge was not measured, and is negligible.** A
`CREATE TEMP TABLE` inside a multi-statement query lives only as long as the
script — a median of 5.6 s here, for a 24 MiB table. Prorated at
$0.020/GiB/month that is about $1×10⁻⁹, six orders of magnitude below the
$0.0025 the query itself cost. Even charged for a full 24 hours it stays two
orders below. It is left unmeasured because it cannot change the conclusion,
not because it might.

## Reproducing

`python3 -m bqbench estimate` dry-runs every variant and prints the bill before
anything is billed.

`results/` is generated by a run, not committed — see the README. A run writes
one JSON record per job with the complete query plan, which is what every number
above is computed from. `python3 -m bqbench verify reproducibility` checks a
`results/` directory against the matrix that claims to describe it.
