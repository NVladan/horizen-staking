"""Backwards-compatible re-export; the implementation now lives in the package."""

from horizen_staking.envfile import set_env_vars

__all__ = ["set_env_vars"]
