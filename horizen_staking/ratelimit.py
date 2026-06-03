"""Lightweight in-memory, per-IP rate limiting.

Sufficient for a single-process / small deployment. Behind multiple workers the
limits are per-worker; for strict global limits use a shared store (Redis +
Flask-Limiter). Keeps the app dependency-free.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from functools import wraps

from flask import jsonify, request

_HITS: dict[str, deque[float]] = defaultdict(deque)


def rate_limit(max_requests: int, window_seconds: float):
    """Allow at most `max_requests` per `window_seconds` per client IP+endpoint."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = f"{fn.__name__}:{request.remote_addr or '?'}"
            now = time.monotonic()
            cutoff = now - window_seconds
            dq = _HITS[key]
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= max_requests:
                return jsonify({"error": "Too many requests — slow down."}), 429
            dq.append(now)
            return fn(*args, **kwargs)

        return wrapper

    return decorator
