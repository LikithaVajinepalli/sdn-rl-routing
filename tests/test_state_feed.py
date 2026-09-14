import json

from controller.network_state import NetworkState
from dashboard.state_feed import (
    build_metrics_snapshot,
    build_snapshot,
    build_summary,
    build_topology_snapshot,
    congestion_level,
    load_benchmark_results,
    load_training_curve,
)
from routing.decision_engine import RoutingDecision
from routing.decision_log import DecisionLog, build_record
from routing.flow_registry import ActiveFlowRegistry, FlowRecord


def ring_state():
    state = NetworkState()
    state.sync_graph(
        [1, 2, 3],
        [
            (1, 2, {"ports": {1: 1, 2: 1}}),
            (2, 3, {"ports": {2: 2, 3: 1}}),
        ],
    )
    return state


def test_congestion_level_thresholds():
    assert congestion_level(0.0) == "ok"
    assert congestion_level(0.39) == "ok"
    assert congestion_level(0.40) == "warn"
    assert congestion_level(0.74) == "warn"
    assert congestion_level(0.75) == "critical"
    assert congestion_level(1.0) == "critical"


def test_topology_snapshot_lists_switches_and_links():
    snapshot = build_topology_snapshot(ring_state(), default_bw_mbps=10.0)
    assert snapshot["switches"] == [1, 2, 3]
    assert len(snapshot["links"]) == 2
    assert snapshot["thresholds"] == {"warn": 0.40, "critical": 0.75}


def test_topology_snapshot_marks_down_links():
    state = ring_state()
    state.set_link_status(1, 1, up=False)
    snapshot = build_topology_snapshot(state, default_bw_mbps=10.0)
    link_1_2 = next(l for l in snapshot["links"] if {l["source"], l["target"]} == {1, 2})
    assert link_1_2["up"] is False


def test_topology_snapshot_with_no_topology():
    snapshot = build_topology_snapshot(NetworkState(), default_bw_mbps=10.0)
    assert snapshot["switches"] == []
    assert snapshot["links"] == []


def test_metrics_snapshot_empty_without_samples():
    # No port samples polled yet - nothing to report, but it must not raise.
    assert build_metrics_snapshot(ring_state(), default_bw_mbps=10.0) == []


def test_summary_counts_flows_and_decisions():
    state = ring_state()
    registry = ActiveFlowRegistry()
    registry.record(FlowRecord(src_mac="a", dst_mac="b", path=[1, 2], mode="rl"))

    log = DecisionLog()
    log.record(build_record("a", "b", RoutingDecision(path=[1, 2], mode_used="rl")))
    log.record(build_record("c", "d", RoutingDecision(path=[1], mode_used="dijkstra", fallback_reason="boom")))

    summary = build_summary(state, log, registry, "rl", default_bw_mbps=10.0)
    assert summary["routing_mode"] == "rl"
    assert summary["active_flows"] == 1
    assert summary["switch_count"] == 3
    assert summary["link_count"] == 2
    assert summary["decisions_total"] == 2
    assert summary["decisions_rl"] == 1
    assert summary["decisions_dijkstra"] == 1
    assert summary["fallbacks"] == 1


def test_build_snapshot_is_json_serialisable():
    """The whole point of this module - whatever it returns gets handed
    straight to the browser, so it must survive json.dumps."""
    snapshot = build_snapshot(ring_state(), DecisionLog(), ActiveFlowRegistry(), "rl", 10.0)
    json.dumps(snapshot)  # must not raise
    assert set(snapshot) == {"timestamp", "summary", "topology", "metrics", "decisions"}


def test_load_training_curve_missing_file():
    assert load_training_curve("models/definitely-not-here.csv") == []


def test_load_training_curve_parses_and_downsamples(tmp_path):
    path = tmp_path / "log.csv"
    rows = "\n".join(f"{i},{0.5 + i / 1000}" for i in range(1, 1001))
    path.write_text(f"episode,avg_reward\n{rows}\n")

    points = load_training_curve(str(path), max_points=100)
    assert 0 < len(points) <= 101
    assert points[0]["episode"] == 1
    assert isinstance(points[0]["avg_reward"], float)


def test_load_training_curve_skips_bad_rows(tmp_path):
    path = tmp_path / "log.csv"
    path.write_text("episode,avg_reward\n1,0.5\nbroken,row\n3,0.7\n")
    assert [p["episode"] for p in load_training_curve(str(path))] == [1, 3]


def test_load_benchmark_results_missing_returns_none():
    assert load_benchmark_results("models/no-benchmark-here.json") is None


def test_load_benchmark_results_reads_json(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps({"modes": {"rl": {"avg_latency_ms": 12.0}}}))
    assert load_benchmark_results(str(path))["modes"]["rl"]["avg_latency_ms"] == 12.0


def test_load_benchmark_results_tolerates_corrupt_json(tmp_path):
    path = tmp_path / "benchmark.json"
    path.write_text("{not valid json")
    assert load_benchmark_results(str(path)) is None
