from scripts.benchmark_metrics import (
    parse_iperf_mbps,
    parse_ping,
    ping_sequence_numbers,
    recovery_time_from_ping,
    summarise_mode,
)

PING_OUTPUT = """PING 10.0.0.9 (10.0.0.9) 56(84) bytes of data.
64 bytes from 10.0.0.9: icmp_seq=1 ttl=64 time=20.6 ms
64 bytes from 10.0.0.9: icmp_seq=2 ttl=64 time=21.5 ms

--- 10.0.0.9 ping statistics ---
20 packets transmitted, 19 received, 5% packet loss, time 19029ms
rtt min/avg/max/mdev = 20.359/21.143/22.682/0.518 ms
"""


def test_parse_ping_extracts_loss_and_rtt():
    result = parse_ping(PING_OUTPUT)
    assert result["transmitted"] == 20
    assert result["received"] == 19
    assert result["packet_loss_pct"] == 5.0
    assert result["avg_latency_ms"] == 21.143
    assert result["max_latency_ms"] == 22.682


def test_parse_ping_handles_garbage():
    result = parse_ping("connect: Network is unreachable")
    assert result["packet_loss_pct"] is None
    assert result["avg_latency_ms"] is None


def test_parse_iperf_mbps_uses_final_summary():
    output = """------------------------------------------------------------
[  3] local 10.0.0.1 port 5001 connected with 10.0.0.9 port 40000
[ ID] Interval       Transfer     Bandwidth
[  3]  0.0- 5.0 sec  4.00 MBytes  6.71 Mbits/sec
[  3]  0.0-10.0 sec  8.62 MBytes  7.23 Mbits/sec
"""
    assert parse_iperf_mbps(output) == 7.23


def test_parse_iperf_normalises_units():
    assert parse_iperf_mbps("[  3]  0.0-10.0 sec  1.00 GBytes  1.05 Gbits/sec") == 1050.0
    assert parse_iperf_mbps("[  3]  0.0-10.0 sec  120 KBytes  850 Kbits/sec") == 0.85


def test_parse_iperf_none_when_absent():
    assert parse_iperf_mbps("iperf: command not found") is None


def test_ping_sequence_numbers():
    assert ping_sequence_numbers(PING_OUTPUT) == [1, 2]


def test_recovery_time_zero_when_nothing_lost():
    output = "icmp_seq=1\nicmp_seq=2\nicmp_seq=3\n"
    assert recovery_time_from_ping(output, interval_s=0.2) == 0.0


def test_recovery_time_measures_longest_gap():
    # 1,2 ... then 6,7 - sequences 3,4,5 are missing = 3 intervals down.
    output = "icmp_seq=1\nicmp_seq=2\nicmp_seq=6\nicmp_seq=7\n"
    assert recovery_time_from_ping(output, interval_s=0.2) == 0.6


def test_recovery_time_ignores_shorter_gaps():
    output = "icmp_seq=1\nicmp_seq=3\nicmp_seq=8\nicmp_seq=9\n"  # gaps of 1 then 4
    assert recovery_time_from_ping(output, interval_s=0.5) == 2.0


def test_recovery_time_none_without_sequences():
    assert recovery_time_from_ping("no pings here", interval_s=0.2) is None


def test_summarise_mode_shape():
    summary = summarise_mode(parse_ping(PING_OUTPUT), throughput_mbps=7.23, recovery_s=0.6)
    assert summary == {
        "avg_latency_ms": 21.143,
        "max_latency_ms": 22.682,
        "packet_loss_pct": 5.0,
        "throughput_mbps": 7.23,
        "recovery_time_s": 0.6,
    }
