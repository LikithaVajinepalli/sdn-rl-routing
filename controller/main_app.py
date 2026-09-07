"""Ryu app entry point (FR1). Run with:

    ryu-manager --observe-links controller.main_app

`--observe-links` is required so ryu.topology.switches performs LLDP-based
discovery and controller/topo_discovery.py can read it via ryu.topology.api.

Wires together: topology discovery, port-stats polling, hybrid latency
probing, the injection REST API, and a live metrics table printed every poll
tick. Phase 1's data-plane forwarding is a minimal learning switch (flood +
learn) - Phase 3 replaces _handle_data_packet_in with RL/Dijkstra-driven flow
installation.
"""

import logging
import time
from typing import Dict, Tuple

from ryu.app.wsgi import WSGIApplication
from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, DEAD_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ethernet, ether_types, packet
from ryu.ofproto import ofproto_v1_3
from ryu.topology import event as topo_event
from tabulate import tabulate

from controller import injection_api, latency_probe, stats_poller, topo_discovery
from controller.metrics import compute_link_metrics
from controller.network_state import LinkKey, NetworkState

logger = logging.getLogger("sdn_rl_routing")

DEFAULT_LINK_BW_MBPS = 10.0  # fallback when a switch reports curr_speed == 0
LINK_PROBE_INTERVAL_SEC = 2.0


class SDNControllerApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths: Dict[int, object] = {}
        self.network_state = NetworkState()
        self.mac_to_port: Dict[int, Dict[str, int]] = {}
        self._pending_probes: Dict[int, Tuple[int, int]] = {}

        wsgi = kwargs["wsgi"]
        wsgi.register(
            injection_api.InjectionController,
            {"network_state": self.network_state, "datapaths": self.datapaths},
        )

        self._poll_thread = hub.spawn(self._poll_loop)
        self._probe_thread = hub.spawn(self._probe_loop)
        self._print_thread = hub.spawn(self._print_loop)

    # --- OpenFlow session lifecycle -------------------------------------

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            self.datapaths.pop(datapath.id, None)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features_handler(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        # Table-miss: send unmatched packets (incl. our delay probes) to the controller.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofproto.OFPP_CONTROLLER, ofproto.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, 0, match, actions)
        datapath.send_msg(parser.OFPPortDescStatsRequest(datapath, 0))

    def _add_flow(self, datapath, priority, match, actions):
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        inst = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]
        datapath.send_msg(parser.OFPFlowMod(datapath=datapath, priority=priority, match=match, instructions=inst))

    # --- Topology discovery (FR1) -----------------------------------------

    @set_ev_cls(
        [
            topo_event.EventSwitchEnter,
            topo_event.EventSwitchLeave,
            topo_event.EventLinkAdd,
            topo_event.EventLinkDelete,
        ]
    )
    def _topology_change_handler(self, ev):
        topo_discovery.sync_from_ryu_topology(self, self.network_state)

    # --- Stats polling (FR1) -----------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        stats_poller.handle_port_stats_reply(ev, self.network_state)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def _port_desc_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        for port in ev.msg.body:
            speed_mbps = (port.curr_speed or 0) / 1000.0
            self.network_state.record_port_speed(dpid, port.port_no, speed_mbps)

    @set_ev_cls(ofp_event.EventOFPEchoReply, MAIN_DISPATCHER)
    def _echo_reply_handler(self, ev):
        latency_probe.handle_echo_reply(ev, self.network_state)

    # --- Packet-in dispatch --------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        pkt = packet.Packet(ev.msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        if eth.ethertype == latency_probe.PROBE_ETH_TYPE:
            self._handle_probe_packet_in(ev)
        elif eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return  # consumed by ryu.topology.switches
        else:
            self._handle_data_packet_in(ev, eth)

    def _handle_probe_packet_in(self, ev):
        parsed = latency_probe.parse_probe_payload(ev.msg.data)
        if parsed is None:
            return
        probe_id, sent_time = parsed
        pending = self._pending_probes.pop(probe_id, None)
        if pending is None:
            return
        src_dpid, src_port = pending
        dst_dpid = ev.msg.datapath.id
        recv_time = time.time()
        src_rtt = self.network_state.echo_rtt_ms.get(src_dpid, 0.0)
        dst_rtt = self.network_state.echo_rtt_ms.get(dst_dpid, 0.0)
        delay_ms = latency_probe.estimate_link_delay_ms(sent_time, recv_time, src_rtt, dst_rtt)
        self.network_state.record_link_delay(LinkKey(src_dpid, src_port, dst_dpid), delay_ms)

    def _handle_data_packet_in(self, ev, eth):
        """Minimal learning-switch fallback so Phase 1 has real traffic to
        measure. Phase 3 replaces this with RL/Dijkstra flow installation."""
        msg = ev.msg
        datapath = msg.datapath
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match["in_port"]

        table = self.mac_to_port.setdefault(dpid, {})
        table[eth.src] = in_port
        out_port = table.get(eth.dst, ofproto.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst)
            self._add_flow(datapath, 1, match, actions)

        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        datapath.send_msg(
            parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data
            )
        )

    # --- Background loops --------------------------------------------------

    def _poll_loop(self):
        while True:
            for datapath in list(self.datapaths.values()):
                stats_poller.request_port_stats(datapath)
                latency_probe.send_echo_request(datapath)
            hub.sleep(stats_poller.POLL_INTERVAL_SEC)

    def _probe_loop(self):
        while True:
            for src_dpid, dst_dpid, data in list(self.network_state.graph.edges(data=True)):
                src_port = data.get("src_port")
                datapath = self.datapaths.get(src_dpid)
                if datapath is None or src_port is None:
                    continue
                probe_id, _sent_time = latency_probe.send_delay_probe(
                    datapath, src_port, "00:00:00:00:00:01", "ff:ff:ff:ff:ff:ff"
                )
                self._pending_probes[probe_id] = (src_dpid, src_port)
            hub.sleep(LINK_PROBE_INTERVAL_SEC)

    def _print_loop(self):
        while True:
            hub.sleep(stats_poller.POLL_INTERVAL_SEC)
            self._print_metrics_table()

    def _print_metrics_table(self):
        rows = []
        for (dpid, port_no), history in list(self.network_state.port_samples.items()):
            pair = self.network_state.port_sample_pair(dpid, port_no)
            if pair is None:
                continue
            prev, curr = pair
            bw = self.network_state.link_bw_mbps(dpid, port_no, DEFAULT_LINK_BW_MBPS)
            neighbour_dpid = self._neighbour_dpid(dpid, port_no)
            recent_delays = self.network_state.recent_delays(LinkKey(dpid, port_no, neighbour_dpid or 0))
            round_trip_ms = recent_delays[-1] * 2 if recent_delays else self.network_state.echo_rtt_ms.get(dpid, 0.0)
            switch_ports = [p for (d, p) in self.network_state.port_samples if d == dpid]
            switch_rates = self.network_state.switch_port_rates_bps(dpid, switch_ports)
            metrics = compute_link_metrics(
                prev, curr, bw, round_trip_ms, recent_delays, switch_rates
            )
            rows.append(
                [
                    dpid,
                    port_no,
                    f"{metrics.utilization * 100:.1f}%",
                    f"{metrics.delay_ms:.2f}ms",
                    f"{metrics.loss_pct:.2f}%",
                    f"{metrics.trust_level:.2f}",
                    f"{metrics.switch_throughput_bps / 1e6:.2f}MB/s",
                    f"{metrics.link_to_switch_rate * 100:.1f}%",
                ]
            )
        if not rows:
            logger.info("No port stats yet - waiting for switches to connect.")
            return
        headers = ["dpid", "port", "util", "delay", "loss", "trust", "switch_thpt", "link/switch"]
        print(tabulate(rows, headers=headers, tablefmt="simple"))

    def _neighbour_dpid(self, dpid: int, port_no: int):
        for u, v, data in self.network_state.graph.edges(data=True):
            if u == dpid and data.get("src_port") == port_no:
                return v
            if v == dpid and data.get("dst_port") == port_no:
                return u
        return None
