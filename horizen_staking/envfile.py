"""Update key=value pairs in the project .env file in place.

Shared by the admin API (saving browser-deployed addresses) and the CLI
deploy script.
"""

from __future__ import annotations

import re
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Z0-9_]+$")


def set_env_vars(env_path: Path, updates: dict[str, str]) -> None:
    """Update or append each key in `updates` within `env_path`.

    Existing lines for a key are replaced; missing keys are appended. Comments
    and unrelated lines are preserved. Keys and values are validated so a value
    can never inject additional `.env` lines (newline injection).
    """
    for key, value in updates.items():
        if not _KEY_RE.match(key):
            raise ValueError(f"Invalid env key: {key!r}")
        if any(c in str(value) for c in "\n\r"):
            raise ValueError(f"Env value for {key} contains a newline")

    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    for key, value in remaining.items():
        out.append(f"{key}={value}")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
