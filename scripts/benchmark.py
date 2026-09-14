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
from typing import Optional

import requests
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController

from controller.injection_validation import (
    build_clear_netem_command,
    build_netem_command,
    iface_name,
    parse_netem_qdisc,
)
from scripts.benchmark_metrics import (
    find_route_port,
    parse_iperf_mbps,
    parse_ping,
    recovery_time_from_ping,
    summarise_mode,
)
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
    parser.add_argument(
        "--congest-delay-ms",
        type=float,
        default=80.0,
        help="delay injected onto the baseline's chosen link before measuring (0 disables congestion)",
    )
    parser.add_argument(
        "--congest-loss-pct",
        type=float,
        default=4.0,
        help="packet loss injected onto the baseline's chosen link before measuring",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=6.0,
        help="pause after injecting congestion so the controller's polled metrics reflect it",
    )
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


def apply_congestion(switch, iface: str, delay_ms: float, loss_pct: float):
    """Applies tc-netem congestion directly through Mininet.

    Deliberately NOT via the controller's /inject/congestion endpoint: that
    shells out to `tc`, which needs root, and the controller is run as a
    normal user (only Mininet needs sudo). This script already runs as root
    with the switch in hand, so it can do it directly and reliably.

    Also targets netem where it actually sits - TCLink puts HTB at the root
    for bandwidth with netem as a child, so `... root netem` targets the
    wrong qdisc."""
    show = switch.cmd(f"tc qdisc show dev {iface}")
    netem = parse_netem_qdisc(show)
    if netem is None:
        return False, f"no netem qdisc found on {iface}: {show.strip()}"

    handle, parent = netem
    command = " ".join(build_netem_command(iface, delay_ms, loss_pct, handle=handle, parent=parent))
    output = switch.cmd(command).strip()
    ok = "RTNETLINK" not in output and "Error" not in output and "Usage" not in output
    return ok, output or command


def clear_congestion(switch, iface: str) -> None:
    show = switch.cmd(f"tc qdisc show dev {iface}")
    netem = parse_netem_qdisc(show)
    if netem is None:
        return
    handle, parent = netem
    switch.cmd(" ".join(build_clear_netem_command(iface, handle=handle, parent=parent)))


def find_baseline_link(net, args) -> Optional[int]:
    """The port the *Dijkstra* baseline routes h1_1 -> h5_1 out of.

    Congesting this specific link is what makes the comparison mean
    something: the baseline picks purely on hop count so it keeps using it
    regardless, while the RL agent sees the degraded delay/loss in its state
    and can choose a different path. Without this, both strategies measure
    identically on an idle network - which is exactly what the first
    benchmark run showed."""
    set_routing_mode(args.rest_url, "dijkstra")
    src, dst = net.get("h1_1"), net.get("h5_1")
    src.cmd(f"ping -c 3 -W 1 {dst.IP()}")
    time.sleep(1)
    return dump_route_port(net.get("s1"), src.MAC(), dst.MAC())


def dump_route_port(switch, src_mac: str, dst_mac: str):
    """Asks the switch for its flow table and reads which port the src->dst
    flow forwards out of (parsing lives in benchmark_metrics so it's tested)."""
    flows = switch.cmd(f"ovs-ofctl -O OpenFlow13 dump-flows {switch.name}")
    return find_route_port(flows, src_mac, dst_mac)


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

    chosen_port = dump_route_port(edge_switch, src.MAC(), dst.MAC())
    print(f"  this mode routes h1_1 -> h5_1 out of s1 port {chosen_port}")

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
    route_port = dump_route_port(edge_switch, src.MAC(), dst.MAC())
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
    summary["chosen_port"] = chosen_port
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

    results = {"timestamp": time.time(), "scenario": vars(args), "modes": {}, "congestion": None}
    congested_port = None
    try:
        net.start()
        print("waiting for topology discovery to settle…")
        time.sleep(12)  # LLDP discovery + a couple of stats polls
        net.pingAll(timeout="1")  # prime host locations for every pair

        # Congest the link the BASELINE insists on using, identically for
        # both modes. On an idle network the two strategies measure the same
        # because there's nothing to route around - this is what gives the
        # comparison something to actually compare.
        if args.congest_delay_ms > 0 or args.congest_loss_pct > 0:
            congested_port = find_baseline_link(net, args)
            if congested_port is None:
                print("! could not determine the baseline's link - running without congestion")
            else:
                iface = iface_name(1, congested_port)
                print(
                    f"\ncongesting {iface} "
                    f"(+{args.congest_delay_ms}ms, {args.congest_loss_pct}% loss) — the path Dijkstra picks"
                )
                ok, detail = apply_congestion(
                    net.get("s1"), iface, args.congest_delay_ms, args.congest_loss_pct
                )
                if not ok:
                    print(f"! congestion injection failed: {detail}")
                    congested_port = None
                else:
                    results["congestion"] = {
                        "dpid": 1,
                        "port": congested_port,
                        "iface": iface,
                        "delay_ms": args.congest_delay_ms,
                        "loss_pct": args.congest_loss_pct,
                    }
                    print(f"waiting {args.settle_seconds}s for the controller's metrics to reflect it…")
                    time.sleep(args.settle_seconds)

        for mode in args.modes:
            results["modes"][mode] = run_mode(net, args, mode)
    finally:
        if congested_port is not None:
            clear_congestion(net.get("s1"), iface_name(1, congested_port))
        net.stop()

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as handle:
        json.dump(results, handle, indent=2)
    print(f"\nWrote {args.out}")
    for mode, summary in results["modes"].items():
        print(f"  {mode:9s} {summary}")

    # The headline the report needs: did the two strategies actually route
    # differently, and did it show up in the measurements?
    ports = {mode: results["modes"][mode].get("chosen_port") for mode in results["modes"]}
    if len(set(p for p in ports.values() if p is not None)) > 1:
        print(f"\n  -> the modes chose DIFFERENT paths out of s1: {ports}")
    elif results["congestion"]:
        print(f"\n  -> both modes chose the same path out of s1 ({ports}) despite injected congestion")


if __name__ == "__main__":
    main()
