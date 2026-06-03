"""Horizen tstZEN staking dApp — modular Flask application package.

Subpackages:
    config        Environment-driven configuration.
    blockchain    Web3 connection, contract loading, compilation, read service.
    api           JSON API blueprint consumed by the frontend.
    web           HTML page blueprint.

``create_app`` is imported lazily so that the ``blockchain`` layer (used by
scripts and tests) does not pull in Flask app wiring.
"""

__all__ = ["create_app"]


def __getattr__(name: str):
    if name == "create_app":
        from .app_factory import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
