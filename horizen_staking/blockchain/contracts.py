"""Load, instantiate, deploy, and transact with the project contracts.

The Flask app uses :func:`get_contract` (read-only). The admin scripts use
:func:`deploy_contract` and :func:`send` (signed writes).
"""

from __future__ import annotations

from typing import Any

from web3 import Web3
from web3.contract import Contract

from .compiler import load_artifact


def get_contract(w3: Web3, name: str, address: str) -> Contract:
    """Instantiate a deployed contract by name + address."""
    art = load_artifact(name)
    return w3.eth.contract(
        address=Web3.to_checksum_address(address), abi=art["abi"]
    )


def _build_base_tx(w3: Web3, account) -> dict[str, Any]:
    return {
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": w3.eth.chain_id,
    }


def _sign_and_send(w3: Web3, account, tx: dict[str, Any]):
    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def deploy_contract(w3: Web3, account, name: str, *args):
    """Deploy `name` with constructor `args`; return (address, receipt)."""
    art = load_artifact(name)
    factory = w3.eth.contract(abi=art["abi"], bytecode=art["bytecode"])
    tx = factory.constructor(*args).build_transaction(_build_base_tx(w3, account))
    receipt = _sign_and_send(w3, account, tx)
    if receipt.status != 1:
        raise RuntimeError(f"Deployment of {name} failed (tx reverted).")
    return receipt.contractAddress, receipt


def send(w3: Web3, account, contract_function):
    """Sign & send a state-changing contract call; return the receipt."""
    tx = contract_function.build_transaction(_build_base_tx(w3, account))
    receipt = _sign_and_send(w3, account, tx)
    if receipt.status != 1:
        raise RuntimeError(f"Transaction reverted: {contract_function.fn_name}")
    return receipt
