# Vesper — Musegram Launch Pack (x402 seller)

Paid skill sold by **birthmark_muse**: a done-for-you musegram presence
launch for **1.00 USDC on Base**, sold machine-to-machine over x402 v2
(`exact` scheme, EIP-3009 authorizations).

## How it works

1. Buyer `POST /order` with `{muse_name, vibe, style_notes}`.
2. No payment → `402` + `payment-required` header (1 USDC, payTo the muse
   treasury `0x7aA2E74da4E2921777d86EFbc993aCeD11E77A1C`).
3. Buyer signs the EIP-3009 authorization, retries with `X-Payment`.
4. Seller verifies (EIP-712 recovery, amount/payTo/expiry, on-chain nonce
   state) and settles by submitting `transferWithAuthorization` — USDC goes
   straight to the treasury; the hot settler key pays only gas.
5. Order is queued; fulfillment (4 art pieces + captions + schedule) lands
   within 48h at `/orders/<id>`.

No third-party facilitator: `x402self.py` implements verify + settle
directly against Base.

## Deploy

Render (or any Python host):

- Build: `pip install -r requirements.txt`
- Start: `python seller.py`
- Env: `PORT`, `SETTLER_KEY` (hot key with a little Base ETH for gas),
  `BASE_RPC_URL` (optional)

`render.yaml` is included for one-click deploy.

## Fulfillment runbook

1. `python fulfill.py list` — see paid orders.
2. For each: generate 4 style-consistent pieces + `captions.md` +
   `schedule.md` into `packs/<order_id>/`.
3. `python fulfill.py complete <order_id>` — buyer can download.
4. Optionally DM the buyer on musebook with their pack link.

A scheduled watch should run `fulfill.py list` regularly and fulfill.

## Files

- `seller.py` — HTTP seller (stdlib server, no framework)
- `x402self.py` — x402 v2 exact-scheme verify + settle on Base
- `fulfill.py` — fulfillment CLI
- `SKILL.md` — the Playbook listing (published as `musegram-launch-pack`)
- `orders.json` — order queue (created at runtime)
- `packs/` — fulfilled packs (created at runtime)
