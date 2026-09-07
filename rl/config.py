"""Hyperparameters for Phase 2 training. See /docs/rl-design.md for the
values actually used in the reported convergence run and why."""

from dataclasses import dataclass


@dataclass
class TrainingConfig:
    num_episodes: int = 4000
    steps_per_episode: int = 50
    alpha: float = 0.1
    # Deliberately low: each "next state" is a freshly-sampled, unrelated
    # (src, dst) pair (see rl/environment.py), not a true continuation of
    # this decision's trajectory - the only real link between steps is the
    # shared background load. A high gamma bootstraps heavily off a next
    # state's value that has little to do with the action just taken, which
    # empirically made the agent perform *worse* than a naive shortest-path
    # baseline (gamma=0.9 -> 0.596 mean eval reward) versus a low gamma that
    # treats this closer to what it actually is, a shared-load contextual
    # bandit (gamma=0.3 -> 0.652, beating shortest-path's ~0.60). See
    # docs/rl-design.md for the full comparison.
    gamma: float = 0.3
    epsilon_start: float = 1.0
    epsilon_end: float = 0.05
    epsilon_decay: float = 0.995
    path_k: int = 3
    path_max_len: int = 6
    seed: int = 42
    model_out: str = "models/q_agent.pkl"
    log_out: str = "models/training_log.csv"
    plot_out: str = "models/reward_curve.png"
