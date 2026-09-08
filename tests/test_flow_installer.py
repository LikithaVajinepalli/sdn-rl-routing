from routing.flow_installer import FlowInstallation, build_bidirectional_flows, build_path_flows


def make_port_towards(edges):
    """edges: {(from_dpid, to_dpid): port_no}"""

    def port_towards(dpid, neighbour):
        return edges.get((dpid, neighbour))

    return port_towards


def test_empty_path_returns_none():
    assert build_path_flows([], lambda a, b: 1, "s", "d", 1) is None


def test_single_switch_path_outputs_directly_to_dst_host_port():
    result = build_path_flows([1], lambda a, b: None, "src_mac", "dst_mac", dst_host_port=7)
    assert result == [FlowInstallation(dpid=1, eth_src="src_mac", eth_dst="dst_mac", out_port=7)]


def test_multi_hop_path_outputs_toward_next_hop_except_last():
    port_towards = make_port_towards({(1, 2): 3, (2, 3): 4})
    result = build_path_flows([1, 2, 3], port_towards, "src_mac", "dst_mac", dst_host_port=9)
    assert result == [
        FlowInstallation(dpid=1, eth_src="src_mac", eth_dst="dst_mac", out_port=3),
        FlowInstallation(dpid=2, eth_src="src_mac", eth_dst="dst_mac", out_port=4),
        FlowInstallation(dpid=3, eth_src="src_mac", eth_dst="dst_mac", out_port=9),
    ]


def test_unresolvable_port_returns_none():
    port_towards = make_port_towards({(1, 2): 3})  # missing (2,3)
    result = build_path_flows([1, 2, 3], port_towards, "src_mac", "dst_mac", dst_host_port=9)
    assert result is None


def test_bidirectional_flows_cover_both_directions():
    port_towards = make_port_towards({(1, 2): 3, (2, 1): 5})
    result = build_bidirectional_flows(
        [1, 2], port_towards, src_mac="A", dst_mac="B", src_host_port=10, dst_host_port=20
    )
    assert result == [
        FlowInstallation(dpid=1, eth_src="A", eth_dst="B", out_port=3),   # forward hop 1 -> 2
        FlowInstallation(dpid=2, eth_src="A", eth_dst="B", out_port=20),  # forward: reaches dst host
        FlowInstallation(dpid=2, eth_src="B", eth_dst="A", out_port=5),   # reverse hop 2 -> 1
        FlowInstallation(dpid=1, eth_src="B", eth_dst="A", out_port=10),  # reverse: reaches src host
    ]


def test_bidirectional_flows_none_if_either_direction_unresolvable():
    port_towards = make_port_towards({(1, 2): 3})  # (2,1) missing
    result = build_bidirectional_flows([1, 2], port_towards, "A", "B", src_host_port=10, dst_host_port=20)
    assert result is None
