"""A small BigQuery REST client, for benchmarking.

Deliberately not `google-cloud-bigquery`: the client library summarises job
statistics, and this benchmark's load-bearing evidence is the per-stage query
plan (`recordsRead`, `recordsWritten`, `shuffleOutputBytes`). Talking to the
REST API directly keeps all of it. Standard library only.

Auth is the caller's `gcloud auth print-access-token`.
"""
import contextlib
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid

API = "https://bigquery.googleapis.com/bigquery/v2"

# gcloud access tokens last ~60 minutes; re-shell every 30 so a long run never
# carries an expiring token into a request.
_TOKEN_TTL_S = 1800
_token = {"value": None, "fetched_at": 0.0}

RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 5


class AlreadyExistsError(RuntimeError):
    """A client-assigned job id was submitted twice - the first one landed."""


def token():
    if _token["value"] is None or time.time() - _token["fetched_at"] > _TOKEN_TTL_S:
        # `gcloud` is resolved from PATH by design: users install it wherever
        # their platform puts it, and pinning an absolute path would break that.
        _token["value"] = subprocess.run(
            ["gcloud", "auth", "print-access-token"],  # noqa: S607
            capture_output=True, text=True, check=True).stdout.strip()
        _token["fetched_at"] = time.time()
    return _token["value"]


def _request(method, url, body=None):
    # Every caller builds on API, but assert it: urlopen would happily follow a
    # file:// or custom scheme if one ever reached here.
    if not url.startswith(API):
        raise ValueError(f"refusing to open non-BigQuery URL: {url[:80]}")
    payload = json.dumps(body).encode() if body is not None else None
    for attempt in range(MAX_ATTEMPTS):
        req = urllib.request.Request(url, data=payload, method=method)
        req.add_header("Authorization", "Bearer " + token())
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            if exc.code == 409:
                raise AlreadyExistsError(detail[:500]) from exc
            raise RuntimeError(
                f"HTTP {exc.code} on {method} {url}: {detail[:2000]}") from exc
        except urllib.error.URLError:
            if attempt < MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise AssertionError("unreachable")


def create_dataset(project, dataset, location="US", description=None):
    """Create a dataset, treating "already exists" as success.

    Returns True if this call created it. Free.
    """
    body = {"datasetReference": {"projectId": project, "datasetId": dataset},
            "location": location}
    if description:
        body["description"] = description
    try:
        _request("POST", f"{API}/projects/{project}/datasets", body)
    except AlreadyExistsError:
        return False
    return True


def dry_run(sql, project, location="US"):
    """Bytes BigQuery says it would read. Free, and the honest way to price a
    run before making it. Note this is bytes *processed*; billing applies a
    10 MB per-table minimum, so tiny queries bill slightly more than this."""
    body = {"configuration": {"dryRun": True,
                              "query": {"query": sql, "useLegacySql": False,
                                        "useQueryCache": False}},
            "jobReference": {"projectId": project, "location": location}}
    stats = _request("POST", f"{API}/projects/{project}/jobs", body)["statistics"]
    return {"bytes_processed": int(stats["query"].get("totalBytesProcessed", 0))}


def run(sql, project, location="US", labels=None, timeout=1800):
    """Submit one query job, wait for it, and return its full statistics.

    The query cache is always off: a cached job bills nothing and reports no
    plan, which would silently void the measurement.
    """
    job_id = f"bqmythbench_{uuid.uuid4().hex[:20]}"
    config = {"query": {"query": sql, "useLegacySql": False,
                        "useQueryCache": False, "priority": "INTERACTIVE"}}
    if labels:
        config["labels"] = labels
    # The job id is client-assigned, so a retried insert whose first attempt
    # actually landed is not a duplicate submission - the job is already
    # running. Poll it rather than discarding a good measurement.
    with contextlib.suppress(AlreadyExistsError):
        _request("POST", f"{API}/projects/{project}/jobs",
                 {"configuration": config,
                  "jobReference": {"projectId": project, "jobId": job_id,
                                   "location": location}})

    url = f"{API}/projects/{project}/jobs/{job_id}?location={location}"
    deadline = time.time() + timeout
    while True:
        job = _request("GET", url)
        if job.get("status", {}).get("state") == "DONE":
            break
        if time.time() > deadline:
            raise RuntimeError(f"timed out after {timeout}s waiting for {job_id}")
        time.sleep(1.0)

    failure = job.get("status", {}).get("errorResult")
    if failure:
        raise RuntimeError(f"job {job_id} failed: {failure}")

    return _statistics(job, job_id, project, location)


def _statistics(job, job_id, project, location):
    stats = job.get("statistics", {})
    query = stats.get("query", {})
    plan = query.get("queryPlan") or []

    children = _script_children(query, stats, job_id, project, location)
    if children:
        # A multi-statement script's own statistics are a shell; the work lives
        # in child jobs. Roll them up so a script is comparable with a single
        # statement. Scripts expose no parent-level plan, hence stages == 0.
        query = dict(query)
        for field, key in (("totalBytesProcessed", "bytes_processed"),
                           ("totalBytesBilled", "bytes_billed"),
                           ("totalSlotMs", "slot_ms")):
            query[field] = sum(c[key] for c in children)

    return {
        "job_id": job_id,
        "bytes_processed": int(query.get("totalBytesProcessed", 0)),
        "bytes_billed": int(query.get("totalBytesBilled", 0)),
        "slot_ms": int(query.get("totalSlotMs", 0)),
        "elapsed_ms": int(stats.get("endTime", 0)) - int(stats.get("startTime", 0)),
        "cache_hit": bool(query.get("cacheHit", False)),
        "statement_type": query.get("statementType"),
        # snake_case in the REST payload, unlike its neighbours
        "reservation": stats.get("reservation_id") or "ON_DEMAND",
        # JobStatistics.edition, not JobStatistics2 - reading it off `query`
        # returns None for every job and makes the on-demand check vacuous.
        "edition": stats.get("edition"),
        "num_stages": len(plan),
        "plan_steps": [s.get("name") for s in plan],
        # (stage, recordsRead, recordsWritten, shuffleOutputBytes) - the work
        # metrics this benchmark argues from. They do not move with slot
        # availability, but they are not constant either; analysis.py takes the
        # median and reports whether they were stable.
        "plan_records": [(s.get("name"),
                          int(s.get("recordsRead") or 0),
                          int(s.get("recordsWritten") or 0),
                          int(s.get("shuffleOutputBytes") or 0)) for s in plan],
        "children": children,
    }


def _script_children(query, stats, job_id, project, location):
    if query.get("statementType") != "SCRIPT" and not stats.get("scriptStatistics"):
        return []
    # jobs.list?parentJobId is immediate; INFORMATION_SCHEMA lags by minutes.
    listing = _request("GET", f"{API}/projects/{project}/jobs"
                              f"?parentJobId={job_id}&location={location}"
                              f"&projection=full")
    children = []
    for child in listing.get("jobs") or []:
        cq = child.get("statistics", {}).get("query", {}) or {}
        children.append({
            "job_id": child.get("jobReference", {}).get("jobId"),
            "statement_type": cq.get("statementType"),
            "bytes_processed": int(cq.get("totalBytesProcessed") or 0),
            "bytes_billed": int(cq.get("totalBytesBilled") or 0),
            "slot_ms": int(cq.get("totalSlotMs") or 0),
        })
    return children
