# RL-Based Intelligent SDN Routing System

A Mininet-simulated SDN where a Ryu controller collects live network state
and an RL agent learns to route traffic, benchmarked against a Dijkstra
shortest-path baseline. See `CLAUDE.md` for the full project brief and
`/docs` for living design documentation. This README covers **Phase 1**
only — later phases append their own run instructions here.

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

## Running the unit tests

The test suite covers only the pure logic (topology graph structure, derived
metrics, injection request validation) — it does not require Mininet, Ryu,
or WSL2, and runs on any platform:

```bash
pip install pytest tabulate networkx
python -m pytest tests/ -v
```

## What's manually verified vs. automated (Phase 1)

- **Automated**: topology parameterization/redundancy (`test_topology_graph.py`),
  all six derived-metric formulas (`test_metrics.py`), injection request
  validation and `tc` command construction (`test_injection_validation.py`).
- **Manual only, inside WSL2**: that the Ryu app actually connects to real
  OVS switches, that LLDP discovery finds the expected topology, that the
  live metrics table shows sensible non-zero values under real traffic, and
  that the injection endpoints visibly affect the emulated network
  (`pingall` failing across a downed link, `iperf` throughput dropping under
  injected congestion). A fully automated Mininet+Ryu integration test is a
  later-phase goal, not yet built.
