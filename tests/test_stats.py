"""The statistics carry the conclusions, so they get checked against values
computed by hand rather than against themselves."""
import unittest

from bqbench.stats import mann_whitney_u, median_ratio_ci


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


if __name__ == "__main__":
    unittest.main()
