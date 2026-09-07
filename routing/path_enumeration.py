"""Candidate path enumeration for a source-destination pair.

Pulled forward from Phase 3 (routing/flow management) because Phase 2's RL
action space needs it too - "choose among enumerated candidate paths" is the
same operation whether the caller is the RL agent's action space or the
real flow-installation logic. Works on any NetworkX graph: the design-time
topology graph (topology/graph.py) during RL training, or the live
discovered graph (controller/network_state.py's NetworkState.graph) once
Phase 3 wires this into the running controller.
"""

from typing import List, Tuple

import networkx as nx


def enumerate_k_paths(graph: nx.Graph, src, dst, k: int = 3, max_len: int = 6) -> List[List]:
    """Up to `k` simple paths between src and dst, shortest-hop-count first,
    each with at most `max_len` edges. Returns [] if src == dst, either
    endpoint is missing from the graph, or no path exists within max_len.
    """
    if src == dst or src not in graph or dst not in graph:
        return []

    result = []
    try:
        # Yen's algorithm - yields paths in non-decreasing length order, so
        # once we see one exceeding max_len every later one will too. Raises
        # NetworkXNoPath lazily, during iteration - not when called - so the
        # try/except has to wrap the loop, not just the generator's creation.
        for path in nx.shortest_simple_paths(graph, src, dst):
            if len(path) - 1 > max_len:
                break
            result.append(path)
            if len(result) >= k:
                break
    except nx.NetworkXNoPath:
        return []
    return result


def path_edges(path: List) -> List[Tuple]:
    """[(n0, n1), (n1, n2), ...] for consecutive nodes in `path`."""
    return list(zip(path[:-1], path[1:]))
