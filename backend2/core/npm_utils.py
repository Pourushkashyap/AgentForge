"""Reliable npm dependency installation for generated projects."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

_TRANSIENT_MARKERS = (
    "econnreset", "etimedout", "eai_again", "enotfound",
    "econnrefused", "socket hang up", "invalid response body",
    "fetch failed", "network timeout", "read ECONNRESET",
)

def install_npm_dependencies(
    project_dir: str | Path,
    *,
    attempts: int = 4,
    timeout: int = 600,
) -> subprocess.CompletedProcess:
    """Install dependencies, retrying transient npm/registry network failures.

    The caller remains in its current pipeline stage while retries run, so the
    job is not marked failed until all attempts are exhausted.
    """
    project_dir = Path(project_dir).resolve()
    npm = "npm.cmd" if os.name == "nt" else "npm"
    command = [
        npm, "install",
        "--no-audit", "--no-fund",
        "--prefer-offline",
        "--fetch-retries=5",
        "--fetch-retry-mintimeout=20000",
        "--fetch-retry-maxtimeout=120000",
        "--fetch-timeout=300000",
    ]
    last_output = ""
    for attempt in range(1, attempts + 1):
        try:
            result = subprocess.run(
                command,
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            last_output = f"npm install timed out after {timeout}s: {exc}"
            retryable = True
        except OSError as exc:
            last_output = f"Could not start npm: {exc}"
            retryable = False
        else:
            if result.returncode == 0:
                return result
            last_output = (result.stderr or result.stdout or "Unknown npm error")[-12000:]
            lowered = last_output.lower()
            retryable = any(marker.lower() in lowered for marker in _TRANSIENT_MARKERS)

        if not retryable or attempt == attempts:
            raise RuntimeError(
                f"npm install failed after {attempt} attempt(s).\n"
                f"Project: {project_dir}\n{last_output}"
            )

        delay = min(5 * (2 ** (attempt - 1)), 30)
        print(
            f"[npm] Temporary registry/network failure "
            f"(attempt {attempt}/{attempts}); retrying in {delay}s..."
        )
        time.sleep(delay)

    raise RuntimeError(f"npm install failed.\n{last_output}")
