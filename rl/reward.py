"""Reward function for the RL routing agent. Computed from a candidate
path's POST-decision aggregated features - i.e. after rl/environment.py (or
later, the live controller) simulates/measures the new traffic demand
actually landing on the chosen path - so the agent is rewarded for the real
consequence of its choice, not just the path's prior/pre-decision state.

See /docs/rl-design.md for the full rationale behind the weights below.
"""

from rl.path_features import PathFeatures

# Weights sum to 1.0. Utilization is weighted heaviest: avoiding congestion
# (not just minimizing hop count) is this project's whole premise versus a
# plain Dijkstra baseline. Loss and delay follow; trust last since it's
# already a composite signal derived from the other two (see
# controller/metrics.py's link_trust_level).
WEIGHT_UTILIZATION = 0.35
WEIGHT_DELAY = 0.25
WEIGHT_LOSS = 0.25
WEIGHT_TRUST = 0.15

DELAY_NORM_MS = 100.0   # a path with >=100ms total delay is treated as "as bad as it gets"
LOSS_NORM_PCT = 20.0    # matches controller/metrics.py's link_trust_level loss_norm


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def compute_reward(features: PathFeatures) -> float:
    """Returns a value in [0, 1]: 1.0 for an ideal path (no utilization,
    delay, loss; full trust), 0.0 for the worst case on every dimension."""
    utilization_penalty = _clamp01(features.bottleneck_utilization)
    delay_penalty = _clamp01(features.total_delay_ms / DELAY_NORM_MS)
    loss_penalty = _clamp01(features.max_loss_pct / LOSS_NORM_PCT)
    distrust_penalty = _clamp01(1.0 - features.min_trust)

    penalty = (
        WEIGHT_UTILIZATION * utilization_penalty
        + WEIGHT_DELAY * delay_penalty
        + WEIGHT_LOSS * loss_penalty
        + WEIGHT_TRUST * distrust_penalty
    )
    return 1.0 - penalty
