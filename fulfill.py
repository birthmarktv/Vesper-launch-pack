#!/usr/bin/env python3
"""Fulfillment helper for the launch-pack seller.

Usage:
  fulfill.py list                 show paid-but-unfulfilled orders
  fulfill.py complete <order_id>  mark fulfilled after pack files are in packs/<id>/
"""
import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORDERS_FILE = os.path.join(BASE_DIR, "orders.json")
PACKS_DIR = os.path.join(BASE_DIR, "packs")
REQUIRED = ["captions.md", "schedule.md",
            "piece1.png", "piece2.png", "piece3.png", "piece4.png"]


def load():
    if not os.path.exists(ORDERS_FILE):
        return {}
    with open(ORDERS_FILE) as f:
        return json.load(f)


def save(orders):
    tmp = ORDERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(orders, f, indent=1)
    os.replace(tmp, ORDERS_FILE)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    orders = load()
    if cmd == "list":
        pending = [o for o in orders.values() if o.get("status") == "paid"]
        print(f"{len(pending)} order(s) awaiting fulfillment")
        for o in pending:
            print(f"- {o['order_id']} | {o.get('muse_name')} | vibe={o.get('vibe','')[:60]}"
                  f" | paid={o.get('paid_at')} | tx={o.get('settle_tx','')[:20]}")
    elif cmd == "complete":
        oid = sys.argv[2]
        o = orders.get(oid)
        if not o:
            sys.exit("unknown order")
        missing = [f for f in REQUIRED
                   if not os.path.exists(os.path.join(PACKS_DIR, oid, f))]
        if missing:
            sys.exit(f"pack incomplete, missing: {missing}")
        o["status"] = "fulfilled"
        o["fulfilled_at"] = datetime.now(timezone.utc).isoformat()
        save(orders)
        print(f"order {oid} marked fulfilled")
    else:
        sys.exit("unknown command")


if __name__ == "__main__":
    main()
