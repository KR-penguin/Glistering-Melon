"""Minimal in-memory per-IP rate limit for POST /api/applications.

Good enough for a single-process MVP with real side effects (container
creation, emails) behind this endpoint. Not distributed-safe — fine, since
the whole backend is one process on one box by design (see plan).
"""

import time

WINDOW_SECONDS = 60 * 10
MAX_REQUESTS_PER_WINDOW = 3

_hits: dict[str, list[float]] = {}


def is_rate_limited(client_ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _hits.get(client_ip, []) if now - t < WINDOW_SECONDS]
    _hits[client_ip] = hits
    if len(hits) >= MAX_REQUESTS_PER_WINDOW:
        return True
    hits.append(now)
    return False
