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
| Order your filters most-eliminating first | **False** | Byte-identical execution across an 892× selectivity range: same plan, same 23,020,279 records, same 1.11 GiB billed. Slot time within ±8% |
| …or the expensive function runs on every row | **True, wrong reason** | 1.73× with a regex (CI 1.69–1.96), nothing with two cheap predicates. Evaluation cost, not selectivity |
| INNER JOIN beats LEFT, which beats OUTER | **False** | All four compile to the same plan and read the same 129,695,296 records for the same 638 MiB. No pairwise timing difference detected; FULL OUTER fastest where results differ |
| Prefer DISTINCT over GROUP BY | **False** | Same plan, records and bytes at 127 groups and at ~5M. At 8.5M the plans differ but the bytes don't. No timing difference detected at any of the three |
| Avoid CTEs, use temp tables | **False, and backwards** | CTE and subquery do identical work at three references. The temp table was worst — 1.28× slower (CI 1.10–1.44) and 420 vs 348 MiB |
| Start your joins with the largest table | **False** | Same plan, same 83,006,916 records, same bytes on two tables. On three it runs opposite the advice, not significantly |
| Denormalise for sub-second latency | **True, and mispriced** | 8.9× cheaper per query (CI 8.5–11.1×), but the refresh cost binds — break-even is 9–14 queries per rebuild |
| Cast string keys to INT64 | **True, modest** | 1.38× slower on STRING (CI 1.21–1.58). `CAST()` in the join clause was indistinguishable from native INT64 |

Three further claims measured as controls:

| Claim | Verdict | What the jobs said |
|---|---|---|
| `LIMIT` reduces bytes scanned | **False** | Billed bytes identical at 1.80 GiB — while slot time drops 3,362× |
| `SELECT *` costs more than naming columns | **True** | 780 MiB vs 37.17 GiB — 48.8× |
| `REGEXP_CONTAINS` is slower than `=` | **True, small** | 1.17× (CI 1.08–1.48). Real, but not the cliff the phrasing implies |

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

So every comparison also records the **work metrics** in the query plan — the
stage sequence, records read, records written, shuffle bytes. These don't move
with slot availability, which is what makes them better evidence than timing:
when two spellings produce the same stages and the same record counts, they are
executing the same query. That is the evidence; timing is corroboration.

They are not constant either, though, so the analysis checks rather than
assumes. A query that short-circuits — anything with a `LIMIT` — reads a
different number of records every run, and BigQuery re-partitions shuffles from
run to run. Each comparison therefore carries two stability flags, and an
identity claim is only made where the underlying figure was actually stable.

**And "no difference detected" is not "no difference".** Every ratio carries a
bootstrap confidence interval, because a p-value says whether a difference was
found and nothing about how large one could have hidden. At n = 6–18 that bound
is often ±20%, so several of the "False" verdicts above rest on the structural
evidence — same plan, same records, same billed bytes — with the timing only
bounding a residual. Where a verdict rests on timing alone, RESULTS.md shows the
interval and the text says so.

Three design choices follow from that:

1. **Repetitions are interleaved and rotated** — rep 1 of every variant, then
   rep 2, and so on, with the variant order inside a comparison shifting each
   rep. Running all of A then all of B would attribute the weather to the SQL;
   always running A first would let a warm-up effect masquerade as one.
2. **The result cache is off on every job.** A cached job bills nothing and
   reports no plan, which would silently void the measurement.
3. **Stability is checked per variant**, not assumed. In 7 of 19 comparisons
   BigQuery produced more than one stage sequence for the *same* SQL, and in 10
   the record counts moved between runs. Those comparisons make no
   corresponding identity claim. See [METHODOLOGY.md](METHODOLOGY.md#caveats).

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
[`bq-myth-bench-results-v1.0.1.tar.gz`][release]. Every number in RESULTS.md is
computed from them, and they carry the complete query plan for each job.

[release]: https://github.com/alvindotai/bq-optimizations-bench/releases/latest

```bash
mkdir -p results
curl -sL https://github.com/alvindotai/bq-optimizations-bench/releases/latest/download/bq-myth-bench-results-v1.0.1.tar.gz \
  | tar -xz -C results

python3 -m bqbench verify reproducibility    # checks them against the matrix
python3 -m bqbench analyze                   # rebuilds the summary
python3 -m bqbench economics                 # rebuilds the break-even
```

Neither of those needs a GCP project or any credentials. If the repository is
still private, the anonymous URL above returns 404 — use
`gh release download v1.0.1 --repo alvindotai/bq-optimizations-bench -p '*.tar.gz'`
instead.

Each record is one JSON object per job, carrying the billing metrics plus
`plan_steps` and `plan_records` — the stage sequence and per-stage records
read / written / shuffled. `plan_records` is the load-bearing evidence: slot
time varied by 3× for identical work; record counts did not.

Set `BENCH_RESULTS` to put a run's output somewhere else.

### Checking the claims rather than trusting them

```bash
python3 -m unittest discover -s tests -t .  # unit tests: no network, no credentials
python3 -m bqbench verify reproducibility   # free, no credentials
python3 -m bqbench check-leaks              # free, no credentials
python3 -m bqbench verify semantics         # ~$0.05, needs credentials
```

CI runs the first three on every push and pull request, on Python 3.9 and 3.13,
along with `ruff check`. It also asserts that the leak gate still catches a
planted credential — a gate nobody tests is a gate nobody can trust.

`verify semantics` answers the obvious objection — that the variants aren't
really equivalent, or are so equivalent the engine folds them together. It shows
that the four join types return genuinely different row counts (46,135,068 /
46,135,386 / 56,180,984 / 56,181,302), while the filter-order, DISTINCT and CTE
pairs each return identical answers.

`verify reproducibility` asserts that `bqbench/matrix.py` still generates every
query in your `results/`, byte-for-byte, that the documented run conditions
(cache off, on-demand, 6–18 reps) actually hold in the data, and that the
schedule is not biasing results — it reports how much faster variants measured
second were, and fails outside ±5%. It catches the
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
