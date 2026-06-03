# Horizen · ZEN Staking (tstZEN testbed)

A non-custodial staking dApp for the **Horizen L3** (Caldera rollup on Base).
Users stake an ERC-20 ZEN token and earn from a fixed annual reward pool
(**50,000 / year** by default), split **pro-rata** by each staker's share of the
pool, settled in **periodic epoch snapshots** (30 minutes by default) — the
program approved in ZenIP 42407 / 42409.

For testing this ships its own ERC-20, **`tstZEN`** (with a faucet), so the whole
flow can be exercised on-chain without touching real ZEN.

> `tstZEN` is a test token. It is **not** real ZEN.

---

## How it works

```
 MetaMask ──sign──►  StakingPool.sol  ◄── holds every stake
    ▲                      │
    │                enforces 50k/yr pro-rata, daily snapshots
 Flask UI ──read via web3.py──►  dashboard (totals, APR, your position)
```

- **On-chain is the source of truth.** A Solidity `StakingPool` holds the stakes
  and computes rewards. The Flask server only *reads* chain state (via web3.py)
  and serves the UI — it never holds a key or custodies funds.
- **Epoch snapshots, gas-bounded.** Each stake/unstake writes an epoch-keyed
  checkpoint (OpenZeppelin `Checkpoints`). For any finalized epoch `e`, your
  reward is `rewardPerEpoch × yourStakeAt(e) / totalStakeAt(e)`, resolved by
  binary search — so claiming never loops over all stakers, and epochs finalize
  automatically from the chain clock (no keeper/cron). Epoch length is a deploy
  parameter (`EPOCH_DURATION_SECONDS`, default 1800 = 30 min); the UI reads it
  from chain and adapts.
- **Non-custodial.** Stake / unstake / claim / faucet are all signed in MetaMask,
  plus one-click "add Horizen network" and "add tstZEN" wallet helpers.
- **Funded-program guarantee.** Rewards accrue only within a funded window
  (`programEpochs`); each `fundRewards` extends it by `amount / rewardPerEpoch`.
  So the pool can never promise more than it holds — a claim can never exceed the
  reserve, and **stakers are always paid in full no matter how late they claim.**
- **Admin console** (`/admin`): SIWE-gated to `ADMIN_ADDRESS`. Deploy the
  contracts in-browser (signing each tx) and top up the reward pool to extend it.

### The two contracts
| Contract | Purpose |
|---|---|
| `contracts/TstZEN.sol` | ERC-20 test token. Owner `mint` (to seed rewards) + rate-limited public `faucet()` (1,000/day). |
| `contracts/StakingPool.sol` | `stake` / `unstake` / `claim` + daily epoch-snapshot reward math, owner-funded reward reserve. |

---

## Prerequisites

- **Python 3.11+**
- **Node.js + npm** — only to vendor the OpenZeppelin Solidity sources (no JS build).
- A throwaway wallet with a small amount of **ETH on the Horizen L3** for gas
  (the L3's gas token is ETH, not ZEN — bridge a little via the Caldera bridge).

`solc` is fetched automatically by `py-solc-x`; no Foundry/Hardhat needed.

## Setup

```bash
python -m pip install -r requirements.txt   # Python deps
npm install                                 # OpenZeppelin contracts (compile-only)
cp .env.example .env                         # then edit .env (see below)
python -m scripts.compile                   # compile contracts -> build/
```

## Configure `.env`

```ini
CHAIN_ID=26514
RPC_URL=https://26514.rpc.thirdweb.com
EXPLORER_URL=https://horizen.calderaexplorer.xyz
DEPLOYER_PRIVATE_KEY=0x...     # throwaway deployer wallet, funded with ETH for gas
REWARD_PER_YEAR=50000          # tstZEN distributed per year
EPOCH_DURATION_SECONDS=1800    # 30 minutes
```

`.env` is gitignored. The web app never reads `DEPLOYER_PRIVATE_KEY`; only the
admin scripts (`deploy`, `fund_rewards`) sign with it.

## Deploy & fund (Horizen L3 mainnet)

```bash
python -m scripts.deploy         # deploys TstZEN + StakingPool, writes addresses to .env
python -m scripts.fund_rewards   # mints 50,000 tstZEN and loads the reward reserve
```

## Run the app

```bash
python app.py        # http://127.0.0.1:5000
```

Open it, **Connect Wallet** (it offers to add/switch to Horizen 26514), click
**Faucet** to grab test tstZEN, then **Stake**. Rewards become claimable once a
daily epoch finalizes.

## Test

The reward math is proven on a local in-memory chain before any deployment:

```bash
python -m pytest -q        # 11 tests: pro-rata splits, snapshots, claiming, etc.
```

---

## Project structure

```
contracts/                 Solidity sources
  TstZEN.sol               ERC-20 test token + faucet
  StakingPool.sol          daily epoch-snapshot staking
horizen_staking/           Flask app package (modular)
  config.py                env-driven Config (single source of truth)
  context.py               lazy Web3 + read-service per app
  app_factory.py           create_app()
  blockchain/              Flask-independent (reused by scripts & tests)
    compiler.py            py-solc-x compilation
    client.py              Web3 connection + signing
    contracts.py           load / deploy / send
    staking_service.py     read-only, API-formatted views
  api/                     JSON API blueprint (/api/*)
  web/                     HTML page blueprint
  templates/ static/       frontend (ethers.js v6, no build step)
scripts/                   compile.py · deploy.py · fund_rewards.py
tests/                     pytest suite (local chain)
app.py                     entry point
```

## Horizen L3 reference

| | |
|---|---|
| Network | Horizen (Caldera L3 on Base) |
| Chain ID | `26514` |
| Gas token | **ETH** (ZEN is an ERC-20) |
| Explorer | https://horizen.calderaexplorer.xyz (Blockscout) |
| Testnet | chain ID `845320009` |

## Security notes

- Web server is read-only; no private keys, no custody. The web app never reads
  `DEPLOYER_PRIVATE_KEY` (only `scripts/` do).
- `stake` / `unstake` / `claim` / `fundRewards` use `nonReentrant`,
  checks-effects-interactions, and `SafeERC20`.
- Reward payouts draw only from the explicitly funded `rewardsReserve`; staked
  principal is tracked separately (`balanceOf(pool) == totalStaked + rewardsReserve`).
  The funded-program cap guarantees a claim can never exceed the reserve.
- `tstZEN` is a test token with an open faucet — do not treat it as valuable.

### Hardening (applied)
- **Admin gate:** SIWE with a single-use, host-bound, 5-minute challenge. The app
  **refuses to start** if `FLASK_SECRET_KEY` is weak/placeholder while
  `ADMIN_ADDRESS` is set (the session cookie would be forgeable). Generate one:
  `python -c "import secrets; print(secrets.token_hex(32))"`.
- **Fail closed:** an empty `ADMIN_ADDRESS` locks `/admin` unless `ADMIN_OPEN=1`.
- **Headers:** CSP (nonce-based scripts, `frame-ancestors 'none'`), `X-Frame-Options`,
  `nosniff`, `Referrer-Policy`, and HSTS when `SESSION_COOKIE_SECURE=1`.
- **Frontend:** all dynamic text is rendered via `textContent` (no `innerHTML`);
  ethers.js is pinned with SRI; URLs are scheme-validated.
- **Rate limiting** on public + auth endpoints; deploy-save verifies on-chain bytecode.

### Before going public
- Set a strong `FLASK_SECRET_KEY`, run with `FLASK_DEBUG=0` behind a WSGI server
  (gunicorn/waitress) and HTTPS, and set `SESSION_COOKIE_SECURE=1`.
- Run `pip-audit -r requirements.txt` (see `requirements-dev.txt`) in CI.

## License

[MIT](LICENSE) © 2026 Vladan Nikolić — free to use, copy, modify, and
distribute (including commercially) with attribution.
