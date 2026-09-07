"""Builds the switch-level NetworkX graph for a TopologyConfig.

This is the "design-time" graph used by: the Mininet topo builder (to know
which switches to link), the test suite (to assert redundancy), and Phase 3's
path enumeration as a fallback/reference. It intentionally does NOT represent
what the controller discovers live via LLDP (topology/discovery is Ryu's job)
- it's the ground-truth graph the topology script itself is constructing.
"""

import networkx as nx

from topology.config import TopologyConfig


def switch_name(index: int) -> str:
    return f"s{index + 1}"


def build_switch_graph(config: TopologyConfig) -> nx.Graph:
    """Circulant graph on config.num_switches nodes with ring + chord offsets.

    Node labels are switch names ("s1".."sN") rather than raw integers so this
    graph can be used directly wherever switch DPIDs/names are expected.
    """
    raw = nx.circulant_graph(config.num_switches, config.circulant_offsets())
    graph = nx.relabel_nodes(raw, {i: switch_name(i) for i in raw.nodes})
    for u, v in graph.edges:
        i, j = int(u[1:]) - 1, int(v[1:]) - 1
        profile = config.link_profile(i, j)
        graph.edges[u, v]["bw_mbps"] = profile.bw_mbps
        graph.edges[u, v]["delay_ms"] = profile.delay_ms
        graph.edges[u, v]["loss_pct"] = profile.loss_pct
    return graph
