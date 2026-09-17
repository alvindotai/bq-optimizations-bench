"""The statistics carry the conclusions, so they get checked against values
computed by hand rather than against themselves."""
import random
import statistics
import unittest

from bqbench.stats import (
    mann_whitney_u,
    median_ratio_ci,
    stratified_median_ratio,
    stratified_median_ratio_ci,
    van_elteren,
)


class MannWhitneyU(unittest.TestCase):
    def test_identical_samples_give_no_evidence_of_difference(self):
        self.assertAlmostEqual(mann_whitney_u([1, 2, 3, 4], [1, 2, 3, 4]), 1.0, places=6)

    def test_all_values_tied(self):
        self.assertEqual(mann_whitney_u([5, 5, 5], [5, 5, 5]), 1.0)

    def test_completely_separated_samples(self):
        p = mann_whitney_u(list(range(1, 10)), list(range(100, 1000, 100)))
        self.assertLess(p, 0.001)

    def test_matches_hand_computed_example(self):
        # [1,3,5,7,9] vs [2,4,6,8,10]: rank sum 25, U = 10, exact two-sided
        # p = 0.690. The normal approximation should land near it, not on it.
        p = mann_whitney_u([1, 3, 5, 7, 9], [2, 4, 6, 8, 10])
        self.assertAlmostEqual(p, 0.69, delta=0.05)

    def test_symmetric_in_its_arguments(self):
        a, b = [3, 1, 4, 1, 5, 9], [2, 7, 1, 8, 2, 8]
        self.assertAlmostEqual(mann_whitney_u(a, b), mann_whitney_u(b, a), places=9)

    def test_empty_sample_returns_none(self):
        self.assertIsNone(mann_whitney_u([], [1, 2]))

    def test_result_is_a_probability(self):
        for a, b in (([1], [2]), ([1, 1, 1], [1, 2, 3]), ([9, 9], [1, 1])):
            self.assertGreaterEqual(mann_whitney_u(a, b), 0.0)
            self.assertLessEqual(mann_whitney_u(a, b), 1.0)


class MedianRatioCI(unittest.TestCase):
    def test_identical_constant_samples_collapse_to_one(self):
        self.assertEqual(median_ratio_ci([10] * 12, [10] * 12), (1.0, 1.0))

    def test_constant_offset_is_recovered_exactly(self):
        self.assertEqual(median_ratio_ci([10] * 12, [20] * 12), (2.0, 2.0))

    def test_is_deterministic_for_a_given_seed(self):
        a, b = [1, 5, 2, 8, 3, 9, 4], [2, 6, 3, 9, 4, 10, 5]
        self.assertEqual(median_ratio_ci(a, b), median_ratio_ci(a, b))

    def test_interval_brackets_the_point_estimate(self):
        a = [10, 12, 11, 13, 9, 14, 10, 11]
        b = [20, 24, 22, 26, 18, 28, 20, 22]
        low, high = median_ratio_ci(a, b)
        self.assertLessEqual(low, 2.0)
        self.assertGreaterEqual(high, 2.0)

    def test_noisy_samples_give_a_wider_interval_than_clean_ones(self):
        clean = median_ratio_ci([10] * 9, [10, 10, 10, 11, 10, 10, 9, 10, 10])
        noisy = median_ratio_ci([10] * 9, [2, 30, 5, 25, 10, 1, 40, 8, 15])
        self.assertLess(clean[1] - clean[0], noisy[1] - noisy[0])

    def test_empty_sample_returns_none(self):
        self.assertEqual(median_ratio_ci([], [1, 2]), (None, None))


class Stratified(unittest.TestCase):
    """Blocking by pass has to be a strict generalisation: a single-pass
    comparison must come out exactly where it did before, or the six
    single-pass results in this run would move for no reason."""

    def test_one_stratum_reduces_to_mann_whitney_exactly(self):
        rng = random.Random(7)
        for _ in range(200):
            n = rng.randint(3, 18)
            a = [rng.randint(1, 50) for _ in range(n)]
            b = [rng.randint(1, 50) for _ in range(n)]
            self.assertAlmostEqual(van_elteren([(a, b)]),
                                   mann_whitney_u(a, b), places=12)

    def test_one_stratum_ratio_reduces_to_the_pooled_ratio(self):
        a, b = [10, 12, 11, 13, 9], [20, 24, 22, 26, 18]
        self.assertAlmostEqual(stratified_median_ratio([(a, b)]),
                               statistics.median(b) / statistics.median(a))

    def test_a_shift_common_to_both_variants_cancels(self):
        """The whole point: a pass that ran 40% slow must leave no trace."""
        a1, b1 = [10, 11, 12], [20, 22, 24]
        a2, b2 = [v * 1.4 for v in a1], [v * 1.4 for v in b1]
        self.assertAlmostEqual(stratified_median_ratio([(a1, b1), (a2, b2)]), 2.0)

    def test_pooling_can_land_outside_the_range_of_its_own_strata(self):
        """Where the estimators actually part company.

        A shift shared by both variants cancels either way - pooling is not
        wrong about that. It goes wrong when the strata disagree about the
        ratio, because the median of a mixture is not a mixture of medians.
        These are the real 127-group DISTINCT/GROUP BY slot times, whose two
        passes measured 0.87 and 1.07: pooled they read as 1.077, which is
        outside the range of both.
        """
        a1 = [1150, 1193, 1219, 1234, 1487, 1508, 1512, 1634, 2140]
        b1 = [1038, 1051, 1174, 1283, 1293, 1554, 1757, 2320, 2621]
        a2 = [843, 1002, 1379, 1533, 1558, 1573, 1771, 1831, 1990]
        b2 = [891, 1120, 1601, 1652, 1667, 1869, 1892, 1976, 3395]
        per_pass = [statistics.median(b1) / statistics.median(a1),
                    statistics.median(b2) / statistics.median(a2)]
        pooled = statistics.median(b1 + b2) / statistics.median(a1 + a2)
        blocked = stratified_median_ratio([(a1, b1), (a2, b2)])
        self.assertGreater(pooled, max(per_pass))
        self.assertLessEqual(min(per_pass), blocked)
        self.assertLessEqual(blocked, max(per_pass))

    def test_a_stratum_missing_one_variant_is_dropped_not_pooled(self):
        a, b = [10, 11, 12], [20, 22, 24]
        self.assertAlmostEqual(stratified_median_ratio([(a, b), (a, [])]), 2.0)
        self.assertEqual(van_elteren([(a, []), ([], b)]), None)

    def test_the_interval_brackets_the_stratified_point_estimate(self):
        a1, b1 = [10, 12, 11, 13, 9, 14], [20, 24, 22, 26, 18, 28]
        a2, b2 = [v * 1.4 for v in a1], [v * 1.4 for v in b1]
        strata = [(a1, b1), (a2, b2)]
        low, high = stratified_median_ratio_ci(strata)
        self.assertLessEqual(low, stratified_median_ratio(strata))
        self.assertGreaterEqual(high, stratified_median_ratio(strata))

    def test_the_interval_is_deterministic(self):
        strata = [([1, 5, 2, 8], [2, 6, 3, 9]), ([3, 7, 4, 9], [4, 8, 5, 10])]
        self.assertEqual(stratified_median_ratio_ci(strata),
                         stratified_median_ratio_ci(strata))


if __name__ == "__main__":
    unittest.main()
