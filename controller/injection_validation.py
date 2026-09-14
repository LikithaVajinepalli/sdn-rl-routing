"""Pure validation and command-building logic for the injection REST API
(controller/injection_api.py). No Ryu/webob imports, so this is testable on
any platform without the Linux-only OpenFlow stack installed.
"""

from typing import List, Optional, Tuple

MAX_DELAY_MS = 2000.0
MAX_LOSS_PCT = 100.0
MAX_BW_KBIT = 1_000_000.0


class ValidationError(ValueError):
    pass


def iface_name(dpid: int, port_no: int) -> str:
    """Mininet veth interface naming assumption - see injection_api.py docstring."""
    return f"s{dpid}-eth{port_no}"


def require_positive_int(payload: dict, key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError(f"'{key}' must be a positive integer")
    return value


def require_range(payload: dict, key: str, lo: float, hi: float, default: Optional[float] = None) -> float:
    if key not in payload:
        if default is not None:
            return default
        raise ValidationError(f"'{key}' is required")
    value = payload.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"'{key}' must be a number")
    if not (lo <= value <= hi):
        raise ValidationError(f"'{key}' must be between {lo} and {hi}")
    return float(value)


def parse_link_target(payload: dict) -> Tuple[int, int]:
    """Validates and returns (dpid, port_no) from a JSON request body. Never
    trust raw values into a shell command or OpenFlow message without this."""
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    dpid = require_positive_int(payload, "dpid")
    port = require_positive_int(payload, "port")
    return dpid, port


def parse_congestion_request(payload: dict) -> Tuple[int, int, float, float, Optional[float]]:
    dpid, port = parse_link_target(payload)
    delay_ms = require_range(payload, "delay_ms", 0, MAX_DELAY_MS, default=0.0)
    loss_pct = require_range(payload, "loss_pct", 0, MAX_LOSS_PCT, default=0.0)
    bw_kbit = None
    if payload.get("bw_kbit") is not None:
        bw_kbit = require_range(payload, "bw_kbit", 1, MAX_BW_KBIT)
    return dpid, port, delay_ms, loss_pct, bw_kbit


def parse_netem_qdisc(tc_show_output: str) -> Optional[Tuple[str, Optional[str]]]:
    """Finds the netem qdisc's (handle, parent) in `tc qdisc show dev X`.

    Mininet's TCLink builds a hierarchy - HTB at the root to enforce
    bandwidth, with netem hanging off it for delay/loss - so a naive
    `tc qdisc change dev X root netem ...` targets the wrong qdisc and
    fails. Returns None when the interface has no netem qdisc at all.

    Example line:  qdisc netem 10: parent 5:1 limit 1000 delay 5.0ms
    """
    for line in tc_show_output.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] != "qdisc" or parts[1] != "netem":
            continue
        handle = parts[2]
        if "parent" in parts:
            return handle, parts[parts.index("parent") + 1]
        if "root" in parts:
            return handle, None
        return handle, None
    return None


def build_netem_command(
    iface: str,
    delay_ms: float,
    loss_pct: float,
    bw_kbit: Optional[float] = None,
    handle: Optional[str] = None,
    parent: Optional[str] = None,
) -> List[str]:
    """Builds an argv list (never a shell string) for `tc qdisc change`, so
    this is safe to pass to subprocess without shell=True.

    Pass handle/parent from parse_netem_qdisc() to target the netem qdisc
    where it actually sits; without them this falls back to assuming netem
    is the root qdisc, which is only true on links built without bandwidth
    limiting."""
    cmd = ["tc", "qdisc", "change", "dev", iface]
    if parent:
        cmd += ["parent", parent]
    else:
        cmd += ["root"]
    if handle:
        cmd += ["handle", handle]
    cmd += ["netem", "delay", f"{delay_ms}ms", "loss", f"{loss_pct}%"]
    if bw_kbit is not None:
        cmd += ["rate", f"{bw_kbit}kbit"]
    return cmd


def build_clear_netem_command(iface: str, handle: Optional[str] = None, parent: Optional[str] = None) -> List[str]:
    return build_netem_command(iface, delay_ms=0.0, loss_pct=0.0, handle=handle, parent=parent)
