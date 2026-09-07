"""Aggregates per-link metrics (controller/metrics.py's LinkMetrics) along a
candidate path into path-level features - this is what the RL agent
actually observes as "state" for one candidate path. Framework-free, so it
works identically whether the LinkMetrics came from live Ryu polling
(Phase 1) or rl/environment.py's simulator (Phase 2).
"""

from dataclasses import dataclass
from typing import Iterable

from controller.metrics import LinkMetrics


@dataclass(frozen=True)
class PathFeatures:
    """One entry per candidate path. Kept as full-fidelity continuous values
    on purpose - rl/state.py is the only place that discretizes these (for
    the tabular agent); a future DQN would consume this dataclass directly.
    """

    bottleneck_utilization: float      # max link utilization along the path
    total_delay_ms: float              # sum of per-link delay (end-to-end estimate)
    max_loss_pct: float                # worst single-hop loss - one bad hop hurts the path
    min_trust: float                   # weakest link's trust level
    max_switch_throughput_bps: float   # busiest switch's throughput along the path
    max_link_to_switch_rate: float     # worst link-to-switch load share along the path


def aggregate_path_features(link_metrics: Iterable[LinkMetrics]) -> PathFeatures:
    metrics = list(link_metrics)
    if not metrics:
        raise ValueError("aggregate_path_features requires at least one link's metrics")
    return PathFeatures(
        bottleneck_utilization=max(m.utilization for m in metrics),
        total_delay_ms=sum(m.delay_ms for m in metrics),
        max_loss_pct=max(m.loss_pct for m in metrics),
        min_trust=min(m.trust_level for m in metrics),
        max_switch_throughput_bps=max(m.switch_throughput_bps for m in metrics),
        max_link_to_switch_rate=max(m.link_to_switch_rate for m in metrics),
    )
