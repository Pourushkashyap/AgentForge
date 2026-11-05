"""Validate the generated frontend and backend running together.

Runtime failures preserve HTTP response bodies and JSON decoding diagnostics so
the debugger can identify the endpoint and source file responsible.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def running_backend(
    backend_dir: str | Path, *, startup_timeout: float = 30.0,
) -> Iterator[str]:
    """Start the generated FastAPI app and yield its base URL."""
    backend_dir = Path(backend_dir).resolve()
    if not (backend_dir / "main.py").is_file():
        raise ValueError(f"Generated backend has no main.py: {backend_dir}")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=backend_dir, stdout=log, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + startup_timeout
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    raise ValueError(
                        "Generated backend exited during startup:\n" + log.read()[-6000:]
                    )
                try:
                    with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() > deadline:
                    log.seek(0)
                    raise ValueError(
                        f"Generated backend did not become ready in {startup_timeout:.0f}s. "
                        "Startup log:\\n" + log.read()[-6000:]
                    )
                time.sleep(0.25)
            yield base_url
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def _request_json(url: str) -> tuple[int, Any, str | None]:
    """Return status, parsed body, and diagnostic (including HTTP error body)."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    except Exception as exc:
        return 0, None, f"{type(exc).__name__}: {exc}"

    text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        return status, None, "Response body was empty"
    try:
        return status, json.loads(text), None
    except json.JSONDecodeError as exc:
        return status, None, (
            f"Response was not valid JSON ({exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}); body={text[:1200]!r}"
        )


def _get_json(url: str) -> tuple[int, Any]:
    """Backward-compatible two-value helper."""
    status, body, _ = _request_json(url)
    return status, body


def _format_failure(method: str, path: str, status: int,
                    body: Any, diagnostic: str | None) -> str:
    detail = diagnostic or (json.dumps(body, ensure_ascii=False, default=str)[:1200]
                            if body is not None else "No response body")
    return f"{method} {path} returned HTTP {status}: {detail}"


def _normalise_path(path: str) -> str:
    path = "/" + path.lstrip("/")
    return path


def smoke_check_api(base_url: str, endpoints: list[dict[str, Any]]) -> list[str]:
    """GET every collection endpoint and one detail endpoint where possible."""
    problems: list[str] = []
    first_ids: dict[str, str] = {}
    normalised = []
    for item in endpoints:
        method = str(item.get("method", "GET")).upper()
        path = _normalise_path(str(item.get("path", "")))
        normalised.append((method, path))
        if method != "GET" or not path:
            continue
        if "{id}" in path or ":id" in path:
            continue
        status, body, diagnostic = _request_json(base_url + path)

        # A current-user endpoint is normally protected. During a smoke check
        # we deliberately make anonymous requests, so 401 from /auth/me is an
        # expected response rather than evidence of a broken backend.
        # Keep this exception narrow: other endpoints must still return 200.
        normalised_for_auth = path.rstrip("/").lower()
        if status == 401 and normalised_for_auth.endswith("/auth/me"):
            continue

        if status != 200:
            problems.append(_format_failure("GET", path, status, body, diagnostic))
            continue
        if not isinstance(body, list):
            problems.append(
                f"GET {path} returned HTTP {status}, expected a JSON list; "
                f"received {type(body).__name__}: "
                f"{json.dumps(body, ensure_ascii=False, default=str)[:1000]}"
            )
            continue
        if body and isinstance(body[0], dict) and body[0].get("id") is not None:
            first_ids[path] = str(body[0]["id"])

    for method, path in normalised:
        if method != "GET" or not ("{id}" in path or ":id" in path):
            continue
        marker = "{id}" if "{id}" in path else ":id"
        list_path = path.replace("/" + marker, "").replace(marker, "").rstrip("/")
        record_id = first_ids.get(list_path)
        if record_id is None:
            # Empty list means there is no valid sample item to fetch.
            continue
        detail_path = path.replace(marker, urllib.parse.quote(record_id, safe=""))
        status, body, diagnostic = _request_json(base_url + detail_path)
        if status != 200 or not isinstance(body, dict):
            problems.append(_format_failure("GET", detail_path, status, body, diagnostic))

    return problems


def static_routes(frontend_plan: Any, limit: int = 8) -> list[str]:
    routes = ["/"]
    for page in getattr(frontend_plan, "pages", []) or []:
        route = str(getattr(page, "route", "") or "")
        if (route.startswith("/") and ":" not in route and "*" not in route
                and route not in routes):
            routes.append(route)
    return routes[:limit]


def validate_connected_runtime(
    frontend_dir: str | Path,
    backend_dir: str | Path,
    endpoints: list[dict[str, Any]],
    routes: list[str] | None = None,
    *, run_browser: bool = True,
) -> None:
    """Raise ValueError with actionable diagnostics when the pair fails."""
    with running_backend(backend_dir) as base_url:
        problems = smoke_check_api(base_url, endpoints)
        if problems:
            raise ValueError(
                "Generated backend failed API smoke checks. "
                "Inspect the endpoint, response body, and backend route implementation:\\n- "
                + "\\n- ".join(problems)
            )
        print(f"Backend API smoke checks passed at {base_url}")

        if not run_browser:
            return

        from generation.frontend_generator import _validate_frontend_runtime
        previous = os.environ.get("VITE_API_PROXY_TARGET")
        os.environ["VITE_API_PROXY_TARGET"] = base_url
        try:
            _validate_frontend_runtime(Path(frontend_dir), routes=routes or ["/"])
        finally:
            if previous is None:
                os.environ.pop("VITE_API_PROXY_TARGET", None)
            else:
                os.environ["VITE_API_PROXY_TARGET"] = previous
