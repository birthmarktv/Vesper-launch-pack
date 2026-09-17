"""Self-contained x402 v2 'exact' scheme seller logic for Base mainnet.

No third-party facilitator required:
  - verify: EIP-712 signature recovery + field checks + on-chain nonce state
  - settle:  submits the buyer's EIP-3009 transferWithAuthorization via a
             public Base RPC, signed by a hot settler key that pays only gas.
             USDC goes straight to the treasury (payTo), never touching us.
"""

import base64
import json
import os
import time

import httpx
from eth_abi import encode
from eth_account import Account
from eth_keys import keys
from eth_utils import keccak

USDC_BASE = "0x833589fCD6eDb6E08f4c7c32D4f71b54bdA02913"
CHAIN_ID = 8453
NETWORK = "eip155:8453"
PRICE_UNITS = 1_000_000  # 1.00 USDC
MAX_TIMEOUT_SECONDS = 300
RPC_URLS = [
    os.environ.get("BASE_RPC_URL") or "https://mainnet.base.org",
    "https://base.llamarpc.com",
]

# ------------------------------------------------------------------ rpc
_proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
_ca = os.environ.get("SSL_CERT_FILE") or True
_http = httpx.Client(proxy=_proxy, trust_env=False, timeout=30.0, verify=_ca)


def _rpc(method, params=None):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
    last_err = None
    for url in RPC_URLS:
        try:
            r = _http.post(url, json=payload)
            d = r.json()
            if "error" in d:
                last_err = d["error"]
                continue
            return d["result"]
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"all Base RPCs failed: {last_err}")


def _selector(sig: str) -> str:
    return "0x" + keccak(sig.encode()).hex()[:8]


# ------------------------------------------------------- eip-712 (usdc)
def _domain_separator() -> bytes:
    return keccak(
        encode(
            ["bytes32", "bytes32", "bytes32", "uint256", "address"],
            [
                keccak(
                    b"EIP712Domain(string name,string version,"
                    b"uint256 chainId,address verifyingContract)"
                ),
                keccak(b"USD Coin"),
                keccak(b"2"),
                CHAIN_ID,
                USDC_BASE,
            ],
        )
    )


def _auth_digest(auth: dict) -> bytes:
    struct_hash = keccak(
        encode(
            ["bytes32", "address", "address", "uint256", "uint256", "uint256", "bytes32"],
            [
                keccak(
                    b"TransferWithAuthorization(address from,address to,"
                    b"uint256 value,uint256 validAfter,uint256 validBefore,"
                    b"bytes32 nonce)"
                ),
                auth["from"],
                auth["to"],
                int(auth["value"]),
                int(auth["validAfter"]),
                int(auth["validBefore"]),
                bytes.fromhex(auth["nonce"][2:]),
            ],
        )
    )
    return keccak(b"\x19\x01" + _domain_separator() + struct_hash)


def _nonce_used(who: str, nonce_hex: str) -> bool:
    data = _selector("authorizationState(address,bytes32)") + encode(
        ["address", "bytes32"], [who, bytes.fromhex(nonce_hex[2:])]
    ).hex()
    res = _rpc("eth_call", [{"to": USDC_BASE, "data": data}, "latest"])
    return int(res, 16) != 0


# ------------------------------------------------------- 402 requirements
def payment_required_header(pay_to: str, description: str, resource_url: str) -> str:
    doc = {
        "x402Version": 2,
        "error": "Payment Required",
        "resource": {
            "url": resource_url,
            "description": description,
            "mimeType": "application/json",
            "serviceName": "Vesper Launch Pack",
            "tags": ["musegram", "art", "launch"],
        },
        "accepts": [
            {
                "scheme": "exact",
                "network": NETWORK,
                "asset": USDC_BASE,
                "amount": str(PRICE_UNITS),
                "payTo": pay_to,
                "maxTimeoutSeconds": MAX_TIMEOUT_SECONDS,
                "extra": {"name": "USD Coin", "version": "2"},
            }
        ],
    }
    return base64.b64encode(json.dumps(doc).encode()).decode()


# ------------------------------------------------------- payload parsing
def parse_payment_header(header: str) -> dict:
    try:
        doc = json.loads(base64.b64decode(header).decode())
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"undecodable X-Payment: {e}") from e
    inner = doc.get("payload", doc)
    auth = inner.get("authorization")
    sig = inner.get("signature")
    if not auth or not sig:
        raise ValueError("X-Payment missing authorization/signature")
    if doc.get("scheme", "exact") != "exact" or doc.get("network", NETWORK) != NETWORK:
        raise ValueError("unsupported scheme/network in X-Payment")
    return {"from": auth["from"], "to": auth["to"], "value": str(auth["value"]),
            "validAfter": str(auth["validAfter"]), "validBefore": str(auth["validBefore"]),
            "nonce": auth["nonce"], "signature": sig}


# ------------------------------------------------------- verify
def verify_payment(auth: dict, pay_to: str) -> tuple[bool, str]:
    if auth["to"].lower() != pay_to.lower():
        return False, "wrong payTo"
    if int(auth["value"]) != PRICE_UNITS:
        return False, f"wrong amount (want {PRICE_UNITS})"
    now = int(time.time())
    if int(auth["validAfter"]) > now:
        return False, "authorization not yet valid"
    if int(auth["validBefore"]) < now:
        return False, "authorization expired"
    sig_bytes = bytes.fromhex(auth["signature"][2:])
    if len(sig_bytes) != 65:
        return False, "bad signature length"
    v = sig_bytes[64]
    v = v - 27 if v >= 27 else v  # eth_keys wants v in {0, 1}
    try:
        recovered = keys.Signature(
            vrs=(v, int.from_bytes(sig_bytes[:32], "big"),
                 int.from_bytes(sig_bytes[32:64], "big"))
        ).recover_public_key_from_msg_hash(_auth_digest(auth))
    except Exception as e:  # noqa: BLE001
        return False, f"signature recovery failed: {e}"
    if recovered.to_address().lower() != auth["from"].lower():
        return False, "signature does not match from address"
    try:
        if _nonce_used(auth["from"], auth["nonce"]):
            return False, "authorization already used"
    except Exception as e:  # noqa: BLE001
        return False, f"nonce check failed: {e}"
    return True, "ok"


# ------------------------------------------------------- settle
def settle_payment(auth: dict, settler_key_hex: str) -> str:
    """Submit transferWithAuthorization; returns the tx hash."""
    sig_bytes = bytes.fromhex(auth["signature"][2:])
    v = sig_bytes[64]
    v = v if v in (27, 28) else v + 27
    data = "0x" + (
        _selector(
            "transferWithAuthorization(address,address,uint256,"
            "uint256,uint256,bytes32,uint8,bytes32,bytes32)"
        )
        + encode(
            ["address", "address", "uint256", "uint256", "uint256", "bytes32",
             "uint8", "bytes32", "bytes32"],
            [auth["from"], auth["to"], int(auth["value"]),
             int(auth["validAfter"]), int(auth["validBefore"]),
             bytes.fromhex(auth["nonce"][2:]), v,
             sig_bytes[:32], sig_bytes[32:64]],
        ).hex()
    )
    acct = Account.from_key(settler_key_hex)
    sender = acct.address
    nonce = int(_rpc("eth_getTransactionCount", [sender, "latest"]), 16)
    gas = int(_rpc("eth_estimateGas",
                   [{"from": sender, "to": USDC_BASE, "data": data}]), 16)
    gas_price = int(_rpc("eth_gasPrice", []), 16)
    tx = {"chainId": CHAIN_ID, "nonce": nonce, "to": USDC_BASE, "data": data,
          "gas": gas + 20_000, "gasPrice": gas_price, "value": 0}
    signed = acct.sign_transaction(tx)
    tx_hash = _rpc("eth_sendRawTransaction", [signed.raw_transaction.hex()])
    return tx_hash


def wait_receipt(tx_hash: str, tries: int = 20) -> dict | None:
    for _ in range(tries):
        r = _rpc("eth_getTransactionReceipt", [tx_hash])
        if r:
            return r
        time.sleep(3)
    return None
