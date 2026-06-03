"""JSON API blueprint consumed by the frontend (ethers.js)."""

from __future__ import annotations

import dataclasses
import logging
import secrets
import time

from eth_account import Account
from eth_account.messages import encode_defunct
from flask import Blueprint, current_app, jsonify, request, session

from web3 import Web3

from ..blockchain.compiler import load_artifact
from ..context import AppContext
from ..envfile import set_env_vars
from ..ratelimit import rate_limit

api_bp = Blueprint("api", __name__, url_prefix="/api")
log = logging.getLogger(__name__)


def _ctx():
    return current_app.config["CTX"]


NONCE_TTL = 300  # seconds a sign-in challenge stays valid


def _is_admin() -> bool:
    """True if authenticated as admin. Fail closed: an empty ADMIN_ADDRESS only
    opens the console when ADMIN_OPEN is explicitly set."""
    cfg = _ctx().cfg
    if cfg.admin_address:
        return bool(session.get("admin"))
    return cfg.admin_open


def _admin_guard():
    """Return a 403 response tuple if not admin, else None."""
    if not _is_admin():
        return jsonify({"error": "Admin only. Sign in with the owner wallet."}), 403
    return None


def _service_or_503():
    ctx = _ctx()
    service = ctx.service
    if service is None:
        return None, (
            jsonify({"error": "Contracts not deployed. Run scripts/deploy.py."}),
            503,
        )
    return service, None


@api_bp.get("/health")
@rate_limit(120, 60)
def health():
    ctx = _ctx()
    try:
        connected = ctx.w3.is_connected()
        block = ctx.w3.eth.block_number if connected else None
    except Exception:  # noqa: BLE001
        return jsonify({"connected": False}), 200
    return jsonify(
        {
            "connected": connected,
            "blockNumber": block,
            "deployed": ctx.cfg.is_deployed,
        }
    )


@api_bp.get("/config")
@rate_limit(120, 60)
def get_config():
    cfg = _ctx().cfg
    return jsonify(
        {
            "deployed": cfg.is_deployed,
            "network": {
                "chainId": cfg.chain_id,
                "chainIdHex": hex(cfg.chain_id),
                "chainName": cfg.chain_name,
                "rpcUrls": [cfg.rpc_url],
                "blockExplorerUrls": [cfg.explorer_url],
                "nativeCurrency": {"name": "Ether", "symbol": "ETH", "decimals": 18},
            },
            "explorerUrl": cfg.explorer_url,
            "tstZenAddress": cfg.tstzen_address,
            "stakingAddress": cfg.staking_address,
            "faucetAmount": "1000",
            "rewardPerYear": cfg.reward_per_year,
            "epochDurationSeconds": cfg.epoch_duration_seconds,
        }
    )


# ---------------------------------------------------------------------- #
# Admin auth (Sign-In With Ethereum)
# ---------------------------------------------------------------------- #
@api_bp.get("/admin/session")
def admin_session():
    cfg = _ctx().cfg
    return jsonify({
        "gated": bool(cfg.admin_address),
        "authenticated": _is_admin(),
    })


@api_bp.get("/admin/nonce")
@rate_limit(20, 60)
def admin_nonce():
    """Issue a single-use, domain-bound, time-limited challenge to sign."""
    cfg = _ctx().cfg
    if not cfg.admin_address:
        return jsonify({"error": "Admin gating is disabled (no ADMIN_ADDRESS)."}), 400
    nonce = secrets.token_hex(16)
    issued = int(time.time())
    message = (
        "Sign in to the Horizen staking admin console.\n\n"
        f"Domain: {request.host}\n"
        f"Owner: {cfg.admin_address}\n"
        f"Nonce: {nonce}\n"
        f"Issued: {issued}"
    )
    session["admin_msg"] = message
    session["admin_msg_at"] = issued
    session["admin_msg_domain"] = request.host
    return jsonify({"message": message})


@api_bp.post("/admin/login")
@rate_limit(10, 60)
def admin_login():
    """Verify the signed challenge recovers to ADMIN_ADDRESS. The challenge is
    single-use (consumed up front, even on failure), expires, and is bound to
    this host to limit replay/phishing."""
    cfg = _ctx().cfg
    if not cfg.admin_address:
        return jsonify({"ok": False, "error": "Admin gating is disabled."}), 400
    if not request.is_json:
        return jsonify({"ok": False, "error": "Expected a JSON request."}), 400

    # Consume the challenge before verifying so a signature can't be retried.
    message = session.pop("admin_msg", None)
    issued = session.pop("admin_msg_at", 0)
    domain = session.pop("admin_msg_domain", None)

    signature = (request.get_json(silent=True) or {}).get("signature", "")
    if not message or not signature:
        return jsonify({"ok": False, "error": "No active challenge — request a new one."}), 400
    if int(time.time()) - int(issued) > NONCE_TTL:
        return jsonify({"ok": False, "error": "Challenge expired — try again."}), 400
    if domain != request.host:
        return jsonify({"ok": False, "error": "Domain mismatch."}), 400

    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "error": "Invalid signature."}), 400

    if recovered.lower() == cfg.admin_address.lower():
        session["admin"] = True
        log.info("Admin authenticated: %s", recovered)
        return jsonify({"ok": True})
    log.warning("Admin login rejected — wallet %s is not the owner", recovered)
    return jsonify({"ok": False, "error": "This wallet is not the admin owner."}), 403


@api_bp.post("/admin/logout")
def admin_logout():
    session.clear()
    return jsonify({"ok": True})


@api_bp.get("/artifacts")
@rate_limit(30, 60)
def get_artifacts():
    """ABI + bytecode for in-browser (admin) deployment. Reads pre-compiled
    artifacts only — never compiles during a request (see startup warm-up)."""
    guard = _admin_guard()
    if guard:
        return guard
    try:
        return jsonify({
            "tstZEN": load_artifact("TstZEN"),
            "stakingPool": load_artifact("StakingPool"),
        })
    except FileNotFoundError:
        return jsonify({"error": "Contracts not compiled. Run python -m scripts.compile."}), 503


@api_bp.post("/admin/deployment")
@rate_limit(10, 60)
def save_deployment():
    """Persist browser-deployed contract addresses and hot-reload the app.

    Writes the addresses to .env (so they survive a restart) and swaps in a
    fresh AppContext so /api/stats etc. start reading the new contracts
    immediately — no server restart needed.
    """
    guard = _admin_guard()
    if guard:
        return guard
    data = request.get_json(silent=True) or {}
    tst = data.get("tstZenAddress", "")
    stk = data.get("stakingAddress", "")
    if not (Web3.is_address(tst) and Web3.is_address(stk)):
        return jsonify({"error": "Invalid contract addresses."}), 400

    tst = Web3.to_checksum_address(tst)
    stk = Web3.to_checksum_address(stk)

    # Both addresses must actually be contracts on the configured chain — don't
    # let the live app be pointed at an EOA or empty address.
    try:
        w3 = _ctx().w3
        if w3.eth.get_code(tst) in (b"", b"0x") or w3.eth.get_code(stk) in (b"", b"0x"):
            return jsonify({"error": "No contract code at one of the addresses."}), 400
    except Exception:  # noqa: BLE001
        log.exception("Bytecode verification failed for %s / %s", tst, stk)
        return jsonify({"error": "Could not verify the contract addresses on-chain."}), 502

    cfg = _ctx().cfg
    set_env_vars(cfg.project_root / ".env", {"TSTZEN_ADDRESS": tst, "STAKING_ADDRESS": stk})
    new_cfg = dataclasses.replace(cfg, tstzen_address=tst, staking_address=stk)
    current_app.config["CTX"] = AppContext(new_cfg)

    log.info("Deployment saved & app hot-swapped: tstZEN=%s staking=%s", tst, stk)
    return jsonify({"ok": True, "tstZenAddress": tst, "stakingAddress": stk})


@api_bp.get("/contracts")
@rate_limit(120, 60)
def get_contracts():
    cfg = _ctx().cfg
    if not cfg.is_deployed:
        return jsonify({"error": "Contracts not deployed."}), 503
    return jsonify(
        {
            "tstZEN": {
                "address": cfg.tstzen_address,
                "abi": load_artifact("TstZEN")["abi"],
            },
            "stakingPool": {
                "address": cfg.staking_address,
                "abi": load_artifact("StakingPool")["abi"],
            },
        }
    )


@api_bp.get("/stats")
@rate_limit(120, 60)
def get_stats():
    service, err = _service_or_503()
    if err:
        return err
    try:
        return jsonify(service.global_stats())
    except Exception:  # noqa: BLE001
        log.exception("Chain read failed")
        return jsonify({"error": "Could not read chain data."}), 502


@api_bp.get("/user/<address>")
@rate_limit(120, 60)
def get_user(address: str):
    if not Web3.is_address(address):
        return jsonify({"error": "Invalid address"}), 400
    service, err = _service_or_503()
    if err:
        return err
    try:
        return jsonify(service.user_stats(address))
    except Exception:  # noqa: BLE001
        log.exception("Chain read failed")
        return jsonify({"error": "Could not read chain data."}), 502
