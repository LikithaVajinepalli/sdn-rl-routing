from rl.path_features import PathFeatures
from routing.decision_engine import RoutingDecision
from routing.decision_log import DecisionLog, build_record


def features(bottleneck=0.2, delay=10.0, loss=0.0, trust=1.0):
    return PathFeatures(
        bottleneck_utilization=bottleneck,
        total_delay_ms=delay,
        max_loss_pct=loss,
        min_trust=trust,
        max_switch_throughput_bps=0.0,
        max_link_to_switch_rate=0.0,
    )


def test_empty_log():
    log = DecisionLog()
    assert len(log) == 0
    assert log.recent() == []
    assert log.counts_by_mode() == {}
    assert log.fallback_count() == 0


def test_recent_is_newest_first():
    log = DecisionLog()
    for i in range(3):
        decision = RoutingDecision(path=[1, i + 2], mode_used="rl")
        log.record(build_record(f"mac{i}", "dst", decision))
    assert [r.src_mac for r in log.recent()] == ["mac2", "mac1", "mac0"]


def test_ring_buffer_drops_oldest():
    log = DecisionLog(max_entries=2)
    for i in range(5):
        log.record(build_record(f"mac{i}", "dst", RoutingDecision(path=[1], mode_used="rl")))
    assert len(log) == 2
    assert [r.src_mac for r in log.recent()] == ["mac4", "mac3"]


def test_counts_by_mode_and_fallbacks():
    log = DecisionLog()
    log.record(build_record("a", "b", RoutingDecision(path=[1], mode_used="rl")))
    log.record(build_record("c", "d", RoutingDecision(path=[1], mode_used="dijkstra")))
    log.record(build_record("e", "f", RoutingDecision(path=[1], mode_used="dijkstra", fallback_reason="boom")))

    assert log.counts_by_mode() == {"rl": 1, "dijkstra": 2}
    assert log.fallback_count() == 1


def test_build_record_marks_the_chosen_candidate():
    decision = RoutingDecision(
        path=[1, 6, 5],
        mode_used="rl",
        candidates=[[1, 2, 3, 5], [1, 6, 5]],
        candidate_features=[features(bottleneck=0.9), features(bottleneck=0.1)],
    )
    record = build_record("src", "dst", decision)

    assert [c.chosen for c in record.candidates] == [False, True]
    assert record.candidates[1].bottleneck_utilization == 0.1
    assert record.path == [1, 6, 5]


def test_build_record_handles_decision_without_candidates():
    record = build_record("src", "dst", RoutingDecision(path=[1, 2], mode_used="dijkstra"))
    assert record.candidates == []
    assert record.mode_used == "dijkstra"


def test_as_dict_is_json_shaped():
    decision = RoutingDecision(
        path=[1, 2],
        mode_used="rl",
        candidates=[[1, 2]],
        candidate_features=[features()],
    )
    payload = build_record("aa", "bb", decision, event="reroute").as_dict()

    assert payload["event"] == "reroute"
    assert payload["path"] == [1, 2]
    assert payload["candidates"][0]["chosen"] is True
    assert set(payload) == {
        "timestamp", "src_mac", "dst_mac", "path", "mode_used", "event", "fallback_reason", "candidates",
    }
