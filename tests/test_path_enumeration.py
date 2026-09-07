import networkx as nx
import pytest

from routing.path_enumeration import enumerate_k_paths, path_edges


def _ring_graph(n=6):
    return nx.cycle_graph(n)


def test_src_equals_dst_returns_empty():
    assert enumerate_k_paths(_ring_graph(), 0, 0) == []


def test_missing_endpoint_returns_empty():
    graph = _ring_graph()
    assert enumerate_k_paths(graph, 0, 99) == []
    assert enumerate_k_paths(graph, 99, 0) == []


def test_disconnected_pair_returns_empty():
    graph = nx.Graph()
    graph.add_edge(0, 1)
    graph.add_edge(2, 3)
    assert enumerate_k_paths(graph, 0, 3) == []


def test_paths_ordered_by_increasing_length():
    graph = _ring_graph(6)
    paths = enumerate_k_paths(graph, 0, 3, k=5, max_len=6)
    lengths = [len(p) - 1 for p in paths]
    assert lengths == sorted(lengths)


def test_respects_k_cap():
    graph = nx.complete_graph(6)  # many possible paths between any pair
    paths = enumerate_k_paths(graph, 0, 1, k=2, max_len=6)
    assert len(paths) <= 2


def test_respects_max_len_cap():
    graph = _ring_graph(8)  # only path 0->4 the "long way" has length 4
    paths = enumerate_k_paths(graph, 0, 4, k=5, max_len=3)
    assert all(len(p) - 1 <= 3 for p in paths)
    assert paths == []  # a ring of 8 has both routes at length 4, over the cap


def test_path_edges_consecutive_pairs():
    assert path_edges([1, 2, 3, 4]) == [(1, 2), (2, 3), (3, 4)]


def test_path_edges_single_node_has_no_edges():
    assert path_edges([1]) == []
