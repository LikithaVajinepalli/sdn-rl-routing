"""A lightweight, NetworkX-based simulated environment for training the RL
routing agent standalone, without Mininet/Ryu running (see CLAUDE.md Phase 2
notes for why: training runs in seconds instead of depending on a live WSL2
session - at the cost of the congestion dynamics being our own approximation
rather than measured from a real network).

One "step" = one independent routing decision for a randomly sampled
(src, dst) pair, played out against a shared, evolving simulated per-link
load - so the agent's own choices affect what it observes later in the
episode, which is what creates a load-balancing incentive rather than a
trivial "always pick the same best path" one. Note this makes each episode
closer to a shared-load contextual bandit than a strict per-trajectory MDP
(see docs/rl-design.md for the honest write-up of that trade-off).
"""

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import networkx as nx

from controller.metrics import LinkMetrics, link_trust_level
from rl.path_features import PathFeatures, aggregate_path_features
from rl.reward import compute_reward
from routing.path_enumeration import enumerate_k_paths, path_edges


@dataclass
class EnvConfig:
    steps_per_episode: int = 50
    path_k: int = 3
    path_max_len: int = 6
    link_bw_mbps: float = 10.0
    # Calibrated so that always taking the shortest path (ignoring
    # congestion) actually overloads chokepoint links under sustained random
    # demand - otherwise minimizing hop count trivially dominates and there
    # is nothing for the agent to learn (see docs/rl-design.md "environment
    # calibration" for the empirical check that led to these values).
    demand_mbps_range: Tuple[float, float] = (1.0, 6.0)
    load_decay: float = 0.93            # background load decays toward baseline each step
    background_noise_std: float = 0.05  # random congestion jitter per step, as a bw fraction
    base_delay_ms: float = 5.0          # matches topology/config.py's default LinkProfile
    seed: Optional[int] = None


class SimulatedRoutingEnv:
    """reset() -> initial_state (List[PathFeatures] for the first sampled pair)
    step(action_index) -> (next_state, reward, done, info)
    """

    def __init__(self, graph: nx.Graph, config: EnvConfig = None):
        self.graph = graph
        self.config = config or EnvConfig()
        self._rng = random.Random(self.config.seed)
        self._nodes = list(graph.nodes)
        self._load_mbps: Dict[Tuple, float] = {}
        self._step_count = 0
        self._current_src = None
        self._current_dst = None
        self._current_paths: List[List] = []
        self._current_demand_mbps = 0.0

    def reset(self) -> List[PathFeatures]:
        self._load_mbps = {tuple(sorted(e)): 0.0 for e in self.graph.edges}
        self._step_count = 0
        return self._new_demand()

    def _edge_key(self, u, v) -> Tuple:
        return tuple(sorted((u, v)))

    def _new_demand(self) -> List[PathFeatures]:
        """Samples a new (src, dst) pair, enumerates its candidate paths, and
        returns their current (pre-decision) PathFeatures - the state the
        agent observes before choosing an action."""
        src = dst = None
        paths: List[List] = []
        for _ in range(20):  # bounded retries for a disconnected/no-path sample
            src, dst = self._rng.sample(self._nodes, 2)
            paths = enumerate_k_paths(self.graph, src, dst, self.config.path_k, self.config.path_max_len)
            if paths:
                break

        self._current_src, self._current_dst = src, dst
        self._current_paths = paths
        self._current_demand_mbps = self._rng.uniform(*self.config.demand_mbps_range)
        return [self._path_features(p) for p in paths]

    def _link_metrics_for_edge(self, edge) -> LinkMetrics:
        key = self._edge_key(*edge)
        load = self._load_mbps.get(key, 0.0)
        bw = self.config.link_bw_mbps
        utilization = max(0.0, min(1.0, load / bw)) if bw > 0 else 0.0

        # Congestion begets loss/delay/jitter in the simulation - a simple,
        # documented approximation, not a measured relationship. Effects
        # ramp in once utilization crosses 80%.
        overload = max(0.0, utilization - 0.8) / 0.2
        loss_pct = overload * 15.0
        delay_ms = self.config.base_delay_ms * (1.0 + 3.0 * overload)
        jitter_ms = overload * 10.0
        trust = link_trust_level(loss_pct, jitter_ms, error_rate_per_sec=0.0)

        return LinkMetrics(
            utilization=utilization,
            delay_ms=delay_ms,
            loss_pct=loss_pct,
            trust_level=trust,
            switch_throughput_bps=load * 1_000_000 / 8,  # crude one-link proxy
            link_to_switch_rate=utilization,             # simplification for the simulator
        )

    def _path_features(self, path: List) -> PathFeatures:
        edges = path_edges(path)
        metrics = [self._link_metrics_for_edge(e) for e in edges]
        return aggregate_path_features(metrics)

    def step(self, action_index: int):
        if not self._current_paths:
            # No path existed for the sampled pair (shouldn't normally
            # happen in a connected topology, but stay safe).
            done = self._advance()
            next_state = self._new_demand()
            return next_state, 0.0, done, {"no_path": True}

        chosen_path = self._current_paths[action_index]
        for edge in path_edges(chosen_path):
            key = self._edge_key(*edge)
            self._load_mbps[key] = self._load_mbps.get(key, 0.0) + self._current_demand_mbps

        reward = compute_reward(self._path_features(chosen_path))
        info = {"src": self._current_src, "dst": self._current_dst, "chosen_path": chosen_path}

        self._decay_background_load()
        done = self._advance()
        next_state = self._new_demand()
        return next_state, reward, done, info

    def _decay_background_load(self) -> None:
        for key in list(self._load_mbps):
            noise = self._rng.gauss(0, self.config.background_noise_std) * self.config.link_bw_mbps
            self._load_mbps[key] = max(0.0, self._load_mbps[key] * self.config.load_decay + noise)

    def _advance(self) -> bool:
        self._step_count += 1
        return self._step_count >= self.config.steps_per_episode

    @property
    def current_candidate_paths(self) -> List[List]:
        return self._current_paths
