"""Builds the JSON snapshots the dashboard frontend renders.

Pure: takes NetworkState / DecisionLog / ActiveFlowRegistry as plain objects
and returns dicts. No Flask, no Ryu, no sockets - so the shape of what the
browser receives is unit-testable without standing up a server or a network
(NFR4's "visualization independently modifiable").
"""

import time
from typing import Dict, List, Optional

# Utilization thresholds for the topology's green/amber/red colour coding.
# Shared with the frontend via the snapshot so the legend can never drift
# from the logic that classifies the links.
UTIL_WARN = 0.40
UTIL_CRITICAL = 0.75


def congestion_level(utilization: float) -> str:
    if utilization >= UTIL_CRITICAL:
        return "critical"
    if utilization >= UTIL_WARN:
        return "warn"
    return "ok"


def build_topology_snapshot(network_state, default_bw_mbps: float) -> Dict:
    """Switches, the links between them, and each link's live congestion
    state - what the topology view draws."""
    graph = network_state.graph
    switches = sorted(graph.nodes)
    links = []

    for u, v, data in graph.edges(data=True):
        ports = data.get("ports", {})
        u_port, v_port = ports.get(u), ports.get(v)

        # Report the busier of the two directions: a link is as congested as
        # its worst direction, and that's what should drive its colour.
        worst_util = 0.0
        delay_ms = 0.0
        loss_pct = 0.0
        for dpid, port_no in ((u, u_port), (v, v_port)):
            if port_no is None:
                continue
            metrics = network_state.live_link_metrics(dpid, port_no, default_bw_mbps)
            if metrics is None:
                continue
            if metrics.utilization >= worst_util:
                worst_util = metrics.utilization
                delay_ms = metrics.delay_ms
                loss_pct = metrics.loss_pct

        links.append(
            {
                "source": u,
                "target": v,
                "source_port": u_port,
                "target_port": v_port,
                "utilization": round(worst_util, 4),
                "delay_ms": round(delay_ms, 2),
                "loss_pct": round(loss_pct, 3),
                "level": congestion_level(worst_util),
                "up": network_state.is_edge_up(u, v),
            }
        )

    links.sort(key=lambda link: (link["source"], link["target"]))
    return {
        "switches": switches,
        "links": links,
        "thresholds": {"warn": UTIL_WARN, "critical": UTIL_CRITICAL},
    }


def build_metrics_snapshot(network_state, default_bw_mbps: float, limit: int = 12) -> List[Dict]:
    """Per-link metrics rows, busiest first - the metrics panel. Only
    switch-to-switch ports; host-facing ports aren't links we route over."""
    rows = []
    for (dpid, port_no) in list(network_state.port_samples.keys()):
        neighbour = network_state.neighbour_dpid(dpid, port_no)
        if neighbour is None:
            continue
        metrics = network_state.live_link_metrics(dpid, port_no, default_bw_mbps)
        if metrics is None:
            continue
        rows.append(
            {
                "link": f"s{dpid}-s{neighbour}",
                "dpid": dpid,
                "port": port_no,
                "utilization": round(metrics.utilization, 4),
                "delay_ms": round(metrics.delay_ms, 2),
                "loss_pct": round(metrics.loss_pct, 3),
                "trust": round(metrics.trust_level, 3),
                "level": congestion_level(metrics.utilization),
                "up": network_state.get_link_status(dpid, port_no).up,
            }
        )
    rows.sort(key=lambda row: row["utilization"], reverse=True)
    return rows[:limit]


def build_summary(network_state, decision_log, flow_registry, routing_mode: str, default_bw_mbps: float) -> Dict:
    """The KPI tiles: active flows, average utilization, decision counts."""
    utilizations = []
    for (dpid, port_no) in list(network_state.port_samples.keys()):
        if network_state.neighbour_dpid(dpid, port_no) is None:
            continue
        metrics = network_state.live_link_metrics(dpid, port_no, default_bw_mbps)
        if metrics is not None:
            utilizations.append(metrics.utilization)

    mode_counts = decision_log.counts_by_mode()
    return {
        "routing_mode": routing_mode,
        "active_flows": len(flow_registry.all_flows()),
        "switch_count": network_state.graph.number_of_nodes(),
        "link_count": network_state.graph.number_of_edges(),
        "avg_utilization": round(sum(utilizations) / len(utilizations), 4) if utilizations else 0.0,
        "decisions_total": len(decision_log),
        "decisions_rl": mode_counts.get("rl", 0),
        "decisions_dijkstra": mode_counts.get("dijkstra", 0),
        "fallbacks": decision_log.fallback_count(),
    }


def build_snapshot(
    network_state,
    decision_log,
    flow_registry,
    routing_mode: str,
    default_bw_mbps: float,
    decision_limit: int = 25,
) -> Dict:
    """One complete frame for the frontend."""
    return {
        "timestamp": time.time(),
        "summary": build_summary(network_state, decision_log, flow_registry, routing_mode, default_bw_mbps),
        "topology": build_topology_snapshot(network_state, default_bw_mbps),
        "metrics": build_metrics_snapshot(network_state, default_bw_mbps),
        "decisions": [record.as_dict() for record in decision_log.recent(decision_limit)],
    }


def load_training_curve(path: str, max_points: int = 400) -> List[Dict]:
    """Reads rl/train.py's episode reward CSV for the convergence chart,
    downsampling so the browser isn't handed thousands of points."""
    import csv
    import os

    if not os.path.exists(path):
        return []

    rows = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append({"episode": int(row["episode"]), "avg_reward": float(row["avg_reward"])})
            except (KeyError, TypeError, ValueError):
                continue

    if len(rows) <= max_points:
        return rows
    step = len(rows) // max_points
    return rows[::step]


def load_benchmark_results(path: str) -> Optional[Dict]:
    """Reads scripts/benchmark.py's output for the RL-vs-baseline view.
    Returns None when no benchmark has been run yet - the dashboard says so
    rather than inventing numbers."""
    import json
    import os

    if not os.path.exists(path):
        return None
    try:
        with open(path) as handle:
            return json.load(handle)
    except (ValueError, OSError):
        return None
