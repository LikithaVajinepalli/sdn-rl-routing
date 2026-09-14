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
    """Operational status of one switch port, mutated by the injection API
    and the port-status handler, read by routing/decision_engine.py's
    fallback logic (never trust a path over a down link).

    Keyed by plain (dpid, port_no) - not the directed LinkKey used for delay
    tracking below. A physical link's up/down state isn't inherently
    directional, and both natural sources of a status change only know one
    side's (dpid, port) at the time: the injection API gets them straight
    from the REST call, and an EventOFPPortStatus is scoped to one switch.
    Keying by LinkKey here would need guessing the neighbour dpid at write
    time, which - before this fix - injection_api.py did by hardcoding a
    fake dst_dpid=0, silently never matching any real lookup. See
    is_edge_up() for how both sides get checked when it matters."""

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
        # (dpid, port) -> the neighbour dpid that port led to, the last time
        # discovery saw the link. Deliberately never cleared on resync: when
        # a link goes down, ryu.topology drops it from the live graph, so
        # neighbour_dpid() starts returning None for exactly the link a
        # port-down event is asking about - a race that silently skips
        # rerouting. See main_app._port_status_handler.
        self.last_known_neighbour: Dict[Tuple[int, int], int] = {}
        # A port's real MAC, straight from the switch's port description.
        # OFPPortMod requires the caller to echo the port's actual hw_addr -
        # OVS silently rejects a port-mod carrying the wrong one (no config
        # change, no port-status event, and no error we'd see), which is
        # exactly how failure injection appeared to "succeed" while doing
        # nothing at all. See controller/injection_api.py's _set_port_down.
        self.port_hw_addr: Dict[Tuple[int, int], str] = {}
        self.link_delay_history_ms: Dict[LinkKey, Deque[float]] = defaultdict(
            lambda: deque(maxlen=DELAY_HISTORY_LEN)
        )
        self.link_status: Dict[Tuple[int, int], LinkStatus] = defaultdict(LinkStatus)
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

    def record_port_hw_addr(self, dpid: int, port_no: int, hw_addr: str) -> None:
        with self._lock:
            if hw_addr:
                self.port_hw_addr[(dpid, port_no)] = hw_addr

    def get_port_hw_addr(self, dpid: int, port_no: int) -> Optional[str]:
        with self._lock:
            return self.port_hw_addr.get((dpid, port_no))

    def link_bw_mbps(self, dpid: int, port_no: int, default_mbps: float) -> float:
        with self._lock:
            return self.port_speed_mbps.get((dpid, port_no), default_mbps)

    def record_link_delay(self, key: LinkKey, delay_ms: float) -> None:
        with self._lock:
            self.link_delay_history_ms[key].append(delay_ms)

    def recent_delays(self, key: LinkKey) -> List[float]:
        with self._lock:
            return list(self.link_delay_history_ms.get(key, ()))

    def set_link_status(self, dpid: int, port_no: int, **changes) -> None:
        with self._lock:
            status = self.link_status[(dpid, port_no)]
            for field_name, value in changes.items():
                setattr(status, field_name, value)

    def get_link_status(self, dpid: int, port_no: int) -> LinkStatus:
        with self._lock:
            return self.link_status[(dpid, port_no)]

    def is_edge_up(self, dpid_a: int, dpid_b: int) -> bool:
        """An undirected edge is up only if *neither* side's port has been
        marked down - a link failure reported from either end is enough to
        distrust the whole edge, since we may not know which side actually
        detected it first."""
        with self._lock:
            port_a = self._port_towards_locked(dpid_a, dpid_b)
            port_b = self._port_towards_locked(dpid_b, dpid_a)
            if port_a is not None and not self.link_status[(dpid_a, port_a)].up:
                return False
            if port_b is not None and not self.link_status[(dpid_b, port_b)].up:
                return False
            return True

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
            for u, v, data in self.graph.edges(data=True):
                ports = data.get("ports", {})
                if ports.get(u) is not None:
                    self.last_known_neighbour[(u, ports[u])] = v
                if ports.get(v) is not None:
                    self.last_known_neighbour[(v, ports[v])] = u

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

    def neighbour_dpid_ever(self, dpid: int, port_no: int) -> Optional[int]:
        """Like neighbour_dpid(), but falls back to the last neighbour this
        port was ever seen connected to. Use this when reacting to a link
        going DOWN - by then discovery has usually already removed the dead
        link from the live graph, so the live lookup returns None."""
        with self._lock:
            live = self._neighbour_dpid_locked(dpid, port_no)
            if live is not None:
                return live
            return self.last_known_neighbour.get((dpid, port_no))

    def _port_towards_locked(self, dpid: int, neighbour_dpid: int) -> Optional[int]:
        for u, v, data in self.graph.edges(data=True):
            if {u, v} == {dpid, neighbour_dpid}:
                return data.get("ports", {}).get(dpid)
        return None

    def port_towards(self, dpid: int, neighbour_dpid: int) -> Optional[int]:
        """The local port on `dpid` that connects toward `neighbour_dpid`,
        per the currently discovered topology."""
        with self._lock:
            return self._port_towards_locked(dpid, neighbour_dpid)

    def live_link_metrics(self, dpid: int, port_no: int, default_bw_mbps: float):
        """Composes a live controller.metrics.LinkMetrics for one switch
        port from current poll/probe data - the single place both the
        Phase 1 metrics table and Phase 3's routing decisions read live
        per-link state from, so they can never disagree."""
        from controller.metrics import compute_link_metrics, fallback_delay_ms

        pair = self.port_sample_pair(dpid, port_no)
        if pair is None:
            return None
        prev, curr = pair
        bw = self.link_bw_mbps(dpid, port_no, default_bw_mbps)
        neighbour = self.neighbour_dpid(dpid, port_no)
        key = LinkKey(dpid, port_no, neighbour or 0)
        recent_delays = self.recent_delays(key)
        if recent_delays:
            delay_ms = recent_delays[-1]
        else:
            with self._lock:
                src_rtt = self.echo_rtt_ms.get(dpid, 0.0)
                dst_rtt = self.echo_rtt_ms.get(neighbour, 0.0) if neighbour else 0.0
            delay_ms = fallback_delay_ms(src_rtt, dst_rtt)
        switch_ports = [p for (d, p) in self.port_samples if d == dpid]
        switch_rates = self.switch_port_rates_bps(dpid, switch_ports)
        return compute_link_metrics(prev, curr, bw, delay_ms, recent_delays, switch_rates)

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
