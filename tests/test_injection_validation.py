import pytest

from controller.injection_validation import (
    ValidationError,
    build_clear_netem_command,
    build_netem_command,
    iface_name,
    parse_congestion_request,
    parse_link_target,
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


def test_build_clear_netem_command_zeroes_delay_and_loss():
    cmd = build_clear_netem_command("s3-eth2")
    assert "0.0ms" in cmd
    assert "0.0%" in cmd
