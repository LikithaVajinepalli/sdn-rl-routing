#!/usr/bin/env python3
"""Standalone training loop for the tabular Q-learning routing agent
(Phase 2). Trains against rl/environment.py's simulated network - no
Mininet/Ryu needed, runs anywhere:

    python -m rl.train --episodes 2000

Writes a per-episode reward log (CSV) and a reward-vs-episode plot (PNG) to
--out-dir (default models/), plus the trained Q-table for later use.
"""

import argparse
import csv
import os

from rl.config import TrainingConfig
from rl.environment import EnvConfig, SimulatedRoutingEnv
from rl.q_agent import QLearningAgent
from topology.config import TopologyConfig
from topology.graph import build_switch_graph


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--episodes", type=int, default=None)
    parser.add_argument("--switches", type=int, default=8)
    parser.add_argument("--chord-offsets", type=int, nargs="*", default=[3])
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out-dir", default="models")
    return parser.parse_args()


def train(cfg: TrainingConfig, graph):
    env = SimulatedRoutingEnv(
        graph,
        EnvConfig(
            steps_per_episode=cfg.steps_per_episode,
            path_k=cfg.path_k,
            path_max_len=cfg.path_max_len,
            seed=cfg.seed,
        ),
    )
    agent = QLearningAgent(
        alpha=cfg.alpha,
        gamma=cfg.gamma,
        epsilon_start=cfg.epsilon_start,
        epsilon_end=cfg.epsilon_end,
        epsilon_decay=cfg.epsilon_decay,
        seed=cfg.seed,
    )

    episode_rewards = []
    for episode in range(cfg.num_episodes):
        state = env.reset()
        total_reward = 0.0
        done = False
        while not done:
            if not state:
                # No candidate paths for the sampled pair - env already
                # re-sampled internally; treat as a no-op zero-reward tick.
                state, _reward, done, _info = env.step(0)
                continue
            action = agent.select_action(state)
            next_state, reward, done, _info = env.step(action)
            agent.update(state, action, reward, next_state, done)
            total_reward += reward
            state = next_state
        agent.decay_epsilon()
        episode_rewards.append(total_reward / cfg.steps_per_episode)

        checkpoint_every = max(1, cfg.num_episodes // 20)
        if (episode + 1) % checkpoint_every == 0:
            recent = episode_rewards[-checkpoint_every:]
            print(
                f"episode {episode + 1}/{cfg.num_episodes}  "
                f"avg_reward={sum(recent) / len(recent):.3f}  epsilon={agent.epsilon:.3f}"
            )

    return agent, episode_rewards


def save_results(agent: QLearningAgent, episode_rewards, cfg: TrainingConfig):
    os.makedirs(os.path.dirname(cfg.model_out) or ".", exist_ok=True)
    agent.save(cfg.model_out)
    print(f"Saved Q-table ({len(agent.q_table)} entries) to {cfg.model_out}")

    with open(cfg.log_out, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["episode", "avg_reward"])
        for i, r in enumerate(episode_rewards):
            writer.writerow([i + 1, r])
    print(f"Saved episode reward log to {cfg.log_out}")

    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.figure(figsize=(8, 4.5))
        plt.plot(range(1, len(episode_rewards) + 1), episode_rewards, linewidth=1)
        plt.xlabel("Episode")
        plt.ylabel("Average reward per step")
        plt.title("Q-learning routing agent: reward convergence")
        plt.tight_layout()
        plt.savefig(cfg.plot_out, dpi=150)
        plt.close()
        print(f"Saved reward curve to {cfg.plot_out}")
    except ImportError:
        print("matplotlib not installed - skipping reward curve plot (CSV log still written)")


def main():
    args = parse_args()
    cfg = TrainingConfig()
    if args.episodes is not None:
        cfg.num_episodes = args.episodes
    if args.seed is not None:
        cfg.seed = args.seed
    cfg.model_out = os.path.join(args.out_dir, "q_agent.pkl")
    cfg.log_out = os.path.join(args.out_dir, "training_log.csv")
    cfg.plot_out = os.path.join(args.out_dir, "reward_curve.png")

    topo_config = TopologyConfig(num_switches=args.switches, chord_offsets=args.chord_offsets)
    graph = build_switch_graph(topo_config)

    agent, episode_rewards = train(cfg, graph)
    save_results(agent, episode_rewards, cfg)


if __name__ == "__main__":
    main()
