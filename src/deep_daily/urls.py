from __future__ import annotations

import hashlib
import hmac
import os
import time


def generate_hmac_url(
    path: str,
    *,
    server_base: str | None = None,
    secret: str | None = None,
    expire_hours: int = 72,
) -> str:
    base = server_base or os.environ.get("RSS_SERVER_BASE", "")
    if secret is None:
        secret = os.environ.get("RSS_HMAC_SECRET", "")
    if not secret or not base:
        return ""
    exp = str(int(time.time()) + expire_hours * 3600)
    token = hmac.new(
        secret.encode(), f"{path}{exp}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{base}/{path}?token={token}&exp={exp}"


def generate_daily_url(date_str: str) -> str:
    """Build daily digest URL. v0.3.0: one HOME = one reader = one URL path."""
    return generate_hmac_url(f"daily/{date_str}")
