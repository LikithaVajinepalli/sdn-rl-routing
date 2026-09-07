"""Tabular-Q-learning-only state discretization.

A future DQN variant would skip this file entirely and consume the
PathFeatures list from rl/path_features.py directly as a continuous vector.
Keeping the discretization isolated here - rather than baked into the
environment or the agent - is what makes that swap possible without
touching rl/environment.py or rl/reward.py (see CLAUDE.md Phase 2 notes).
"""

from typing import List, Tuple

from rl.path_features import PathFeatures

# Fixed (low, high) thresholds per feature - NOT the reward weights. This is
# a deliberately separate, coarser view used only to form a Q-table lookup
# key; bin 0 ~ "clearly fine", bin 2 ~ "clearly bad". Roughly calibrated to
# the same norms reward.py/controller/metrics.py use.
_THRESHOLDS = {
    "bottleneck_utilization": (0.4, 0.75),
    "total_delay_ms": (30.0, 70.0),
    "max_loss_pct": (2.0, 10.0),
    "min_trust": (0.4, 0.7),  # inverted below: low trust -> high "badness" bin
    "max_switch_throughput_bps": (1_000_000.0, 5_000_000.0),
    "max_link_to_switch_rate": (0.4, 0.7),
}


def _bin_value(value: float, low_high: Tuple[float, float], invert: bool = False) -> int:
    low, high = low_high
    if invert:
        value = 1.0 - value
        low, high = 1.0 - high, 1.0 - low
    if value < low:
        return 0
    if value < high:
        return 1
    return 2


def _feature_bins(features: PathFeatures) -> Tuple[int, ...]:
    return (
        _bin_value(features.bottleneck_utilization, _THRESHOLDS["bottleneck_utilization"]),
        _bin_value(features.total_delay_ms, _THRESHOLDS["total_delay_ms"]),
        _bin_value(features.max_loss_pct, _THRESHOLDS["max_loss_pct"]),
        _bin_value(features.min_trust, _THRESHOLDS["min_trust"], invert=True),
        _bin_value(features.max_switch_throughput_bps, _THRESHOLDS["max_switch_throughput_bps"]),
        _bin_value(features.max_link_to_switch_rate, _THRESHOLDS["max_link_to_switch_rate"]),
    )


def discretize_state(candidate_features: List[PathFeatures]) -> Tuple[Tuple[int, ...], ...]:
    """One 6-tuple of bins per candidate path, in the same order as the
    action space (action i selects candidate_features[i]'s path)."""
    return tuple(_feature_bins(f) for f in candidate_features)
