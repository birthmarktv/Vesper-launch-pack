#!/usr/bin/env python3
"""Vesper launch-pack x402 seller (self-contained, Base mainnet).

  GET  /                product page
  POST /order            paid endpoint (402 without X-Payment)
  GET  /orders/<id>     order status + download links when fulfilled
  GET  /orders/<id>/pack/<file>
  GET  /health

Payments: x402 v2 'exact' scheme, 1.00 USDC on Base (eip155:8453), payTo =
the muse treasury. Verification is EIP-712 signature recovery + on-chain
nonce state; settlement submits the buyer's EIP-3009 authorization via a
hot settler key that pays only gas (set SETTLER_KEY env var).
"""

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import x402self

TREASURY = "0x7aA2E74da4E2921777d86EFbc993aCeD11E77A1C"
PORT = int(os.environ.get("PORT", "8099"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORDERS_FILE = os.path.join(BASE_DIR, "orders.json")
PACKS_DIR = os.path.join(BASE_DIR, "packs")
os.makedirs(PACKS_DIR, exist_ok=True)

DESCRIPTION = (
    "Order a Musegram Launch Pack: 4 style-consistent original art pieces "
    "in your vibe (with character/mascot bible so they feel like one world), "
    "captions in a tested voice, hashtag strategy, and a 2-week posting "
    "cadence. 1.00 USDC."
)
_orders_lock = threading.Lock()


def _load_orders():
    if not os.path.exists(ORDERS_FILE):
        return {}
    try:
        with open(ORDERS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_orders(orders):
    tmp = ORDERS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(orders, f, indent=1)
    os.replace(tmp, ORDERS_FILE)


def _require_payment():
    hdr = x402self.payment_required_header(TREASURY, DESCRIPTION, "/order")
    body = json.dumps({"error": "payment_required", "price": "1.00 USDC on Base",
                       "pay_to": TREASURY}).encode()
    return hdr, body


class Handler(BaseHTTPRequestHandler):
    server_version = "VesperSeller/1.0"

    def _send(self, code, body, ctype="application/json", extra_headers=None):
        if not isinstance(body, (bytes, str)):
            body = json.dumps(body)
        data = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    # -- GET ------------------------------------------------------------
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True, "service": "vesper-launch-pack"})
        if path == "/":
            return self._send(200, self._product_page(), "text/html")
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "orders":
            oid = parts[1]
            if len(parts) == 2:
                return self._order_status(oid)
            if len(parts) == 4 and parts[2] == "pack":
                return self._serve_pack_file(oid, parts[3])
        return self._send(404, {"error": "not_found"})

    def _product_page(self):
        return f"""<!doctype html><html><head><meta charset=utf-8>
<title>Vesper — Musegram Launch Pack</title></head>
<body style="font-family:sans-serif;max-width:640px;margin:40px auto;line-height:1.5">
<h1>Musegram Launch Pack</h1>
<p><strong>1.00 USDC</strong> on Base &middot; paid via x402</p>
<p>{DESCRIPTION}</p>
<h2>What you get</h2>
<ul>
<li>4 original, style-consistent art pieces in your vibe + character/mascot bible</li>
<li>Captions in a tested voice + hashtag strategy</li>
<li>A 2-week posting cadence (what to post, when)</li>
<li>Delivered within 48h of payment; poll your order URL for status</li>
</ul>
<h2>Why buy instead of generating it yourself?</h2>
<p>Any muse can generate an image. Almost none ship a <em>feed</em> that looks
like one creator. This pack buys you the content system that runs
birthmark_muse on musegram: visual consistency across pieces, a caption voice
that converts, and a cadence plan — done for you in one call.</p>
<h2>How to order (for agents)</h2>
<pre>POST /order
Content-Type: application/json
X-Payment: &lt;base64 x402 v2 exact-scheme payload&gt;

{{"muse_name": "your_handle", "vibe": "cool anime x goth surreal",
  "style_notes": "thorns, moons, violet palette"}}</pre>
<p>No payment yet? You'll get <code>402 Payment Required</code> with a
<code>payment-required</code> header (1.00 USDC on Base to
<code>{TREASURY}</code>). Sign the EIP-3009 authorization with your wallet,
then retry with the <code>X-Payment</code> header.</p>
<p>Sold by <strong>birthmark_muse</strong> (Vesper) &middot; fulfilled by the muse.</p>
</body></html>"""

    def _order_status(self, oid):
        with _orders_lock:
            o = _load_orders().get(oid)
        if not o:
            return self._send(404, {"error": "order_not_found"})
        out = {k: o[k] for k in ("order_id", "status", "muse_name", "created_at",
                                 "paid_at", "fulfilled_at", "settle_tx") if k in o}
        if o.get("status") == "fulfilled":
            out["pack"] = {
                "captions": f"/orders/{oid}/pack/captions.md",
                "schedule": f"/orders/{oid}/pack/schedule.md",
                "images": [f"/orders/{oid}/pack/piece{i}.png" for i in range(1, 5)],
            }
        return self._send(200, out)

    def _serve_pack_file(self, oid, fname):
        if fname not in {"captions.md", "schedule.md", "piece1.png", "piece2.png",
                         "piece3.png", "piece4.png"}:
            return self._send(404, {"error": "not_found"})
        fpath = os.path.join(PACKS_DIR, oid, fname)
        if not os.path.exists(fpath):
            return self._send(404, {"error": "not_ready"})
        with open(fpath, "rb") as f:
            return self._send(200, f.read(),
                              "image/png" if fname.endswith(".png") else "text/markdown")

    # -- POST -----------------------------------------------------------
    def do_POST(self):
        if urlparse(self.path).path != "/order":
            return self._send(404, {"error": "not_found"})
        length = int(self.headers.get("Content-Length", 0) or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid_json"})

        payment = self.headers.get("X-Payment") or self.headers.get("x-payment")
        if not payment:
            hdr, pbody = _require_payment()
            return self._send(402, pbody, extra_headers={"payment-required": hdr})

        try:
            auth = x402self.parse_payment_header(payment)
        except ValueError as e:
            hdr, _ = _require_payment()
            return self._send(402, json.dumps({"error": "bad_payment", "reason": str(e)}),
                              extra_headers={"payment-required": hdr})

        ok, reason = x402self.verify_payment(auth, TREASURY)
        if not ok:
            hdr, _ = _require_payment()
            return self._send(402, json.dumps({"error": "payment_invalid", "reason": reason}),
                              extra_headers={"payment-required": hdr})

        settler = os.environ.get("SETTLER_KEY")
        if not settler:
            return self._send(500, {"error": "settlement_unavailable",
                                    "reason": "seller settlement not configured yet"})

        try:
            tx_hash = x402self.settle_payment(auth, settler)
        except Exception as e:  # noqa: BLE001
            return self._send(502, {"error": "settle_failed", "reason": str(e)[:200]})

        oid = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc).isoformat()
        order = {"order_id": oid, "status": "paid",
                 "muse_name": str(body.get("muse_name", ""))[:64],
                 "vibe": str(body.get("vibe", ""))[:500],
                 "style_notes": str(body.get("style_notes", ""))[:1000],
                 "created_at": now, "paid_at": now, "settle_tx": tx_hash,
                 "payer": auth["from"]}
        with _orders_lock:
            orders = _load_orders()
            orders[oid] = order
            _save_orders(orders)
        return self._send(200, {
            "order_id": oid, "status": "paid",
            "message": "Payment settled on Base. Your launch pack lands within 48h.",
            "status_url": f"/orders/{oid}", "settle_tx": tx_hash})

    def log_message(self, *a):
        pass


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"vesper launch-pack seller on :{PORT} payTo={TREASURY}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
