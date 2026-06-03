// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Checkpoints} from "@openzeppelin/contracts/utils/structs/Checkpoints.sol";

/// @title StakingPool — daily epoch-snapshot staking for tstZEN.
/// @notice Users stake an ERC-20 (tstZEN) and earn from a fixed annual reward
///         pool (e.g. 50,000 tstZEN/year), split pro-rata by each staker's share
///         of the total stake. Rewards are accounted in discrete EPOCHS (1 day).
///
/// @dev    Snapshot model without on-chain iteration:
///         Each stake/unstake writes a checkpoint keyed by the current epoch for
///         both the user's balance and the global total (OpenZeppelin
///         `Checkpoints.Trace208`). For any finalized epoch `e`, a user's reward
///         is `rewardPerEpoch * stakeAt(e) / totalAt(e)`, where `stakeAt`/`totalAt`
///         are binary-search lookups of the value in effect at the end of epoch
///         `e`. This is O(log n) per epoch claimed and never loops over stakers.
///
///         Epochs are derived purely from `block.timestamp`, so no keeper/cron is
///         required to "take" a snapshot — epoch `e` is final as soon as the chain
///         clock passes into epoch `e + 1`.
contract StakingPool is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using Checkpoints for Checkpoints.Trace208;

    uint256 private constant SECONDS_PER_YEAR = 365 days;

    /// @notice Token that is staked.
    IERC20 public immutable stakeToken;
    /// @notice Token that rewards are paid in (same as stakeToken in our setup).
    IERC20 public immutable rewardToken;

    /// @notice Length of one reward epoch, in seconds (86400 = 1 day).
    uint256 public immutable epochDuration;
    /// @notice Unix time at which epoch 0 began (contract deploy time).
    uint256 public immutable startTime;
    /// @notice Total rewards budgeted per year, in rewardToken wei.
    uint256 public immutable rewardPerYear;
    /// @notice Rewards distributed per epoch = rewardPerYear * epochDuration / year.
    uint256 public immutable rewardPerEpoch;

    /// @notice Live staked balance per account.
    mapping(address account => uint256 amount) public stakedBalance;
    /// @notice Live total staked across all accounts.
    uint256 public totalStaked;
    /// @notice Unspent reward tokens held for distribution (funded by admin).
    uint256 public rewardsReserve;

    /// @notice Number of epochs the program is funded for. Rewards accrue ONLY
    ///         for finalized epochs in [0, programEpochs). Each fundRewards call
    ///         extends this by amount / rewardPerEpoch, so the contract can never
    ///         promise more rewards than have been funded — making it impossible
    ///         for a claim to exceed the reserve, so late claimers are always paid.
    uint256 public programEpochs;

    /// @notice Next epoch each account is eligible to claim from (claim cursor).
    mapping(address account => uint256 epoch) private _nextClaimEpoch;
    mapping(address account => bool staked) private _everStaked;

    /// @dev Per-account stake history, keyed by epoch index.
    mapping(address account => Checkpoints.Trace208 history) private _userCheckpoints;
    /// @dev Global total-staked history, keyed by epoch index.
    Checkpoints.Trace208 private _totalCheckpoints;

    /// @notice Safety bound on epochs scanned in a single view call.
    uint256 public constant MAX_VIEW_EPOCHS = 1000;

    event Staked(address indexed account, uint256 amount, uint256 epoch);
    event Unstaked(address indexed account, uint256 amount, uint256 epoch);
    event RewardsClaimed(address indexed account, uint256 amount, uint256 throughEpoch);
    event RewardsFunded(address indexed from, uint256 amount, uint256 newReserve);
    event ProgramExtended(uint256 programEpochs);

    error ZeroAmount();
    error InsufficientStake();
    error NothingToClaim();
    error InsufficientRewardReserve();

    /// @param _stakeToken    ERC-20 to be staked (tstZEN).
    /// @param _rewardToken   ERC-20 rewards are paid in (tstZEN).
    /// @param _rewardPerYear Annual reward budget in wei (e.g. 50_000 ether).
    /// @param _epochDuration Epoch length in seconds (86_400 for daily).
    /// @param initialOwner   Admin able to fund rewards.
    constructor(
        IERC20 _stakeToken,
        IERC20 _rewardToken,
        uint256 _rewardPerYear,
        uint256 _epochDuration,
        address initialOwner
    ) Ownable(initialOwner) {
        require(_epochDuration > 0, "epoch=0");
        stakeToken = _stakeToken;
        rewardToken = _rewardToken;
        rewardPerYear = _rewardPerYear;
        epochDuration = _epochDuration;
        startTime = block.timestamp;
        rewardPerEpoch = (_rewardPerYear * _epochDuration) / SECONDS_PER_YEAR;
    }

    // ---------------------------------------------------------------------
    // Epoch helpers
    // ---------------------------------------------------------------------

    /// @notice The epoch currently in progress (not yet finalized).
    function currentEpoch() public view returns (uint256) {
        return (block.timestamp - startTime) / epochDuration;
    }

    /// @notice The most recent finalized (claimable) epoch, or type(uint256).max
    ///         sentinel meaning "none yet" when still in epoch 0.
    function lastFinalizedEpoch() public view returns (bool exists, uint256 epoch) {
        uint256 cur = currentEpoch();
        if (cur == 0) return (false, 0);
        return (true, cur - 1);
    }

    /// @notice Unix time at which the current epoch ends / next snapshot is taken.
    function nextSnapshotTime() external view returns (uint256) {
        return startTime + (currentEpoch() + 1) * epochDuration;
    }

    /// @notice Seconds remaining until the current epoch finalizes.
    function secondsUntilNextEpoch() external view returns (uint256) {
        uint256 next = startTime + (currentEpoch() + 1) * epochDuration;
        return next - block.timestamp;
    }

    // ---------------------------------------------------------------------
    // Staking
    // ---------------------------------------------------------------------

    /// @notice Stake `amount` of stakeToken. Requires prior ERC-20 approval.
    function stake(uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();

        // First-ever stake: start the claim cursor at the current epoch so the
        // user never wastes gas scanning epochs in which they held nothing.
        if (!_everStaked[msg.sender]) {
            _everStaked[msg.sender] = true;
            _nextClaimEpoch[msg.sender] = currentEpoch();
        }

        uint256 newUser = stakedBalance[msg.sender] + amount;
        uint256 newTotal = totalStaked + amount;
        stakedBalance[msg.sender] = newUser;
        totalStaked = newTotal;
        _writeCheckpoints(msg.sender, newUser, newTotal);

        stakeToken.safeTransferFrom(msg.sender, address(this), amount);
        emit Staked(msg.sender, amount, currentEpoch());
    }

    /// @notice Withdraw `amount` of previously staked tokens.
    function unstake(uint256 amount) external nonReentrant {
        if (amount == 0) revert ZeroAmount();
        uint256 bal = stakedBalance[msg.sender];
        if (amount > bal) revert InsufficientStake();

        uint256 newUser = bal - amount;
        uint256 newTotal = totalStaked - amount;
        stakedBalance[msg.sender] = newUser;
        totalStaked = newTotal;
        _writeCheckpoints(msg.sender, newUser, newTotal);

        stakeToken.safeTransfer(msg.sender, amount);
        emit Unstaked(msg.sender, amount, currentEpoch());
    }

    /// @dev Mirror live balances into the epoch-keyed checkpoint history.
    function _writeCheckpoints(address account, uint256 newUser, uint256 newTotal) private {
        uint48 epoch = uint48(currentEpoch());
        _userCheckpoints[account].push(epoch, uint208(newUser));
        _totalCheckpoints.push(epoch, uint208(newTotal));
    }

    // ---------------------------------------------------------------------
    // Rewards
    // ---------------------------------------------------------------------

    /// @notice Admin: deposit reward tokens and extend the funded program.
    /// @dev Extends `programEpochs` by `amount / rewardPerEpoch`, so the total
    ///      rewards the contract can ever emit is bounded by what's been funded.
    ///      Call again any time to top up and lengthen the program.
    function fundRewards(uint256 amount) external onlyOwner {
        if (amount == 0) revert ZeroAmount();
        rewardsReserve += amount;
        programEpochs += amount / rewardPerEpoch;
        rewardToken.safeTransferFrom(msg.sender, address(this), amount);
        emit RewardsFunded(msg.sender, amount, rewardsReserve);
        emit ProgramExtended(programEpochs);
    }

    /// @notice The highest epoch index that can still earn rewards (programEpochs - 1).
    /// @dev Rewards never accrue at or beyond `programEpochs`.
    function lastRewardEpoch() public view returns (bool exists, uint256 epoch) {
        if (programEpochs == 0) return (false, 0);
        return (true, programEpochs - 1);
    }

    /// @dev The last epoch a user can claim now: finalized AND within the program.
    function _claimableThrough() private view returns (bool exists, uint256 epoch) {
        (bool fin, uint256 lastFinal) = lastFinalizedEpoch();
        if (!fin || programEpochs == 0) return (false, 0);
        uint256 lastProgram = programEpochs - 1;
        return (true, lastFinal < lastProgram ? lastFinal : lastProgram);
    }

    /// @notice Claim accrued rewards for up to `maxEpochs` finalized epochs.
    /// @param maxEpochs Cap on epochs processed this call (bounds gas). Use a
    ///        large number to claim everything outstanding.
    function claim(uint256 maxEpochs) external nonReentrant returns (uint256 reward) {
        (bool exists, uint256 lastClaimable) = _claimableThrough();
        if (!exists) revert NothingToClaim();

        uint256 from = _nextClaimEpoch[msg.sender];
        if (from > lastClaimable) revert NothingToClaim();

        uint256 end = lastClaimable;
        if (maxEpochs != 0 && end - from + 1 > maxEpochs) {
            end = from + maxEpochs - 1;
        }

        reward = _sumRewards(msg.sender, from, end);
        _nextClaimEpoch[msg.sender] = end + 1;

        if (reward > 0) {
            if (reward > rewardsReserve) revert InsufficientRewardReserve();
            rewardsReserve -= reward;
            rewardToken.safeTransfer(msg.sender, reward);
        }
        emit RewardsClaimed(msg.sender, reward, end);
    }

    /// @dev Sum a user's rewards over the inclusive epoch range [from, to].
    function _sumRewards(address account, uint256 from, uint256 to) private view returns (uint256 total) {
        for (uint256 e = from; e <= to; e++) {
            total += _rewardForEpoch(account, e);
        }
    }

    /// @dev Reward owed to `account` for a single finalized epoch.
    function _rewardForEpoch(address account, uint256 epoch) private view returns (uint256) {
        if (epoch >= programEpochs) return 0; // beyond the funded program window
        uint256 totalAt = _totalCheckpoints.upperLookup(uint48(epoch));
        if (totalAt == 0) return 0;
        uint256 userAt = _userCheckpoints[account].upperLookup(uint48(epoch));
        if (userAt == 0) return 0;
        return (rewardPerEpoch * userAt) / totalAt;
    }

    // ---------------------------------------------------------------------
    // Views for the frontend / indexer
    // ---------------------------------------------------------------------

    /// @notice A user's staked amount as snapshotted at the end of `epoch`.
    function stakeAtEpoch(address account, uint256 epoch) external view returns (uint256) {
        return _userCheckpoints[account].upperLookup(uint48(epoch));
    }

    /// @notice Total staked as snapshotted at the end of `epoch`.
    function totalStakedAtEpoch(uint256 epoch) external view returns (uint256) {
        return _totalCheckpoints.upperLookup(uint48(epoch));
    }

    /// @notice Next epoch `account` will claim from.
    function nextClaimEpoch(address account) external view returns (uint256) {
        return _nextClaimEpoch[account];
    }

    /// @notice Total unclaimed rewards across finalized epochs (bounded scan).
    /// @dev Scans at most MAX_VIEW_EPOCHS epochs; if a user is further behind,
    ///      this is a lower bound and they should claim to advance the cursor.
    function pendingRewards(address account) external view returns (uint256) {
        (bool exists, uint256 lastClaimable) = _claimableThrough();
        if (!exists) return 0;
        if (!_everStaked[account]) return 0;

        uint256 from = _nextClaimEpoch[account];
        if (from > lastClaimable) return 0;

        uint256 end = lastClaimable;
        if (end - from + 1 > MAX_VIEW_EPOCHS) {
            end = from + MAX_VIEW_EPOCHS - 1;
        }
        return _sumRewards(account, from, end);
    }

    /// @notice Convenience: a user's current share of the pool, in basis points.
    function shareBips(address account) external view returns (uint256) {
        if (totalStaked == 0) return 0;
        return (stakedBalance[account] * 10_000) / totalStaked;
    }
}
