"""The gate is the last thing between an internal identifier and a public
commit, so it is tested for what it catches AND what it lets through."""
import contextlib
import io
import os
import pathlib
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("BENCH_PROJECT", "example-project")

from bqbench.commands import leaks  # noqa: E402


class Args:
    def __init__(self, path, patterns, include_results=False):
        self.path = str(path)
        self.patterns = str(patterns)
        self.include_results = include_results


class Gate(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name)
        self.patterns = self.root / ".leakpatterns"

    def tearDown(self):
        self._tmp.cleanup()

    def scan(self):
        """Run the gate, swallowing its report - the exit code is the assertion."""
        with contextlib.redirect_stdout(io.StringIO()):
            return leaks.main(Args(self.root, self.patterns))

    def write(self, name, text):
        (self.root / name).write_text(text)

    def test_clean_tree_passes(self):
        self.write("ok.py", 'greeting = "hello"\n')
        self.assertEqual(self.scan(), 0)

    def test_catches_a_private_key(self):
        self.write("k.pem", "-----BEGIN RSA PRIVATE KEY-----\n")  # leak-gate-ok
        self.assertEqual(self.scan(), 1)

    def test_catches_an_aws_key(self):
        self.write("c.py", 'key = "AKIAIOSFODNN7EXAMPLE"\n')  # leak-gate-ok
        self.assertEqual(self.scan(), 1)

    def test_catches_a_hardcoded_credential(self):
        self.write("c.py", 'password = "correct-horse-battery"\n')  # leak-gate-ok
        self.assertEqual(self.scan(), 1)

    def test_catches_an_absolute_home_path(self):
        self.write("c.py", 'p = "/Users/someone/keys"\n')  # leak-gate-ok
        self.assertEqual(self.scan(), 1)

    def test_catches_a_real_email_but_allows_example_com(self):
        self.write("a.py", 'who = "person@realcompany.com"\n')  # leak-gate-ok
        self.assertEqual(self.scan(), 1)
        (self.root / "a.py").write_text('who = "person@example.com"\n')
        self.assertEqual(self.scan(), 0)

    def test_local_patterns_extend_the_builtins(self):
        self.write("c.py", "project = 'acme-internal-1234'\n")
        self.assertEqual(self.scan(), 0)
        self.patterns.write_text("acme-internal\n")
        self.assertEqual(self.scan(), 1)

    def test_comments_and_blank_lines_in_the_pattern_file_are_ignored(self):
        self.write("c.py", "x = 1\n")
        self.patterns.write_text("# a comment\n\n   \n")
        self.assertEqual(self.scan(), 0)

    def test_bang_prefix_exempts_a_line(self):
        self.write("c.py", "url = 'https://github.com/acme/public-repo'\n")
        self.patterns.write_text("acme\n")
        self.assertEqual(self.scan(), 1)
        self.patterns.write_text("acme\n!github.com/acme/public-repo\n")
        self.assertEqual(self.scan(), 0)

    def test_inline_pragma_silences_a_line(self):
        self.write("c.py", 'k = "AKIA' + 'IOSFODNN7EXAMPLE"\n')
        self.assertEqual(self.scan(), 1)
        self.write("c.py", 'k = "AKIA' + 'IOSFODNN7EXAMPLE"  # leak-gate-ok\n')
        self.assertEqual(self.scan(), 0)

    def test_the_pattern_file_does_not_scan_itself(self):
        self.write("c.py", "x = 1\n")
        self.patterns.write_text("supersecretcorp\n")
        self.assertEqual(self.scan(), 0)

    def test_tool_caches_are_skipped(self):
        cache = self.root / ".ruff_cache"
        cache.mkdir()
        (cache / "blob").write_text('p = "/Users/someone/x"\n')  # leak-gate-ok
        self.assertEqual(self.scan(), 0)


class ConfiguredIdentifiers(unittest.TestCase):
    """The billing project is the identifier most likely to reach a published
    artifact, and no built-in pattern matches it - a GCP project id is just a
    hyphenated word. The gate has to learn it from the environment."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = pathlib.Path(self._dir.name)
        (self.root / "job.jsonl").write_text(
            '{"sql": "SELECT 1 FROM `acme-analytics-42.ds.t`"}\n')

    def _run(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = leaks.main(Args(path=str(self.root),
                                   patterns=str(self.root / ".nonexistent"),
                                   include_results=True))
        return code, out.getvalue()

    def test_an_unset_project_leaves_the_project_id_undetected(self):
        with mock.patch.object(leaks, "PROJECT", ""):
            code, _ = self._run()
        self.assertEqual(code, 0)

    def test_the_configured_project_is_caught(self):
        with mock.patch.object(leaks, "PROJECT", "acme-analytics-42"):
            code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn("BENCH_PROJECT", output)

    def test_the_placeholder_project_is_not_a_leak(self):
        with mock.patch.object(leaks, "PROJECT", leaks.PLACEHOLDER_PROJECT):
            code, _ = self._run()
        self.assertEqual(code, 0)

    def test_a_default_dataset_name_is_not_a_leak(self):
        (self.root / "job.jsonl").write_text(
            '{"sql": "SELECT 1 FROM `example-project.bq_myth_bench.t`"}\n')
        with mock.patch.object(leaks, "PROJECT", ""):
            code, _ = self._run()
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
