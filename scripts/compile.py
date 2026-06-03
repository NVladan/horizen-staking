"""Compile the project contracts into ``build/``.

Usage:
    python -m scripts.compile
"""

from horizen_staking.blockchain.compiler import compile_contracts


def main() -> None:
    artifacts = compile_contracts(write=True)
    print("Compiled contracts -> build/")
    for name, art in artifacts.items():
        print(f"  - {name}: {len(art['abi'])} ABI entries, "
              f"{len(art['bytecode']) // 2 - 1} bytes")


if __name__ == "__main__":
    main()
