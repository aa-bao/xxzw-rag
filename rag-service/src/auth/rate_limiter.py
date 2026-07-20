from __future__ import annotations

import hashlib
from collections import defaultdict
from time import monotonic

_LOGIN_ATTEMPTS: dict[str, list[float]] = defaultdict(list)
_MAX_FAILURES = 5
_WINDOW_SECONDS = 60.0


def login_rate_limit_key(username: str, client_ip: str) -> str:
    normalized = hashlib.sha256(username.strip().lower().encode()).hexdigest()[:32]
    return f"{normalized}:{client_ip}"


def record_login_failure(key: str) -> None:
    now = monotonic()
    _LOGIN_ATTEMPTS[key].append(now)


def is_login_rate_limited(key: str) -> bool:
    now = monotonic()
    cutoff = now - _WINDOW_SECONDS
    entry = _LOGIN_ATTEMPTS[key]
    _LOGIN_ATTEMPTS[key] = [ts for ts in entry if ts > cutoff]
    return len(_LOGIN_ATTEMPTS[key]) >= _MAX_FAILURES


def clear_login_attempts(key: str) -> None:
    _LOGIN_ATTEMPTS.pop(key, None)
