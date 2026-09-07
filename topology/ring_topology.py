"""Mininet Topo: a parameterized ring-of-switches with redundant chord links.

Requires Mininet (Linux / WSL2 Ubuntu) to actually instantiate - see
topology/graph.py for the platform-independent graph structure this mirrors,
and topology/run_topology.py for the CLI entry point that runs this in Mininet.
"""

from mininet.topo import Topo

from topology.config import TopologyConfig
from topology.graph import build_switch_graph, switch_name


class RingChordTopo(Topo):
    """Ring of `num_switches` OVS switches plus chord shortcuts, each switch
    carrying `hosts_per_switch` hosts. Every link's bandwidth/delay/loss comes
    from the TopologyConfig so nothing about size or link quality is hardcoded.
    """

    def build(self, config: TopologyConfig = None, **_ignored):
        config = config or TopologyConfig()
        graph = build_switch_graph(config)

        switches = {}
        for index in range(config.num_switches):
            name = switch_name(index)
            switches[name] = self.addSwitch(name, protocols="OpenFlow13")

        for index in range(config.num_switches):
            sw_name = switch_name(index)
            for h in range(config.hosts_per_switch):
                host_name = f"h{index + 1}_{h + 1}"
                host = self.addHost(host_name)
                self.addLink(host, switches[sw_name])

        for u, v, data in graph.edges(data=True):
            self.addLink(
                switches[u],
                switches[v],
                bw=data["bw_mbps"],
                delay=f"{data['delay_ms']}ms",
                loss=data["loss_pct"],
                use_htb=True,
            )


topos = {"ringchord": lambda: RingChordTopo(config=TopologyConfig())}
