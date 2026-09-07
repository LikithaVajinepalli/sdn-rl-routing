import pytest

from controller.metrics import LinkMetrics
from rl.path_features import aggregate_path_features


def make_metrics(**overrides):
    defaults = dict(
        utilization=0.1,
        delay_ms=5.0,
        loss_pct=0.0,
        trust_level=1.0,
        switch_throughput_bps=1000.0,
        link_to_switch_rate=0.1,
    )
    defaults.update(overrides)
    return LinkMetrics(**defaults)


def test_empty_list_raises():
    with pytest.raises(ValueError):
        aggregate_path_features([])


def test_single_link_passthrough():
    m = make_metrics(utilization=0.5, delay_ms=10.0, loss_pct=2.0, trust_level=0.8)
    features = aggregate_path_features([m])
    assert features.bottleneck_utilization == 0.5
    assert features.total_delay_ms == 10.0
    assert features.max_loss_pct == 2.0
    assert features.min_trust == 0.8


def test_bottleneck_utilization_is_max_across_links():
    links = [make_metrics(utilization=0.2), make_metrics(utilization=0.9), make_metrics(utilization=0.4)]
    assert aggregate_path_features(links).bottleneck_utilization == 0.9


def test_total_delay_is_sum_across_links():
    links = [make_metrics(delay_ms=5.0), make_metrics(delay_ms=7.0), make_metrics(delay_ms=3.0)]
    assert aggregate_path_features(links).total_delay_ms == 15.0


def test_max_loss_is_max_across_links():
    links = [make_metrics(loss_pct=1.0), make_metrics(loss_pct=8.0)]
    assert aggregate_path_features(links).max_loss_pct == 8.0


def test_min_trust_is_min_across_links():
    links = [make_metrics(trust_level=0.9), make_metrics(trust_level=0.3)]
    assert aggregate_path_features(links).min_trust == 0.3


def test_switch_throughput_and_link_to_switch_are_max_across_links():
    links = [
        make_metrics(switch_throughput_bps=100.0, link_to_switch_rate=0.1),
        make_metrics(switch_throughput_bps=500.0, link_to_switch_rate=0.6),
    ]
    features = aggregate_path_features(links)
    assert features.max_switch_throughput_bps == 500.0
    assert features.max_link_to_switch_rate == 0.6
