# Changelog

All notable changes to this project are logged here, one phase/feature per
entry, newest first.

## [Unreleased] — Phase 1: Network Emulation & Statistics Collection

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
