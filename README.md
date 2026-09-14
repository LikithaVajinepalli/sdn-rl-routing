# RL-Based Intelligent SDN Routing System

A Mininet-simulated SDN where a Ryu controller collects live network state
and an RL agent learns to route traffic, benchmarked against a Dijkstra
shortest-path baseline. See `CLAUDE.md` for the full project brief and
`/docs` for living design documentation. This README covers **Phases 1-3**
so far — later phases append their own run instructions here.

## Requirements

- **Dev machine**: any OS for editing/unit tests (this repo's pure-Python
  modules run fine on Windows/macOS/Linux).
- **Runtime**: Mininet + Open vSwitch + Ryu only run on Linux. On Windows,
  use **WSL2 Ubuntu 20.04/22.04**.

## One-time setup (inside WSL2 Ubuntu)

```bash
sudo apt update
sudo apt install -y mininet openvswitch-switch python3-pip
sudo service openvswitch-switch start
sudo pip3 install -r requirements.txt
```

No venv here on purpose: Mininet needs `sudo` to run (root, for network namespaces),
and `mn`/`mnexec` come from the APT package, not pip — installing everything with
`sudo pip3` system-wide means the same packages are visible whether a command is run
as your normal user (`ryu-manager`) or with `sudo` (`python3 -m topology.run_topology`),
without extra `--system-site-packages` venv wiring.

**Known gotcha:** the `mininet` APT package on Ubuntu 20.04 only ships a **Python 2**
module — running `python3 -c "import mininet"` fails even after installing it. The
`mininet` entry in `requirements.txt` (pip, not apt) supplies the importable Python 3
module; the APT package is still required for `mn`/`mnexec` and Open vSwitch itself.

## Running Phase 1

Three terminals, all inside WSL2, all `cd`'d into this project directory
(e.g. `/mnt/d/sdn-rl-routing` if this repo lives on a Windows drive).

**Terminal 1 — Ryu controller** (discovery + stats collector + injection API):

```bash
PYTHONPATH=. ryu-manager --observe-links controller.main_app
```

No `sudo` needed - Ryu is just a plain TCP/WSGI server. `PYTHONPATH=.` is
needed because `ryu-manager` is a script installed under `/usr/local/bin`,
so it doesn't automatically add the current directory to Python's import
path the way `python3 -m ...` does; without it, `controller.main_app` fails
to import with `ModuleNotFoundError: No module named 'controller'`.
`--observe-links` is required so Ryu's built-in topology app performs
LLDP-based discovery; without it, `controller/topo_discovery.py` sees no
switches/links. The controller prints a live metrics table (utilization,
delay, loss %, trust level, switch throughput, link/switch share) once per
poll tick, and serves the injection REST API on `127.0.0.1:8080`. It's
normal for it to print "No port stats yet - waiting for switches to
connect." on repeat until Terminal 2's Mininet topology comes up.

**Terminal 2 — Mininet topology**:

```bash
sudo python3 -m topology.run_topology --switches 8 --hosts-per-switch 2 \
    --chord-offsets 3 --controller-ip 127.0.0.1 --controller-port 6653
```

This drops into the Mininet CLI once switches connect to the controller.
Useful commands from there: `pingall` (verify connectivity across the
redundant links), `iperf h1_1 h4_1` (generate traffic so the metrics table
shows non-zero utilization).

**Terminal 3 — inject synthetic congestion/failure** (optional, while the
above two are running):

```bash
python -m scripts.inject_cli congestion --dpid 3 --port 2 --delay-ms 80 --loss-pct 5
python -m scripts.inject_cli failure --dpid 3 --port 2
python -m scripts.inject_cli recover --dpid 3 --port 2
```

`--dpid`/`--port` identify the link's switch-side endpoint; find them from
the metrics table (Terminal 1) or `ovs-ofctl show s3` inside Mininet.

## Running Phase 2 (train the RL agent)

No Mininet/Ryu/WSL2 needed - trains against a NetworkX simulator, runs
anywhere:

```bash
pip install -r requirements.txt   # or just: pip install networkx matplotlib
python -m rl.train --episodes 4000 --seed 42
```

Writes `models/q_agent.pkl` (the checkpoint `main_app.py` loads in Phase 3),
`models/training_log.csv`, and `models/reward_curve.png`. See
`docs/rl-design.md` for the full design and real benchmark numbers.

## Running Phase 3 (real routing)

Same three terminals as Phase 1, with one addition: make sure
`models/q_agent.pkl` exists first (run Phase 2's training above at least
once) if you want to use RL mode.

**Terminal 1 — Ryu controller**, now with a routing-mode flag:

```bash
PYTHONPATH=. ryu-manager --observe-links controller.main_app                    # RL mode (default)
PYTHONPATH=. ryu-manager --observe-links --routing-mode dijkstra controller.main_app  # baseline mode
```

If `models/q_agent.pkl` is missing, RL mode logs a warning and falls back to
Dijkstra for every decision rather than failing to start.

**Terminal 2 — Mininet topology**: same command as Phase 1. Try `pingall`
(now routed via RL/Dijkstra flow installation instead of Phase 1's flood
fallback) and `iperf h1_1 h4_1` to generate real traffic.

**Terminal 3 — test failure-triggered rerouting**:

```bash
python -m scripts.inject_cli failure --dpid 3 --port 1
```

Then, in Terminal 2, re-run traffic between a host pair that was routed
through that link - watch Terminal 1's log for a "rerouted ... after link
failure" message, and confirm the traffic still gets through (NFR1/NFR3).
`python -m scripts.inject_cli recover --dpid 3 --port 1` restores the link
(existing flows are not automatically moved back - only broken ones reroute).

## Running Phase 4 (the dashboard)

The dashboard runs **inside the controller process** (it reads NetworkState
directly rather than polling a REST API), so there's no separate
`dashboard.py` to launch — starting the controller starts it:

```bash
PYTHONPATH=. ryu-manager --observe-links controller.main_app
```

Then open **http://localhost:8081** in your browser (on Windows, WSL2 forwards
localhost automatically). Add `--dashboard-port 0` to disable it, or
`--dashboard-port 9000` to move it.

The page shows the live topology with congestion colour-coding, per-link
metrics, the routing decision feed (with the candidate paths and live state
behind each choice), the reward-convergence curve from Phase 2's training
run, and the RL-vs-baseline comparison. The **RL / Dijkstra toggle** in the
header switches routing strategy at runtime and flushes installed routes, so
traffic after the switch is genuinely routed by the newly selected strategy.

Endpoints, handy without a browser:
```bash
curl localhost:8081/api/snapshot        # exactly what the socket pushes
curl localhost:8080/routing/mode        # current mode (Ryu's REST API)
curl -X POST localhost:8080/routing/mode -H 'Content-Type: application/json' -d '{"mode":"dijkstra"}'
```

### Benchmarking RL vs the Dijkstra baseline

With the controller running (and **no** other Mininet instance up — this
builds its own):

```bash
sudo python3 -m scripts.benchmark
```

It runs the same scripted scenario under each mode — steady-state ping,
`iperf` throughput, then a link failure injected into live traffic on the
link that traffic is actually using — and writes measured latency,
throughput, loss, and recovery time to `models/benchmark_results.json`. The
dashboard's comparison view picks that file up automatically.

### Static charts for the report

```bash
python3 -m scripts.make_report_charts
```

Writes `docs/figures/reward_convergence.png` and (once a benchmark exists)
`docs/figures/rl_vs_baseline.png`. Needs no Mininet/Ryu — it only reads
`models/training_log.csv` and `models/benchmark_results.json`, and skips any
chart whose source data is missing rather than inventing numbers.

## Running the unit tests

The test suite covers only the pure logic (topology graph structure, derived
metrics, injection request validation) — it does not require Mininet, Ryu,
or WSL2, and runs on any platform:

```bash
pip install pytest tabulate networkx
python -m pytest tests/ -v
```

## What's manually verified vs. automated

- **Automated** (133 tests, pure Python, run anywhere): topology
  parameterization/redundancy, all six derived-metric formulas, injection
  request validation, network-state spanning-tree/flood/link-status logic,
  RL path features/reward/state-discretization/Q-agent/environment, and
  Phase 3's Dijkstra baseline, flow installation, flow registry, and
  routing-decision-engine logic (including RL-failure fallback and
  down-link avoidance) - all testable without Mininet/Ryu since those
  modules take `NetworkState`/graphs/fake agents as plain data, not live
  OpenFlow connections.
- **Manual only, inside WSL2** (Phase 1 was fully verified this way - see
  CLAUDE.md's "Current state" section for the actual results): that the Ryu
  app connects to real OVS switches, LLDP discovery finds the expected
  topology, the live metrics table shows sensible values under real
  traffic, the injection endpoints visibly affect the emulated network, and
  (Phase 3, not yet manually verified as of this writing) that real flow
  installation and failure-triggered rerouting behave correctly against
  live Mininet traffic. A fully automated Mininet+Ryu integration test is a
  later-phase goal, not yet built.
