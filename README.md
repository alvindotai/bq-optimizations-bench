# bq-optimizations-bench

**Benchmarks for the BigQuery query-optimization advice that gets repeated as fact.**

Seven pieces of folklore — the kind that consistently ranks at the top of search
results — each run as two or more spellings of the exact same question, measured
over **640 query jobs** against `bigquery-public-data.stackoverflow`.

Most produced byte-for-byte identical execution. The ones that did make a
difference rarely worked for the reasons people claim, and several of them move
a meter nobody is billed for.

That last point is the one to take away, and it is worth stating precisely.
**Billed bytes were identical in 15 of the 20 comparisons** — and 14 of those 15
were identical *by construction*, because the two spellings touch the same
columns and BigQuery's on-demand meter charges for columns referenced.

That is not a disappointing result; it is the whole argument. If a predicate or
join rewrite is structurally incapable of moving the meter, then on on-demand
pricing its **cost** payoff is bounded at zero before you measure anything at
all. No confidence interval required. What the timing then adds is only how much slot
time you are moving around for no money — and on capacity pricing, that is
money. Which meter you are on decides whether an optimization is worth doing.

The two that are more than a restatement of the design: `LIMIT`, where the
folklore predicts fewer bytes and the bill did not move; and myth 1b, where
block pruning cut the rows read by **104×** — 221,081 against 23,020,279 — for
byte-for-byte the same 1.39 GiB. Rows read and bytes billed are not the same
quantity, and only one of them is on your invoice.

Everything runs on a public dataset. You can reproduce the whole thing for about
**$4**.

**Scope.** One dataset, one region, one pricing model, and tables that are
**neither partitioned nor clustered**. Several of these claims behave differently
on a partitioned or clustered table — which is where most production tables live
— so read every verdict below as scoped to that setting, not as universal.

---

## The verdicts

| Claim | Verdict | What the jobs said |
|---|---|---|
| Order your filters most-eliminating first | **False** | Four predicates reversed across an 892× selectivity range: same plan, same records, same 1.11 GiB billed. Slot time within ±8%, wall clock within 7% |
| …or the expensive function runs on every row | **True, wrong reason** | 1.73× the *slot time* with a regex (CI 1.69–1.96), 1.12× the wall clock, no change in the bill. Nothing with two cheap predicates. Evaluation cost, not selectivity |
| INNER JOIN beats LEFT, which beats OUTER | **False** | All four compile to the same plan, scan the same 64,847,598 rows for the same 638 MiB, and finish in the same wall clock. Where results differ, FULL OUTER used half the slots for the same elapsed time |
| Prefer DISTINCT over GROUP BY | **False** | Same plan, records and bytes at 127 groups. At ~5M and 8.5M BigQuery's plan varies run to run — identically for both spellings — and the bytes never move. No timing difference at any of the three |
| Avoid CTEs, use temp tables | **False, and backwards** | CTE and subquery do identical work at three references. The temp table was worst — 1.28× the slots (CI 1.09–1.44), **4.6× the wall clock**, 420 vs 348 MiB. The cost is the write: the CTAS burns 51,918 slot-ms, all three read-backs 268 |
| Start your joins with the largest table | **False** | Same plan, same total work, same bytes on two tables. On three it runs opposite the advice, not significantly |
| Denormalise for sub-second latency | **Half true, and mispriced** | 8.9× less *slot time*, but only **1.61× less wall clock and 1.18× cheaper on the bill** — and neither query was sub-second. The refresh cost binds: break-even is 9–14 queries per rebuild |
| Cast string keys to INT64 | **True — and `CAST` is not a substitute** | 1.38× the slot time on STRING (CI 1.20–1.58) but the **same wall clock**; the real cost is bytes, 458 → 504 MiB. Casting both sides at query time recovers 73% of the slot penalty (1.38× → 1.10×) and **none of the bytes** — still 504 MiB. A `CAST` cannot shrink what you scan, so on on-demand it buys nothing. Migrate the column |

Three further claims measured as controls:

| Claim | Verdict | What the jobs said |
|---|---|---|
| `LIMIT` reduces bytes scanned | **False** | Billed bytes identical at 1.80 GiB — while slot time drops 3,362× and wall clock 52×. The slots went on shuffling and materialising 23M rows, not on scanning |
| `SELECT *` costs more than naming columns | **True** | 780 MiB for the two columns wanted vs 37.17 GiB for all twenty — 48.8× |
| `REGEXP_CONTAINS` is slower than `=` | **True, small** | 1.17× on slots (CI 1.07–1.48), 1.15× once plan-shape jitter is controlled for. Real, but not the cliff the phrasing implies |

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

The hard part is that **slot time is a bad metric on its own**, in two separate
ways. It is noisy — on a shared on-demand pool the same query ran between 42,254
and 140,546 slot-ms across its own repetitions, so any single-run comparison is
worthless. And it is not what most of the folklore is about: slot-ms is
aggregate slot consumption across parallel workers, which is what *capacity*
pricing bills. On the on-demand pricing this whole run used, you pay for bytes
and slot time is free. So every comparison is reported against all three meters —
slot-ms, wall clock, billed bytes — and no ratio is called "faster" or "cheaper"
without naming which one moved. They disagree more often than you would expect:
the temp table is 1.28× on slots and 4.6× on the clock, while the STRING join
key is 1.38× on slots and 1.00× on the clock.

So every comparison also records the **work metrics** in the query plan — the
stage sequence, records read, records written, shuffle bytes. These don't move
with slot availability, which is what makes them better evidence than timing:
when two spellings produce the same stages and the same record counts, they are
executing the same query. That is the evidence; timing is corroboration.

They are not constant either, though, so the analysis checks rather than
assumes. A query behind a `LIMIT` short-circuits at a different point every run;
BigQuery re-partitions shuffles between runs; and a fast query sometimes
collapses to a single stage, which changes how many stages report reads without
changing the work. Each comparison therefore carries two stability flags, and an
identity claim is only made where the underlying figure was actually stable.

One caution about `records read`: it is a **sum over plan stages**, so a row
scanned and then read back off a shuffle is counted twice. It is good evidence —
the same number for two variants means the same work — but it is not a row
count. Myth 2's 129,695,296 is 64,847,598 rows, counted at two stages.

**And "no difference detected" is not "no difference".** Every ratio carries a
bootstrap confidence interval, because a p-value says whether a difference was
found and nothing about how large one could have hidden. At n = 6–18 that bound
is often ±20%, so several of the "False" verdicts above rest on the structural
evidence — same plan, same records, same billed bytes — with the timing only
bounding a residual. Where a verdict rests on timing alone, RESULTS.md shows the
interval and the text says so.

Three design choices follow from that:

1. **Repetitions are interleaved** — rep 1 of every variant, then rep 2, and so
   on. Running all of A then all of B would attribute the weather to the SQL.
   The runner now also **rotates** variant order each rep so no query is always
   measured first; most of the published run predates that, so
   `verify reproducibility` measures the positional effect from the recorded
   execution order instead. It is 1.054 — position moved slot time by about 6%,
   an order of magnitude below anything reported here as real.
2. **The result cache is off on every job.** A cached job bills nothing and
   reports no plan, which would silently void the measurement.
3. **Stability is checked per variant**, not assumed. In 7 of 20 comparisons
   BigQuery produced more than one stage sequence for the *same* SQL, and
   in 10 the record counts moved between runs. Those comparisons print "plans not
   comparable" rather than a verdict, and where a plan is shown at all it is the
   variant's *modal* sequence — reading it off an arbitrary repetition instead
   flips the verdict in four of the twenty. See
   [METHODOLOGY.md](METHODOLOGY.md#caveats).

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
python3 -m bqbench fixtures                 # ~$0.04, builds the myth 6/7 tables
python3 -m bqbench run --matrix core        # -> results/core.jsonl
python3 -m bqbench run --matrix followups   # -> results/followups.jsonl
python3 -m bqbench rebuild                  # myth 6's refresh cost
python3 -m bqbench analyze                  # reads every results/*.jsonl
python3 -m bqbench economics
python3 -m bqbench results-doc              # regenerates RESULTS.md
```

Expect the core matrix to take roughly 16 minutes; it is dominated by BigQuery
job latency, not by anything local. Note that these commands run the matrix
**once** — 382 jobs. Of the published 635, the core matrix was run twice and the
follow-ups once, pooled, so reproducing the published n means running the core
matrix a second time.

Drop `$BENCH_DATASET` when you are finished — the fixtures are 5.9 GiB, which is
$0.118/month of storage.

### `results/` is generated, not committed

A run writes to `results/`, which is gitignored — the repository ships the
**code** and the **findings** ([RESULTS.md](RESULTS.md), regenerated from a run
and never hand-edited), not a data dump.

**All 635 job records are published as a release asset:**
[`bq-myth-bench-results-v1.1.0.tar.gz`][release]. Every number in RESULTS.md is
computed from them, and they carry the complete query plan for each job.

[release]: https://github.com/alvindotai/bq-optimizations-bench/releases/latest

```bash
mkdir -p results
curl -sL https://github.com/alvindotai/bq-optimizations-bench/releases/latest/download/bq-myth-bench-results-v1.1.0.tar.gz \
  | tar -xz -C results

python3 -m bqbench verify reproducibility    # checks them against the matrix
python3 -m bqbench analyze                   # rebuilds the summary
python3 -m bqbench economics                 # rebuilds the break-even
```

Neither of those needs a GCP project or any credentials. If the repository is
still private, the anonymous URL above returns 404 — use
`gh release download v1.1.0 --repo alvindotai/bq-optimizations-bench -p '*.tar.gz'`
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

CI runs the unit tests and the leak gate on every push and pull request, on
Python 3.9 and 3.13, along with `ruff check`. It also asserts that the leak gate
still catches a planted credential — a gate nobody tests is a gate nobody can
trust. `verify reproducibility` is **not** in CI and cannot be: it needs
`results/`, which is generated rather than committed. Run it yourself against a
downloaded release, as below.

`verify semantics` answers the obvious objection — that the variants aren't
really equivalent, or are so equivalent the engine folds them together. It shows
that the four join types return genuinely different row counts (46,135,068 /
46,135,386 / 56,180,984 / 56,181,302), while the filter-order, DISTINCT and CTE
pairs each return identical answers.

`verify reproducibility` asserts that `bqbench/matrix.py` still generates every
query in your `results/`, byte-for-byte; that the documented run conditions hold
in the data (cache off, on-demand, 6–18 reps, equal n per variant within a
comparison); and that the schedule is not biasing results — it measures, from
the order the jobs actually ran in, how much a query's position moved its slot
time, and fails above 1.10. It catches the failure mode specific to benchmark
repos: the code drifts, the recorded results stay, and the two quietly stop
describing each other.

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
