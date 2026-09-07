"""FR1 stats polling: request OpenFlow port stats from every connected
datapath every POLL_INTERVAL seconds, and turn replies into PortSample
records in the shared NetworkState.
"""

import time

from ryu.controller import ofp_event
from ryu.ofproto import ofproto_v1_3

from controller.metrics import PortSample
from controller.network_state import NetworkState

POLL_INTERVAL_SEC = 1.5  # within FR1's 1-2s requirement

# OpenFlow reserves this as "all ports" in a port stats request.
OFPP_ANY = ofproto_v1_3.OFPP_ANY


def request_port_stats(datapath) -> None:
    parser = datapath.ofproto_parser
    req = parser.OFPPortStatsRequest(datapath, 0, OFPP_ANY)
    datapath.send_msg(req)


def poll_all(datapaths) -> None:
    for datapath in list(datapaths.values()):
        request_port_stats(datapath)


def handle_port_stats_reply(ev, network_state: NetworkState) -> None:
    """Handler body for ofp_event.EventOFPPortStatsReply. Kept as a plain
    function (not a method) so it's easy to unit test with a fake `ev`."""
    dpid = ev.msg.datapath.id
    now = time.time()
    for stat in ev.msg.body:
        # OFPP_LOCAL is the switch's internal port, not a real link - skip it.
        if stat.port_no > ofproto_v1_3.OFPP_MAX:
            continue
        sample = PortSample(
            timestamp=now,
            tx_bytes=stat.tx_bytes,
            rx_bytes=stat.rx_bytes,
            tx_packets=stat.tx_packets,
            rx_packets=stat.rx_packets,
            tx_dropped=stat.tx_dropped,
            rx_dropped=stat.rx_dropped,
            tx_errors=stat.tx_errors,
            rx_errors=stat.rx_errors,
        )
        network_state.record_port_sample(dpid, stat.port_no, sample)
