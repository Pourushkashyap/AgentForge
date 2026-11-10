"""Validate the generated frontend and backend running together."""
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

    from core.dev_env import backend_env

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", str(port)],
            cwd=backend_dir, stdout=log, stderr=subprocess.STDOUT,
            env={**os.environ, **backend_env(str(backend_dir.parent.name))},
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
                        "Startup log:\n" + log.read()[-6000:]
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
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
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
    detail = diagnostic or (
        json.dumps(body, ensure_ascii=False, default=str)[:1200]
        if body is not None else "No response body"
    )
    return f"{method} {path} returned HTTP {status}: {detail}"


def _normalise_path(path: str) -> str:
    return "/" + path.lstrip("/")


def _expected_json_type(item: dict[str, Any], path: str, *, detail: bool = False) -> str:
    """Resolve an optional contract hint; retain conventional defaults."""
    hint = str(item.get("response_type") or item.get("returns") or "").lower()
    if hint in {"list", "array"}:
        return "list"
    if hint in {"object", "dict"}:
        return "object"
    if hint in {"any", "json"}:
        return "any"
    if detail or path.rstrip("/").lower().endswith(("/health", "/auth/me", "/profile")):
        return "object"
    return "list"


def smoke_check_api(base_url: str, endpoints: list[dict[str, Any]]) -> list[str]:
    """GET collection endpoints and one detail endpoint where possible.

    Endpoint dictionaries may declare ``response_type`` as list, object, or any.
    Without a hint, collection routes default to list and known singleton routes
    (/health, /auth/me, /profile) default to object.
    """
    problems: list[str] = []
    first_ids: dict[str, str] = {}
    detail_specs: list[tuple[str, str, dict[str, Any]]] = []
    for item in endpoints:
        if not isinstance(item, dict):
            continue
        method = str(item.get("method", "GET")).upper()
        path = _normalise_path(str(item.get("path", item.get("endpoint", ""))))
        if method != "GET" or not path or path == "/":
            continue
        marker = "{id}" if "{id}" in path else ":id" if ":id" in path else None
        if marker:
            detail_specs.append((path, marker, item))
            continue

        status, body, diagnostic = _request_json(base_url + path)
        auth_me = path.rstrip("/").lower().endswith("/auth/me")
        if auth_me and status == 401:
            continue
        if status != 200:
            problems.append(_format_failure("GET", path, status, body, diagnostic))
            continue

        expected = _expected_json_type(item, path)
        valid = (
            expected == "any"
            or (expected == "list" and isinstance(body, list))
            or (expected == "object" and isinstance(body, dict))
        )
        if not valid:
            problems.append(
                f"GET {path} returned HTTP {status}, expected JSON {expected}; "
                f"received {type(body).__name__}: "
                f"{json.dumps(body, ensure_ascii=False, default=str)[:1000]}"
            )
            continue
        if isinstance(body, list) and body and isinstance(body[0], dict):
            record_id = body[0].get("id")
            if record_id is not None:
                first_ids[path.rstrip("/")] = str(record_id)

    for path, marker, item in detail_specs:
        list_path = path.replace("/" + marker, "").replace(marker, "").rstrip("/")
        record_id = first_ids.get(list_path)
        if record_id is None:
            continue
        detail_path = path.replace(marker, urllib.parse.quote(record_id, safe=""))
        status, body, diagnostic = _request_json(base_url + detail_path)
        expected = _expected_json_type(item, detail_path, detail=True)
        valid = (
            status == 200 and (
                expected == "any"
                or (expected == "object" and isinstance(body, dict))
                or (expected == "list" and isinstance(body, list))
            )
        )
        if not valid:
            problems.append(_format_failure("GET", detail_path, status, body, diagnostic))
            if status == 200 and body is not None:
                problems[-1] += f" (expected JSON {expected})"
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
                "Inspect the endpoint, response body, and backend route implementation:\n- "
                + "\n- ".join(problems)
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
