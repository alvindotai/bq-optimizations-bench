"""Invariants the matrix must hold for any result computed from it to mean
what the documents say it means."""
import importlib
import os
import re
import unittest

os.environ.setdefault("BENCH_PROJECT", "example-project")

from bqbench import matrix  # noqa: E402  - after the env default above


class Structure(unittest.TestCase):
    def test_keys_are_unique(self):
        keys = [m["key"] for m in matrix.ALL]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_comparison_has_at_least_two_variants(self):
        for m in matrix.ALL:
            self.assertGreaterEqual(len(m["variants"]), 2, m["key"])

    def test_variant_names_are_unique_within_a_comparison(self):
        for m in matrix.ALL:
            names = [n for n, _ in m["variants"]]
            self.assertEqual(len(names), len(set(names)), m["key"])

    def test_every_comparison_is_titled_and_claimed(self):
        for m in matrix.ALL:
            self.assertTrue(m["claim"].strip(), m["key"])
            self.assertTrue(m["title"].strip(), m["key"])

    def test_all_is_core_plus_followups_with_no_overlap(self):
        self.assertEqual(len(matrix.ALL), len(matrix.CORE) + len(matrix.FOLLOWUPS))
        core = {m["key"] for m in matrix.CORE}
        self.assertFalse(core & {m["key"] for m in matrix.FOLLOWUPS})


class Sql(unittest.TestCase):
    def test_rendering_is_deterministic(self):
        first = {(m["key"], n): s for m in matrix.ALL for n, s in m["variants"]}
        importlib.reload(matrix)
        second = {(m["key"], n): s for m in matrix.ALL for n, s in m["variants"]}
        self.assertEqual(first, second)

    def test_no_unresolved_placeholders_survive(self):
        for m in matrix.ALL:
            for name, sql in m["variants"]:
                self.assertNotRegex(sql, r"\{[a-z_]*\}", f"{m['key']}/{name}")

    def test_every_query_reads_public_data_or_a_declared_fixture(self):
        allowed = re.compile(r"`(bigquery-public-data\.\w+\.\w+"
                             r"|example-project\.bq_myth_bench\.\w+)`")
        for m in matrix.ALL:
            for name, sql in m["variants"]:
                for ref in re.findall(r"`[^`]+`", sql):
                    self.assertRegex(ref, allowed, f"{m['key']}/{name}")

    def test_fixture_queries_resolve_against_the_configured_project(self):
        fixture_users = [s for m in matrix.ALL for _, s in m["variants"]
                         if "bq_myth_bench" in s]
        self.assertTrue(fixture_users)
        for sql in fixture_users:
            self.assertIn("example-project.bq_myth_bench.", sql)


class DocOrder(unittest.TestCase):
    def test_covers_every_comparison_exactly_once(self):
        self.assertEqual(sorted(matrix.DOC_ORDER),
                         sorted(m["key"] for m in matrix.ALL))
        self.assertEqual(len(matrix.DOC_ORDER), len(set(matrix.DOC_ORDER)))

    def test_in_doc_order_returns_them_in_that_order(self):
        self.assertEqual([m["key"] for m in matrix.in_doc_order()], matrix.DOC_ORDER)

    def test_drift_is_refused_rather_than_silently_dropping_a_comparison(self):
        original = list(matrix.DOC_ORDER)
        try:
            matrix.DOC_ORDER.remove(original[0])
            with self.assertRaises(SystemExit):
                matrix.in_doc_order()
        finally:
            matrix.DOC_ORDER[:] = original

    def test_unknown_key_is_refused(self):
        original = list(matrix.DOC_ORDER)
        try:
            matrix.DOC_ORDER.append("no_such_myth")
            with self.assertRaises(SystemExit):
                matrix.in_doc_order()
        finally:
            matrix.DOC_ORDER[:] = original


class Selection(unittest.TestCase):
    def test_named_matrices_resolve(self):
        self.assertIs(matrix.select("core"), matrix.CORE)
        self.assertIs(matrix.select("all"), matrix.ALL)

    def test_a_typo_exits_with_the_valid_names(self):
        with self.assertRaises(SystemExit) as caught:
            matrix.select("cor")
        self.assertIn("core", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
