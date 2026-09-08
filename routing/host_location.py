"""Tracks which switch/port each host (by MAC) is attached to, learned
reactively from data-plane packet-ins on host-facing ports. Framework-free -
controller/main_app.py's packet-in handler just calls record().
"""

import threading
from typing import Dict, Optional, Tuple


class HostLocationTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._locations: Dict[str, Tuple[int, int]] = {}

    def record(self, mac: str, dpid: int, port_no: int) -> None:
        with self._lock:
            self._locations[mac] = (dpid, port_no)

    def get(self, mac: str) -> Optional[Tuple[int, int]]:
        with self._lock:
            return self._locations.get(mac)

    def dpid_of(self, mac: str) -> Optional[int]:
        location = self.get(mac)
        return location[0] if location else None
