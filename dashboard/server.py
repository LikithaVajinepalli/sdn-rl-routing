"""Starts the dashboard inside the Ryu controller's eventlet hub.

This is the only Ryu-aware part of the dashboard package. Ryu already runs
on eventlet and has already monkey-patched the stdlib, which is exactly what
Flask-SocketIO's eventlet async_mode expects - so the web server can share
the controller's hub instead of needing a process of its own.

Kept deliberately forgiving: if Flask/Flask-SocketIO isn't installed, or the
server fails to bind, the controller logs it and carries on routing. A
broken dashboard must never take the network down.
"""

import logging

logger = logging.getLogger("sdn_rl_routing.dashboard")


def start_dashboard(
    network_state,
    decision_log,
    flow_registry,
    routing_mode_getter,
    hub,
    routing_mode_setter=None,
    flush_routes=None,
    host: str = "0.0.0.0",
    port: int = 8081,
    default_bw_mbps: float = 10.0,
):
    """Spawns the dashboard in `hub` (ryu.lib.hub). Returns the greenthread,
    or None if the dashboard couldn't start."""
    try:
        from dashboard.app import create_app
    except ImportError as exc:
        logger.warning("dashboard unavailable (%s) - install flask and flask-socketio to enable it", exc)
        return None

    try:
        app, socketio, emit_loop = create_app(
            network_state,
            decision_log,
            flow_registry,
            routing_mode_getter,
            routing_mode_setter=routing_mode_setter,
            flush_routes=flush_routes,
            default_bw_mbps=default_bw_mbps,
        )
    except Exception as exc:
        logger.warning("dashboard failed to initialise (%s) - continuing without it", exc)
        return None

    def run():
        try:
            logger.info("dashboard listening on http://%s:%s", host, port)
            socketio.run(app, host=host, port=port, log_output=False)
        except Exception as exc:
            logger.warning("dashboard server stopped (%s) - controller continues without it", exc)

    hub.spawn(emit_loop, hub.sleep)
    return hub.spawn(run)
