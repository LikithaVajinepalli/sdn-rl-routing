import networkx as nx
import pytest

from rl.environment import EnvConfig, SimulatedRoutingEnv


def small_graph():
    # A ring of 6 plus one chord - guarantees >1 candidate path for most pairs.
    graph = nx.cycle_graph(6)
    graph.add_edge(0, 3)
    return graph


def test_reset_returns_path_features_for_first_pair():
    env = SimulatedRoutingEnv(small_graph(), EnvConfig(seed=1))
    state = env.reset()
    assert len(state) >= 1
    assert all(0.0 <= f.bottleneck_utilization <= 1.0 for f in state)


def test_step_returns_reward_in_valid_range():
    env = SimulatedRoutingEnv(small_graph(), EnvConfig(seed=1))
    state = env.reset()
    next_state, reward, done, info = env.step(0)
    assert 0.0 <= reward <= 1.0
    assert isinstance(done, bool)
    assert "src" in info and "dst" in info


def test_episode_ends_after_configured_steps():
    env = SimulatedRoutingEnv(small_graph(), EnvConfig(seed=1, steps_per_episode=3))
    env.reset()
    dones = []
    for _ in range(3):
        _state, _reward, done, _info = env.step(0)
        dones.append(done)
    assert dones == [False, False, True]


def test_loading_a_path_increases_its_own_future_utilization():
    """Repeatedly routing demand onto the same path should make that path's
    own bottleneck utilization rise - the core signal the agent needs to
    learn to spread load across candidates instead of reusing one path."""
    env = SimulatedRoutingEnv(small_graph(), EnvConfig(seed=2, steps_per_episode=100, load_decay=1.0, background_noise_std=0.0))
    env.reset()
    # Force the same (src, dst, path) repeatedly by reaching into internals -
    # simplest reliable way to test the load-accumulation mechanic directly.
    env._current_paths = [[0, 1, 2]]
    env._current_demand_mbps = 2.0
    first_features = env._path_features(env._current_paths[0])
    for _ in range(5):
        env.step(0)
        env._current_paths = [[0, 1, 2]]  # re-pin after step()'s internal resample
        env._current_demand_mbps = 2.0
    later_features = env._path_features(env._current_paths[0])
    assert later_features.bottleneck_utilization > first_features.bottleneck_utilization


def test_deterministic_with_same_seed():
    env_a = SimulatedRoutingEnv(small_graph(), EnvConfig(seed=7))
    env_b = SimulatedRoutingEnv(small_graph(), EnvConfig(seed=7))
    state_a = env_a.reset()
    state_b = env_b.reset()
    assert state_a == state_b
    _next_a, reward_a, _done_a, info_a = env_a.step(0)
    _next_b, reward_b, _done_b, info_b = env_b.step(0)
    assert reward_a == pytest.approx(reward_b)
    assert info_a["src"] == info_b["src"] and info_a["dst"] == info_b["dst"]
