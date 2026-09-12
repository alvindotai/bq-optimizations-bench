"""Run a matrix and write one JSON record per job.

Two scheduling rules, both there to stop the measurement picking up something
other than the SQL:

  interleaved   rep 1 of every variant, then rep 2, and so on. Running all of A
                and then all of B would attribute drift in the shared slot pool
                to the query.
  rotated       the variant order inside a comparison shifts by one each rep, so
                no variant is always measured first. `bqbench verify
                reproducibility` checks the recorded runs for the positional
                bias this removes.
"""
import datetime
import json

from .. import client, matrix, paths
from ..config import LOCATION, require_project

HELP = "run a matrix of comparisons"


def add_arguments(p):
    p.add_argument("--matrix", default="core", choices=sorted(matrix.MATRICES))
    p.add_argument("--out", help="output JSONL (default: results/<matrix>.jsonl)")
    p.add_argument("--only", nargs="*", metavar="KEY",
                   help="restrict to these myth keys")


def main(args):
    project = require_project()
    myths = matrix.select(args.matrix)
    if args.only:
        myths = [m for m in myths if m["key"] in args.only]
        if not myths:
            raise SystemExit(f"no myths matched {args.only}")

    out = args.out or str(paths.ensure_results_dir() / f"{args.matrix}.jsonl")
    schedule = interleave(myths)
    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"run_id={run_id}  matrix={args.matrix}  jobs={len(schedule)}  -> {out}",
          flush=True)

    done = failed = 0
    with open(out, "a") as fh:
        for i, (key, variant, sql, rep) in enumerate(schedule, 1):
            prefix = f"[{i:3}/{len(schedule)}] {key:28} {variant:22} rep{rep:<3}"
            try:
                record = client.run(sql, project, location=LOCATION,
                                    labels={"bq_myth_bench": "myth",
                                            "myth": key.replace("_", "-")[:63]})
            except Exception as exc:  # noqa: BLE001 - one failed job must not abandon a 300-job run
                failed += 1
                print(f"{prefix} FAILED: {str(exc)[:160]}", flush=True)
                continue
            record.update(run_id=run_id, myth=key, variant=variant, rep=rep, sql=sql,
                          ts=datetime.datetime.now(datetime.timezone.utc).isoformat())
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            done += 1
            print(f"{prefix} slot={record['slot_ms']:>9,}ms  "
                  f"billed={record['bytes_billed']/2**30:7.2f}GiB  "
                  f"elapsed={record['elapsed_ms']:>6,}ms", flush=True)

    print(f"\ndone={done} failed={failed}  -> {out}")
    return 1 if failed else 0


def interleave(myths):
    """[(key, variant, sql, rep), ...] ordered rep-major, variants rotated."""
    schedule = []
    for rep in range(1, max(m["reps"] for m in myths) + 1):
        for m in myths:
            if rep > m["reps"]:
                continue
            variants = m["variants"]
            shift = (rep - 1) % len(variants)
            for variant, sql in variants[shift:] + variants[:shift]:
                schedule.append((m["key"], variant, sql, rep))
    return schedule
