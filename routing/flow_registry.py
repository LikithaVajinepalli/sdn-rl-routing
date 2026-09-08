"""Tracks currently-installed host-to-host routes, so a link-failure event
can quickly find and reroute the flows that were actually using it. Pure -
no Ryu import.
"""

import threading
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional

from routing.path_enumeration import path_edges


@dataclass(frozen=True)
class FlowRecord:
    src_mac: str
    dst_mac: str
    path: List[int]   # switch dpids, in the src_mac -> dst_mac direction
    mode: str          # "rl" or "dijkstra" - whichever strategy actually chose this path


class ActiveFlowRegistry:
    def __init__(self):
        self._lock = threading.Lock()
        self._flows: Dict[FrozenSet[str], FlowRecord] = {}

    @staticmethod
    def _key(mac_a: str, mac_b: str) -> FrozenSet[str]:
        return frozenset({mac_a, mac_b})

    def record(self, record: FlowRecord) -> None:
        with self._lock:
            self._flows[self._key(record.src_mac, record.dst_mac)] = record

    def get(self, mac_a: str, mac_b: str) -> Optional[FlowRecord]:
        with self._lock:
            return self._flows.get(self._key(mac_a, mac_b))

    def remove(self, mac_a: str, mac_b: str) -> None:
        with self._lock:
            self._flows.pop(self._key(mac_a, mac_b), None)

    def flows_using_edge(self, dpid_a: int, dpid_b: int) -> List[FlowRecord]:
        edge = frozenset({dpid_a, dpid_b})
        with self._lock:
            return [r for r in self._flows.values() if edge in (frozenset(e) for e in path_edges(r.path))]

    def all_flows(self) -> List[FlowRecord]:
        with self._lock:
            return list(self._flows.values())
