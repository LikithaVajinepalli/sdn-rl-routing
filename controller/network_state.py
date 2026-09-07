"""Shared, in-memory network state updated by the discovery/stats/latency Ryu
apps and read by the metrics printer, the injection API, and (later) the RL
agent. Deliberately framework-free so it's unit-testable without Ryu/Mininet.
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, FrozenSet, List, Optional, Set, Tuple

import networkx as nx

from controller.metrics import PortSample

DELAY_HISTORY_LEN = 10  # samples kept per link for jitter calculation


@dataclass
class LinkKey:
    """Identifies a directed link by (source switch, source port, dst dpid)."""

    src_dpid: int
    src_port: int
    dst_dpid: int

    def __hash__(self):
        return hash((self.src_dpid, self.src_port, self.dst_dpid))


@dataclass
class LinkStatus:
    """Operational status of a link, mutated by the injection API and read by
    the fallback logic in later phases (never trust a path over a down link)."""

    up: bool = True
    congestion_injected: bool = False


class NetworkState:
    """Thread-safe(ish) shared state. Ryu handlers run in green threads
    (eventlet), so plain locks are sufficient - no real OS-thread concurrency.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.graph = nx.Graph()  # discovered topology: switch dpids as nodes
        # A spanning tree over `graph`, recomputed on every sync_graph() call.
        # Ports on a switch-to-switch link that ISN'T in this tree are excluded
        # from flood_ports() - the topology is deliberately built with loops
        # (redundant paths for RL), and a naive learning switch that floods
        # blindly out every port would broadcast-storm across them. Real
        # routing (Phase 3) installs explicit unicast flows and isn't
        # restricted to the tree; this only constrains the Phase 1 fallback's
        # flood/broadcast behavior.
        self.tree_edges: Set[FrozenSet[int]] = set()
        self.switch_ports: Dict[int, Set[int]] = defaultdict(set)  # all ports seen per dpid
        self.port_samples: Dict[Tuple[int, int], Deque[PortSample]] = defaultdict(
            lambda: deque(maxlen=2)
        )
        self.echo_rtt_ms: Dict[int, float] = {}
        self.port_speed_mbps: Dict[Tuple[int, int], float] = {}
        self.link_delay_history_ms: Dict[LinkKey, Deque[float]] = defaultdict(
            lambda: deque(maxlen=DELAY_HISTORY_LEN)
        )
        self.link_status: Dict[LinkKey, LinkStatus] = defaultdict(LinkStatus)
        self.last_updated: float = time.time()

    def record_port_sample(self, dpid: int, port_no: int, sample: PortSample) -> None:
        with self._lock:
            self.port_samples[(dpid, port_no)].append(sample)
            self.last_updated = time.time()

    def port_sample_pair(self, dpid: int, port_no: int) -> Optional[Tuple[PortSample, PortSample]]:
        with self._lock:
            history = self.port_samples.get((dpid, port_no))
            if not history or len(history) < 2:
                return None
            return history[0], history[1]

    def record_echo_rtt(self, dpid: int, rtt_ms: float) -> None:
        with self._lock:
            self.echo_rtt_ms[dpid] = rtt_ms

    def record_port_speed(self, dpid: int, port_no: int, speed_mbps: float) -> None:
        with self._lock:
            if speed_mbps > 0:
                self.port_speed_mbps[(dpid, port_no)] = speed_mbps

    def link_bw_mbps(self, dpid: int, port_no: int, default_mbps: float) -> float:
        with self._lock:
            return self.port_speed_mbps.get((dpid, port_no), default_mbps)

    def record_link_delay(self, key: LinkKey, delay_ms: float) -> None:
        with self._lock:
            self.link_delay_history_ms[key].append(delay_ms)

    def recent_delays(self, key: LinkKey) -> List[float]:
        with self._lock:
            return list(self.link_delay_history_ms.get(key, ()))

    def set_link_status(self, key: LinkKey, **changes) -> None:
        with self._lock:
            status = self.link_status[key]
            for field_name, value in changes.items():
                setattr(status, field_name, value)

    def get_link_status(self, key: LinkKey) -> LinkStatus:
        with self._lock:
            return self.link_status[key]

    def switch_port_rates_bps(self, dpid: int, ports: List[int]) -> List[float]:
        from controller.metrics import throughput_bps

        rates = []
        for port_no in ports:
            pair = self.port_sample_pair(dpid, port_no)
            if pair:
                prev, curr = pair
                rates.append(throughput_bps(prev, curr))
        return rates

    def sync_graph(self, nodes: List[int], edges: List[Tuple[int, int, dict]]) -> None:
        """edges carry a `ports` dict mapping {dpid: local_port_no} for the two
        endpoints of that link - see topo_discovery.sync_from_ryu_topology for
        why this (rather than direction-dependent src_port/dst_port keys) is
        needed on an undirected graph."""
        with self._lock:
            self.graph.clear()
            self.graph.add_nodes_from(nodes)
            self.graph.add_edges_from(edges)
            self.tree_edges = set()
            if self.graph.number_of_nodes() > 0:
                tree = nx.minimum_spanning_tree(self.graph)
                self.tree_edges = {frozenset(e) for e in tree.edges()}

    def record_switch_port(self, dpid: int, port_no: int) -> None:
        with self._lock:
            self.switch_ports[dpid].add(port_no)

    def _neighbour_dpid_locked(self, dpid: int, port_no: int) -> Optional[int]:
        for u, v, data in self.graph.edges(data=True):
            ports = data.get("ports", {})
            if ports.get(dpid) == port_no:
                return v if u == dpid else u
        return None

    def neighbour_dpid(self, dpid: int, port_no: int) -> Optional[int]:
        with self._lock:
            return self._neighbour_dpid_locked(dpid, port_no)

    def flood_ports(self, dpid: int, in_port: int) -> Set[int]:
        """Ports safe to flood out of: every host-facing port (one with no
        known switch neighbour), plus inter-switch ports on the spanning
        tree. Never the ingress port, never an off-tree chord/redundant link
        (that would broadcast-storm around the loop)."""
        with self._lock:
            result = set()
            for port_no in self.switch_ports.get(dpid, ()):
                if port_no == in_port:
                    continue
                neighbour = self._neighbour_dpid_locked(dpid, port_no)
                if neighbour is None or frozenset({dpid, neighbour}) in self.tree_edges:
                    result.add(port_no)
            return result
