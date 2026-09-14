import pytest

from controller.injection_validation import (
    ValidationError,
    build_clear_netem_command,
    build_netem_command,
    iface_name,
    parse_congestion_request,
    parse_link_target,
    parse_netem_qdisc,
)


def test_iface_name_matches_mininet_naming_convention():
    assert iface_name(3, 2) == "s3-eth2"


def test_parse_link_target_accepts_valid_payload():
    assert parse_link_target({"dpid": 3, "port": 2}) == (3, 2)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"dpid": 3},
        {"dpid": -1, "port": 2},
        {"dpid": 3, "port": 0},
        {"dpid": "3", "port": 2},
        {"dpid": True, "port": 2},  # bool is an int subclass - must be rejected
        "not a dict",
        None,
    ],
)
def test_parse_link_target_rejects_invalid_payload(payload):
    with pytest.raises(ValidationError):
        parse_link_target(payload)


def test_parse_congestion_request_defaults():
    dpid, port, delay_ms, loss_pct, bw_kbit = parse_congestion_request({"dpid": 1, "port": 1})
    assert (dpid, port, delay_ms, loss_pct, bw_kbit) == (1, 1, 0.0, 0.0, None)


def test_parse_congestion_request_full():
    result = parse_congestion_request(
        {"dpid": 1, "port": 1, "delay_ms": 50.0, "loss_pct": 5.0, "bw_kbit": 1000.0}
    )
    assert result == (1, 1, 50.0, 5.0, 1000.0)


@pytest.mark.parametrize(
    "payload",
    [
        {"dpid": 1, "port": 1, "delay_ms": -5.0},
        {"dpid": 1, "port": 1, "delay_ms": 99999.0},
        {"dpid": 1, "port": 1, "loss_pct": 150.0},
        {"dpid": 1, "port": 1, "bw_kbit": -10.0},
    ],
)
def test_parse_congestion_request_rejects_out_of_range_values(payload):
    with pytest.raises(ValidationError):
        parse_congestion_request(payload)


def test_build_netem_command_is_argv_list_not_shell_string():
    cmd = build_netem_command("s3-eth2", 50.0, 5.0)
    assert cmd == ["tc", "qdisc", "change", "dev", "s3-eth2", "root", "netem", "delay", "50.0ms", "loss", "5.0%"]


def test_build_netem_command_includes_rate_when_given():
    cmd = build_netem_command("s3-eth2", 50.0, 5.0, bw_kbit=1000.0)
    assert cmd[-2:] == ["rate", "1000.0kbit"]


def test_build_netem_command_rejects_shell_metacharacters_in_iface():
    """Defence in depth: even though iface names are derived from validated
    ints (never raw user strings), the command must stay an argv list so a
    stray metacharacter can never be interpreted by a shell."""
    cmd = build_netem_command("s3-eth2; rm -rf /", 50.0, 5.0)
    assert "; rm -rf /" not in " ".join(cmd[:4])  # sanity: it's one argv element, not concatenated shell text
    assert cmd[4] == "s3-eth2; rm -rf /"  # passed through as a single argv token, never shell-interpreted


TC_SHOW_TCLINK = """qdisc htb 5: root refcnt 2 r2q 10 default 0x1 direct_packets_stat 0 direct_qlen 1000
qdisc netem 10: parent 5:1 limit 1000 delay 5.0ms
"""

TC_SHOW_NETEM_ROOT = "qdisc netem 8001: root refcnt 2 limit 1000 delay 5.0ms\n"

TC_SHOW_NO_NETEM = """qdisc noqueue 0: root refcnt 2
"""


def test_parse_netem_qdisc_finds_child_of_htb():
    """Mininet's TCLink puts HTB at the root for bandwidth with netem as a
    child - targeting `root netem` edits the wrong qdisc and the command
    fails, which is what broke congestion injection."""
    assert parse_netem_qdisc(TC_SHOW_TCLINK) == ("10:", "5:1")


def test_parse_netem_qdisc_handles_netem_at_root():
    assert parse_netem_qdisc(TC_SHOW_NETEM_ROOT) == ("8001:", None)


def test_parse_netem_qdisc_none_when_absent():
    assert parse_netem_qdisc(TC_SHOW_NO_NETEM) is None
    assert parse_netem_qdisc("") is None


def test_build_netem_command_targets_parent_and_handle():
    handle, parent = parse_netem_qdisc(TC_SHOW_TCLINK)
    cmd = build_netem_command("s1-eth5", 80.0, 4.0, handle=handle, parent=parent)
    assert cmd == [
        "tc", "qdisc", "change", "dev", "s1-eth5",
        "parent", "5:1", "handle", "10:",
        "netem", "delay", "80.0ms", "loss", "4.0%",
    ]


def test_build_netem_command_falls_back_to_root_without_handle():
    cmd = build_netem_command("s1-eth5", 80.0, 4.0)
    assert cmd[:6] == ["tc", "qdisc", "change", "dev", "s1-eth5", "root"]
    assert "handle" not in cmd


def test_build_clear_netem_command_targets_the_same_qdisc():
    handle, parent = parse_netem_qdisc(TC_SHOW_TCLINK)
    cmd = build_clear_netem_command("s1-eth5", handle=handle, parent=parent)
    assert "parent" in cmd and "5:1" in cmd and "10:" in cmd
    assert "0.0ms" in cmd and "0.0%" in cmd


def test_build_clear_netem_command_zeroes_delay_and_loss():
    cmd = build_clear_netem_command("s3-eth2")
    assert "0.0ms" in cmd
    assert "0.0%" in cmd
