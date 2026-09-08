"""Dijkstra/BFS shortest-path baseline - deliberately its own independent
implementation (not just "take candidate 0" from routing/path_enumeration.py)
so the RL-vs-baseline comparison this project is built around is genuine,
not two names for the same code path.
"""

from typing import List, Optional

import networkx as nx


def shortest_path(graph: nx.Graph, src, dst) -> Optional[List]:
    """Pure hop-count shortest path (unweighted). Deliberately NOT weighted
    by delay/utilization/etc: the whole point of contrasting this against
    the RL agent is that traditional OSPF-style routing picks routes on hop
    count alone, ignoring real-time network conditions. Returns None if no
    path exists or either endpoint is missing from the graph."""
    if src == dst:
        return [src]
    try:
        return nx.shortest_path(graph, src, dst)
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        return None
