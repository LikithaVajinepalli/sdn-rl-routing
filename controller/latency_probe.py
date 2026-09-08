"""Hybrid delay measurement (see CLAUDE.md Phase 1 decision):

1. Periodic OFPEchoRequest/Reply per switch -> controller<->switch control
   channel RTT. Used both as a link-trust signal and to net out control-plane
   overhead from (2).
2. A custom-ethertype probe packet sent controller -> switch A -> (link) ->
   switch B -> controller. The elapsed time, minus the two echo-derived
   control-channel latencies from (1), estimates the link's one-way delay -
   which should track the tc-netem `delay_ms` configured in
   topology/config.py's LinkProfile (the injectable ground truth used to
   sanity-check this estimate in tests).

Both signals feed controller/metrics.py's link_trust_level (jitter + control
channel health) and the `delay_ms` state feature.
"""

import struct
import time
from typing import Optional, Tuple

from ryu.lib.packet import ethernet, packet
from ryu.ofproto import ofproto_v1_3

from controller.network_state import LinkKey, NetworkState

PROBE_ETH_TYPE = 0x8999
_PROBE_STRUCT = struct.Struct("!Id")  # probe_id (uint32), send_timestamp (double)
_ETH_HEADER_LEN = 14  # dst(6) + src(6) + ethertype(2), no 802.1Q tag
_next_probe_id = 0


def send_echo_request(datapath) -> None:
    parser = datapath.ofproto_parser
    payload = struct.pack("!d", time.time())
    datapath.send_msg(parser.OFPEchoRequest(datapath, data=payload))


def handle_echo_reply(ev, network_state: NetworkState) -> None:
    dpid = ev.msg.datapath.id
    try:
        (sent_time,) = struct.unpack("!d", ev.msg.data)
    except struct.error:
        return  # not one of ours (unexpected payload size) - ignore
    rtt_ms = max(0.0, (time.time() - sent_time) * 1000.0)
    network_state.record_echo_rtt(dpid, rtt_ms)


def _next_id() -> int:
    global _next_probe_id
    _next_probe_id = (_next_probe_id + 1) % (2**32)
    return _next_probe_id


def build_probe_packet(src_mac: str, dst_mac: str) -> Tuple[bytes, int]:
    probe_id = _next_id()
    payload = _PROBE_STRUCT.pack(probe_id, time.time())
    pkt = packet.Packet()
    pkt.add_protocol(ethernet.ethernet(ethertype=PROBE_ETH_TYPE, src=src_mac, dst=dst_mac))
    pkt.serialize()
    return pkt.data + payload, probe_id


def send_delay_probe(datapath, out_port: int, src_mac: str, dst_mac: str) -> Tuple[int, float]:
    ofproto = datapath.ofproto
    parser = datapath.ofproto_parser
    data, probe_id = build_probe_packet(src_mac, dst_mac)
    sent_time = time.time()
    actions = [parser.OFPActionOutput(out_port)]
    out = parser.OFPPacketOut(
        datapath=datapath,
        buffer_id=ofproto.OFP_NO_BUFFER,
        in_port=ofproto.OFPP_CONTROLLER,
        actions=actions,
        data=data,
    )
    datapath.send_msg(out)
    return probe_id, sent_time


def is_probe_packet(pkt: packet.Packet) -> bool:
    eth = pkt.get_protocol(ethernet.ethernet)
    return eth is not None and eth.ethertype == PROBE_ETH_TYPE


def parse_probe_payload(raw_data: bytes) -> Optional[Tuple[int, float]]:
    """raw_data is the full Ethernet frame bytes from a packet-in. The probe
    payload sits at a fixed offset right after the Ethernet header (not at
    the end of the frame - a real NIC/OVS may zero-pad short frames up to the
    60-byte minimum, which would corrupt a "read from the tail" parse)."""
    start = _ETH_HEADER_LEN
    end = start + _PROBE_STRUCT.size
    if len(raw_data) < end:
        return None
    try:
        return _PROBE_STRUCT.unpack(raw_data[start:end])
    except struct.error:
        return None


def estimate_link_delay_ms(
    controller_send_time: float,
    controller_recv_time: float,
    src_echo_rtt_ms: float,
    dst_echo_rtt_ms: float,
) -> float:
    """Nets out estimated control-channel overhead from the total probe
    round trip, isolating the link's data-plane one-way delay."""
    elapsed_ms = (controller_recv_time - controller_send_time) * 1000.0
    overhead_ms = (src_echo_rtt_ms / 2.0) + (dst_echo_rtt_ms / 2.0)
    return max(0.0, elapsed_ms - overhead_ms)
