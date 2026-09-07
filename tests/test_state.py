import pytest

from rl.path_features import PathFeatures
from rl.state import discretize_state


def make_features(**overrides):
    defaults = dict(
        bottleneck_utilization=0.1,
        total_delay_ms=10.0,
        max_loss_pct=0.0,
        min_trust=1.0,
        max_switch_throughput_bps=0.0,
        max_link_to_switch_rate=0.1,
    )
    defaults.update(overrides)
    return PathFeatures(**defaults)


def test_discretize_state_one_tuple_per_candidate():
    result = discretize_state([make_features(), make_features(), make_features()])
    assert len(result) == 3
    assert all(len(bins) == 6 for bins in result)


def test_low_values_bin_to_zero():
    bins = discretize_state([make_features()])[0]
    # utilization=0.1 (<0.4), delay=10 (<30), loss=0 (<2), trust=1.0 (best)
    assert bins[0] == 0  # utilization
    assert bins[1] == 0  # delay
    assert bins[2] == 0  # loss
    assert bins[3] == 0  # trust (inverted: high trust -> low badness bin)


def test_high_values_bin_to_two():
    bad = make_features(bottleneck_utilization=0.95, total_delay_ms=200.0, max_loss_pct=50.0, min_trust=0.05)
    bins = discretize_state([bad])[0]
    assert bins[0] == 2
    assert bins[1] == 2
    assert bins[2] == 2
    assert bins[3] == 2


def test_trust_inversion_mid_value():
    mid_trust = make_features(min_trust=0.55)  # between the 0.4/0.7 thresholds
    bins = discretize_state([mid_trust])[0]
    assert bins[3] == 1


def test_empty_candidate_list_returns_empty_tuple():
    assert discretize_state([]) == ()
