import pytest

from rl.path_features import PathFeatures
from rl.reward import compute_reward


def make_features(**overrides):
    defaults = dict(
        bottleneck_utilization=0.0,
        total_delay_ms=0.0,
        max_loss_pct=0.0,
        min_trust=1.0,
        max_switch_throughput_bps=0.0,
        max_link_to_switch_rate=0.0,
    )
    defaults.update(overrides)
    return PathFeatures(**defaults)


def test_perfect_path_gets_max_reward():
    assert compute_reward(make_features()) == pytest.approx(1.0)


def test_worst_case_path_gets_min_reward():
    worst = make_features(
        bottleneck_utilization=1.0,
        total_delay_ms=1000.0,   # clamped, way over DELAY_NORM_MS
        max_loss_pct=100.0,      # clamped, way over LOSS_NORM_PCT
        min_trust=0.0,
    )
    assert compute_reward(worst) == pytest.approx(0.0)


def test_reward_is_bounded_even_beyond_norms():
    extreme = make_features(bottleneck_utilization=5.0, total_delay_ms=1_000_000, max_loss_pct=500.0, min_trust=-3.0)
    reward = compute_reward(extreme)
    assert 0.0 <= reward <= 1.0


@pytest.mark.parametrize("field", ["bottleneck_utilization", "total_delay_ms", "max_loss_pct"])
def test_reward_decreases_monotonically_with_worse_signal(field):
    better = compute_reward(make_features(**{field: 0.0}))
    worse = compute_reward(make_features(**{field: 50.0 if field != "bottleneck_utilization" else 0.9}))
    assert worse < better


def test_reward_decreases_with_lower_trust():
    high_trust = compute_reward(make_features(min_trust=1.0))
    low_trust = compute_reward(make_features(min_trust=0.1))
    assert low_trust < high_trust


def test_utilization_weighted_heaviest():
    """Equal-magnitude 'badness' on each dimension alone - utilization
    should hurt the reward the most, matching the documented weighting."""
    from rl.reward import WEIGHT_DELAY, WEIGHT_LOSS, WEIGHT_TRUST, WEIGHT_UTILIZATION

    assert WEIGHT_UTILIZATION > WEIGHT_DELAY
    assert WEIGHT_UTILIZATION > WEIGHT_LOSS
    assert WEIGHT_UTILIZATION > WEIGHT_TRUST
    assert WEIGHT_UTILIZATION + WEIGHT_DELAY + WEIGHT_LOSS + WEIGHT_TRUST == pytest.approx(1.0)
