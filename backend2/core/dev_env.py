"""Environment a generated backend needs to start on this machine (preview + validation).

The generated backend refuses to start without JWT_SECRET_KEY (a hard-coded default would be
insecure). Nothing used to provide it, so every preview died at import time, and the auto-fixer
"repaired" the project by hard-coding a dev secret into main.py. The secret is now supplied
through the process environment and never written into the generated project.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path

_KEY_FILE = Path(__file__).resolve().parents[1] / ".dev_secret"
_cached: str | None = None


def _server_secret() -> str:
    """One random secret per AgentForge install, created on first use."""
    global _cached
    if _cached:
        return _cached
    value = ""
    try:
        if _KEY_FILE.is_file():
            value = _KEY_FILE.read_text(encoding="utf-8").strip()
        if len(value) < 32:
            value = secrets.token_urlsafe(48)
            _KEY_FILE.write_text(value, encoding="utf-8")
    except OSError:
        value = value if len(value) >= 32 else secrets.token_urlsafe(48)
    _cached = value
    return value


def backend_env(project_key: str = "") -> dict[str, str]:
    """Variables for the backend process. A JWT_SECRET_KEY set by the user always wins.

    The value is derived per project, so a project keeps the same secret across preview
    restarts (logged-in sessions survive) while projects never share one."""
    if os.environ.get("JWT_SECRET_KEY"):
        return {}
    digest = hmac.new(_server_secret().encode(), str(project_key).encode(), hashlib.sha256)
    return {"JWT_SECRET_KEY": digest.hexdigest()}
