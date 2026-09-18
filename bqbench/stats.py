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


def _stratum_rank_stats(a, b):
    """(rank sum of `a`, its mean, its tie-corrected variance) within one stratum."""
    n1, n2 = len(a), len(b)
    total = n1 + n2
    pooled = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks = [0.0] * total
    i = 0
    while i < total:
        j = i
        while j + 1 < total and pooled[j + 1][0] == pooled[i][0]:
            j += 1
        shared = (i + j) / 2.0 + 1
        for k in range(i, j + 1):
            ranks[k] = shared
        i = j + 1
    rank_sum = sum(r for r, (_, group) in zip(ranks, pooled) if group == 0)
    mean = n1 * (total + 1) / 2.0
    if total < 2:
        return rank_sum, mean, 0.0
    sum_sq = sum(r * r for r in ranks)
    variance = ((n1 * n2) / (total * (total - 1.0))) * (
        sum_sq - total * (total + 1.0) ** 2 / 4.0)
    return rank_sum, mean, max(0.0, variance)


def van_elteren(strata):
    """Two-sided van Elteren test: Mann-Whitney stratified by a blocking factor.

    `strata` is [(baseline, other), ...], one entry per block. Ranking happens
    *within* each block, so a level shift common to both variants of a block -
    here, a whole pass running on a busier slot pool - cancels instead of
    inflating the variance it is compared against. Blocks are weighted
    1/(N_h + 1), van Elteren's optimal weighting for a location shift.

    With a single stratum this reduces exactly to `mann_whitney_u`, continuity
    correction included, so a one-pass comparison is unaffected by stratifying.
    """
    usable = [(a, b) for a, b in strata if a and b]
    if not usable:
        return None
    numerator = denominator = correction = 0.0
    for a, b in usable:
        rank_sum, mean, variance = _stratum_rank_stats(a, b)
        weight = 1.0 / (len(a) + len(b) + 1.0)
        numerator += weight * (rank_sum - mean)
        denominator += weight * weight * variance
        # The half-unit correction belongs to each stratum's own rank sum, so it
        # is weighted alongside it. Applying a bare 0.5 to the weighted total
        # would correct on a scale (N_h + 1) times too large.
        correction += 0.5 * weight
    if denominator <= 0:
        return 1.0
    z = max(0.0, abs(numerator) - correction) / math.sqrt(denominator)
    p = 2 * (1 - 0.5 * (1 + math.erf(z / math.sqrt(2))))
    return max(0.0, min(1.0, p))


def stratified_median_ratio(strata):
    """median(other)/median(baseline) computed within each block, then combined.

    The geometric mean is the right combiner because these are ratios: a pass
    that ran 15% slow multiplies both variants alike, so it divides out within
    the block and leaves nothing behind when the blocks are recombined.

    Pooling instead takes the median of a mixture of blocks, which is not the
    quantity anyone means. On this data it moves the 127-group DISTINCT
    comparison from 1.077 to 0.965 - across 1.0, reversing the apparent
    direction - because its two passes disagree (0.87 and 1.07).
    """
    ratios = []
    for baseline, other in strata:
        if not baseline or not other:
            continue
        denominator = statistics.median(baseline)
        if denominator:
            ratios.append(statistics.median(other) / denominator)
    if not ratios or any(r <= 0 for r in ratios):
        return None
    return math.exp(sum(math.log(r) for r in ratios) / len(ratios))


def stratified_median_ratio_ci(strata, *, resamples=10000, seed=0, level=0.95):
    """Percentile bootstrap for `stratified_median_ratio`, resampling in-block.

    Resampling the pooled sample would let a replicate draw, say, nine jobs from
    the slow pass and none from the fast one - a combination the experiment
    could not have produced, since the passes are balanced by construction.
    Drawing within each block keeps every replicate the shape of the real run.
    """
    usable = [(a, b) for a, b in strata if a and b]
    if not usable:
        return None, None
    rng = random.Random(seed)
    usable = [(sorted(a), sorted(b)) for a, b in usable]
    ratios = []
    for _ in range(resamples):
        drawn = [(rng.choices(a, k=len(a)), rng.choices(b, k=len(b)))
                 for a, b in usable]
        ratio = stratified_median_ratio(drawn)
        if ratio:
            ratios.append(ratio)
    if not ratios:
        return None, None
    ratios.sort()
    tail = (1 - level) / 2
    return (ratios[int(tail * len(ratios))],
            ratios[min(len(ratios) - 1, int((1 - tail) * len(ratios)))])
