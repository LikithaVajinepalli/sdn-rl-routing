"""Tabular Q-learning agent with epsilon-greedy exploration - the Phase 2
baseline algorithm. A future DQN variant would replace this class (swapping
rl/state.py's discretize_state() for a neural net consuming PathFeatures
directly) without touching rl/environment.py or rl/reward.py.
"""

import pickle
import random
from collections import defaultdict
from typing import List, Optional

from rl.path_features import PathFeatures
from rl.state import discretize_state


class QLearningAgent:
    def __init__(
        self,
        alpha: float = 0.1,
        gamma: float = 0.9,
        epsilon_start: float = 1.0,
        epsilon_end: float = 0.05,
        epsilon_decay: float = 0.995,
        seed: Optional[int] = None,
    ):
        self.alpha = alpha
        self.gamma = gamma
        self.epsilon = epsilon_start
        self.epsilon_end = epsilon_end
        self.epsilon_decay = epsilon_decay
        self._rng = random.Random(seed)
        self.q_table = defaultdict(float)  # (state_key, action_index) -> value

    def select_action(self, candidate_features: List[PathFeatures]) -> int:
        n = len(candidate_features)
        if n == 0:
            raise ValueError("no candidate paths to choose from")
        if self._rng.random() < self.epsilon:
            return self._rng.randrange(n)
        state_key = discretize_state(candidate_features)
        q_values = [self.q_table[(state_key, a)] for a in range(n)]
        best = max(q_values)
        best_actions = [a for a, v in enumerate(q_values) if v == best]
        return self._rng.choice(best_actions)

    def update(
        self,
        state_features: List[PathFeatures],
        action: int,
        reward: float,
        next_state_features: List[PathFeatures],
        done: bool,
    ) -> None:
        state_key = discretize_state(state_features)
        current = self.q_table[(state_key, action)]
        if done or not next_state_features:
            target = reward
        else:
            next_key = discretize_state(next_state_features)
            next_max = max(self.q_table[(next_key, a)] for a in range(len(next_state_features)))
            target = reward + self.gamma * next_max
        self.q_table[(state_key, action)] = current + self.alpha * (target - current)

    def decay_epsilon(self) -> None:
        self.epsilon = max(self.epsilon_end, self.epsilon * self.epsilon_decay)

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump({"q_table": dict(self.q_table), "epsilon": self.epsilon}, f)

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.q_table = defaultdict(float, data["q_table"])
        self.epsilon = data.get("epsilon", self.epsilon)
