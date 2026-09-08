"""Ryu app entry point (FR1-FR3). Run with:

    ryu-manager --observe-links controller.main_app
    ryu-manager --observe-links --routing-mode dijkstra controller.main_app

`--observe-links` is required so ryu.topology.switches performs LLDP-based
discovery and controller/topo_discovery.py can read it via ryu.topology.api.
`--routing-mode` picks the Phase 3 routing strategy (default "rl", or
"dijkstra" for the baseline) - see routing/decision_engine.py.

Wires together: topology discovery, port-stats polling, hybrid latency
probing, the injection REST API, a live metrics table printed every poll
tick, and (Phase 3) real RL/Dijkstra-driven routing: new host-to-host flows
get a path from routing/decision_engine.py and hop-by-hop OpenFlow rules via
controller/flow_manager.py; a link failure (EventOFPPortStatus) reroutes any
already-installed flow that used it. Broadcast/multicast traffic (ARP, etc.)
still uses Phase 1's loop-safe flood (NetworkState.flood_ports) - routing
decisions only apply to known unicast host pairs.
"""

import logging
import pickle
import time
from typing import Dict, Tuple

from ryu import cfg
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
from controller.flow_manager import install_flows, remove_flows_for_hosts
from controller.network_state import LinkKey, NetworkState
from routing.decision_engine import RoutingDecisionEngine
from routing.flow_installer import build_bidirectional_flows
from routing.flow_registry import ActiveFlowRegistry, FlowRecord
from routing.host_location import HostLocationTracker

logger = logging.getLogger("sdn_rl_routing")

DEFAULT_LINK_BW_MBPS = 10.0  # fallback when a switch reports curr_speed == 0
LINK_PROBE_INTERVAL_SEC = 2.0
DEFAULT_MODEL_PATH = "models/q_agent.pkl"

CONF = cfg.CONF
CONF.register_opts(
    [
        cfg.StrOpt("routing-mode", default="rl", help="Phase 3 routing strategy: 'rl' or 'dijkstra'"),
        cfg.StrOpt("rl-model-path", default=DEFAULT_MODEL_PATH, help="trained Q-agent pickle (rl/train.py's output)"),
    ]
)


def _load_agent(path: str):
    from rl.q_agent import QLearningAgent

    try:
        agent = QLearningAgent()
        agent.load(path)
        agent.epsilon = 0.0  # greedy at inference time - no exploration in production
        logger.info("loaded RL agent from %s (%d Q-table entries)", path, len(agent.q_table))
        return agent
    except (FileNotFoundError, pickle.PickleError, EOFError) as exc:
        logger.warning("could not load RL agent from %s (%s) - routing-mode 'rl' will fall back to Dijkstra every time", path, exc)
        return None


class SDNControllerApp(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.datapaths: Dict[int, object] = {}
        self.network_state = NetworkState()
        self.host_locations = HostLocationTracker()
        self.flow_registry = ActiveFlowRegistry()
        self._pending_probes: Dict[int, Tuple[int, int]] = {}

        self.routing_mode = CONF.routing_mode
        agent = _load_agent(CONF.rl_model_path) if self.routing_mode == "rl" else None
        self.decision_engine = RoutingDecisionEngine(
            self.network_state, self.host_locations, agent=agent, mode=self.routing_mode
        )
        logger.info("routing mode: %s", self.routing_mode)

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

    # --- Failure detection & rerouting (FR3, NFR1, NFR3) --------------------

    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status_handler(self, ev):
        msg = ev.msg
        ofproto = msg.datapath.ofproto
        dpid = msg.datapath.id
        port_no = msg.desc.port_no
        is_down = bool(msg.desc.state & ofproto.OFPPS_LINK_DOWN) or bool(msg.desc.config & ofproto.OFPPC_PORT_DOWN)

        neighbour = self.network_state.neighbour_dpid(dpid, port_no)
        if neighbour is None:
            return  # host-facing port - not a link we route traffic over

        self.network_state.set_link_status(dpid, port_no, up=not is_down)
        if is_down:
            logger.warning("link dpid=%s port=%s (-> dpid=%s) went down - rerouting affected flows", dpid, port_no, neighbour)
            self._reroute_affected_flows(dpid, neighbour)

    def _reroute_affected_flows(self, dpid_a: int, dpid_b: int) -> None:
        for record in self.flow_registry.flows_using_edge(dpid_a, dpid_b):
            remove_flows_for_hosts(self.datapaths, record.path, record.src_mac, record.dst_mac)
            self.flow_registry.remove(record.src_mac, record.dst_mac)

            decision = self.decision_engine.decide(record.src_mac, record.dst_mac)
            if decision is None:
                logger.warning("no alternate path for %s <-> %s after link failure", record.src_mac, record.dst_mac)
                continue

            self._install_route(decision, record.src_mac, record.dst_mac)
            logger.info(
                "rerouted %s <-> %s onto %s (mode=%s)%s",
                record.src_mac,
                record.dst_mac,
                decision.path,
                decision.mode_used,
                f" [fallback: {decision.fallback_reason}]" if decision.fallback_reason else "",
            )

    # --- Stats polling (FR1) -----------------------------------------------

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        stats_poller.handle_port_stats_reply(ev, self.network_state)

    @set_ev_cls(ofp_event.EventOFPPortDescStatsReply, MAIN_DISPATCHER)
    def _port_desc_reply_handler(self, ev):
        dpid = ev.msg.datapath.id
        ofproto = ev.msg.datapath.ofproto
        for port in ev.msg.body:
            if port.port_no > ofproto.OFPP_MAX:
                continue  # skip OFPP_LOCAL etc - not a real data-plane port
            self.network_state.record_switch_port(dpid, port.port_no)
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
        """FR3: real unicast host traffic gets an RL/Dijkstra-routed flow
        installed (routing/decision_engine.py); everything else (broadcast/
        multicast - ARP, etc. - or a destination we haven't located yet)
        still uses Phase 1's loop-safe flood (NetworkState.flood_ports)."""
        msg = ev.msg
        datapath = msg.datapath
        ofproto, parser = datapath.ofproto, datapath.ofproto_parser
        dpid = datapath.id
        in_port = msg.match["in_port"]

        if self.network_state.neighbour_dpid(dpid, in_port) is None:
            self.host_locations.record(eth.src, dpid, in_port)

        is_multicast = int(eth.dst.split(":")[0], 16) & 1 == 1  # covers ff:ff:...:ff broadcast too
        if not is_multicast and self._route_unicast(datapath, in_port, eth.src, eth.dst, msg):
            return

        flood_ports = self.network_state.flood_ports(dpid, in_port)
        actions = [parser.OFPActionOutput(p) for p in sorted(flood_ports)]
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        datapath.send_msg(
            parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data)
        )

    def _route_unicast(self, datapath, in_port: int, src_mac: str, dst_mac: str, msg) -> bool:
        """Returns True if this packet was handled (flow installed and/or
        packet-out sent along the route), False if the caller should fall
        back to flooding (destination location not known yet)."""
        record = self.flow_registry.get(src_mac, dst_mac)
        if record is None:
            decision = self.decision_engine.decide(src_mac, dst_mac)
            if decision is None:
                return False  # don't know dst's location yet - flood so ARP can teach us
            if not self._install_route(decision, src_mac, dst_mac):
                return False
            if decision.fallback_reason:
                logger.warning("RL fallback to Dijkstra for %s <-> %s: %s", src_mac, dst_mac, decision.fallback_reason)
            record = self.flow_registry.get(src_mac, dst_mac)

        out_port = self._next_hop_out_port(record, datapath.id, src_mac, dst_mac)
        if out_port is None:
            return False
        parser = datapath.ofproto_parser
        ofproto = datapath.ofproto
        actions = [parser.OFPActionOutput(out_port)]
        data = msg.data if msg.buffer_id == ofproto.OFP_NO_BUFFER else None
        datapath.send_msg(
            parser.OFPPacketOut(datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port, actions=actions, data=data)
        )
        return True

    def _install_route(self, decision, src_mac: str, dst_mac: str) -> bool:
        src_location = self.host_locations.get(src_mac)
        dst_location = self.host_locations.get(dst_mac)
        if src_location is None or dst_location is None:
            return False
        installations = build_bidirectional_flows(
            decision.path, self.network_state.port_towards, src_mac, dst_mac, src_location[1], dst_location[1]
        )
        if installations is None:
            logger.warning("could not resolve ports for path %s (%s <-> %s) - stale topology view?", decision.path, src_mac, dst_mac)
            return False
        install_flows(self.datapaths, installations)
        self.flow_registry.record(FlowRecord(src_mac=src_mac, dst_mac=dst_mac, path=decision.path, mode=decision.mode_used))
        return True

    def _next_hop_out_port(self, record: FlowRecord, dpid: int, src_mac: str, dst_mac: str):
        path = record.path if record.src_mac == src_mac else list(reversed(record.path))
        if dpid not in path:
            return None
        idx = path.index(dpid)
        if idx < len(path) - 1:
            return self.network_state.port_towards(dpid, path[idx + 1])
        dst_location = self.host_locations.get(dst_mac)
        return dst_location[1] if dst_location else None

    # --- Background loops --------------------------------------------------

    def _poll_loop(self):
        while True:
            for datapath in list(self.datapaths.values()):
                stats_poller.request_port_stats(datapath)
                latency_probe.send_echo_request(datapath)
            hub.sleep(stats_poller.POLL_INTERVAL_SEC)

    def _probe_loop(self):
        while True:
            for u, v, data in list(self.network_state.graph.edges(data=True)):
                ports = data.get("ports", {})
                # Probe from a single, deterministic direction per undirected
                # edge (the lower dpid) - delay is a property of the link,
                # not of which end we happen to measure from.
                src_dpid, dst_dpid = (u, v) if u < v else (v, u)
                src_port = ports.get(src_dpid)
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
        for dpid, port_no in list(self.network_state.port_samples.keys()):
            metrics = self.network_state.live_link_metrics(dpid, port_no, DEFAULT_LINK_BW_MBPS)
            if metrics is None:
                continue
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
