import networkx as nx
import pytest

from routing.dijkstra import shortest_path


def test_src_equals_dst():
    graph = nx.cycle_graph(5)
    assert shortest_path(graph, 0, 0) == [0]


def test_finds_shortest_hop_count_path():
    graph = nx.path_graph(5)  # 0-1-2-3-4
    assert shortest_path(graph, 0, 4) == [0, 1, 2, 3, 4]


def test_ignores_edge_weights_pure_hop_count():
    """Deliberately unweighted - see routing/dijkstra.py's docstring: the
    whole point of the baseline is that it ignores real-time conditions.
    A weighted shortest-path would prefer the low-total-weight 2-hop route;
    hop-count-only must still pick the direct (1-hop) edge."""
    graph = nx.Graph()
    graph.add_edge(0, 2, weight=100)
    graph.add_edge(0, 1, weight=1)
    graph.add_edge(1, 2, weight=1)
    assert shortest_path(graph, 0, 2) == [0, 2]


def test_disconnected_returns_none():
    graph = nx.Graph()
    graph.add_edge(0, 1)
    graph.add_edge(2, 3)
    assert shortest_path(graph, 0, 3) is None


def test_missing_node_returns_none():
    graph = nx.cycle_graph(4)
    assert shortest_path(graph, 0, 99) is None
