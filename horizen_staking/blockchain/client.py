"""Web3 connection + signing helpers.

Kept tiny and stateless so both the Flask app (read-only) and the admin
scripts (signing) can share it.
"""

from __future__ import annotations

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

from ..config import Config, config as default_config


def _retrying_session() -> requests.Session:
    """A requests session that retries on rate-limit / transient RPC errors."""
    session = requests.Session()
    retries = Retry(
        total=4,
        backoff_factor=0.5,  # 0.5s, 1s, 2s, 4s
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["POST"]),  # JSON-RPC uses POST
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_web3(cfg: Config | None = None) -> Web3:
    """Return a Web3 connected to the configured RPC.

    - POA middleware is injected because Caldera / OP-stack chains put more than
      32 bytes in the block ``extraData`` field, which the default formatter rejects.
    - The HTTP session retries on 429 / 5xx so public RPC rate-limits don't break
      the dashboard.
    """
    cfg = cfg or default_config
    w3 = Web3(Web3.HTTPProvider(
        cfg.rpc_url,
        request_kwargs={"timeout": 30},
        session=_retrying_session(),
    ))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_account(w3: Web3, private_key: str):
    """Load a local signing account from a private key."""
    if not private_key:
        raise ValueError(
            "No DEPLOYER_PRIVATE_KEY set. Add it to .env (a throwaway test wallet)."
        )
    return w3.eth.account.from_key(private_key)
