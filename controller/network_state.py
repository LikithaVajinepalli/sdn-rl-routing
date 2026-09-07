"""Shared, in-memory network state updated by the discovery/stats/latency Ryu
apps and read by the metrics printer, the injection API, and (later) the RL
agent. Deliberately framework-free so it's unit-testable without Ryu/Mininet.
"""

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Tuple

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
        with self._lock:
            self.graph.clear()
            self.graph.add_nodes_from(nodes)
            self.graph.add_edges_from(edges)
