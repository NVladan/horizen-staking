"""Mint tstZEN and load it into the StakingPool reward reserve.

Usage:
    python -m scripts.fund_rewards [AMOUNT]

AMOUNT is in whole tstZEN and defaults to REWARD_PER_YEAR (50,000) — one full
year of rewards. Run again any time to top the pool up.
"""

from __future__ import annotations

import sys

from horizen_staking.blockchain.client import get_account, get_web3
from horizen_staking.blockchain.contracts import get_contract, send
from horizen_staking.config import config

DECIMALS = 18


def main() -> None:
    if not config.is_deployed:
        raise SystemExit("Contracts not deployed. Run python -m scripts.deploy first.")

    amount_tokens = int(sys.argv[1]) if len(sys.argv) > 1 else config.reward_per_year
    amount_wei = amount_tokens * (10 ** DECIMALS)

    w3 = get_web3(config)
    account = get_account(w3, config.deployer_private_key)
    token = get_contract(w3, "TstZEN", config.tstzen_address)
    pool = get_contract(w3, "StakingPool", config.staking_address)

    print(f"Funding {amount_tokens} tstZEN into the reward pool...")

    print("  1/3 mint")
    send(w3, account, token.functions.mint(account.address, amount_wei))

    print("  2/3 approve")
    send(w3, account, token.functions.approve(config.staking_address, amount_wei))

    print("  3/3 fundRewards")
    send(w3, account, pool.functions.fundRewards(amount_wei))

    reserve = pool.functions.rewardsReserve().call()
    print(f"\nReward reserve is now {reserve / 10**DECIMALS:.4f} tstZEN.")
    print(f"  {config.explorer_url}/address/{config.staking_address}")


if __name__ == "__main__":
    main()
