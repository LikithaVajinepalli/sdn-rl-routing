"""Pure derived-metric functions for the stats collector (FR1).

No Ryu/Mininet imports on purpose: this module is unit-testable on any
platform and is the single place that defines what each of the six RL state
features actually means. See /docs/architecture.md for the rationale behind
each formula.
"""

from dataclasses import dataclass
from statistics import pstdev
from typing import Iterable, List, Optional


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class PortSample:
    """One OFPPortStatsReply snapshot for a single switch port, taken at `timestamp`
    (seconds, monotonic clock). Field names mirror the OpenFlow 1.3 port stats body."""

    timestamp: float
    tx_bytes: int
    rx_bytes: int
    tx_packets: int
    rx_packets: int
    tx_dropped: int = 0
    rx_dropped: int = 0
    tx_errors: int = 0
    rx_errors: int = 0


def throughput_bps(prev: PortSample, curr: PortSample, direction: str = "tx") -> float:
    """Bytes/sec on the link between two poll samples."""
    dt = curr.timestamp - prev.timestamp
    if dt <= 0:
        return 0.0
    delta = getattr(curr, f"{direction}_bytes") - getattr(prev, f"{direction}_bytes")
    return max(0.0, delta) / dt


def link_utilization(prev: PortSample, curr: PortSample, bw_mbps: float, direction: str = "tx") -> float:
    """Fraction of link capacity in use, in [0, 1]."""
    if bw_mbps <= 0:
        return 0.0
    bits_per_sec = throughput_bps(prev, curr, direction) * 8
    capacity_bps = bw_mbps * 1_000_000
    return _clamp(bits_per_sec / capacity_bps)


def packet_loss_pct(prev: PortSample, curr: PortSample, direction: str = "tx") -> float:
    """Percentage of attempted packets that were dropped/errored on this port
    between two samples. Attempted = packets that got sent + those that didn't.
    """
    packets_delta = max(0, getattr(curr, f"{direction}_packets") - getattr(prev, f"{direction}_packets"))
    dropped_delta = max(0, getattr(curr, f"{direction}_dropped") - getattr(prev, f"{direction}_dropped"))
    errors_delta = max(0, getattr(curr, f"{direction}_errors") - getattr(prev, f"{direction}_errors"))
    failed = dropped_delta + errors_delta
    attempted = packets_delta + failed
    if attempted == 0:
        return 0.0
    return 100.0 * failed / attempted


def one_way_delay_ms(round_trip_ms: float) -> float:
    """Hybrid delay estimate: half the measured echo/LLDP round-trip time.
    See controller/latency_probe.py for how round_trip_ms is measured, and
    topology/config.py's LinkProfile.delay_ms for the injectable tc-netem
    ground truth used to validate this estimate in controlled tests."""
    return max(0.0, round_trip_ms) / 2.0


def delay_jitter_ms(recent_delays_ms: Iterable[float]) -> float:
    """Population stdev of recent one-way delay samples - a stability signal
    feeding into link_trust_level."""
    values: List[float] = list(recent_delays_ms)
    if len(values) < 2:
        return 0.0
    return pstdev(values)


def link_trust_level(
    loss_pct: float,
    jitter_ms: float,
    error_rate_per_sec: float,
    *,
    loss_norm: float = 20.0,
    jitter_norm: float = 50.0,
    error_norm: float = 5.0,
    weights: tuple = (0.5, 0.3, 0.2),
) -> float:
    """Composite reliability score in [0, 1], 1 = fully trustworthy.

    trust = 1 - (w_loss * norm(loss) + w_jitter * norm(jitter) + w_err * norm(errors))

    Each raw signal is normalized against a "this is clearly bad" threshold
    (loss_norm %, jitter_norm ms, error_norm errors/sec) and clamped to
    [0, 1] before weighting, so one very bad signal can't be cancelled out
    by two good ones producing a falsely-high trust score.
    """
    w_loss, w_jitter, w_err = weights
    norm_loss = _clamp(loss_pct / loss_norm)
    norm_jitter = _clamp(jitter_ms / jitter_norm)
    norm_err = _clamp(error_rate_per_sec / error_norm)
    penalty = w_loss * norm_loss + w_jitter * norm_jitter + w_err * norm_err
    return _clamp(1.0 - penalty)


def switch_throughput_rate(port_rates_bps: Iterable[float]) -> float:
    """Aggregate load on a switch: sum of bytes/sec across all its active ports."""
    return sum(max(0.0, r) for r in port_rates_bps)


def link_to_switch_rate(link_rate_bps: float, switch_total_bps: float) -> float:
    """Share of a switch's total traffic carried by one specific link, in [0, 1].
    Surfaces load imbalance across a switch's redundant links even when the
    switch overall isn't congested."""
    if switch_total_bps <= 0:
        return 0.0
    return _clamp(link_rate_bps / switch_total_bps)


@dataclass(frozen=True)
class LinkMetrics:
    """The six RL state features for a single directed link, at one poll tick."""

    utilization: float
    delay_ms: float
    loss_pct: float
    trust_level: float
    switch_throughput_bps: float
    link_to_switch_rate: float


def compute_link_metrics(
    prev: PortSample,
    curr: PortSample,
    bw_mbps: float,
    round_trip_ms: float,
    recent_delays_ms: Iterable[float],
    switch_port_rates_bps: Iterable[float],
    error_rate_per_sec: Optional[float] = None,
) -> LinkMetrics:
    """Convenience wrapper computing all six features for one link/port in
    one call, given the raw samples plus the switch-wide port rates needed
    for the two switch-relative features."""
    util = link_utilization(prev, curr, bw_mbps)
    delay = one_way_delay_ms(round_trip_ms)
    loss = packet_loss_pct(prev, curr)
    jitter = delay_jitter_ms(recent_delays_ms)
    dt = curr.timestamp - prev.timestamp
    err_rate = error_rate_per_sec
    if err_rate is None:
        err_rate = max(0, curr.tx_errors - prev.tx_errors) / dt if dt > 0 else 0.0
    trust = link_trust_level(loss, jitter, err_rate)
    link_rate = throughput_bps(prev, curr)
    switch_total = switch_throughput_rate(switch_port_rates_bps)
    share = link_to_switch_rate(link_rate, switch_total)
    return LinkMetrics(
        utilization=util,
        delay_ms=delay,
        loss_pct=loss,
        trust_level=trust,
        switch_throughput_bps=switch_total,
        link_to_switch_rate=share,
    )
