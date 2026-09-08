import pytest

from controller.network_state import NetworkState
from routing.decision_engine import RoutingDecisionEngine
from routing.host_location import HostLocationTracker


class StubAgent:
    """A minimal fake QLearningAgent for testing decision_engine without a
    real trained model - just picks a fixed action index, or raises."""

    def __init__(self, action=0, error=None):
        self.action = action
        self.error = error

    def select_action(self, candidate_features):
        if self.error is not None:
            raise self.error
        return self.action


def ring_of_4():
    """1-2-3-4-1, so dpid 1 and dpid 3 have two equal-length (2-hop) routes
    between them: [1,2,3] and [1,4,3]."""
    state = NetworkState()
    state.sync_graph(
        [1, 2, 3, 4],
        [
            (1, 2, {"ports": {1: 1, 2: 1}}),
            (2, 3, {"ports": {2: 2, 3: 1}}),
            (3, 4, {"ports": {3: 2, 4: 1}}),
            (4, 1, {"ports": {4: 2, 1: 2}}),
        ],
    )
    return state


def hosts_at_1_and_3():
    hosts = HostLocationTracker()
    hosts.record("mac_a", dpid=1, port_no=10)
    hosts.record("mac_b", dpid=3, port_no=10)
    return hosts


def test_unknown_host_location_returns_none():
    engine = RoutingDecisionEngine(ring_of_4(), HostLocationTracker(), mode="dijkstra")
    assert engine.decide("mac_a", "mac_b") is None


def test_same_switch_hosts_return_single_node_path():
    hosts = HostLocationTracker()
    hosts.record("mac_a", dpid=1, port_no=10)
    hosts.record("mac_b", dpid=1, port_no=11)
    engine = RoutingDecisionEngine(ring_of_4(), hosts, mode="dijkstra")
    decision = engine.decide("mac_a", "mac_b")
    assert decision.path == [1]


def test_dijkstra_mode_returns_a_valid_two_hop_path():
    engine = RoutingDecisionEngine(ring_of_4(), hosts_at_1_and_3(), mode="dijkstra")
    decision = engine.decide("mac_a", "mac_b")
    assert decision.mode_used == "dijkstra"
    assert decision.path[0] == 1 and decision.path[-1] == 3
    assert len(decision.path) == 3  # 2 hops


def test_rl_mode_with_no_agent_falls_back_to_dijkstra():
    engine = RoutingDecisionEngine(ring_of_4(), hosts_at_1_and_3(), agent=None, mode="rl")
    decision = engine.decide("mac_a", "mac_b")
    assert decision.mode_used == "dijkstra"


def test_rl_mode_uses_agents_chosen_candidate():
    agent = StubAgent(action=1)
    engine = RoutingDecisionEngine(ring_of_4(), hosts_at_1_and_3(), agent=agent, mode="rl")
    decision = engine.decide("mac_a", "mac_b")
    assert decision.mode_used == "rl"
    assert decision.fallback_reason is None
    assert decision.path[0] == 1 and decision.path[-1] == 3


def test_rl_agent_error_falls_back_to_dijkstra_with_reason_logged():
    agent = StubAgent(error=ValueError("boom"))
    engine = RoutingDecisionEngine(ring_of_4(), hosts_at_1_and_3(), agent=agent, mode="rl")
    decision = engine.decide("mac_a", "mac_b")
    assert decision.mode_used == "dijkstra"
    assert decision.fallback_reason == "boom"


def test_never_returns_a_path_using_a_down_link():
    state = ring_of_4()
    # Take down the 1<->2 link (port 1 on dpid 1's side) - only [1,4,3] survives.
    state.set_link_status(1, 1, up=False)
    engine = RoutingDecisionEngine(state, hosts_at_1_and_3(), mode="dijkstra")
    decision = engine.decide("mac_a", "mac_b")
    assert decision.path == [1, 4, 3]


def test_rl_never_returns_a_path_using_a_down_link_either():
    state = ring_of_4()
    state.set_link_status(1, 1, up=False)  # kills the [1,2,3] route
    agent = StubAgent(action=0)  # would pick "first" candidate - must still be the up one
    engine = RoutingDecisionEngine(state, hosts_at_1_and_3(), agent=agent, mode="rl")
    decision = engine.decide("mac_a", "mac_b")
    assert 2 not in decision.path  # never routes through the switch behind the down link...
    assert decision.path == [1, 4, 3]


def test_returns_none_when_every_path_is_down():
    state = ring_of_4()
    state.set_link_status(1, 1, up=False)  # kills 1<->2
    state.set_link_status(1, 2, up=False)  # kills 1<->4 too - dpid 1 fully isolated
    engine = RoutingDecisionEngine(state, hosts_at_1_and_3(), mode="dijkstra")
    assert engine.decide("mac_a", "mac_b") is None
