"""LAN model sharing, source side (0.6.146): short-lived read-only tokens for one verified package.

A token names one package revision and the exact files `model_packages.shared_files` returned for
it; nothing else in the library is reachable through it. It lives in this process only (a Hub
restart ends every share, and the copying Mac falls back to the internet), expires after `TTL_S`
without use, and each use renews it. At most `MAX_TRANSFERS` files stream out of this Mac at once.
"""
from __future__ import annotations

from pathlib import Path
import secrets
import threading
import time

TTL_S = 30 * 60
MAX_TRANSFERS = 2
RETRY_AFTER_S = 30
PREFIX = "/api/hub/model-share/"

_shares: dict[str, dict] = {}
_active = 0
_lock = threading.Lock()


def create(package_id: str, revision: str, files: dict[str, Path]) -> tuple[str, float]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _lock:
        for stale in [key for key, share in _shares.items() if share["expires_at"] < now]:
            del _shares[stale]
        _shares[token] = {"package_id": package_id, "revision": revision, "files": dict(files),
                          "expires_at": now + TTL_S}
    return token, now + TTL_S


def file_for(token: str, path: str) -> tuple[dict, Path] | None:
    """The share and the file a request names, renewing the share; None when either is unknown."""
    now = time.time()
    with _lock:
        share = _shares.get(token)
        if share is None or share["expires_at"] < now:
            _shares.pop(token, None)
            return None
        target = share["files"].get(path)
        if target is None:
            return None
        share["expires_at"] = now + TTL_S
        return share, target


def renew(token: str) -> bool:
    """Renew an unexpired grant after actual body delivery; never revive one."""
    now = time.time()
    with _lock:
        share = _shares.get(token)
        if share is None or share["expires_at"] < now:
            return False
        share["expires_at"] = now + TTL_S
        return True


def acquire() -> bool:
    global _active
    with _lock:
        if _active >= MAX_TRANSFERS:
            return False
        _active += 1
        return True


def release() -> None:
    global _active
    with _lock:
        _active = max(0, _active - 1)
