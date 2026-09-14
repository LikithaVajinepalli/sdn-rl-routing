# Changelog

All notable changes to this project are logged here, one phase/feature per
entry, newest first.

## [Unreleased] — Phase 4: Visualization & Evaluation Dashboard

### Added
- `dashboard/state_feed.py` - pure snapshot builders turning NetworkState /
  DecisionLog / ActiveFlowRegistry into the JSON the browser renders. No
  Flask, no Ryu, so the payload shape is unit-testable on its own.
- `dashboard/app.py` - Flask + Flask-SocketIO app: live snapshot pushes,
  `/api/snapshot`, `/api/training-curve`, `/api/benchmark`, and a
  same-origin `/api/mode/<mode>` so the header toggle doesn't need CORS
  against Ryu's REST port.
- `dashboard/server.py` - starts the dashboard inside Ryu's eventlet hub.
  Deliberately forgiving: a missing Flask install or a failed bind logs a
  warning and leaves the controller routing.
- `dashboard/templates/` + `static/` - the live UI: SVG topology with
  congestion colour-coding (positions computed client-side, so it adapts to
  any switch count), link metrics table, decision feed showing the candidate
  paths and live state behind each choice, and Chart.js reward/comparison
  charts.
- `routing/decision_log.py` - bounded decision history with per-candidate
  state snapshots; `RoutingDecision` now carries the candidates and their
  features, which is what lets the feed explain *why* a path won.
- `controller/routing_api.py` - `GET/POST /routing/mode`. Switching flushes
  installed routes so a benchmark run under each mode starts clean.
- `scripts/benchmark.py` + `scripts/benchmark_metrics.py` - runs the same
  scripted scenario under each routing mode and records measured latency,
  throughput, loss and post-failure recovery time to JSON. The failure is
  injected on the port the traffic is *actually* using (looked up from the
  installed flow), since targeting an unrelated link makes a reroute test
  silently vacuous - a mistake we made by hand during Phase 3 testing.
  Parsing/measurement logic is split out and unit-tested without Mininet.
- `scripts/make_report_charts.py` - static Matplotlib figures for the
  written report; skips any chart whose source data is missing rather than
  drawing invented numbers.
- 37 new unit tests (174 total).

### Note on architecture
The dashboard runs **in-process with the controller** rather than as a
separate service, so there is no `python dashboard.py` - `ryu-manager`
serves it on port 8081. That was a deliberate trade (chosen over a
polling REST client): zero lag and one copy of the state, at the cost of
tying the dashboard's lifecycle to the controller's.

## [Unreleased] — Phase 3 fixes found during manual WSL2 testing

### Fixed
- **Failure injection never actually did anything.** `injection_api.py`'s
  `_set_port_down` hardcoded `hw_addr="00:00:00:00:00:00"` in its
  `OFPPortMod`. OpenFlow requires that field to match the port's *real*
  hardware address; OVS silently rejects a mismatched port-mod, so no config
  change was applied, no `OFPPortStatus` event was emitted, and the REST call
  still returned 200. Every "failure injected" result since Phase 1 was a
  no-op - which is also why Phase 1's `pingall`-survives-failure check passed
  so easily (connectivity survives trivially when the link never went down).
  `NetworkState` now records each port's real `hw_addr` from the port
  description the switch already sends, and the endpoints return 409 instead
  of falsely reporting success when it isn't known yet.
- **Race that could silently skip rerouting.** When a link goes down,
  `ryu.topology` removes it from the discovered graph, so
  `neighbour_dpid()` returns `None` for exactly the link a port-down event
  is about - making `_port_status_handler` return early without rerouting.
  Added `NetworkState.neighbour_dpid_ever()`, backed by a persistent
  `last_known_neighbour` map that survives graph resyncs, and switched the
  handler to it. Caught from real logs: the first port-down event for the
  failed link hit the `None` path and skipped the reroute; only a later
  duplicate event happened to arrive before the resync and worked.

### Verified working end-to-end in WSL2 after these fixes
- `pingall`: 0% dropped (240/240) across the 8-switch/16-host topology, now
  routed by real RL/Dijkstra flow installation rather than Phase 1's flood.
- `ovs-ofctl dump-flows` confirms per-(src,dst)-MAC flow rules with correct
  per-hop output ports, priority 10 above the table-miss entry.
- Failure-triggered rerouting: forcing `dpid=1 port=3` down produced
  `link dpid=1 port=3 (-> dpid=8) went down - rerouting affected flows` and
  `rerouted 00:00:00:00:00:09 <-> 00:00:00:00:00:01 onto [5, 6, 1] (mode=rl)`
  - the RL agent picking the replacement path (FR3, NFR1, NFR3).

## [Unreleased] — Phase 3: Routing & Flow Management

### Added
- `routing/host_location.py` - `HostLocationTracker`, MAC -> (dpid, port).
- `routing/dijkstra.py` - independent hop-count-only shortest-path baseline
  (not piggybacked on the RL's candidate list, so the comparison is real).
- `routing/decision_engine.py` - `RoutingDecisionEngine`: resolves a routing
  decision from RL or Dijkstra depending on mode, filters out any candidate
  using a currently-down link, and catches any RL failure with an automatic
  Dijkstra fallback (NFR3). Fully unit-testable without Ryu.
- `routing/flow_installer.py` + `controller/flow_manager.py` - pure
  path-to-OpenFlow-rule translation (bidirectional/symmetric routing) and
  the thin Ryu glue that actually installs/removes them.
- `routing/flow_registry.py` - `ActiveFlowRegistry`, tracking which path is
  installed for each host pair so a link failure can find affected flows.
- `controller/main_app.py` - packet-in handling now routes known unicast
  host pairs via the decision engine (Phase 1's flood fallback still
  handles broadcast/multicast and not-yet-located destinations); a new
  `EventOFPPortStatus` handler detects link failure near-instantly (not
  LLDP-timeout-based) and reroutes affected flows (NFR1, NFR3);
  `--routing-mode {rl,dijkstra}` / `--rl-model-path` are new `ryu.cfg`
  options.
- 34 new unit tests (133 total) covering the new routing/ modules,
  including RL-failure fallback and down-link-avoidance scenarios.

### Fixed (found while designing the failure-fallback path)
- `NetworkState`'s link up/down status was keyed by the directed `LinkKey`
  used for delay tracking, which is asymmetric - and `injection_api.py` was
  already writing it with a hardcoded fake neighbour dpid (`0`), so it could
  never match a real lookup. Dormant since nothing read link status in
  Phase 1/2; Phase 3's fallback logic is the first real consumer. Re-keyed
  to plain `(dpid, port_no)`, with a new `NetworkState.is_edge_up(a, b)`
  that checks both sides of an edge.
- Extracted `NetworkState.live_link_metrics()` so the Phase 1 metrics table
  and Phase 3's routing decisions read live per-link state from the exact
  same code path instead of two independent implementations that could
  silently drift apart; moved the small `fallback_delay_ms` helper from
  `latency_probe.py` (Ryu-dependent) into `metrics.py` (pure) so
  `network_state.py` can use it without pulling Ryu into every
  framework-free module that touches live link state.

## [Unreleased] — Phase 2: Reinforcement Learning Agent

### Added
- `routing/path_enumeration.py` - candidate path enumeration (Yen's
  algorithm via NetworkX), pulled forward from Phase 3 since the RL action
  space needs it too.
- `rl/path_features.py` - aggregates per-link metrics along a path into
  `PathFeatures` (the state vector), full continuous fidelity.
- `rl/state.py` - tabular-only discretization of `PathFeatures` into a
  Q-table lookup key; isolated so a future DQN can skip it entirely.
- `rl/reward.py` - the documented reward formula (utilization-weighted
  heaviest, then loss/delay, then trust).
- `rl/environment.py` - `SimulatedRoutingEnv`, a NetworkX-based simulator so
  the agent trains standalone without Mininet/Ryu running.
- `rl/q_agent.py` - tabular Q-learning, epsilon-greedy, save/load.
- `rl/train.py` + `rl/config.py` - training loop CLI, episode/reward CSV
  logging, reward-curve PNG (matplotlib), Q-table checkpointing.
- `docs/rl-design.md` - state vector, action space, reward function,
  hyperparameters, and (critically) a benchmark against random and
  always-shortest-path baselines - not just a reward curve.
- 40 new unit tests across path enumeration, path features, reward,
  discretization, the Q-agent, and the environment (99 total, all pure
  Python - no Mininet/Ryu needed to run any of Phase 2).

### Found and fixed during actual training (not just unit tests)
- The environment's initial default demand (0.5-3 Mbps) never pushed any
  link past ~43% utilization even under a policy that ignores congestion
  entirely - meaning there was nothing real to learn to avoid. Increased to
  1-6 Mbps with slower background-load decay so congestion becomes a real,
  visible factor.
- `gamma=0.9` (the initial default) made the trained agent perform *worse*
  than a naive always-shortest-path baseline - caught by benchmarking
  against baselines rather than only watching the reward curve trend
  upward. Root cause: each step's "next state" is an unrelated, freshly
  sampled (src, dst) pair, so a high discount factor bootstraps heavily off
  a value unrelated to the action just taken. Lowered to `gamma=0.3`
  (empirically swept 0.0/0.3/0.6/0.9), after which the trained agent beats
  the shortest-path baseline by ~8% across 4 independent evaluation seeds.

## [Unreleased] — Phase 1 fixes found during manual WSL2 testing

### Fixed
- `pingall` was 100% dropped (even between hosts on the same switch), and
  the delay metric showed ~30s instead of ~5ms. Root cause: the Phase 1
  fallback learning-switch flooded blindly out every port, broadcast-
  storming across the topology's intentional loops (ring + chords) and
  saturating the controller. `NetworkState` now computes a spanning tree
  over the discovered topology and `flood_ports()` restricts flooding to
  host-facing + spanning-tree ports; off-tree chord links stay fully usable
  for Phase 3's real routing, just not for broadcast.
- `topo_discovery.py`'s undirected-graph edges silently lost one direction's
  port number (`ryu.topology.api.get_link` returns each link as two `Link`
  objects, and `nx.Graph` being undirected meant the second `add_edges_from`
  call overwrote the first's attributes). Edges now carry a merged
  `ports: {dpid: port_no}` dict instead of direction-dependent
  `src_port`/`dst_port` keys.
- `controller/metrics.py`'s `compute_link_metrics` took a `round_trip_ms`
  and halved it internally, but `main_app.py`'s caller was already passing
  an already-halved probe estimate through a needless double/halve dance
  that obscured the real bug above - simplified to take `delay_ms` directly.
- Setup docs (README/requirements.txt): the Ubuntu 20.04 apt `mininet`
  package only ships a Python 2 module; `ryu-manager` needs `PYTHONPATH=.`
  since it's a console-script entry point, not run via `python3 -m`.

## Phase 1: Network Emulation & Statistics Collection

### Added
- `topology/config.py` — `TopologyConfig`/`LinkProfile` dataclasses; enforces
  the 6-10 switch range from NFR2 at construction time.
- `topology/graph.py` — switch-level graph as a NetworkX circulant graph
  (ring + configurable chord offsets), shared by the Mininet topo and tests.
- `topology/ring_topology.py` — parameterized `RingChordTopo` Mininet `Topo`.
- `topology/run_topology.py` — CLI to launch the topology under Mininet with
  a remote controller.
- `controller/metrics.py` — pure functions deriving the six RL state
  features (utilization, delay, loss %, trust level, switch throughput rate,
  link-to-switch rate) from raw OpenFlow counters.
- `controller/network_state.py` — shared in-memory state (port sample
  history, echo RTTs, link delay history, link up/down + congestion status,
  discovered topology graph).
- `controller/topo_discovery.py` — LLDP-based topology discovery via Ryu's
  built-in `ryu.topology.switches`/`ryu.topology.api`.
- `controller/stats_poller.py` — OpenFlow port-stats polling every 1.5s.
- `controller/latency_probe.py` — hybrid delay measurement: echo-RTT +
  custom-ethertype link delay probes.
- `controller/injection_validation.py` + `controller/injection_api.py` — REST
  API (`/inject/failure`, `/inject/recover`, `/inject/congestion`) for
  synthetic failure/congestion injection, with input validation split out as
  pure, platform-independent logic.
- `controller/main_app.py` — the `ryu-manager` entry point wiring all of the
  above together, plus a minimal learning-switch packet-in fallback and a
  live metrics table printed every poll tick.
- `scripts/inject_cli.py` — CLI wrapper around the injection REST API.
- `tests/test_topology_graph.py`, `tests/test_metrics.py`,
  `tests/test_injection_validation.py` — unit tests for all pure logic
  (53 tests, platform-independent, no Mininet/Ryu required to run them).
- `docs/architecture.md` — Phase 1 component diagram and design rationale
  (delay measurement, injection mechanism, known limitations).
- `CLAUDE.md` — persistent project brief/working agreement for future
  sessions (tech stack, phases, SRS requirements, working agreement).
