"""Fail-closed publication gate.

This repository is public. Nothing in it may carry an internal project id,
dataset name, hostname, employee identity or company-specific token. Run it
before every commit; it exits non-zero on the first hit.
"""
import pathlib
import re

from .. import paths

HELP = "scan the tree for anything that must not be published"

# Case-insensitive. Additions belong here rather than in .gitignore: the point
# is to catch content, not to hide files.
FORBIDDEN = [
    r"alvin",                    # company name, project prefixes, dataset names
    r"\balv[-_]",                # alv-proxy, alv-costs, alv_...
    r"alvbench",                 # superseded job-id prefix
    r"25846",                    # internal GCP project number
    r"blog_bench|opt_harness",   # internal dataset names
    r"[A-Za-z0-9._%+-]+@(?!example\.com)[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
    r"mesmacosta|marcelocosta",  # personal handles / home paths
    r"/Users/[a-z]+/",           # absolute local paths
    r"prod-\d{4,}",              # internal project numbering
    r"\bsecret[s]?/|SECRET_|PRIVATE_KEY|BEGIN [A-Z ]*PRIVATE KEY",
]
SKIP_DIRS = {".git", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache"}
ALLOW = re.compile(r"example-project|bq_myth_bench|example\.com")

#: Narrow, line-level exemptions. The repository's own public URL necessarily
#: contains the org name; that is published by definition, not leaked. Keep this
#: list to exact strings - never relax the patterns above to accommodate a line.
ALLOW_LINES = (
    "alvindotai/bq-optimizations-bench",   # the repo's own slug and URLs
)

HERE = pathlib.Path(__file__).resolve()


def add_arguments(p):
    p.add_argument("--path", default=str(paths.ROOT),
                   help="tree to scan (default: the repository root)")
    p.add_argument("--include-results", action="store_true",
                   help="also scan results/, which is not committed")


def main(args):
    root = pathlib.Path(args.path).resolve()
    patterns = [(p, re.compile(p, re.I)) for p in FORBIDDEN]
    hits, scanned = [], 0

    for f in sorted(root.rglob("*")):
        if not f.is_file() or any(d in f.parts for d in SKIP_DIRS):
            continue
        if f.resolve() == HERE:
            continue                      # necessarily contains the patterns
        if not args.include_results and paths.RESULTS in f.parents:
            continue
        scanned += 1
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if any(allowed in line for allowed in ALLOW_LINES):
                continue
            for src, pattern in patterns:
                m = pattern.search(line)
                if m and not ALLOW.fullmatch(m.group(0)):
                    hits.append((f.relative_to(root), n, src, m.group(0)[:60],
                                 line.strip()[:100]))

    if hits:
        print(f"LEAK GATE FAILED - {len(hits)} hit(s):\n")
        for f, n, src, tok, line in hits[:60]:
            print(f"  {f}:{n}  /{src}/  matched {tok!r}\n      {line}")
        if len(hits) > 60:
            print(f"  ... and {len(hits)-60} more")
        return 1
    print(f"leak gate PASSED - {scanned} files clean")
    return 0
