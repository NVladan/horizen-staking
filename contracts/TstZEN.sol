// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

/// @title tstZEN — Test ZEN token for the Horizen staking testbed.
/// @notice A plain ERC-20 used to exercise the staking flow on the Horizen L3.
///         It is NOT the real ZEN token. It adds two test-only conveniences:
///         an owner mint (to seed the reward pool) and a rate-limited public
///         faucet (so testers can grab tokens to stake without asking anyone).
contract TstZEN is ERC20, Ownable {
    /// @notice Amount handed out per faucet claim (1,000 tstZEN).
    uint256 public constant FAUCET_AMOUNT = 1_000 ether;

    /// @notice Minimum delay between faucet claims for a single address.
    uint256 public constant FAUCET_COOLDOWN = 1 days;

    /// @notice Last time each address successfully used the faucet.
    mapping(address account => uint256 timestamp) public lastFaucetClaim;

    event FaucetClaimed(address indexed account, uint256 amount);

    error FaucetCooldownActive(uint256 availableAt);

    constructor(address initialOwner)
        ERC20("Test ZEN", "tstZEN")
        Ownable(initialOwner)
    {}

    /// @notice Mint new tstZEN. Owner-only; used to seed the reward reserve.
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    /// @notice Claim FAUCET_AMOUNT once per FAUCET_COOLDOWN. Test-only.
    function faucet() external {
        uint256 nextAllowed = lastFaucetClaim[msg.sender] + FAUCET_COOLDOWN;
        if (lastFaucetClaim[msg.sender] != 0 && block.timestamp < nextAllowed) {
            revert FaucetCooldownActive(nextAllowed);
        }
        lastFaucetClaim[msg.sender] = block.timestamp;
        _mint(msg.sender, FAUCET_AMOUNT);
        emit FaucetClaimed(msg.sender, FAUCET_AMOUNT);
    }

    /// @notice Seconds until `account` may use the faucet again (0 if ready).
    function faucetCooldownRemaining(address account) external view returns (uint256) {
        if (lastFaucetClaim[account] == 0) return 0;
        uint256 nextAllowed = lastFaucetClaim[account] + FAUCET_COOLDOWN;
        if (block.timestamp >= nextAllowed) return 0;
        return nextAllowed - block.timestamp;
    }
}
