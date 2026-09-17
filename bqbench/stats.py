"""Comparing two slot-time samples without assuming a distribution.

Slot time on a shared on-demand pool is not normally distributed and carries
occasional large outliers, so both tools here are rank- or resample-based rather
than parametric.
"""
import math
import random
import statistics


def mann_whitney_u(a, b):
    """Two-sided Mann-Whitney U, normal approximation with tie correction.

    Returns an indicative p-value. At the sample sizes used here (n = 6 to 18)
    the normal approximation is serviceable but not exact, and the benchmark
    runs many comparisons without a multiple-testing correction - so treat this
    as "does this gap survive resampling" rather than as a formal test. Where a
    result is reported as real, the plan and record-count evidence agrees.
    """
    n1, n2 = len(a), len(b)
    if n1 == 0 or n2 == 0:
        return None

    pooled = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = [0.0] * len(pooled)
    ties = 0.0
    i = 0
    while i < len(pooled):
        j = i
        while j + 1 < len(pooled) and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        shared = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[k] = shared
        run = j - i + 1
        if run > 1:
            ties += run ** 3 - run
        i = j + 1

    rank_sum_a = sum(r for r, (_, group) in zip(ranks, pooled) if group == 0)
    u = rank_sum_a - n1 * (n1 + 1) / 2.0
    mean_u = n1 * n2 / 2.0
    n = n1 + n2
    if n < 2:
        return 1.0
    var_u = (n1 * n2 / 12.0) * ((n + 1) - ties / (n * (n - 1)))
    if var_u <= 0:                      # every observation identical
        return 1.0
    z = (abs(u - mean_u) - 0.5) / math.sqrt(var_u)   # continuity-corrected
    p = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    return max(0.0, min(1.0, p))


def median_ratio_ci(baseline, other, *, resamples=10000, seed=0, level=0.95):
    """Percentile-bootstrap CI for median(other) / median(baseline).

    A p-value says whether a difference was detected. It says nothing about how
    large a difference could have gone undetected, and at these sample sizes
    that gap is wide: "we measured no difference" and "there is no difference"
    are not the same claim, and only this interval distinguishes them.

    Seeded, so the reported bounds are reproducible.
    """
    if not baseline or not other:
        return None, None
    # Canonicalise the inputs. Resampling walks the list, so two runs over the
    # same measurements in a different order produce different bounds - which
    # would make "seeded, so reproducible" true only for one arrangement of the
    # file the records happen to sit in.
    baseline, other = sorted(baseline), sorted(other)
    rng = random.Random(seed)
    ratios = []
    for _ in range(resamples):
        denominator = statistics.median(rng.choices(baseline, k=len(baseline)))
        if denominator:
            ratios.append(
                statistics.median(rng.choices(other, k=len(other))) / denominator)
    if not ratios:
        return None, None
    ratios.sort()
    tail = (1 - level) / 2
    return (ratios[int(tail * len(ratios))],
            ratios[min(len(ratios) - 1, int((1 - tail) * len(ratios)))])
