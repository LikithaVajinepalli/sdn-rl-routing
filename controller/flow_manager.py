"""Ryu glue for routing/flow_installer.py's pure FlowInstallation objects:
actually sends OFPFlowMod to install or remove them.
"""

from typing import Dict, Iterable, List

from routing.flow_installer import FlowInstallation

ROUTE_FLOW_PRIORITY = 10  # above Phase 1's table-miss (0) and any leftover learning-switch flow (1)


def install_flows(datapaths: Dict[int, object], installations: Iterable[FlowInstallation], priority: int = ROUTE_FLOW_PRIORITY) -> List[FlowInstallation]:
    installed = []
    for inst in installations:
        datapath = datapaths.get(inst.dpid)
        if datapath is None:
            continue
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        match = parser.OFPMatch(eth_src=inst.eth_src, eth_dst=inst.eth_dst)
        actions = [parser.OFPActionOutput(inst.out_port)]
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        datapath.send_msg(
            parser.OFPFlowMod(datapath=datapath, priority=priority, match=match, instructions=instructions)
        )
        installed.append(inst)
    return installed


def remove_flows_for_hosts(datapaths: Dict[int, object], dpids: Iterable[int], src_mac: str, dst_mac: str) -> None:
    """Deletes any installed route flow (both directions) between src_mac
    and dst_mac on the given switches - used when rerouting a flow off a
    failed link, before installing its replacement."""
    for dpid in dpids:
        datapath = datapaths.get(dpid)
        if datapath is None:
            continue
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        for eth_src, eth_dst in ((src_mac, dst_mac), (dst_mac, src_mac)):
            match = parser.OFPMatch(eth_src=eth_src, eth_dst=eth_dst)
            mod = parser.OFPFlowMod(
                datapath=datapath,
                command=ofproto.OFPFC_DELETE,
                out_port=ofproto.OFPP_ANY,
                out_group=ofproto.OFPG_ANY,
                match=match,
            )
            datapath.send_msg(mod)
