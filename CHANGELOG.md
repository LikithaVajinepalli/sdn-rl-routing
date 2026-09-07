# Changelog

All notable changes to this project are logged here, one phase/feature per
entry, newest first.

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
