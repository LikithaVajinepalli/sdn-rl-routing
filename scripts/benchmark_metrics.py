"""Pure parsing/arithmetic for scripts/benchmark.py.

Split out so the measurement logic is unit-testable without Mininet: the
benchmark itself can only run as root inside WSL2, but "did we parse this
iperf output correctly" and "how long was the outage" are just functions.
"""

import re
from typing import Dict, List, Optional

# "20 packets transmitted, 19 received, 5% packet loss, time 19029ms"
_PING_SUMMARY = re.compile(
    r"(\d+) packets transmitted,\s*(\d+) received.*?([\d.]+)% packet loss", re.DOTALL
)
# "rtt min/avg/max/mdev = 20.359/21.143/22.682/0.518 ms"
_PING_RTT = re.compile(r"rtt min/avg/max/mdev = ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)")
# iperf client summary, e.g. "  0.0-10.0 sec  8.62 MBytes  7.23 Mbits/sec"
_IPERF_BW = re.compile(r"([\d.]+)\s*([KMG])bits/sec")


def parse_ping(output: str) -> Dict[str, Optional[float]]:
    """Extracts loss % and RTT stats from `ping` output."""
    result: Dict[str, Optional[float]] = {
        "transmitted": None,
        "received": None,
        "packet_loss_pct": None,
        "avg_latency_ms": None,
        "max_latency_ms": None,
    }

    summary = _PING_SUMMARY.search(output)
    if summary:
        result["transmitted"] = float(summary.group(1))
        result["received"] = float(summary.group(2))
        result["packet_loss_pct"] = float(summary.group(3))

    rtt = _PING_RTT.search(output)
    if rtt:
        result["avg_latency_ms"] = float(rtt.group(2))
        result["max_latency_ms"] = float(rtt.group(3))
    return result


def parse_iperf_mbps(output: str) -> Optional[float]:
    """Last bandwidth figure in iperf output, normalised to Mbit/s. The last
    one is the client's overall summary line rather than an interval."""
    matches = _IPERF_BW.findall(output)
    if not matches:
        return None
    value, unit = matches[-1]
    scale = {"K": 0.001, "M": 1.0, "G": 1000.0}[unit]
    return round(float(value) * scale, 3)


def ping_sequence_numbers(output: str) -> List[int]:
    return [int(seq) for seq in re.findall(r"icmp_seq=(\d+)", output)]


def recovery_time_from_ping(output: str, interval_s: float) -> Optional[float]:
    """Longest run of consecutive missing icmp_seq numbers x the ping
    interval - i.e. how long traffic was actually down before rerouting
    restored it (NFR1's 2-second bound is measured against this).

    Returns 0.0 when nothing was lost, and None when the output had no
    sequence numbers at all to reason about."""
    seqs = ping_sequence_numbers(output)
    if not seqs:
        return None

    received = set(seqs)
    longest_gap = 0
    current_gap = 0
    for seq in range(min(seqs), max(seqs) + 1):
        if seq in received:
            current_gap = 0
        else:
            current_gap += 1
            longest_gap = max(longest_gap, current_gap)
    return round(longest_gap * interval_s, 3)


def summarise_mode(ping_result: Dict, throughput_mbps: Optional[float], recovery_s: Optional[float]) -> Dict:
    return {
        "avg_latency_ms": ping_result.get("avg_latency_ms"),
        "max_latency_ms": ping_result.get("max_latency_ms"),
        "packet_loss_pct": ping_result.get("packet_loss_pct"),
        "throughput_mbps": throughput_mbps,
        "recovery_time_s": recovery_s,
    }
