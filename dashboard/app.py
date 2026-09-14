"""Flask + Flask-SocketIO dashboard (FR4).

Runs INSIDE the Ryu controller process (see dashboard/server.py) so it reads
NetworkState directly rather than polling a REST API - zero lag, one copy of
the state. Everything Ryu-specific stays in server.py; this module only
needs the plain state objects, so it can be exercised without a controller.

Auth is deliberately absent here - it lands in Phase 5 along with the
injection API's, so both get the same treatment in one pass.
"""

import logging
import os
from typing import Callable, Optional

from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO

from dashboard.state_feed import build_snapshot, load_benchmark_results, load_training_curve

logger = logging.getLogger("sdn_rl_routing.dashboard")

DEFAULT_PORT = 8081
EMIT_INTERVAL_SEC = 1.5  # matches the controller's stats poll cadence
TRAINING_LOG_PATH = "models/training_log.csv"
BENCHMARK_PATH = "models/benchmark_results.json"


def _async_mode() -> Optional[str]:
    """'eventlet' when it's available - which it always is inside the Ryu
    controller, whose hub this server shares. Falling back to SocketIO's own
    auto-selection lets the app be imported and exercised off-controller
    (tests, a quick curl against /api/snapshot) on machines without it."""
    try:
        import eventlet  # noqa: F401

        return "eventlet"
    except ImportError:
        logger.warning("eventlet not available - dashboard falling back to SocketIO's default async mode")
        return None


def create_app(
    network_state,
    decision_log,
    flow_registry,
    routing_mode_getter: Callable[[], str],
    routing_mode_setter: Optional[Callable[[str], None]] = None,
    flush_routes: Optional[Callable[[], int]] = None,
    default_bw_mbps: float = 10.0,
    training_log_path: str = TRAINING_LOG_PATH,
    benchmark_path: str = BENCHMARK_PATH,
):
    """Returns (app, socketio, emit_loop). `routing_mode_getter` is a
    callable rather than a string so the dashboard reflects a mode switched
    at runtime. The setter is exposed through this app (rather than the
    page calling Ryu's REST API on another port) to keep the toggle
    same-origin - no CORS, no second base URL in the frontend."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("DASHBOARD_SECRET", "sdn-rl-routing-dev")
    # Flask caches compiled templates for the process lifetime and tells
    # browsers to cache static files for 12 hours. Both bite hard here: the
    # controller is long-running, so an edited page silently kept serving the
    # old markup while the freshly-read JS confusingly did load - which is
    # exactly how a dead CDN reference survived a restart. Neither cache is
    # worth anything for a locally-served dashboard.
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.jinja_env.auto_reload = True
    socketio = SocketIO(
        app,
        async_mode=_async_mode(),
        cors_allowed_origins="*",
        logger=False,
        engineio_logger=False,
    )

    def snapshot():
        return build_snapshot(
            network_state, decision_log, flow_registry, routing_mode_getter(), default_bw_mbps
        )

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/snapshot")
    def api_snapshot():
        """Same payload the socket pushes - handy for debugging without a
        browser (curl localhost:8081/api/snapshot)."""
        return jsonify(snapshot())

    @app.route("/api/training-curve")
    def api_training_curve():
        return jsonify({"points": load_training_curve(training_log_path), "source": training_log_path})

    @app.route("/api/benchmark")
    def api_benchmark():
        results = load_benchmark_results(benchmark_path)
        return jsonify({"results": results, "source": benchmark_path, "available": results is not None})

    @app.route("/api/mode/<mode>", methods=["POST"])
    def api_set_mode(mode):
        if routing_mode_setter is None:
            return jsonify({"error": "routing mode is read-only here"}), 400
        if mode not in ("rl", "dijkstra"):
            return jsonify({"error": "mode must be 'rl' or 'dijkstra'"}), 400
        previous = routing_mode_getter()
        routing_mode_setter(mode)
        flushed = flush_routes() if flush_routes else 0
        logger.info("dashboard switched routing mode %s -> %s (flushed %d routes)", previous, mode, flushed)
        socketio.emit("snapshot", snapshot())
        return jsonify({"mode": mode, "previous_mode": previous, "flushed_routes": flushed})

    @socketio.on("connect")
    def on_connect():
        socketio.emit("snapshot", snapshot())

    def emit_loop(sleep: Optional[Callable[[float], None]] = None):
        """Pushes a fresh snapshot to every connected browser. `sleep` is
        injectable so the Ryu integration can use its own eventlet hub's
        sleep rather than importing one here."""
        sleeper = sleep or socketio.sleep
        while True:
            sleeper(EMIT_INTERVAL_SEC)
            try:
                socketio.emit("snapshot", snapshot())
            except Exception as exc:  # a broken client must never kill the loop
                logger.warning("dashboard snapshot emit failed: %s", exc)

    return app, socketio, emit_loop
