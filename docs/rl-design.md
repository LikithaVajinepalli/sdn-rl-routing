# RL Agent Design (Phase 2)

Living document - update if hyperparameters or the environment change enough
to invalidate the numbers below. Companion to `/docs/architecture.md`
(Phase 1) and `CLAUDE.md` (overall project brief/phase tracker).

## State vector

For a given (source, destination) pair, each enumerated candidate path gets
aggregated from its links' six Phase 1 metrics
(`controller/metrics.py: LinkMetrics`) into one `PathFeatures`
(`rl/path_features.py`):

| Feature | Aggregation | Why |
|---|---|---|
| `bottleneck_utilization` | max across links | a path is only as good as its most congested hop |
| `total_delay_ms` | sum across links | delay is additive end-to-end |
| `max_loss_pct` | max across links | one lossy hop hurts the whole path |
| `min_trust` | min across links | the weakest link's trust dominates |
| `max_switch_throughput_bps` | max across links | busiest switch touched by the path |
| `max_link_to_switch_rate` | max across links | worst per-switch load-share along the path |

`PathFeatures` stays a full-fidelity continuous vector on purpose. The
**tabular** Q-learning baseline is the only thing that discretizes it -
`rl/state.py` bins each of the 6 features into 3 bins (low/med/high) using
fixed thresholds (not the reward weights) to form a Q-table lookup key. A
future DQN variant would delete `rl/state.py` and feed `PathFeatures`
straight into a network, without touching the environment or reward code -
that's the whole point of keeping this split.

## Action space

Up to `K=3` candidate simple paths per (src, dst) pair, enumerated via
`routing/path_enumeration.py` (Yen's algorithm, shortest-hop-count first,
capped at `max_len=6` hops). This module was pulled forward from Phase 3
because the RL action space needs the exact same "enumerate candidate
paths" operation that Phase 3's real flow installation will need later -
both will end up calling the same function on either the design-time
topology graph (training) or the live discovered graph (Phase 3 runtime).

## Reward function

```
reward = 1.0 - (0.35 * bottleneck_utilization
              + 0.25 * clamp01(total_delay_ms / 100)
              + 0.25 * clamp01(max_loss_pct / 20)
              + 0.15 * (1 - min_trust))
```

Range `[0, 1]`, computed from the chosen path's **post-decision** features
(after the environment applies the new demand). Utilization is weighted
heaviest (0.35) because avoiding congestion - not just minimizing hop count
- is this project's whole premise versus a plain Dijkstra baseline. Loss and
delay follow at 0.25 each; trust last at 0.15 since it's already a composite
of loss/jitter/errors (see `controller/metrics.py`'s `link_trust_level`).

## Environment (training-time simulator)

`rl/environment.py`'s `SimulatedRoutingEnv` trains the agent without
Mininet/Ryu running (see CLAUDE.md's Phase 2 kickoff for why: standalone
training in seconds vs. depending on a live WSL2 session). One **episode**
= 50 independent routing decisions against a shared, evolving simulated
per-link load:
1. Sample a random (src, dst) pair + a random traffic demand (1-6 Mbps).
2. Agent observes each candidate path's current `PathFeatures` and picks one.
3. The demand is added onto the chosen path's links; loss/delay/jitter ramp
   up once a link's utilization crosses 80% (a documented approximation, not
   a measured relationship).
4. Reward is computed from the path's new (post-decision) features.
5. Background load decays (`load_decay=0.93`) with small random noise before
   the next decision, so congestion persists across a few steps but isn't
   permanent.

**Environment calibration (an actual finding, not a guess):** the initial
default demand range (0.5-3 Mbps) never pushed any link's utilization past
~43%, even under a policy that always ignores congestion - meaning there was
no real penalty for ignoring it, and "always take the shortest path" was
trivially optimal. Demand was increased to 1-6 Mbps with slower decay
(0.93) specifically so that always-shortest-path visibly overloads
chokepoint links under sustained random demand, giving the agent something
real to learn to avoid.

**Known simplification:** each step samples a *new, unrelated* (src, dst)
pair for the next state - it is not a continuation of the same trajectory.
The only thing actually connecting consecutive steps is the shared
background load. This makes the problem closer to a shared-load contextual
bandit than a strict per-trajectory MDP - see the gamma finding below for
what that implies about hyperparameters.

## Algorithm & hyperparameters

Tabular Q-learning, epsilon-greedy (`rl/q_agent.py`):

| Hyperparameter | Value | Note |
|---|---|---|
| `alpha` (learning rate) | 0.1 | |
| `gamma` (discount) | **0.3** | see finding below - not the initial 0.9 |
| `epsilon` | 1.0 -> 0.05, decay 0.995/episode | |
| `num_episodes` | 4000 | |
| `steps_per_episode` | 50 | |
| path `K` / `max_len` | 3 / 6 | |

**Gamma finding:** since the "next state" bootstrapped into the Q-update is
an unrelated (src, dst) pair (see environment simplification above), a high
gamma bootstraps heavily off a value that has little to do with the action
just taken. This was tested empirically, training for 4000 episodes at each
gamma and evaluating greedily (epsilon=0) over 300 episodes:

| gamma | mean eval reward |
|---|---|
| 0.0 | 0.6486 |
| **0.3** | **0.6518** |
| 0.6 | 0.6200 |
| 0.9 | 0.5962 |

`gamma=0.9` made the agent perform *worse than a naive shortest-path
baseline* (see below) - a real bug caught by comparing against baselines,
not just watching the reward curve trend upward. `gamma=0.3` was selected
as the default.

## Convergence behavior (actual training run: seed=42, 4000 episodes)

Average reward per step rose from **0.524** (episode 200, still mostly
exploring) to a plateau around **0.62-0.64** from roughly episode 1000
onward (once epsilon reached its floor of 0.05), ending at **0.631**. Full
per-episode log: `models/training_log.csv`; plot: `models/reward_curve.png`.
Q-table converged to 2433 entries - the discretized state space is nominally
enormous (3^18 per candidate-path-count bucket), but sparse dict-based
lookup means only actually-visited combinations matter, and in practice a
small fraction of that space gets visited under this topology/demand
pattern.

## Benchmark against baselines (the actual "did it learn something real" check)

Reward-curve-trending-upward alone doesn't prove the agent learned anything
*useful* - it could just be exploiting a quirk of its own training
trajectory. Evaluated the trained agent (epsilon=0, greedy) against a random
policy and an always-take-the-shortest-candidate policy (the "Dijkstra-like"
baseline), across 4 independent evaluation seeds never seen during
training, 500 episodes each:

| Policy | seed 100 | seed 200 | seed 300 | seed 999 |
|---|---|---|---|---|
| random | 0.4759 | 0.4724 | 0.4735 | 0.4762 |
| always-shortest-path | 0.6015 | 0.6038 | 0.6009 | 0.6034 |
| **trained Q-agent** | **0.6564** | **0.6519** | **0.6501** | **0.6521** |

Consistent, reproducible ~8% relative improvement over the shortest-path
baseline across every seed, and both far ahead of random. This is the
result that actually justifies the project's premise - the RL agent learned
to route around congestion in a way a hop-count-only strategy structurally
cannot.

## Known limitations (be honest about scope)

- The environment's congestion dynamics (loss/delay/jitter ramping past 80%
  utilization) are a documented approximation calibrated to make congestion
  matter, not measured from a real network.
- Training happens entirely in the simulator; Phase 3 will be the first
  time the agent's decisions get exercised against live Mininet traffic.
- The per-step "new unrelated (src,dst) pair" environment structure (a
  shared-load contextual bandit, not a strict MDP) is a simplification -
  documented above, and the low gamma is a direct, empirically-justified
  response to it rather than a default left unexamined.
- Tabular Q-learning's discretization (`rl/state.py`) is a coarse view of
  the full state; a DQN variant (mentioned as optional in the project brief)
  would consume `PathFeatures` directly and could likely do better, but was
  out of scope to build in Phase 2.
