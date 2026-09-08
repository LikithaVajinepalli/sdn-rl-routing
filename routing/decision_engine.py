"""Resolves a routing decision for a (src_mac, dst_mac) pair: RL or Dijkstra
depending on mode, validated against current link status, with automatic
fallback + logging on any RL failure - FR3's "detect congestion/failure and
reroute without manual intervention" and NFR3's "fall back to baseline if
RL agent fails".

Framework-free: operates entirely on NetworkState, HostLocationTracker and
QLearningAgent, none of which import Ryu, so this is fully unit testable
without the Linux-only OpenFlow stack installed.
"""

import logging
from dataclasses import dataclass
from typing import List, Optional

from controller.network_state import NetworkState
from rl.path_features import PathFeatures, aggregate_path_features
from routing.dijkstra import shortest_path
from routing.host_location import HostLocationTracker
from routing.path_enumeration import enumerate_k_paths, path_edges

logger = logging.getLogger("sdn_rl_routing.decision_engine")

DEFAULT_LINK_BW_MBPS = 10.0


@dataclass(frozen=True)
class RoutingDecision:
    path: List[int]                        # switch dpids, src -> dst
    mode_used: str                          # "rl" or "dijkstra" (fallback still reports "dijkstra")
    fallback_reason: Optional[str] = None   # set only when RL was asked for but couldn't be used


class RoutingDecisionEngine:
    def __init__(
        self,
        network_state: NetworkState,
        host_locations: HostLocationTracker,
        agent=None,
        mode: str = "rl",
        path_k: int = 3,
        path_max_len: int = 6,
        default_link_bw_mbps: float = DEFAULT_LINK_BW_MBPS,
    ):
        self.network_state = network_state
        self.host_locations = host_locations
        self.agent = agent
        self.mode = mode
        self.path_k = path_k
        self.path_max_len = path_max_len
        self.default_link_bw_mbps = default_link_bw_mbps

    def decide(self, src_mac: str, dst_mac: str) -> Optional[RoutingDecision]:
        src_location = self.host_locations.get(src_mac)
        dst_location = self.host_locations.get(dst_mac)
        if src_location is None or dst_location is None:
            return None  # don't know where one of the hosts is yet
        src_dpid, dst_dpid = src_location[0], dst_location[0]

        if src_dpid == dst_dpid:
            return RoutingDecision(path=[src_dpid], mode_used=self.mode)

        candidates = [
            p
            for p in enumerate_k_paths(self.network_state.graph, src_dpid, dst_dpid, self.path_k, self.path_max_len)
            if self._path_is_up(p)
        ]
        if not candidates:
            logger.warning("no viable (up) path from dpid %s to dpid %s", src_dpid, dst_dpid)
            return None

        if self.mode != "rl" or self.agent is None:
            return self._dijkstra_decision(src_dpid, dst_dpid, candidates)

        try:
            return self._rl_decision(candidates)
        except Exception as exc:  # RL must never take the network down - fall back, log, move on
            logger.warning("RL decision failed (%s) - falling back to Dijkstra", exc)
            return self._dijkstra_decision(src_dpid, dst_dpid, candidates, fallback_reason=str(exc))

    def _path_is_up(self, path: List[int]) -> bool:
        return all(self.network_state.is_edge_up(u, v) for u, v in path_edges(path))

    def _dijkstra_decision(
        self, src_dpid: int, dst_dpid: int, candidates: List[List[int]], fallback_reason: Optional[str] = None
    ) -> Optional[RoutingDecision]:
        path = shortest_path(self.network_state.graph, src_dpid, dst_dpid)
        if path is None or not self._path_is_up(path):
            # The graph-wide shortest path is unavailable/down - fall back
            # further to the best still-up enumerated candidate, if any.
            path = candidates[0] if candidates else None
        if path is None:
            return None
        return RoutingDecision(path=path, mode_used="dijkstra", fallback_reason=fallback_reason)

    def _rl_decision(self, candidates: List[List[int]]) -> RoutingDecision:
        features = [self._path_features(p) for p in candidates]
        action = self.agent.select_action(features)
        return RoutingDecision(path=candidates[action], mode_used="rl")

    def _path_features(self, path: List[int]) -> PathFeatures:
        return aggregate_path_features(self._live_link_metrics(u, v) for u, v in path_edges(path))

    def _live_link_metrics(self, dpid_a: int, dpid_b: int):
        from controller.metrics import LinkMetrics

        port = self.network_state.port_towards(dpid_a, dpid_b)
        if port is None:
            raise RuntimeError(f"no known port from dpid {dpid_a} towards dpid {dpid_b}")
        metrics = self.network_state.live_link_metrics(dpid_a, port, self.default_link_bw_mbps)
        if metrics is None:
            # No polled samples yet for this port (e.g. just discovered) -
            # treat as a neutral/unknown link rather than rejecting an
            # otherwise-fine, brand-new path.
            return LinkMetrics(
                utilization=0.0, delay_ms=5.0, loss_pct=0.0, trust_level=1.0, switch_throughput_bps=0.0, link_to_switch_rate=0.0
            )
        return metrics
