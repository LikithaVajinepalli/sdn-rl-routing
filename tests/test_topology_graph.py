import networkx as nx
import pytest

from topology.config import MAX_SWITCHES, MIN_SWITCHES, LinkProfile, TopologyConfig
from topology.graph import build_switch_graph, switch_name


def test_default_config_switch_count():
    config = TopologyConfig()
    graph = build_switch_graph(config)
    assert graph.number_of_nodes() == config.num_switches


@pytest.mark.parametrize("n", [MIN_SWITCHES, 7, MAX_SWITCHES])
def test_parameterized_switch_counts(n):
    config = TopologyConfig(num_switches=n, chord_offsets=[])
    graph = build_switch_graph(config)
    assert graph.number_of_nodes() == n
    assert set(graph.nodes) == {switch_name(i) for i in range(n)}


@pytest.mark.parametrize("n", [MIN_SWITCHES - 1, MAX_SWITCHES + 1])
def test_switch_count_outside_nfr2_range_rejected(n):
    with pytest.raises(ValueError):
        TopologyConfig(num_switches=n)


def test_ring_alone_is_2_edge_connected():
    """NFR3: connectivity must survive a single link failure - the ring
    topology alone (no chords) already guarantees this."""
    config = TopologyConfig(num_switches=8, chord_offsets=[])
    graph = build_switch_graph(config)
    assert nx.edge_connectivity(graph) >= 2


def test_chords_increase_redundancy_over_plain_ring():
    ring_only = build_switch_graph(TopologyConfig(num_switches=8, chord_offsets=[]))
    with_chords = build_switch_graph(TopologyConfig(num_switches=8, chord_offsets=[3]))
    assert with_chords.number_of_edges() > ring_only.number_of_edges()
    assert nx.edge_connectivity(with_chords) >= nx.edge_connectivity(ring_only)


def test_multiple_simple_paths_exist_between_any_pair():
    """The whole point of redundant links: RL should have more than one
    candidate path to choose between for any source-destination pair."""
    config = TopologyConfig(num_switches=8, chord_offsets=[3])
    graph = build_switch_graph(config)
    src, dst = switch_name(0), switch_name(4)
    paths = list(nx.all_simple_paths(graph, src, dst, cutoff=5))
    assert len(paths) > 1


def test_invalid_chord_offset_rejected():
    with pytest.raises(ValueError):
        TopologyConfig(num_switches=8, chord_offsets=[5])  # > n//2: wraps, duplicating offset 3
    with pytest.raises(ValueError):
        TopologyConfig(num_switches=8, chord_offsets=[1])  # duplicates the ring's own offset


def test_link_profile_override_applied_to_edge():
    override = LinkProfile(bw_mbps=100.0, delay_ms=1.0, loss_pct=0.0)
    config = TopologyConfig(num_switches=6, chord_offsets=[], link_overrides={(0, 1): override})
    graph = build_switch_graph(config)
    data = graph.edges[switch_name(0), switch_name(1)]
    assert data["bw_mbps"] == 100.0
    assert data["delay_ms"] == 1.0


def test_default_link_profile_used_when_no_override():
    config = TopologyConfig(num_switches=6, chord_offsets=[])
    graph = build_switch_graph(config)
    u, v = switch_name(0), switch_name(1)
    data = graph.edges[u, v]
    assert data["bw_mbps"] == config.default_link.bw_mbps
    assert data["delay_ms"] == config.default_link.delay_ms
