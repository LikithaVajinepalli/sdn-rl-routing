"""REST API for reading and switching the routing strategy at runtime.

Without this, comparing RL against the Dijkstra baseline would mean
restarting the controller between runs - which also wipes the flow registry
and host locations, making a clean back-to-back benchmark awkward. Switching
here flushes installed route flows too, so traffic after the switch is
genuinely routed by the newly-selected strategy rather than riding rules the
previous one installed (scripts/benchmark.py depends on that).

Auth lands in Phase 5, alongside the injection API's.
"""

import json
import logging
from typing import Callable, Dict

from ryu.app.wsgi import ControllerBase, Response, route
from webob import Request

from controller.injection_validation import ValidationError

logger = logging.getLogger("sdn_rl_routing")

VALID_MODES = ("rl", "dijkstra")


def parse_mode_request(payload: dict) -> str:
    if not isinstance(payload, dict):
        raise ValidationError("request body must be a JSON object")
    mode = payload.get("mode")
    if not isinstance(mode, str) or mode not in VALID_MODES:
        raise ValidationError(f"'mode' must be one of {list(VALID_MODES)}")
    return mode


class RoutingController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super().__init__(req, link, data, **config)
        self.get_mode: Callable[[], str] = data["get_mode"]
        self.set_mode: Callable[[str], None] = data["set_mode"]
        self.flush_routes: Callable[[], int] = data["flush_routes"]

    @staticmethod
    def _json_response(status: int, body: dict) -> Response:
        return Response(status=status, content_type="application/json", body=json.dumps(body))

    @route("routing", "/routing/mode", methods=["GET"])
    def get_routing_mode(self, _req: Request, **_kwargs) -> Response:
        return self._json_response(200, {"mode": self.get_mode()})

    @route("routing", "/routing/mode", methods=["POST"])
    def set_routing_mode(self, req: Request, **_kwargs) -> Response:
        try:
            payload = json.loads(req.body.decode("utf-8") or "{}")
            mode = parse_mode_request(payload)
        except (ValueError, UnicodeDecodeError):
            return self._json_response(400, {"error": "request body must be valid JSON"})
        except ValidationError as exc:
            return self._json_response(400, {"error": str(exc)})

        previous = self.get_mode()
        self.set_mode(mode)
        flushed = self.flush_routes()
        logger.info("routing mode changed %s -> %s (flushed %d installed routes)", previous, mode, flushed)
        return self._json_response(
            200, {"mode": mode, "previous_mode": previous, "flushed_routes": flushed}
        )
