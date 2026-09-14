"""Route-level tests for the dashboard. Skipped where Flask isn't installed
(it's only needed on the machine actually running the controller)."""

import pytest

pytest.importorskip("flask")
pytest.importorskip("flask_socketio")

from controller.network_state import NetworkState  # noqa: E402
from dashboard.app import create_app  # noqa: E402
from routing.decision_engine import RoutingDecision  # noqa: E402
from routing.decision_log import DecisionLog, build_record  # noqa: E402
from routing.flow_registry import ActiveFlowRegistry, FlowRecord  # noqa: E402


@pytest.fixture
def dashboard(tmp_path):
    state = NetworkState()
    state.sync_graph([1, 2, 3], [(1, 2, {"ports": {1: 1, 2: 1}}), (2, 3, {"ports": {2: 2, 3: 1}})])

    log = DecisionLog()
    log.record(build_record("00:00:00:00:00:01", "00:00:00:00:00:09", RoutingDecision(path=[1, 2, 3], mode_used="rl")))

    registry = ActiveFlowRegistry()
    registry.record(FlowRecord(src_mac="a", dst_mac="b", path=[1, 2], mode="rl"))

    mode = {"value": "rl"}
    app, _socketio, _emit = create_app(
        state,
        log,
        registry,
        routing_mode_getter=lambda: mode["value"],
        routing_mode_setter=lambda m: mode.update(value=m),
        flush_routes=lambda: 3,
        training_log_path=str(tmp_path / "missing.csv"),
        benchmark_path=str(tmp_path / "missing.json"),
    )
    return app.test_client(), mode


def test_index_renders(dashboard):
    client, _ = dashboard
    response = client.get("/")
    assert response.status_code == 200
    assert b"SDN" in response.data


def test_snapshot_endpoint_shape(dashboard):
    client, _ = dashboard
    payload = client.get("/api/snapshot").get_json()
    assert set(payload) == {"timestamp", "summary", "topology", "metrics", "decisions"}
    assert payload["summary"]["switch_count"] == 3
    assert payload["summary"]["active_flows"] == 1
    assert len(payload["decisions"]) == 1


def test_mode_switch_applies_and_flushes(dashboard):
    client, mode = dashboard
    payload = client.post("/api/mode/dijkstra").get_json()
    assert payload == {"mode": "dijkstra", "previous_mode": "rl", "flushed_routes": 3}
    assert mode["value"] == "dijkstra"
    assert client.get("/api/snapshot").get_json()["summary"]["routing_mode"] == "dijkstra"


def test_mode_switch_rejects_unknown_mode(dashboard):
    client, mode = dashboard
    response = client.post("/api/mode/bogus")
    assert response.status_code == 400
    assert mode["value"] == "rl"  # unchanged


def test_missing_training_log_reports_empty(dashboard):
    client, _ = dashboard
    assert client.get("/api/training-curve").get_json()["points"] == []


def test_missing_benchmark_reports_unavailable(dashboard):
    """The comparison view must say "no benchmark yet" rather than render
    numbers nobody measured."""
    client, _ = dashboard
    payload = client.get("/api/benchmark").get_json()
    assert payload["available"] is False
    assert payload["results"] is None
