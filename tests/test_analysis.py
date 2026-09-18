"""The analysis decides what the benchmark is allowed to claim, so the stability
flags and identity verdicts are tested against records built to trip them."""
import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path

from bqbench import analysis


def record(myth, variant, slot_ms, *, stages=("S00: Input", "S01: Output"),
           read=100, written=10, billed=1024, processed=1024):
    return {
        "myth": myth, "variant": variant, "slot_ms": slot_ms,
        "elapsed_ms": slot_ms // 2,
        "bytes_processed": processed, "bytes_billed": billed,
        "plan_steps": list(stages),
        "plan_records": [(s, read // len(stages), written // len(stages), 0)
                         for s in stages],
    }


def grouped(*records):
    out = defaultdict(lambda: defaultdict(list))
    for r in records:
        out[r["myth"]][r["variant"]].append(r)
    return out


META = {"m": {"key": "m", "claim": "c", "title": "t", "note": "",
              "variants": [("a", "SELECT 1"), ("b", "SELECT 1")]}}


class Identity(unittest.TestCase):
    def test_identical_variants_are_reported_identical(self):
        g = grouped(*[record("m", v, 100 + i) for v in "ab" for i in range(4)])
        block = analysis.summarise(g, META)["m"]
        self.assertTrue(block["plans_identical"])
        self.assertTrue(block["work_identical"])
        self.assertTrue(block["bytes_identical"])
        self.assertTrue(block["plans_stable"])
        self.assertTrue(block["work_stable"])

    def test_differing_plans_are_not_reported_identical(self):
        g = grouped(*[record("m", "a", 100 + i) for i in range(4)],
                    *[record("m", "b", 100 + i, stages=("S00: Output",))
                      for i in range(4)])
        self.assertFalse(analysis.summarise(g, META)["m"]["plans_identical"])

    def test_differing_billed_bytes_are_not_reported_identical(self):
        g = grouped(*[record("m", "a", 100 + i) for i in range(4)],
                    *[record("m", "b", 100 + i, billed=2048) for i in range(4)])
        self.assertFalse(analysis.summarise(g, META)["m"]["bytes_identical"])


class Stability(unittest.TestCase):
    def test_a_plan_that_changes_between_reps_is_flagged_unstable(self):
        g = grouped(record("m", "a", 100),
                    record("m", "a", 101, stages=("S00: Output",)),
                    record("m", "b", 100), record("m", "b", 101))
        block = analysis.summarise(g, META)["m"]
        self.assertFalse(block["plans_stable"])
        self.assertFalse(block["variants"]["a"]["plan_stable_across_reps"])
        self.assertTrue(block["variants"]["b"]["plan_stable_across_reps"])

    def test_record_counts_that_change_between_reps_are_flagged_unstable(self):
        g = grouped(record("m", "a", 100, read=100), record("m", "a", 101, read=900),
                    record("m", "b", 100), record("m", "b", 101))
        block = analysis.summarise(g, META)["m"]
        self.assertFalse(block["work_stable"])
        self.assertFalse(block["variants"]["a"]["work_stable_across_reps"])

    def test_unstable_work_reports_the_median_not_an_extreme(self):
        # 10 / 20 / 900: the median is the honest summary; min() would report 10
        # and make this variant look like it read far less than it did.
        g = grouped(record("m", "a", 100, read=10), record("m", "a", 101, read=20),
                    record("m", "a", 102, read=900),
                    *[record("m", "b", 100 + i) for i in range(3)])
        self.assertEqual(analysis.summarise(g, META)["m"]["variants"]["a"]["records_read"], 20)


class Ratios(unittest.TestCase):
    def test_baseline_has_no_ratio_partner_and_no_p_value(self):
        g = grouped(*[record("m", v, 100) for v in "ab" for _ in range(3)])
        a = analysis.summarise(g, META)["m"]["variants"]["a"]
        self.assertIsNone(a["p_vs_base"])
        self.assertIsNone(a["ratio_ci"])
        self.assertEqual(a["ratio_vs_base"], 1.0)

    def test_ratio_is_measured_against_the_first_declared_variant(self):
        g = grouped(*[record("m", "a", 100) for _ in range(4)],
                    *[record("m", "b", 200) for _ in range(4)])
        self.assertEqual(analysis.summarise(g, META)["m"]["variants"]["b"]["ratio_vs_base"], 2.0)

    def test_every_non_baseline_variant_carries_an_interval(self):
        g = grouped(*[record("m", "a", 100 + i) for i in range(5)],
                    *[record("m", "b", 150 + i) for i in range(5)])
        b = analysis.summarise(g, META)["m"]["variants"]["b"]
        self.assertEqual(len(b["ratio_ci"]), 2)
        self.assertLessEqual(b["ratio_ci"][0], b["ratio_ci"][1])


class ByteFormatting(unittest.TestCase):
    def test_scales_at_a_gibibyte(self):
        self.assertEqual(analysis.format_bytes(512 * 2**20), "512 MiB")
        self.assertEqual(analysis.format_bytes(2 * 2**30), "2.00 GiB")

    def test_a_variant_whose_bytes_moved_renders_every_value(self):
        self.assertEqual(analysis.format_bytes([2**20, 2 * 2**20]), "1 MiB / 2 MiB")


class SummarySchema(unittest.TestCase):
    """A summary is derived: an old one must be refused, not crash a consumer."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)

    def _write(self, payload):
        path = Path(self._dir.name) / "summary.json"
        path.write_text(json.dumps(payload))
        return str(path)

    def test_a_current_summary_loads(self):
        g = grouped(*[record("m", v, 100 + i) for v in "ab" for i in range(4)])
        path = self._write(analysis.summarise(g, META))
        self.assertIn("m", analysis.load_summary(path))

    def test_a_summary_missing_a_variant_key_is_refused(self):
        g = grouped(*[record("m", v, 100 + i) for v in "ab" for i in range(4)])
        summary = analysis.summarise(g, META)
        del summary["m"]["variants"]["a"]["ratio_ci"]
        with self.assertRaises(SystemExit) as caught:
            analysis.load_summary(self._write(summary))
        self.assertIn("ratio_ci", str(caught.exception))

    def test_a_summary_missing_a_block_key_is_refused(self):
        g = grouped(*[record("m", v, 100 + i) for v in "ab" for i in range(4)])
        summary = analysis.summarise(g, META)
        del summary["m"]["work_stable"]
        with self.assertRaises(SystemExit) as caught:
            analysis.load_summary(self._write(summary))
        self.assertIn("work_stable", str(caught.exception))

    def test_the_error_says_how_to_fix_it(self):
        g = grouped(*[record("m", v, 100 + i) for v in "ab" for i in range(4)])
        summary = analysis.summarise(g, META)
        del summary["m"]["work_stable"]
        with self.assertRaises(SystemExit) as caught:
            analysis.load_summary(self._write(summary))
        self.assertIn("bqbench analyze", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
