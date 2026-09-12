"""The run schedule and the break-even arithmetic - the two places where a
quiet mistake would change a published number."""
import os
import unittest
from collections import defaultdict

os.environ.setdefault("BENCH_PROJECT", "example-project")

from bqbench import pricing  # noqa: E402
from bqbench.commands.run import interleave  # noqa: E402

MYTHS = [
    {"key": "one", "reps": 3, "variants": [("a", "A"), ("b", "B")]},
    {"key": "two", "reps": 2, "variants": [("x", "X"), ("y", "Y"), ("z", "Z")]},
]


class Schedule(unittest.TestCase):
    def test_every_variant_runs_its_declared_number_of_times(self):
        counts = defaultdict(int)
        for key, variant, _, _ in interleave(MYTHS):
            counts[(key, variant)] += 1
        self.assertEqual(counts[("one", "a")], 3)
        self.assertEqual(counts[("two", "x")], 2)

    def test_repetitions_are_interleaved_not_batched(self):
        reps = [rep for _, _, _, rep in interleave(MYTHS)]
        self.assertEqual(reps, sorted(reps), "schedule must be rep-major")

    def test_a_comparison_stops_at_its_own_rep_count(self):
        for key, _, _, rep in interleave(MYTHS):
            self.assertLessEqual(rep, {"one": 3, "two": 2}[key])

    def test_variant_order_rotates_so_none_is_always_measured_first(self):
        first_each_rep = {}
        for key, variant, _, rep in interleave(MYTHS):
            first_each_rep.setdefault((key, rep), variant)
        for key in ("one", "two"):
            seen = {v for (k, _), v in first_each_rep.items() if k == key}
            self.assertGreater(len(seen), 1,
                               f"{key} always measured {seen} first")

    def test_rotation_is_a_rotation_not_a_reshuffle(self):
        by_rep = defaultdict(list)
        for key, variant, _, rep in interleave(MYTHS):
            if key == "two":
                by_rep[rep].append(variant)
        self.assertEqual(by_rep[1], ["x", "y", "z"])
        self.assertEqual(by_rep[2], ["y", "z", "x"])


SUMMARY = {"m6_denormalisation": {"variants": {
    "star_join_3_tables": {"slot_ms_median": 100_000, "bytes_billed": 1_000_000},
    "denormalised_1_table": {"slot_ms_median": 20_000, "bytes_billed": 600_000},
}}}
REBUILDS = [{"slot_ms": 800_000, "bytes_billed": 4_000_000}]
SIZES = {"denorm_wide": {"bytes": 2_100},
         "n_posts": {"bytes": 1_000}, "n_users": {"bytes": 700},
         "n_badges": {"bytes": 300}}


class Breakeven(unittest.TestCase):
    def setUp(self):
        self.b = pricing.denormalisation_breakeven(SUMMARY, REBUILDS, SIZES)

    def test_saving_is_the_difference_per_query(self):
        self.assertEqual(self.b["saved_slot_ms"], 80_000)
        self.assertEqual(self.b["saved_bytes"], 400_000)

    def test_breakeven_is_rebuild_cost_over_per_query_saving(self):
        self.assertAlmostEqual(self.b["breakeven_slot"], 10.0)
        self.assertAlmostEqual(self.b["breakeven_bytes"], 10.0)

    def test_storage_overhead_compares_wide_against_the_star(self):
        self.assertAlmostEqual(self.b["storage_overhead_pct"], 5.0)

    def test_a_cheaper_rebuild_lowers_the_breakeven(self):
        cheap = pricing.denormalisation_breakeven(
            SUMMARY, [{"slot_ms": 80_000, "bytes_billed": 400_000}], SIZES)
        self.assertLess(cheap["breakeven_slot"], self.b["breakeven_slot"])

    def test_rebuild_cost_is_the_median_of_several_runs(self):
        noisy = pricing.denormalisation_breakeven(
            SUMMARY,
            [{"slot_ms": 1, "bytes_billed": 1},
             {"slot_ms": 800_000, "bytes_billed": 4_000_000},
             {"slot_ms": 9_000_000, "bytes_billed": 9_000_000}],
            SIZES)
        self.assertEqual(noisy["rebuild_slot_ms"], 800_000)


if __name__ == "__main__":
    unittest.main()
