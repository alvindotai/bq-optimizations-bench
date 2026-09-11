# bq-optimizations-bench

**Benchmarks for the BigQuery query-optimization advice that gets repeated as fact.**

Seven pieces of folklore — the kind that consistently ranks at the top of search
results — each run as two or more spellings of the exact same question, measured
over **622 query jobs** against `bigquery-public-data.stackoverflow`.

Most produced byte-for-byte identical execution. The ones that did make a
difference rarely worked for the reasons people claim. And notice where they
cluster: almost all of the busted ones live in the predicate or join layer,
which is exactly where the financial payoff is smallest — **billed bytes were
identical in 15 of the 19 comparisons**, so on on-demand pricing those rewrites
have an arithmetically bounded payoff of zero.

Everything runs on a public dataset. You can reproduce the whole thing for about
**$4**.

---

## The verdicts

| Claim | Verdict | What the jobs said |
|---|---|---|
| Order your filters most-eliminating first | **False** | Identical plans over an 892× selectivity range, p = 0.96 |
| …or the expensive function runs on every row | **True, wrong reason** | 1.73× with a regex, nothing with two cheap predicates. Evaluation cost, not selectivity |
| INNER JOIN beats LEFT, which beats OUTER | **False** | All four types cost the same, within 3.3%. FULL OUTER fastest where results differ |
| Prefer DISTINCT over GROUP BY | **False** | Three cardinalities up to 8.5M groups, identical bytes, p ≥ 0.38 |
| Avoid CTEs, use temp tables | **False, and backwards** | CTE and subquery identical at three references. The temp table was worst, 1.28× slower |
| Start your joins with the largest table | **False** | Ratio 1.036 (p = 0.86); three tables runs opposite the advice, though not significantly |
| Denormalise for sub-second latency | **True, and mispriced** | 8.9× cheaper per query, but the refresh cost binds — break-even is 9–14 queries per rebuild |
| Cast string keys to INT64 | **True, modest** | 1.38× slower on STRING, but `CAST()` in the join clause matched native INT64 |

Three further claims measured as controls:

| Claim | Verdict | What the jobs said |
|---|---|---|
| `LIMIT` reduces bytes scanned | **False** | Billed bytes identical at 1.80 GiB — while slot time drops 3,362× |
| `SELECT *` costs more than naming columns | **True** | 780 MiB vs 37.17 GiB — 48.8× |
| `REGEXP_CONTAINS` is slower than `=` | **True, small** | 1.17× (p = 0.005). Real, but not the cliff the phrasing implies |

The `LIMIT` row is the most useful single measurement here: the same rewrite is
worth **nothing** on on-demand and **enormous** on capacity pricing. Which meter
you are on decides whether an optimization is worth doing at all.

Per-comparison numbers: **[RESULTS.md](RESULTS.md)**.
How it was measured and what that does and doesn't support:
**[METHODOLOGY.md](METHODOLOGY.md)**.

---

## How it works

Each claim becomes a set of **variants**: SQL that differs only in the thing the
folklore says matters. Where a pair touches identical columns the bytes are
identical by construction, so any cost difference has to come from the spelling
rather than from the work.

The hard part is that **slot time is a bad metric on its own**. On a shared
on-demand pool the same query ran between 42,254 and 140,546 slot-ms across its
own repetitions. Any single-run comparison is worthless, and even medians move a
few percent between passes.

So every comparison also records **deterministic work metrics** read out of the
query plan — the stage sequence, records read, records written, shuffle bytes.
These don't move with slot availability. When two spellings produce the same
stages and the same record counts, they are executing the same query. That is
the evidence; timing is corroboration.

Three design choices follow from that:

1. **Repetitions are interleaved** — rep 1 of every variant, then rep 2, and so
   on. Running all of A then all of B would attribute the weather to the SQL.
2. **The result cache is off on every job.** A cached job bills nothing and
   reports no plan, which would silently void the measurement.
3. **Plan stability is checked per variant**, not assumed. In 7 of 19
   comparisons BigQuery's runtime adaptivity produced more than one stage
   sequence for the *same* SQL across repetitions. Those comparisons make no
   plan-identity claim. See [METHODOLOGY.md](METHODOLOGY.md#caveats).

---

## Running it

Python 3.9+ and the `gcloud` CLI. **No third-party packages** — the standard
library is a deliberate constraint, not an accident: `google-cloud-bigquery`
summarises away the per-stage query plan this benchmark argues from, so
`bqbench/client.py` talks to the REST API directly.

```bash
gcloud auth login
export BENCH_PROJECT=my-gcp-project     # billed for the jobs
export BENCH_DATASET=bq_myth_bench      # optional; created by `bqbench fixtures`
export BENCH_LOCATION=US                # must colocate with bigquery-public-data
```

Everything is one command with subcommands. No install step needed:

```bash
python3 -m bqbench --help
```

(or `pip install -e .` to get a `bqbench` binary on your PATH.)

**Price it before you run it.** Dry runs are free:

```bash
python3 -m bqbench estimate --matrix all
```

Then:

```bash
python3 -m bqbench fixtures                 # ~$0.01, builds the myth 6/7 tables
python3 -m bqbench run --matrix core        # -> results/core.jsonl
python3 -m bqbench run --matrix followups   # -> results/followups.jsonl
python3 -m bqbench rebuild                  # myth 6's refresh cost
python3 -m bqbench analyze                  # reads every results/*.jsonl
python3 -m bqbench economics
python3 -m bqbench results-doc              # regenerates RESULTS.md
```

Expect the core matrix to take roughly 25 minutes; it is dominated by BigQuery
job latency, not by anything local.

Drop `$BENCH_DATASET` when you are finished — the fixtures are about 5 GiB.

### `results/` is generated, not committed

A run writes to `results/`, which is gitignored — the repository ships the
**code** and the **findings** ([RESULTS.md](RESULTS.md), regenerated from a run
and never hand-edited), not a data dump.

**The original 617 job records are published as a release asset:**
[`bq-myth-bench-results-v1.0.tar.gz`][release]. Every number in RESULTS.md is
computed from them, and they carry the complete query plan for each job.

[release]: https://github.com/alvindotai/bq-optimizations-bench/releases/latest

```bash
mkdir -p results
curl -sL https://github.com/alvindotai/bq-optimizations-bench/releases/latest/download/bq-myth-bench-results-v1.0.tar.gz \
  | tar -xz -C results

python3 -m bqbench verify reproducibility    # checks them against the matrix
python3 -m bqbench analyze                   # rebuilds the summary
python3 -m bqbench economics                 # rebuilds the break-even
```

Neither of those needs a GCP project or any credentials. If the repository is
still private, the anonymous URL above returns 404 — use
`gh release download v1.0 --repo alvindotai/bq-optimizations-bench -p '*.tar.gz'`
instead.

Each record is one JSON object per job, carrying the billing metrics plus
`plan_steps` and `plan_records` — the stage sequence and per-stage records
read / written / shuffled. `plan_records` is the load-bearing evidence: slot
time varied by 3× for identical work; record counts did not.

Set `BENCH_RESULTS` to put a run's output somewhere else.

### Checking the claims rather than trusting them

```bash
python3 -m bqbench verify semantics        # ~$0.05, needs credentials
python3 -m bqbench verify reproducibility  # free, no credentials
python3 -m bqbench check-leaks             # free, no credentials
```

`verify semantics` answers the obvious objection — that the variants aren't
really equivalent, or are so equivalent the engine folds them together. It shows
that the four join types return genuinely different row counts (46,135,068 /
46,135,386 / 56,180,984 / 56,181,302), while the filter-order, DISTINCT and CTE
pairs each return identical answers.

`verify reproducibility` asserts that `bqbench/matrix.py` still generates every
query in your `results/`, byte-for-byte, and that the documented run conditions
(cache off, on-demand, 6–18 reps) actually hold in the data. It catches the
failure mode specific to benchmark repos: the code drifts, the recorded results
stay, and the two quietly stop describing each other.

Both free checks run with **no GCP project set at all** — SQL rendering falls
back to a placeholder project, while anything that bills refuses to start
without `BENCH_PROJECT`.

---

## Layout

```
bqbench/
  matrix.py       every claim, its source, and its variants - start here
  client.py       BigQuery REST client (submit, poll, full job statistics)
  analysis.py     job records -> per-comparison summary
  stats.py        Mann-Whitney U with tie correction
  config.py       project / dataset / location, all from environment
  paths.py        where things live on disk
  commands/       one module per subcommand
sql/
  fixtures.sql    the two fixture tables, derived from the same public data
```

`matrix.py` is the file to read first — it is the entire experiment, and if a
myth is framed unfairly it is visible there.

---

## Licence

Apache 2.0. See [LICENSE](LICENSE).
