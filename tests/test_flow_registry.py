from routing.flow_registry import ActiveFlowRegistry, FlowRecord


def make_record(src="A", dst="B", path=(1, 2, 3), mode="rl"):
    return FlowRecord(src_mac=src, dst_mac=dst, path=list(path), mode=mode)


def test_get_unknown_pair_returns_none():
    registry = ActiveFlowRegistry()
    assert registry.get("A", "B") is None


def test_record_and_get_is_direction_agnostic():
    registry = ActiveFlowRegistry()
    registry.record(make_record(src="A", dst="B"))
    assert registry.get("A", "B") is not None
    assert registry.get("B", "A") is not None  # same unordered pair


def test_remove():
    registry = ActiveFlowRegistry()
    registry.record(make_record())
    registry.remove("A", "B")
    assert registry.get("A", "B") is None


def test_flows_using_edge_finds_matching_flow():
    registry = ActiveFlowRegistry()
    registry.record(make_record(path=(1, 2, 3)))
    found = registry.flows_using_edge(2, 3)
    assert len(found) == 1
    assert found[0].src_mac == "A"


def test_flows_using_edge_edge_direction_agnostic():
    registry = ActiveFlowRegistry()
    registry.record(make_record(path=(1, 2, 3)))
    assert len(registry.flows_using_edge(3, 2)) == 1  # reversed edge order


def test_flows_using_edge_ignores_unrelated_flows():
    registry = ActiveFlowRegistry()
    registry.record(make_record(src="A", dst="B", path=(1, 2, 3)))
    registry.record(make_record(src="C", dst="D", path=(4, 5)))
    assert registry.flows_using_edge(4, 5) == [registry.get("C", "D")]
    assert registry.flows_using_edge(1, 2) == [registry.get("A", "B")]


def test_all_flows():
    registry = ActiveFlowRegistry()
    registry.record(make_record(src="A", dst="B"))
    registry.record(make_record(src="C", dst="D"))
    assert len(registry.all_flows()) == 2
