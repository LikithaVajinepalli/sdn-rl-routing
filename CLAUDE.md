# RL-Based Intelligent SDN Routing System

## Role & working agreement

Lead-engineer mode, phase-by-phase (see Phases below), not one giant generation pass.
**Before writing code for any phase:**
1. Restate that phase's scope in 3-5 bullets.
2. Ask clarifying questions that would materially change the design (e.g. tabular
   Q-learning vs. DQN, single vs. multi-agent, dashboard auth approach).
3. Propose the file/module structure before writing full implementations.
4. After the phase, give a short test plan and how to run it (Mininet/Ryu commands)
   before moving to the next phase.

Maintain `/docs` as living documentation (architecture, SRS traceability, security
notes) updated as we build, not left to the end. Maintain `CHANGELOG.md`. Commit
logically — one phase/feature per commit.

## Project context

Traditional routing (OSPF/static shortest-path) picks routes on hop count only,
ignoring real-time congestion/delay/loss. This project: a Mininet-simulated SDN with
a centralized Ryu controller that collects live network state, and an RL agent that
learns to route intelligently (reroute around congestion/failure), benchmarked against
a Dijkstra shortest-path baseline.

Simulation only (Mininet) — no real hardware. Single centralized RL agent — no
distributed multi-agent architecture.

## Tech stack (fixed — do not substitute without asking)

- Emulation: Mininet, Open vSwitch, OpenFlow 1.3
- Controller: Ryu Controller (Python)
- RL: Python, NumPy, Pandas; Q-learning baseline, optional DQN via PyTorch/SB3
- Routing/graph: NetworkX
- Dashboard backend: Flask + Flask-SocketIO
- Dashboard frontend: Chart.js or Plotly
- Traffic gen/testing: iperf/iperf3, Scapy
- Target OS: Ubuntu 20.04/22.04 LTS, Python 3.8+ (dev machine is Windows — Mininet/OVS
  need WSL2 Ubuntu; document this in README)

## Phases

1. **Network Emulation & Statistics Collection** — Mininet topology (6-10 switches,
   multiple hosts, redundant links, parameterized not hardcoded); Ryu app with LLDP
   topology discovery + OpenFlow stats polling every 1-2s (bytes tx/rx, drops, errors);
   derive: link utilization, delay, packet loss %, link trust level, switch throughput
   rate, link-to-switch rate (feed Phase 2 state vector); synthetic congestion
   injection + link/switch failure simulation (CLI or REST, propose one). Deliverable:
   topology + stats collector running, live metrics table.
2. **RL Agent** — state = normalized 6 features; action = choice among NetworkX
   candidate paths for a src-dst pair; Q-learning + epsilon-greedy baseline, structured
   so DQN can swap in later; reward function documented in code + `/docs/rl-design.md`;
   training loop with episode tracking, convergence logging, save/load. Deliverable:
   standalone-trainable agent showing upward reward trend.
3. **Routing & Flow Management** — topology as NetworkX graph, enumerate simple paths
   (capped length); translate RL path choice into OpenFlow flow rules via Ryu; Dijkstra
   baseline as alternate mode; fallback to Dijkstra on RL error/timeout/dead-link path,
   logged. Deliverable: switchable RL/baseline mode, traffic flows correctly under both.
4. **Visualization & Evaluation Dashboard** — Flask+SocketIO live updates; live topology
   w/ congestion color-coding, metrics panel, RL decision log w/ reasoning, live reward-
   convergence graph, RL-vs-baseline comparison view; static Matplotlib charts for the
   report. Deliverable: `python dashboard.py` serves live dashboard.
5. **Security Hardening** — TLS note/config for controller-switch channel; auth +
   input validation on any REST endpoints (Ryu fault-injection, Flask); dashboard auth;
   RL agent input validation (NaN/out-of-range state) and output validation before
   installing flow rules; flow rule validation against current valid topology; log
   rotation, no secrets in logs. Deliverable: `/docs/security-notes.md` — honest about
   what's implemented vs. known limitation of simulation scope.
6. **Documentation & SRS Traceability** — `/docs/architecture.md` (Mermaid diagram +
   component descriptions), `/docs/srs-traceability.md` (FR1-4/NFR1-4 → module →
   test), `/docs/rl-design.md`, `README.md` (setup, how to run each phase/dashboard,
   how to reproduce RL-vs-baseline comparison).

## SRS requirements

**Functional**
- FR1: Auto-discover topology; poll link stats every 1-2s via OpenFlow.
- FR2: RL agent takes state, evaluates candidate paths, selects one, computes reward,
  updates policy over episodes.
- FR3: Translate RL decision into flow rules; detect congestion/failure and reroute
  without manual intervention.
- FR4: Run RL and baseline routing under identical traffic; live dashboard (topology,
  metrics, decision log, reward-convergence graph).

**Non-functional**
- NFR1: Reroute within 2 seconds of detecting congestion/failure.
- NFR2: Reliable on 6-10 switch topologies, no degraded decision quality.
- NFR3: Maintain connectivity during failures; fall back to baseline if RL agent fails.
- NFR4: Dashboard clearly presents live state; codebase modular (topology/stats/RL/
  visualization independently modifiable).

## Testing expectations (per phase)

- Unit tests for pure logic (reward function, state normalization, path enumeration).
- Integration test script: Mininet + Ryu + agent together for a short scripted
  scenario (e.g. inject congestion at t=30s, assert reroute within NFR1's 2s bound).
- Documented manual test procedure for failure/recovery-time comparison (be upfront
  about what's automated vs. manually verified — full e2e Mininet tests are hard to
  fully automate).

## What NOT to do

- No real hardware deployment, no distributed multi-agent architecture.
- Don't silently swap the fixed tech stack without asking.
- Don't generate all phases in one response — go phase by phase.

## Current state (update as phases complete)

**Phase 1 implemented** (topology, discovery, stats polling, hybrid delay
probing, metrics, injection API, live metrics table) — see CHANGELOG.md for
the file list and docs/architecture.md for design rationale. Decisions locked
in for Phase 1: ring-with-chords topology (NetworkX circulant graph), hybrid
delay measurement (echo RTT + custom-ethertype link probe, validated against
injectable tc-netem ground truth), REST + CLI injection interface. 53 unit
tests pass for the pure logic (topology graph, metrics, injection
validation); the Ryu/Mininet-dependent code (main_app.py, ring_topology.py,
run_topology.py, topo_discovery.py, stats_poller.py, latency_probe.py,
injection_api.py) is untested end-to-end — this dev machine is Windows, so
Mininet/Ryu integration must be verified manually inside WSL2 Ubuntu (see
README.md's "what's manually verified vs. automated" section).

Not yet started: Phase 2 (RL agent), Phase 3 (routing/flow mgmt — main_app.py's
packet-in handler is currently just a minimal learning switch, a stand-in),
Phase 4 (dashboard), Phase 5 (security hardening — injection API has no auth
yet), Phase 6 (remaining docs: srs-traceability.md, rl-design.md).
