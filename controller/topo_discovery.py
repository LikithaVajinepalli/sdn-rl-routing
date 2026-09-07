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

    # ryu.topology.api.get_link returns each physical link as TWO Link
    # objects (A->B and B->A). NetworkX's Graph is undirected, so naively
    # adding both as separate (u, v, {...}) edges just overwrites the same
    # undirected edge's attributes with whichever direction was added last -
    # silently losing one endpoint's port number. Merge both directions'
    # port info into a single `ports` dict keyed by dpid instead.
    edge_ports: dict = {}
    for link in links:
        a, b = link.src.dpid, link.dst.dpid
        key = (min(a, b), max(a, b))
        edge_ports.setdefault(key, {})[link.src.dpid] = link.src.port_no

    edges = [(a, b, {"ports": ports}) for (a, b), ports in edge_ports.items()]
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
