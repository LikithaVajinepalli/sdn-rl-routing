"""FR1 topology auto-discovery: thin wrapper around Ryu's built-in LLDP-based
switches/links tracker (ryu.topology.switches), converted into the shared
NetworkState's NetworkX graph. We intentionally reuse Ryu's LLDP discovery
rather than reimplementing LLDP - it's battle-tested and gives us switch/link
add/remove events for free via ryu.topology.event.
"""

from ryu.topology import event
from ryu.topology.api import get_link, get_switch

from controller.network_state import NetworkState


def sync_from_ryu_topology(app, network_state: NetworkState) -> None:
    """Pull the current switch/link view from ryu.topology.api and mirror it
    into network_state.graph. `app` is the RyuApp instance calling this (Ryu's
    topology API needs an app reference to talk to the topology discovery
    app)."""
    switches = get_switch(app, None)
    links = get_link(app, None)

    nodes = [sw.dp.id for sw in switches]
    edges = []
    for link in links:
        edges.append(
            (
                link.src.dpid,
                link.dst.dpid,
                {"src_port": link.src.port_no, "dst_port": link.dst.port_no},
            )
        )
    network_state.sync_graph(nodes, edges)


def is_topology_event(ev) -> bool:
    return isinstance(
        ev,
        (
            event.EventSwitchEnter,
            event.EventSwitchLeave,
            event.EventLinkAdd,
            event.EventLinkDelete,
            event.EventPortAdd,
            event.EventPortDelete,
            event.EventPortModify,
        ),
    )
