"""REST API for synthetic congestion/failure injection (Phase 1 deliverable;
auth + stricter validation land in Phase 5 - see CLAUDE.md).

Failure injection uses a real OpenFlow OFPPortMod (clean, controller-native).
Congestion injection has no OpenFlow equivalent (OpenFlow doesn't control
link delay/loss), so it shells out to `tc netem` on the underlying Mininet
veth interface. This only works because, in this simulation, the Ryu
controller and Mininet run in the same Linux (WSL2) environment - in a real
deployment the controller would not have host-level access to switch
interfaces. That assumption is documented in /docs/security-notes.md
(Phase 5).

All request validation and command-building is pure logic that lives in
controller/injection_validation.py (no Ryu/webob import needed there) so it
can be unit tested without the Linux-only OpenFlow stack installed. This
module is just the thin Ryu/WSGI glue on top.
"""

import json
import subprocess
from typing import Dict

from ryu.app.wsgi import ControllerBase, Response, route
from webob import Request

from controller.injection_validation import (
    ValidationError,
    build_netem_command,
    iface_name,
    parse_congestion_request,
    parse_link_target,
)
from controller.network_state import LinkKey, NetworkState


def apply_congestion(iface: str, delay_ms: float, loss_pct: float, bw_kbit=None) -> None:
    subprocess.run(build_netem_command(iface, delay_ms, loss_pct, bw_kbit), check=True, timeout=5)


class InjectionController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.network_state: NetworkState = data["network_state"]
        self.datapaths: Dict[int, object] = data["datapaths"]

    @staticmethod
    def _json_response(status: int, body: dict) -> Response:
        return Response(status=status, content_type="application/json", body=json.dumps(body))

    @staticmethod
    def _read_json(req: Request) -> dict:
        try:
            return json.loads(req.body.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("request body must be valid JSON")

    def _set_port_down(self, datapath, port_no: int, down: bool) -> None:
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        port_mod = parser.OFPPortMod(
            datapath=datapath,
            port_no=port_no,
            hw_addr="00:00:00:00:00:00",
            config=ofproto.OFPPC_PORT_DOWN if down else 0,
            mask=ofproto.OFPPC_PORT_DOWN,
            advertise=0,
        )
        datapath.send_msg(port_mod)

    @route("injection", "/inject/failure", methods=["POST"])
    def inject_failure(self, req: Request, **_kwargs) -> Response:
        try:
            dpid, port = parse_link_target(self._read_json(req))
        except ValidationError as exc:
            return self._json_response(400, {"error": str(exc)})

        datapath = self.datapaths.get(dpid)
        if datapath is None:
            return self._json_response(404, {"error": f"unknown dpid {dpid}"})

        self._set_port_down(datapath, port, down=True)
        self.network_state.set_link_status(LinkKey(dpid, port, 0), up=False)
        return self._json_response(200, {"status": "failure injected", "dpid": dpid, "port": port})

    @route("injection", "/inject/recover", methods=["POST"])
    def inject_recover(self, req: Request, **_kwargs) -> Response:
        try:
            dpid, port = parse_link_target(self._read_json(req))
        except ValidationError as exc:
            return self._json_response(400, {"error": str(exc)})

        datapath = self.datapaths.get(dpid)
        if datapath is None:
            return self._json_response(404, {"error": f"unknown dpid {dpid}"})

        self._set_port_down(datapath, port, down=False)
        self.network_state.set_link_status(LinkKey(dpid, port, 0), up=True)
        return self._json_response(200, {"status": "link recovered", "dpid": dpid, "port": port})

    @route("injection", "/inject/congestion", methods=["POST"])
    def inject_congestion(self, req: Request, **_kwargs) -> Response:
        try:
            dpid, port, delay_ms, loss_pct, bw_kbit = parse_congestion_request(self._read_json(req))
        except ValidationError as exc:
            return self._json_response(400, {"error": str(exc)})

        iface = iface_name(dpid, port)
        try:
            apply_congestion(iface, delay_ms, loss_pct, bw_kbit)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
            return self._json_response(500, {"error": f"tc command failed: {exc}"})

        self.network_state.set_link_status(LinkKey(dpid, port, 0), congestion_injected=True)
        return self._json_response(
            200, {"status": "congestion injected", "iface": iface, "delay_ms": delay_ms, "loss_pct": loss_pct}
        )
