import os

import pytest

from rl.path_features import PathFeatures
from rl.q_agent import QLearningAgent


def make_features(bottleneck_utilization=0.1):
    return PathFeatures(
        bottleneck_utilization=bottleneck_utilization,
        total_delay_ms=10.0,
        max_loss_pct=0.0,
        min_trust=1.0,
        max_switch_throughput_bps=0.0,
        max_link_to_switch_rate=0.1,
    )


def test_select_action_always_random_when_epsilon_is_one():
    agent = QLearningAgent(epsilon_start=1.0, epsilon_end=1.0, epsilon_decay=1.0, seed=1)
    candidates = [make_features(), make_features(), make_features()]
    actions = {agent.select_action(candidates) for _ in range(50)}
    assert actions == {0, 1, 2}  # with enough draws, random should hit every action


def test_select_action_greedy_picks_highest_q_value():
    agent = QLearningAgent(epsilon_start=0.0, epsilon_end=0.0, epsilon_decay=1.0, seed=1)
    candidates = [make_features(), make_features(), make_features()]
    from rl.state import discretize_state

    key = discretize_state(candidates)
    agent.q_table[(key, 0)] = 0.1
    agent.q_table[(key, 1)] = 0.9
    agent.q_table[(key, 2)] = 0.2
    assert agent.select_action(candidates) == 1


def test_select_action_raises_on_no_candidates():
    agent = QLearningAgent()
    with pytest.raises(ValueError):
        agent.select_action([])


def test_update_moves_q_value_toward_target_terminal():
    agent = QLearningAgent(alpha=0.5, gamma=0.9)
    state = [make_features()]
    agent.update(state, action=0, reward=1.0, next_state_features=[], done=True)
    from rl.state import discretize_state

    key = discretize_state(state)
    # old=0, target=reward=1.0 (done) -> new = 0 + 0.5*(1.0-0) = 0.5
    assert agent.q_table[(key, 0)] == pytest.approx(0.5)


def test_update_bootstraps_from_next_state_when_not_done():
    agent = QLearningAgent(alpha=1.0, gamma=1.0)  # alpha=1 -> jumps straight to target
    state = [make_features()]
    next_state = [make_features(), make_features()]
    from rl.state import discretize_state

    next_key = discretize_state(next_state)
    agent.q_table[(next_key, 0)] = 2.0
    agent.q_table[(next_key, 1)] = 5.0

    agent.update(state, action=0, reward=1.0, next_state_features=next_state, done=False)
    key = discretize_state(state)
    # target = reward + gamma * max(next Q) = 1.0 + 1.0*5.0 = 6.0, alpha=1 -> new=6.0
    assert agent.q_table[(key, 0)] == pytest.approx(6.0)


def test_decay_epsilon_respects_floor():
    agent = QLearningAgent(epsilon_start=1.0, epsilon_end=0.1, epsilon_decay=0.5)
    for _ in range(20):
        agent.decay_epsilon()
    assert agent.epsilon == pytest.approx(0.1)


def test_save_load_roundtrip(tmp_path):
    agent = QLearningAgent(seed=1)
    state = [make_features()]
    agent.update(state, action=0, reward=0.7, next_state_features=[], done=True)
    agent.epsilon = 0.42

    path = os.path.join(str(tmp_path), "agent.pkl")
    agent.save(path)

    restored = QLearningAgent()
    restored.load(path)
    assert dict(restored.q_table) == dict(agent.q_table)
    assert restored.epsilon == pytest.approx(0.42)
