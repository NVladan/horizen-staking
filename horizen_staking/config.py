"""Environment-driven configuration.

A single ``Config`` object is the source of truth for every module (scripts,
blockchain client, Flask app). Nothing else reads ``os.environ`` directly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Project root = parent of this package directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load .env once, on import, if present.
load_dotenv(PROJECT_ROOT / ".env")


def _to_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration, hydrated from environment variables."""

    # --- Chain ---
    chain_id: int
    chain_name: str
    rpc_url: str
    explorer_url: str

    # --- Contracts (may be empty before deploy) ---
    tstzen_address: str
    staking_address: str

    # --- Staking parameters ---
    reward_per_year: int          # whole tokens (e.g. 50000)
    epoch_duration_seconds: int   # 86400 = 1 day

    # --- Admin / scripts only ---
    deployer_private_key: str
    admin_address: str  # SIWE-gated /admin console
    admin_open: bool    # if admin_address is empty, open the console ONLY when this is true

    # --- Flask ---
    flask_secret_key: str
    debug: bool

    # --- Paths ---
    project_root: Path = PROJECT_ROOT

    def __post_init__(self):
        # These values are surfaced to the browser (and into wallet_addEthereumChain),
        # so reject anything that isn't a plain http(s) URL.
        for label, url in (("RPC_URL", self.rpc_url), ("EXPLORER_URL", self.explorer_url)):
            if url and not url.lower().startswith(("http://", "https://")):
                raise ValueError(f"{label} must start with http:// or https:// (got {url!r})")

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            chain_id=int(os.getenv("CHAIN_ID", "26514")),
            chain_name=os.getenv("CHAIN_NAME", "Horizen"),
            rpc_url=os.getenv("RPC_URL", "https://horizen.calderachain.xyz/http"),
            explorer_url=os.getenv("EXPLORER_URL", "https://horizen.calderaexplorer.xyz"),
            tstzen_address=os.getenv("TSTZEN_ADDRESS", "").strip(),
            staking_address=os.getenv("STAKING_ADDRESS", "").strip(),
            reward_per_year=int(os.getenv("REWARD_PER_YEAR", "50000")),
            epoch_duration_seconds=int(os.getenv("EPOCH_DURATION_SECONDS", "1800")),
            deployer_private_key=os.getenv("DEPLOYER_PRIVATE_KEY", "").strip(),
            admin_address=os.getenv("ADMIN_ADDRESS", "").strip(),
            admin_open=_to_bool(os.getenv("ADMIN_OPEN")),
            flask_secret_key=os.getenv("FLASK_SECRET_KEY", "").strip(),
            debug=_to_bool(os.getenv("FLASK_DEBUG"), default=False),
        )

    @property
    def build_dir(self) -> Path:
        return self.project_root / "build"

    @property
    def contracts_dir(self) -> Path:
        return self.project_root / "contracts"

    @property
    def node_modules_dir(self) -> Path:
        return self.project_root / "node_modules"

    @property
    def is_deployed(self) -> bool:
        return bool(self.tstzen_address and self.staking_address)


# A convenient default instance for modules that just want the current config.
config = Config.from_env()
