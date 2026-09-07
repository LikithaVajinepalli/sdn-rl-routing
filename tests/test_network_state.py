import networkx as nx

from controller.network_state import LinkKey, NetworkState


def test_neighbour_dpid_resolves_from_ports_mapping():
    state = NetworkState()
    state.sync_graph([1, 2], [(1, 2, {"ports": {1: 1, 2: 5}})])
    assert state.neighbour_dpid(1, 1) == 2
    assert state.neighbour_dpid(2, 5) == 1
    assert state.neighbour_dpid(1, 99) is None  # unknown port


def test_flood_ports_excludes_in_port():
    state = NetworkState()
    state.sync_graph([1, 2], [(1, 2, {"ports": {1: 1, 2: 1}})])
    state.record_switch_port(1, 1)  # switch-to-switch port
    state.record_switch_port(1, 10)  # host-facing port
    result = state.flood_ports(1, in_port=10)
    assert 10 not in result
    assert 1 in result  # tree link, safe to flood


def test_flood_ports_includes_host_ports_always():
    """A port with no discovered switch neighbour is assumed host-facing and
    always safe to flood out of (never part of a loop)."""
    state = NetworkState()
    state.sync_graph([1, 2], [(1, 2, {"ports": {1: 1, 2: 1}})])
    state.record_switch_port(1, 1)
    state.record_switch_port(1, 10)
    state.record_switch_port(1, 11)
    result = state.flood_ports(1, in_port=1)
    assert {10, 11}.issubset(result)


def test_flood_ports_excludes_off_tree_links_no_broadcast_storm():
    """A ring (loop) with no chords: a spanning tree must exclude exactly one
    edge. flood_ports must never include the port for that excluded edge -
    that's the whole point (no storm around the loop)."""
    state = NetworkState()
    nodes = [1, 2, 3, 4]
    edges = [
        (1, 2, {"ports": {1: 1, 2: 1}}),
        (2, 3, {"ports": {2: 2, 3: 1}}),
        (3, 4, {"ports": {3: 2, 4: 1}}),
        (4, 1, {"ports": {4: 2, 1: 2}}),
    ]
    state.sync_graph(nodes, edges)
    for dpid in nodes:
        state.record_switch_port(dpid, 1)
        state.record_switch_port(dpid, 2)
        state.record_switch_port(dpid, 99)  # a host port on every switch

    assert len(state.tree_edges) == 3  # 4 nodes -> spanning tree has 3 edges

    for dpid, port_no in [(1, 1), (1, 2), (2, 1), (2, 2), (3, 1), (3, 2), (4, 1), (4, 2)]:
        neighbour = state.neighbour_dpid(dpid, port_no)
        on_tree = frozenset({dpid, neighbour}) in state.tree_edges
        # flood from the switch's host port (99) and check whether this
        # inter-switch port shows up in the result - must match on_tree exactly.
        result = state.flood_ports(dpid, in_port=99)
        assert (port_no in result) == on_tree, f"dpid={dpid} port={port_no} on_tree={on_tree}"


def test_flood_ports_all_included_when_topology_already_loop_free():
    """A simple path (no cycles) - every edge is already part of the only
    possible spanning tree, so nothing should ever be excluded."""
    state = NetworkState()
    edges = [(1, 2, {"ports": {1: 1, 2: 1}}), (2, 3, {"ports": {2: 2, 3: 1}})]
    state.sync_graph([1, 2, 3], edges)
    for dpid in (1, 2, 3):
        state.record_switch_port(dpid, 1)
    state.record_switch_port(2, 2)
    state.record_switch_port(1, 99)

    assert nx.is_tree(nx.Graph([(u, v) for u, v, _ in edges]))
    result = state.flood_ports(2, in_port=1)
    assert 2 in result  # the only other inter-switch port, must be included


def test_link_status_and_delay_history_roundtrip():
    state = NetworkState()
    key = LinkKey(1, 1, 2)
    state.record_link_delay(key, 5.0)
    state.record_link_delay(key, 6.0)
    assert state.recent_delays(key) == [5.0, 6.0]

    state.set_link_status(key, up=False)
    assert state.get_link_status(key).up is False
