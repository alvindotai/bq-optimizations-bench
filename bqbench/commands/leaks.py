"""Fail-closed scan for anything that should not be published.

Generic patterns (credentials, private keys, real email addresses, absolute home
paths) are built in. Organisation-specific strings - company names, internal
project ids, dataset names - belong in a local `.leakpatterns` file, which is
gitignored: a denylist committed to a public repository publishes exactly the
identifiers it is meant to suppress. See `.leakpatterns.example`.

A line beginning with `!` in that file is an exemption: any source line
containing it is skipped. That is how a repository's own public URL survives a
pattern matching its organisation name.

A source line carrying the `# leak-gate-ok` pragma is also skipped. It exists
for one honest case - the gate's own tests, which must contain planted
credentials to prove the gate catches them - and should stay that rare. It
silences a real finding just as readily as a false one.
"""
import pathlib
import re

from .. import paths

HELP = "scan the tree for anything that must not be published"

BUILTIN = [
    (r"BEGIN [A-Z ]*PRIVATE KEY", "private key"),
    (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
    (r"\bghp_[A-Za-z0-9]{36}\b", "GitHub token"),
    (r"\bAIza[0-9A-Za-z_\-]{35}\b", "Google API key"),
    (r"(?i)\b(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][^'\"]{8,}",
     "hardcoded credential"),
    (r"[A-Za-z0-9._%+-]+@(?!example\.(com|org))[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
     "email address"),
    (r"/(?:Users|home)/[a-z][a-z0-9_-]*/", "absolute home path"),
]

INLINE_PRAGMA = "# leak-gate-ok"

SKIP_DIRS = {".git", "__pycache__", ".venv", ".mypy_cache", ".pytest_cache",
             ".ruff_cache", "node_modules", "dist", "build", ".tox", ".idea"}

PATTERN_FILE = paths.ROOT / ".leakpatterns"
HERE = pathlib.Path(__file__).resolve()


def add_arguments(p):
    p.add_argument("--path", default=str(paths.ROOT),
                   help="tree to scan (default: the repository root)")
    p.add_argument("--patterns", default=str(PATTERN_FILE),
                   help=f"extra patterns, one regex per line (default: {PATTERN_FILE.name})")
    p.add_argument("--include-results", action="store_true",
                   help="also scan results/, which is not committed")


def main(args):
    root = pathlib.Path(args.path).resolve()
    pattern_file = pathlib.Path(args.patterns).resolve()
    patterns = [(re.compile(p, re.IGNORECASE), why) for p, why in BUILTIN]
    local, exempt = _local_patterns(pattern_file)
    patterns += local

    hits, scanned = [], 0
    for path in sorted(root.rglob("*")):
        if path.resolve() == pattern_file:
            continue
        if not _scannable(path, root, args.include_results):
            continue
        scanned += 1
        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if INLINE_PRAGMA in line or any(a in line for a in exempt):
                continue
            for pattern, why in patterns:
                found = pattern.search(line)
                if found:
                    hits.append((path.relative_to(root), n, why,
                                 found.group(0)[:60], line.strip()[:100]))

    if hits:
        print(f"LEAK GATE FAILED - {len(hits)} hit(s):\n")
        for path, n, why, token, line in hits[:50]:
            print(f"  {path}:{n}  {why}: {token!r}\n      {line}")
        if len(hits) > 50:
            print(f"  ... and {len(hits) - 50} more")
        return 1
    print(f"leak gate PASSED - {scanned} files clean "
          f"({len(BUILTIN)} built-in + {len(local)} local patterns, "
          f"{len(exempt)} exemptions)")
    return 0


def _scannable(path, root, include_results):
    if not path.is_file() or path.resolve() == HERE:
        return False
    if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
        return False
    return include_results or paths.RESULTS not in path.parents


def _local_patterns(path):
    """(patterns, exemptions) from a `.leakpatterns` file.

    One regex per line; `#` starts a comment; a leading `!` marks a literal
    string whose presence exempts the whole line from scanning.
    """
    if not path.exists():
        return [], []
    patterns, exempt = [], []
    for raw in path.read_text().splitlines():
        entry = raw.split("#", 1)[0].strip()
        if not entry:
            continue
        if entry.startswith("!"):
            exempt.append(entry[1:].strip())
        else:
            patterns.append((re.compile(entry, re.IGNORECASE), "local pattern"))
    return patterns, exempt
