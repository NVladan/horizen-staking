"""Read-only view of the staking system, formatted for the JSON API.

All amounts are returned twice: ``*_wei`` (raw integer string, safe for JS
BigInt) and a human-readable decimal string, so the frontend never has to do
fragile floating-point math on token amounts.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

from web3 import Web3

from ..config import Config
from .contracts import get_contract

# Global stats are identical for every visitor, so cache them briefly: with N
# users polling, the RPC sees one batch of reads per window instead of N.
GLOBAL_STATS_TTL = 8.0  # seconds


def _fmt(amount_wei: int, decimals: int) -> dict[str, str]:
    human = Decimal(amount_wei) / (Decimal(10) ** decimals)
    return {"wei": str(amount_wei), "amount": format(human, "f")}


class StakingService:
    """Wraps the tstZEN + StakingPool contracts for read access."""

    def __init__(self, w3: Web3, cfg: Config):
        self.w3 = w3
        self.cfg = cfg
        self.token = get_contract(w3, "TstZEN", cfg.tstzen_address)
        self.pool = get_contract(w3, "StakingPool", cfg.staking_address)
        self.decimals = self.token.functions.decimals().call()
        self.symbol = self.token.functions.symbol().call()
        self._stats_cache: dict[str, Any] | None = None
        self._stats_at = 0.0
        self._first_stake_epoch_cache: int | None = None
        self._total_funded_cache: int | None = None

    # --- reward accounting helpers ------------------------------------- #
    def _first_stake_epoch(self, current_epoch: int) -> int | None:
        """Smallest epoch that ever had stake (binary search, then cached)."""
        if self._first_stake_epoch_cache is not None:
            return self._first_stake_epoch_cache
        pool = self.pool.functions
        lo, hi, found = 0, current_epoch, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if pool.totalStakedAtEpoch(mid).call() > 0:
                found, hi = mid, mid - 1
            else:
                lo = mid + 1
        if found is not None:
            self._first_stake_epoch_cache = found
        return found

    def _emitted_wei(self, current_epoch: int, reward_per_epoch: int) -> int:
        """Rewards committed to stakers over finalized epochs that had stake.

        Assumes staking has been continuous since the first stake (the normal
        case). Mid-program full-unstake gaps would slightly over-count emission,
        which is the safe direction (shows the budget as more depleted).
        """
        if current_epoch == 0:
            return 0
        s = self._first_stake_epoch(current_epoch)
        if s is None or s >= current_epoch:
            return 0
        return (current_epoch - s) * reward_per_epoch

    def _total_funded_wei(self, reward_per_year: int) -> int:
        """Total tstZEN ever loaded into the reward reserve (cached)."""
        if self._total_funded_cache is not None:
            return self._total_funded_cache
        try:
            logs = self.pool.events.RewardsFunded().get_logs(from_block=0)
            total = sum(int(lg["args"]["amount"]) for lg in logs)
            if total > 0:
                self._total_funded_cache = total
                return total
        except Exception:  # noqa: BLE001 — RPC may limit getLogs range
            pass
        return reward_per_year  # fallback: the configured annual budget

    # ------------------------------------------------------------------ #
    def global_stats(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._stats_cache is not None and now - self._stats_at < GLOBAL_STATS_TTL:
            return self._stats_cache
        stats = self._read_global_stats()
        self._stats_cache = stats
        self._stats_at = now
        return stats

    def _read_global_stats(self) -> dict[str, Any]:
        pool = self.pool.functions
        total_staked = pool.totalStaked().call()
        reserve = pool.rewardsReserve().call()
        reward_per_epoch = pool.rewardPerEpoch().call()
        reward_per_year = pool.rewardPerYear().call()
        current_epoch = pool.currentEpoch().call()
        seconds_left = pool.secondsUntilNextEpoch().call()
        next_snapshot = pool.nextSnapshotTime().call()

        # APR = annual reward budget / total staked (only meaningful when >0).
        apr_percent = None
        if total_staked > 0:
            apr_percent = float(
                Decimal(reward_per_year) / Decimal(total_staked) * 100
            )

        # Reward accounting that counts COMMITTED rewards (claimed + still-owed),
        # so the pool reflects what's been earned, not just what's been withdrawn.
        funded = self._total_funded_wei(reward_per_year)
        distributed = self._emitted_wei(current_epoch, reward_per_epoch)
        budget_left = max(0, funded - distributed)

        return {
            "totalStaked": _fmt(total_staked, self.decimals),
            "rewardsReserve": _fmt(reserve, self.decimals),       # physical tokens left
            "rewardBudgetLeft": _fmt(budget_left, self.decimals), # funded - committed
            "distributed": _fmt(distributed, self.decimals),      # committed (claimed+owed)
            "totalFunded": _fmt(funded, self.decimals),
            "rewardPerEpoch": _fmt(reward_per_epoch, self.decimals),
            "rewardPerYear": _fmt(reward_per_year, self.decimals),
            "currentEpoch": current_epoch,
            "secondsUntilNextEpoch": seconds_left,
            "nextSnapshotTime": next_snapshot,
            "epochDurationSeconds": pool.epochDuration().call(),
            "aprPercent": apr_percent,
            "symbol": self.symbol,
            "decimals": self.decimals,
        }

    # ------------------------------------------------------------------ #
    def user_stats(self, address: str) -> dict[str, Any]:
        addr = Web3.to_checksum_address(address)
        pool = self.pool.functions
        token = self.token.functions

        staked = pool.stakedBalance(addr).call()
        pending = pool.pendingRewards(addr).call()
        share_bips = pool.shareBips(addr).call()
        next_claim = pool.nextClaimEpoch(addr).call()
        wallet = token.balanceOf(addr).call()
        allowance = token.allowance(addr, self.pool.address).call()
        faucet_cd = token.faucetCooldownRemaining(addr).call()

        return {
            "address": addr,
            "walletBalance": _fmt(wallet, self.decimals),
            "stakedBalance": _fmt(staked, self.decimals),
            "pendingRewards": _fmt(pending, self.decimals),
            "allowance": _fmt(allowance, self.decimals),
            "sharePercent": share_bips / 100.0,
            "nextClaimEpoch": next_claim,
            "faucetCooldownSeconds": faucet_cd,
        }
