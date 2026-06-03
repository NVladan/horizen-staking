"""Per-application runtime context: the Web3 connection and read service.

Both are built lazily so the web server starts cleanly even when the contracts
are not deployed yet or the RPC is momentarily unreachable.
"""

from __future__ import annotations

from .blockchain.client import get_web3
from .blockchain.compiler import ensure_compiled
from .blockchain.staking_service import StakingService
from .config import Config


class AppContext:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._w3 = None
        self._service: StakingService | None = None

    @property
    def w3(self):
        if self._w3 is None:
            self._w3 = get_web3(self.cfg)
        return self._w3

    @property
    def service(self) -> StakingService | None:
        """Return the staking read-service, or None if contracts aren't deployed."""
        if self._service is None:
            if not self.cfg.is_deployed:
                return None
            ensure_compiled()
            self._service = StakingService(self.w3, self.cfg)
        return self._service
