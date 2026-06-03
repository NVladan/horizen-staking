"""End-to-end tests for TstZEN + StakingPool against a local py-evm chain.

These prove the daily epoch-snapshot reward math *before* anything is deployed
to mainnet. Run with:  python -m pytest -q
"""

import pytest
from web3 import EthereumTesterProvider, Web3

from horizen_staking.blockchain.compiler import compile_contracts

# Compile once for the whole module.
ARTIFACTS = compile_contracts(write=False)

ETHER = 10**18
REWARD_PER_YEAR = 50_000 * ETHER
EPOCH = 86_400  # 1 day
SECONDS_PER_YEAR = 365 * 86_400
REWARD_PER_EPOCH = (REWARD_PER_YEAR * EPOCH) // SECONDS_PER_YEAR


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def w3():
    return Web3(EthereumTesterProvider())


def _deploy(w3, name, *args, sender=None):
    sender = sender or w3.eth.accounts[0]
    art = ARTIFACTS[name]
    factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    tx = factory.constructor(*args).transact({"from": sender})
    rcpt = w3.eth.wait_for_transaction_receipt(tx)
    return w3.eth.contract(address=rcpt.contractAddress, abi=art["abi"])


@pytest.fixture
def deployment(w3):
    """Deploy tstZEN + StakingPool, owner = accounts[0]."""
    owner = w3.eth.accounts[0]
    token = _deploy(w3, "TstZEN", owner)
    pool = _deploy(
        w3,
        "StakingPool",
        token.address,   # stakeToken
        token.address,   # rewardToken
        REWARD_PER_YEAR,
        EPOCH,
        owner,
    )
    return {"w3": w3, "owner": owner, "token": token, "pool": pool}


def _mint(d, to, amount):
    d["token"].functions.mint(to, amount).transact({"from": d["owner"]})


def _stake(d, who, amount):
    d["token"].functions.approve(d["pool"].address, amount).transact({"from": who})
    d["pool"].functions.stake(amount).transact({"from": who})


def _fund_rewards(d, amount):
    _mint(d, d["owner"], amount)
    d["token"].functions.approve(d["pool"].address, amount).transact({"from": d["owner"]})
    d["pool"].functions.fundRewards(amount).transact({"from": d["owner"]})


def _goto_epoch(d, target):
    """Advance chain time so currentEpoch() == target, then mine a block."""
    pool = d["pool"]
    start = pool.functions.startTime().call()
    ts = start + target * EPOCH + 1
    tester = d["w3"].provider.ethereum_tester
    tester.time_travel(ts)
    tester.mine_block()
    assert pool.functions.currentEpoch().call() == target


def _invariant(d):
    """contract token balance == totalStaked + rewardsReserve."""
    pool, token = d["pool"], d["token"]
    bal = token.functions.balanceOf(pool.address).call()
    staked = pool.functions.totalStaked().call()
    reserve = pool.functions.rewardsReserve().call()
    assert bal == staked + reserve


# --------------------------------------------------------------------------- #
# Token / faucet
# --------------------------------------------------------------------------- #
def test_faucet_and_cooldown(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    d["pool"]  # noqa
    d["token"].functions.faucet().transact({"from": user})
    assert d["token"].functions.balanceOf(user).call() == 1_000 * ETHER

    # Second immediate claim must revert (cooldown active).
    with pytest.raises(Exception):
        d["token"].functions.faucet().transact({"from": user})

    # After a day, it works again.
    _goto_epoch(d, 1)
    d["token"].functions.faucet().transact({"from": user})
    assert d["token"].functions.balanceOf(user).call() == 2_000 * ETHER


def test_reward_per_epoch_constant(deployment):
    assert deployment["pool"].functions.rewardPerEpoch().call() == REWARD_PER_EPOCH


# --------------------------------------------------------------------------- #
# Staking mechanics
# --------------------------------------------------------------------------- #
def test_stake_and_unstake_balances(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _mint(d, user, 500 * ETHER)
    _stake(d, user, 200 * ETHER)

    assert d["pool"].functions.stakedBalance(user).call() == 200 * ETHER
    assert d["pool"].functions.totalStaked().call() == 200 * ETHER
    assert d["token"].functions.balanceOf(user).call() == 300 * ETHER
    _invariant(d)

    d["pool"].functions.unstake(50 * ETHER).transact({"from": user})
    assert d["pool"].functions.stakedBalance(user).call() == 150 * ETHER
    assert d["token"].functions.balanceOf(user).call() == 350 * ETHER
    _invariant(d)


def test_unstake_more_than_staked_reverts(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)
    with pytest.raises(Exception):
        d["pool"].functions.unstake(101 * ETHER).transact({"from": user})


# --------------------------------------------------------------------------- #
# Reward distribution
# --------------------------------------------------------------------------- #
def test_single_staker_earns_full_epoch(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, REWARD_PER_YEAR)
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)

    # No finalized epoch yet.
    assert d["pool"].functions.pendingRewards(user).call() == 0

    _goto_epoch(d, 1)  # epoch 0 now finalized
    assert d["pool"].functions.pendingRewards(user).call() == REWARD_PER_EPOCH

    _goto_epoch(d, 3)  # epochs 0,1,2 finalized
    assert d["pool"].functions.pendingRewards(user).call() == 3 * REWARD_PER_EPOCH


def test_two_stakers_prorata(deployment):
    d = deployment
    a, b = d["w3"].eth.accounts[1], d["w3"].eth.accounts[2]
    _fund_rewards(d, REWARD_PER_YEAR)
    _mint(d, a, 100 * ETHER)
    _mint(d, b, 300 * ETHER)
    _stake(d, a, 100 * ETHER)   # 25%
    _stake(d, b, 300 * ETHER)   # 75%

    _goto_epoch(d, 1)  # epoch 0 finalized
    total = 400 * ETHER
    exp_a = REWARD_PER_EPOCH * (100 * ETHER) // total
    exp_b = REWARD_PER_EPOCH * (300 * ETHER) // total
    assert d["pool"].functions.pendingRewards(a).call() == exp_a
    assert d["pool"].functions.pendingRewards(b).call() == exp_b
    # Pro-rata split sums to the epoch budget (modulo integer dust).
    assert exp_a + exp_b <= REWARD_PER_EPOCH
    assert REWARD_PER_EPOCH - (exp_a + exp_b) < 10


def test_share_changes_after_unstake(deployment):
    d = deployment
    a, b = d["w3"].eth.accounts[1], d["w3"].eth.accounts[2]
    _fund_rewards(d, REWARD_PER_YEAR)
    _mint(d, a, 100 * ETHER)
    _mint(d, b, 300 * ETHER)
    _stake(d, a, 100 * ETHER)
    _stake(d, b, 300 * ETHER)

    _goto_epoch(d, 1)
    # In epoch 1, B fully unstakes -> snapshot at end of epoch 1: A=100, total=100.
    d["pool"].functions.unstake(300 * ETHER).transact({"from": b})

    _goto_epoch(d, 2)  # epoch 1 finalized
    # Epoch 0: A got 25%. Epoch 1: A is the only staker -> full epoch reward.
    total0 = 400 * ETHER
    exp_a = REWARD_PER_EPOCH * (100 * ETHER) // total0 + REWARD_PER_EPOCH
    assert d["pool"].functions.pendingRewards(a).call() == exp_a
    # B keeps only its epoch-0 share, nothing for epoch 1.
    exp_b = REWARD_PER_EPOCH * (300 * ETHER) // total0
    assert d["pool"].functions.pendingRewards(b).call() == exp_b


def test_empty_epoch_pays_nothing(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, REWARD_PER_YEAR)
    reserve_before = d["pool"].functions.rewardsReserve().call()

    # Nobody stakes during epochs 0 and 1.
    _goto_epoch(d, 2)
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)
    # Even after staking, the empty earlier epochs award nothing.
    assert d["pool"].functions.pendingRewards(user).call() == 0
    assert d["pool"].functions.rewardsReserve().call() == reserve_before


# --------------------------------------------------------------------------- #
# Claiming
# --------------------------------------------------------------------------- #
def test_claim_pays_and_advances_cursor(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, REWARD_PER_YEAR)
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)

    _goto_epoch(d, 3)  # epochs 0,1,2 finalized
    expected = 3 * REWARD_PER_EPOCH
    bal_before = d["token"].functions.balanceOf(user).call()
    reserve_before = d["pool"].functions.rewardsReserve().call()

    d["pool"].functions.claim(0).transact({"from": user})  # 0 = claim all

    assert d["token"].functions.balanceOf(user).call() == bal_before + expected
    assert d["pool"].functions.rewardsReserve().call() == reserve_before - expected
    assert d["pool"].functions.pendingRewards(user).call() == 0
    assert d["pool"].functions.nextClaimEpoch(user).call() == 3
    _invariant(d)

    # Claiming again with nothing new must revert.
    with pytest.raises(Exception):
        d["pool"].functions.claim(0).transact({"from": user})


def test_claim_before_any_finalized_reverts(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, REWARD_PER_YEAR)
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)
    with pytest.raises(Exception):
        d["pool"].functions.claim(0).transact({"from": user})


def test_claim_respects_max_epochs_batch(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, REWARD_PER_YEAR)
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)

    _goto_epoch(d, 5)  # epochs 0..4 finalized (5 epochs)
    # Claim only 2 epochs.
    d["pool"].functions.claim(2).transact({"from": user})
    assert d["pool"].functions.nextClaimEpoch(user).call() == 2
    # Remaining 3 epochs still pending.
    assert d["pool"].functions.pendingRewards(user).call() == 3 * REWARD_PER_EPOCH


# --------------------------------------------------------------------------- #
# Program cap — the funded-budget fairness guarantee
# --------------------------------------------------------------------------- #
def test_funding_sets_program_length(deployment):
    d = deployment
    _fund_rewards(d, REWARD_PER_YEAR)  # one year of budget
    assert d["pool"].functions.programEpochs().call() == REWARD_PER_YEAR // REWARD_PER_EPOCH


def test_topup_extends_program(deployment):
    d = deployment
    _fund_rewards(d, 2 * REWARD_PER_EPOCH)
    assert d["pool"].functions.programEpochs().call() == 2
    _fund_rewards(d, 3 * REWARD_PER_EPOCH)  # top up
    assert d["pool"].functions.programEpochs().call() == 5


def test_no_rewards_before_funding(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)
    _goto_epoch(d, 3)
    # programEpochs == 0 -> nothing accrues until the pool is funded.
    assert d["pool"].functions.pendingRewards(user).call() == 0


def test_rewards_stop_beyond_funded_program(deployment):
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, 3 * REWARD_PER_EPOCH)  # program covers epochs 0,1,2 only
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)
    _goto_epoch(d, 6)  # epochs 0..5 finalized, but only 0,1,2 are in-program
    # Sole staker: full per-epoch for the 3 funded epochs, nothing beyond.
    assert d["pool"].functions.pendingRewards(user).call() == 3 * REWARD_PER_EPOCH


def test_late_claim_always_paid_in_full(deployment):
    """The guarantee: claiming long after the program ends still pays in full,
    and the reserve exactly covers it — never an InsufficientRewardReserve."""
    d = deployment
    user = d["w3"].eth.accounts[1]
    _fund_rewards(d, 3 * REWARD_PER_EPOCH)  # reserve == 3 epochs, program == 3
    _mint(d, user, 100 * ETHER)
    _stake(d, user, 100 * ETHER)

    _goto_epoch(d, 50)  # far past the program's end
    expected = 3 * REWARD_PER_EPOCH
    assert d["pool"].functions.pendingRewards(user).call() == expected

    bal_before = d["token"].functions.balanceOf(user).call()
    d["pool"].functions.claim(0).transact({"from": user})  # must NOT revert
    assert d["token"].functions.balanceOf(user).call() == bal_before + expected
    assert d["pool"].functions.rewardsReserve().call() == 0  # drained exactly, never short
    _invariant(d)
