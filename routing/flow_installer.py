"""Pure translation of a switch-level path + host MAC pair into the flow
rules needed to route between them - no Ryu import, so this is unit
testable. controller/flow_manager.py is the thin Ryu glue that actually
sends these as OFPFlowMod messages.
"""

from dataclasses import dataclass
from typing import Callable, List, Optional


@dataclass(frozen=True)
class FlowInstallation:
    dpid: int
    eth_src: str
    eth_dst: str
    out_port: int


def build_path_flows(
    path: List[int],
    port_towards: Callable[[int, int], Optional[int]],
    src_mac: str,
    dst_mac: str,
    dst_host_port: int,
) -> Optional[List[FlowInstallation]]:
    """One FlowInstallation per switch in `path`, all matching (src_mac ->
    dst_mac) traffic. Every switch but the last outputs toward the next hop
    (looked up via port_towards); the last switch outputs to dst_host_port -
    the port the destination host is actually attached to. Returns None if
    any hop's port can't be resolved (a stale/incomplete topology view)."""
    if not path:
        return None
    installations = []
    for i, dpid in enumerate(path):
        if i < len(path) - 1:
            out_port = port_towards(dpid, path[i + 1])
        else:
            out_port = dst_host_port
        if out_port is None:
            return None
        installations.append(FlowInstallation(dpid=dpid, eth_src=src_mac, eth_dst=dst_mac, out_port=out_port))
    return installations


def build_bidirectional_flows(
    path: List[int],
    port_towards: Callable[[int, int], Optional[int]],
    src_mac: str,
    dst_mac: str,
    src_host_port: int,
    dst_host_port: int,
) -> Optional[List[FlowInstallation]]:
    """Forward (src->dst) plus reverse (dst->src) flows along the same
    physical path - symmetric routing, so e.g. TCP/ICMP replies take the
    same path back rather than a possibly-different one requiring a second
    independent routing decision."""
    forward = build_path_flows(path, port_towards, src_mac, dst_mac, dst_host_port)
    reverse = build_path_flows(list(reversed(path)), port_towards, dst_mac, src_mac, src_host_port)
    if forward is None or reverse is None:
        return None
    return forward + reverse
