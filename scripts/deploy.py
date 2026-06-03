"""Deploy TstZEN + StakingPool to the configured chain (Horizen L3 by default).

Usage:
    python -m scripts.deploy

Requires in .env:
    RPC_URL, CHAIN_ID, DEPLOYER_PRIVATE_KEY (test wallet funded with ETH for gas),
    REWARD_PER_YEAR, EPOCH_DURATION_SECONDS

On success the TSTZEN_ADDRESS / STAKING_ADDRESS are written back to .env.
"""

from __future__ import annotations

from web3 import Web3

from horizen_staking.blockchain.client import get_account, get_web3
from horizen_staking.blockchain.compiler import ensure_compiled
from horizen_staking.blockchain.contracts import deploy_contract
from horizen_staking.config import config
from scripts.envfile import set_env_vars

DECIMALS = 18


def main() -> None:
    ensure_compiled()
    w3 = get_web3(config)
    if not w3.is_connected():
        raise SystemExit(f"Could not connect to RPC at {config.rpc_url}")

    account = get_account(w3, config.deployer_private_key)
    balance = w3.eth.get_balance(account.address)
    print(f"Deployer: {account.address}")
    print(f"Network : chainId={w3.eth.chain_id}  ({config.rpc_url})")
    print(f"Gas ETH : {w3.from_wei(balance, 'ether')}")
    if balance == 0:
        raise SystemExit(
            "Deployer has 0 ETH for gas. Bridge a little ETH onto the Horizen "
            "L3 (Caldera bridge) before deploying."
        )

    reward_per_year_wei = config.reward_per_year * (10 ** DECIMALS)

    print("\nDeploying TstZEN...")
    tstzen_addr, _ = deploy_contract(w3, account, "TstZEN", account.address)
    print(f"  TstZEN -> {tstzen_addr}")

    print("Deploying StakingPool...")
    staking_addr, _ = deploy_contract(
        w3,
        account,
        "StakingPool",
        Web3.to_checksum_address(tstzen_addr),  # stakeToken
        Web3.to_checksum_address(tstzen_addr),  # rewardToken
        reward_per_year_wei,
        config.epoch_duration_seconds,
        account.address,                        # owner
    )
    print(f"  StakingPool -> {staking_addr}")

    set_env_vars(
        config.project_root / ".env",
        {"TSTZEN_ADDRESS": tstzen_addr, "STAKING_ADDRESS": staking_addr},
    )

    print("\nSaved addresses to .env. Explorer:")
    print(f"  {config.explorer_url}/address/{tstzen_addr}")
    print(f"  {config.explorer_url}/address/{staking_addr}")
    print("\nNext: python -m scripts.fund_rewards   (mint + load the reward pool)")


if __name__ == "__main__":
    main()
