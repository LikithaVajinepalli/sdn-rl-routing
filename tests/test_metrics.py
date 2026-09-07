import pytest

from controller.metrics import (
    PortSample,
    compute_link_metrics,
    delay_jitter_ms,
    link_to_switch_rate,
    link_trust_level,
    link_utilization,
    one_way_delay_ms,
    packet_loss_pct,
    switch_throughput_rate,
    throughput_bps,
)


def make_sample(t, tx_bytes=0, rx_bytes=0, tx_packets=0, rx_packets=0, tx_dropped=0, tx_errors=0):
    return PortSample(
        timestamp=t,
        tx_bytes=tx_bytes,
        rx_bytes=rx_bytes,
        tx_packets=tx_packets,
        rx_packets=rx_packets,
        tx_dropped=tx_dropped,
        rx_dropped=0,
        tx_errors=tx_errors,
        rx_errors=0,
    )


def test_throughput_bps_basic():
    prev = make_sample(0, tx_bytes=0)
    curr = make_sample(1, tx_bytes=125_000)  # 125,000 bytes in 1s = 1 Mbit/s
    assert throughput_bps(prev, curr) == 125_000


def test_throughput_bps_zero_dt_is_safe():
    prev = make_sample(1.0, tx_bytes=1000)
    curr = make_sample(1.0, tx_bytes=2000)
    assert throughput_bps(prev, curr) == 0.0


def test_link_utilization_full_capacity():
    prev = make_sample(0, tx_bytes=0)
    curr = make_sample(1, tx_bytes=1_250_000)  # 10 Mbit/s over 1 Mbps... let's use bw=10
    util = link_utilization(prev, curr, bw_mbps=10.0)
    assert util == pytest.approx(1.0)


def test_link_utilization_clamped_above_capacity():
    prev = make_sample(0, tx_bytes=0)
    curr = make_sample(1, tx_bytes=100_000_000)
    util = link_utilization(prev, curr, bw_mbps=10.0)
    assert util == 1.0


def test_link_utilization_zero_bandwidth_is_safe():
    prev = make_sample(0, tx_bytes=0)
    curr = make_sample(1, tx_bytes=1000)
    assert link_utilization(prev, curr, bw_mbps=0.0) == 0.0


def test_packet_loss_pct_no_loss():
    prev = make_sample(0, tx_packets=100)
    curr = make_sample(1, tx_packets=200)
    assert packet_loss_pct(prev, curr) == 0.0


def test_packet_loss_pct_with_drops():
    prev = make_sample(0, tx_packets=100, tx_dropped=0)
    curr = make_sample(1, tx_packets=190, tx_dropped=10)  # 90 sent + 10 dropped = 100 attempted
    assert packet_loss_pct(prev, curr) == pytest.approx(10.0)


def test_packet_loss_pct_no_traffic_is_zero_not_nan():
    prev = make_sample(0)
    curr = make_sample(1)
    assert packet_loss_pct(prev, curr) == 0.0


def test_one_way_delay_is_half_round_trip():
    assert one_way_delay_ms(20.0) == 10.0


def test_one_way_delay_never_negative():
    assert one_way_delay_ms(-5.0) == 0.0


def test_delay_jitter_needs_at_least_two_samples():
    assert delay_jitter_ms([5.0]) == 0.0
    assert delay_jitter_ms([]) == 0.0


def test_delay_jitter_zero_for_constant_delay():
    assert delay_jitter_ms([10.0, 10.0, 10.0]) == 0.0


def test_delay_jitter_positive_for_varying_delay():
    assert delay_jitter_ms([5.0, 15.0, 5.0, 15.0]) > 0.0


def test_link_trust_level_perfect_link():
    assert link_trust_level(loss_pct=0.0, jitter_ms=0.0, error_rate_per_sec=0.0) == 1.0


def test_link_trust_level_degrades_with_loss():
    good = link_trust_level(loss_pct=0.0, jitter_ms=0.0, error_rate_per_sec=0.0)
    bad = link_trust_level(loss_pct=15.0, jitter_ms=0.0, error_rate_per_sec=0.0)
    assert bad < good


def test_link_trust_level_never_below_zero_even_with_extreme_inputs():
    assert link_trust_level(loss_pct=1000.0, jitter_ms=1000.0, error_rate_per_sec=1000.0) == 0.0


def test_switch_throughput_rate_sums_ports():
    assert switch_throughput_rate([100.0, 200.0, 300.0]) == 600.0


def test_switch_throughput_rate_ignores_negative_readings():
    assert switch_throughput_rate([100.0, -50.0]) == 100.0


def test_link_to_switch_rate_share():
    assert link_to_switch_rate(link_rate_bps=250.0, switch_total_bps=1000.0) == pytest.approx(0.25)


def test_link_to_switch_rate_zero_switch_total_is_safe():
    assert link_to_switch_rate(link_rate_bps=250.0, switch_total_bps=0.0) == 0.0


def test_compute_link_metrics_end_to_end():
    prev = make_sample(0, tx_bytes=0, tx_packets=100)
    curr = make_sample(1, tx_bytes=1_250_000, tx_packets=200)
    result = compute_link_metrics(
        prev,
        curr,
        bw_mbps=10.0,
        round_trip_ms=20.0,
        recent_delays_ms=[9.0, 10.0, 11.0],
        switch_port_rates_bps=[1_250_000, 500_000],
    )
    assert result.utilization == pytest.approx(1.0)
    assert result.delay_ms == pytest.approx(10.0)
    assert result.loss_pct == 0.0
    assert 0.0 <= result.trust_level <= 1.0
    assert result.switch_throughput_bps == pytest.approx(1_750_000)
    assert result.link_to_switch_rate == pytest.approx(1_250_000 / 1_750_000)
