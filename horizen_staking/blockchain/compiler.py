"""Solidity compilation via py-solc-x.

OpenZeppelin contracts are vendored under ``node_modules/@openzeppelin`` (npm)
and resolved through a solc remapping, so no Foundry/Hardhat is required. The
compiled ABI + bytecode for each contract is written to ``build/<Name>.json``
and also returned in-memory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TypedDict

import solcx

from ..config import config

SOLC_VERSION = "0.8.24"
EVM_VERSION = "shanghai"  # PUSH0-enabled; supported by Horizen L3 and local py-evm

# Contracts we compile and deploy (file name -> contract name).
CONTRACTS: dict[str, str] = {
    "TstZEN.sol": "TstZEN",
    "StakingPool.sol": "StakingPool",
}


class Artifact(TypedDict):
    abi: list
    bytecode: str


def _ensure_solc() -> None:
    installed = {str(v) for v in solcx.get_installed_solc_versions()}
    if SOLC_VERSION not in installed:
        solcx.install_solc(SOLC_VERSION)
    solcx.set_solc_version(SOLC_VERSION)


def compile_contracts(write: bool = True) -> dict[str, Artifact]:
    """Compile all project contracts and return ``{name: {abi, bytecode}}``."""
    _ensure_solc()

    root = config.project_root
    contracts_dir = config.contracts_dir
    node_modules = config.node_modules_dir

    if not (node_modules / "@openzeppelin").exists():
        raise FileNotFoundError(
            "OpenZeppelin contracts not found. Run `npm install` first "
            f"(expected at {node_modules / '@openzeppelin'})."
        )

    sources = {}
    for filename in CONTRACTS:
        path = contracts_dir / filename
        sources[f"contracts/{filename}"] = {"content": path.read_text(encoding="utf-8")}

    # Use a RELATIVE remapping target (resolved against base_path below). An
    # absolute target with a Windows drive letter ("C:/...") confuses solc's
    # path resolver, so we keep it relative and let base_path anchor it.
    remapping = "@openzeppelin/=node_modules/@openzeppelin/"

    standard_input = {
        "language": "Solidity",
        "sources": sources,
        "settings": {
            "remappings": [remapping],
            "optimizer": {"enabled": True, "runs": 200},
            "evmVersion": EVM_VERSION,
            "outputSelection": {
                "*": {"*": ["abi", "evm.bytecode.object"]},
            },
        },
    }

    output = solcx.compile_standard(
        standard_input,
        allow_paths=[str(root)],
        base_path=str(root),
    )

    artifacts: dict[str, Artifact] = {}
    for filename, name in CONTRACTS.items():
        contract = output["contracts"][f"contracts/{filename}"][name]
        artifacts[name] = {
            "abi": contract["abi"],
            "bytecode": "0x" + contract["evm"]["bytecode"]["object"],
        }

    if write:
        build_dir = config.build_dir
        build_dir.mkdir(parents=True, exist_ok=True)
        for name, art in artifacts.items():
            (build_dir / f"{name}.json").write_text(
                json.dumps(art, indent=2), encoding="utf-8"
            )

    return artifacts


def ensure_compiled() -> dict[str, Artifact]:
    """Compile if any artifact is missing; otherwise load from ``build/``."""
    missing = any(
        not (config.build_dir / f"{name}.json").exists()
        for name in CONTRACTS.values()
    )
    if missing:
        return compile_contracts(write=True)
    return {name: load_artifact(name) for name in CONTRACTS.values()}


def load_artifact(name: str) -> Artifact:
    """Load a previously compiled artifact from ``build/``."""
    path = config.build_dir / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Artifact {name} not found at {path}. Run scripts/compile.py first."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"abi": data["abi"], "bytecode": data["bytecode"]}


if __name__ == "__main__":
    arts = compile_contracts()
    for nm, a in arts.items():
        print(f"{nm}: {len(a['abi'])} ABI entries, {len(a['bytecode'])} bytes bytecode")
