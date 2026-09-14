#!/usr/bin/env python3
"""RL vs Dijkstra benchmark harness (FR4: "run RL and baseline routing under
identical traffic conditions").

Runs the SAME scripted scenario under each routing mode and records real
measurements from Mininet - latency, throughput, packet loss, and how long
traffic was actually down after a link failure. Results go to
models/benchmark_results.json, which the dashboard's comparison view renders
and scripts/make_report_charts.py turns into figures for the report.

Must run as root inside WSL2, with the Ryu controller already running:

    # terminal 1
    PYTHONPATH=. ryu-manager --observe-links controller.main_app
    # terminal 2
    sudo python3 -m scripts.benchmark

It builds (and tears down) its own Mininet topology, so don't have another
one running at the same time.
"""

import argparse
import json
import os
import time

import requests
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from scripts.benchmark_metrics import parse_iperf_mbps, parse_ping, recovery_time_from_ping, summarise_mode
from topology.config import LinkProfile, TopologyConfig
from topology.ring_topology import RingChordTopo

DEFAULT_OUT = "models/benchmark_results.json"
PING_INTERVAL_S = 0.2  # 5 pings/sec - fine enough to measure a sub-second outage


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--switches", type=int, default=8)
    parser.add_argument("--hosts-per-switch", type=int, default=2)
    parser.add_argument("--chord-offsets", type=int, nargs="*", default=[3])
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    parser.add_argument("--rest-url", default="http://127.0.0.1:8080")
    parser.add_argument("--modes", nargs="*", default=["dijkstra", "rl"])
    parser.add_argument("--iperf-seconds", type=int, default=8)
    parser.add_argument("--ping-count", type=int, default=60, help="pings during the failure phase")
    parser.add_argument("--out", default=DEFAULT_OUT)
    return parser.parse_args()


def set_routing_mode(rest_url: str, mode: str) -> dict:
    response = requests.post(f"{rest_url}/routing/mode", json={"mode": mode}, timeout=5)
    response.raise_for_status()
    return response.json()


def inject_failure(rest_url: str, dpid: int, port: int) -> dict:
    response = requests.post(f"{rest_url}/inject/failure", json={"dpid": dpid, "port": port}, timeout=5)
    response.raise_for_status()
    return response.json()


def recover_link(rest_url: str, dpid: int, port: int) -> None:
    requests.post(f"{rest_url}/inject/recover", json={"dpid": dpid, "port": port}, timeout=5)


def find_route_port(switch, src_mac: str, dst_mac: str):
    """Which port the installed flow for src->dst actually uses - so the
    failure we inject hits the link this traffic is really on, rather than
    an unrelated one (a mistake that makes a reroute test silently vacuous)."""
    flows = switch.cmd(f"ovs-ofctl -O OpenFlow13 dump-flows {switch.name}")
    for line in flows.splitlines():
        if f"dl_src={src_mac}" in line and f"dl_dst={dst_mac}" in line:
            marker = "actions=output:"
            if marker in line:
                return int(line.split(marker)[1].strip().strip('"').split(",")[0].strip('"'))
    return None


def run_mode(net, args, mode: str) -> dict:
    """One full measurement pass: settle, measure steady state, break the
    link the traffic is using, measure the outage, then restore."""
    print(f"\n=== mode: {mode} ===")
    switch_result = set_routing_mode(args.rest_url, mode)
    print(f"  routing mode set (flushed {switch_result.get('flushed_routes', 0)} existing routes)")

    src, dst = net.get("h1_1"), net.get("h5_1")
    edge_switch = net.get("s1")

    # Warm-up: get ARP resolved and flows installed under this mode.
    src.cmd(f"ping -c 3 -W 1 {dst.IP()}")
    time.sleep(1)

    print("  measuring steady-state latency/loss…")
    ping_output = src.cmd(f"ping -c 20 -i 0.2 -W 1 {dst.IP()}")
    steady = parse_ping(ping_output)

    print("  measuring throughput…")
    dst.cmd("iperf -s -D > /dev/null 2>&1")
    time.sleep(0.5)
    iperf_output = src.cmd(f"iperf -c {dst.IP()} -t {args.iperf_seconds}")
    throughput = parse_iperf_mbps(iperf_output)
    dst.cmd("kill %iperf 2>/dev/null; pkill -f 'iperf -s' 2>/dev/null")

    print("  measuring failure recovery…")
    route_port = find_route_port(edge_switch, src.MAC(), dst.MAC())
    recovery = None
    if route_port is None:
        print("  ! could not find the installed route's port - skipping the failure phase")
    else:
        print(f"    traffic leaves s1 via port {route_port}; failing it mid-ping")
        # Ping in the background so the failure lands mid-stream, then read
        # the sequence gaps to see how long traffic was actually down.
        src.cmd(
            f"ping -c {args.ping_count} -i {PING_INTERVAL_S} -W 1 {dst.IP()} "
            f"> /tmp/bench_ping_{mode}.txt 2>&1 &"
        )
        time.sleep(args.ping_count * PING_INTERVAL_S * 0.35)
        inject_failure(args.rest_url, 1, route_port)
        failure_at = time.time()
        src.cmd("wait")
        failure_ping = src.cmd(f"cat /tmp/bench_ping_{mode}.txt")
        recovery = recovery_time_from_ping(failure_ping, PING_INTERVAL_S)
        print(f"    outage lasted ~{recovery}s (failure injected {time.time() - failure_at:.1f}s ago)")
        recover_link(args.rest_url, 1, route_port)
        time.sleep(2)  # let the link come back before the next mode

    summary = summarise_mode(steady, throughput, recovery)
    print(f"  -> {summary}")
    return summary


def main():
    args = parse_args()
    setLogLevel("warning")

    config = TopologyConfig(
        num_switches=args.switches,
        hosts_per_switch=args.hosts_per_switch,
        chord_offsets=args.chord_offsets,
        default_link=LinkProfile(),
    )
    net = Mininet(
        topo=RingChordTopo(config=config),
        link=TCLink,
        switch=OVSSwitch,
        controller=lambda name: RemoteController(name, ip=args.controller_ip, port=args.controller_port),
        autoSetMacs=True,
    )

    results = {"timestamp": time.time(), "scenario": vars(args), "modes": {}}
    try:
        net.start()
        print("waiting for topology discovery to settle…")
        time.sleep(12)  # LLDP discovery + a couple of stats polls
        net.pingAll(timeout="1")  # prime host locations for every pair

        for mode in args.modes:
            results["modes"][mode] = run_mode(net, args, mode)
    finally:
        net.stop()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(results, handle, indent=2)
    print(f"\nWrote {args.out}")
    for mode, summary in results["modes"].items():
        print(f"  {mode:9s} {summary}")


if __name__ == "__main__":
    main()
