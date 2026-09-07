#!/usr/bin/env python3
"""Thin CLI wrapper around the Ryu injection REST API (controller/injection_api.py).

Examples:
    python -m scripts.inject_cli failure --dpid 3 --port 2
    python -m scripts.inject_cli recover --dpid 3 --port 2
    python -m scripts.inject_cli congestion --dpid 3 --port 2 --delay-ms 80 --loss-pct 5
"""

import argparse
import sys

import requests


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1", help="Ryu controller REST host")
    parser.add_argument("--port", type=int, default=8080, help="Ryu controller REST/WSGI port")
    sub = parser.add_subparsers(dest="action", required=True)

    for name in ("failure", "recover"):
        p = sub.add_parser(name)
        p.add_argument("--dpid", type=int, required=True)
        p.add_argument("--port", type=int, required=True, dest="target_port")

    p = sub.add_parser("congestion")
    p.add_argument("--dpid", type=int, required=True)
    p.add_argument("--port", type=int, required=True, dest="target_port")
    p.add_argument("--delay-ms", type=float, default=0.0)
    p.add_argument("--loss-pct", type=float, default=0.0)
    p.add_argument("--bw-kbit", type=float, default=None)

    return parser.parse_args()


def main():
    args = parse_args()
    base_url = f"http://{args.host}:{args.port}"

    if args.action in ("failure", "recover"):
        body = {"dpid": args.dpid, "port": args.target_port}
        url = f"{base_url}/inject/{args.action}"
    else:
        body = {"dpid": args.dpid, "port": args.target_port, "delay_ms": args.delay_ms, "loss_pct": args.loss_pct}
        if args.bw_kbit is not None:
            body["bw_kbit"] = args.bw_kbit
        url = f"{base_url}/inject/congestion"

    response = requests.post(url, json=body, timeout=5)
    print(response.status_code, response.text)
    if response.status_code >= 400:
        sys.exit(1)


if __name__ == "__main__":
    main()
