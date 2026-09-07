#!/usr/bin/env python3
"""CLI entry point: build a RingChordTopo and run it under Mininet with a
remote (Ryu) controller. Run this inside WSL2 Ubuntu with root privileges:

    sudo python3 -m topology.run_topology --switches 8 --hosts-per-switch 2 \\
        --chord-offsets 3 --controller-ip 127.0.0.1 --controller-port 6653
"""

import argparse

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.cli import CLI

from topology.config import LinkProfile, TopologyConfig
from topology.ring_topology import RingChordTopo


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--switches", type=int, default=8)
    parser.add_argument("--hosts-per-switch", type=int, default=1)
    parser.add_argument(
        "--chord-offsets",
        type=int,
        nargs="*",
        default=[3],
        help="extra circulant-graph offsets beyond the ring (offset=1)",
    )
    parser.add_argument("--bw", type=float, default=10.0, help="link bandwidth (Mbps)")
    parser.add_argument("--delay", type=float, default=5.0, help="base link delay (ms)")
    parser.add_argument("--loss", type=float, default=0.0, help="base link loss (%%)")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)
    return parser.parse_args()


def main():
    args = parse_args()
    config = TopologyConfig(
        num_switches=args.switches,
        hosts_per_switch=args.hosts_per_switch,
        chord_offsets=args.chord_offsets,
        default_link=LinkProfile(bw_mbps=args.bw, delay_ms=args.delay, loss_pct=args.loss),
    )

    setLogLevel("info")
    topo = RingChordTopo(config=config)
    net = Mininet(
        topo=topo,
        link=TCLink,
        switch=OVSSwitch,
        controller=lambda name: RemoteController(
            name, ip=args.controller_ip, port=args.controller_port
        ),
        autoSetMacs=True,
    )
    net.start()
    CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
