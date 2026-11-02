"""Validate the generated frontend and backend running together.

The frontend is generated before the backend exists, so its browser smoke test
cannot run against a real API at that point. After the backend is generated
this module:

1. starts it with uvicorn on a free port,
2. smoke-tests every GET endpoint the frontend uses,
3. runs the frontend's Playwright runtime check with Vite's /api proxy
   pointed at that backend (VITE_API_PROXY_TARGET).
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
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def running_backend(
    backend_dir: str | Path,
    *,
    startup_timeout: float = 30.0,
) -> Iterator[str]:
    """Start the generated FastAPI app and yield its base URL."""
    backend_dir = Path(backend_dir).resolve()
    if not (backend_dir / "main.py").is_file():
        raise ValueError(f"Generated backend has no main.py: {backend_dir}")

    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"

    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=backend_dir,
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        try:
            deadline = time.monotonic() + startup_timeout
            while True:
                if process.poll() is not None:
                    log.seek(0)
                    raise ValueError(
                        "Generated backend exited during startup:\n"
                        + log.read()[-4000:]
                    )
                try:
                    with urllib.request.urlopen(
                        f"{base_url}/health", timeout=1
                    ) as response:
                        if response.status == 200:
                            break
                except Exception:
                    pass
                if time.monotonic() > deadline:
                    raise ValueError(
                        "Generated backend did not become ready in "
                        f"{startup_timeout:.0f}s."
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


def _get_json(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def smoke_check_api(
    base_url: str,
    endpoints: list[dict[str, Any]],
) -> list[str]:
    """GET every list endpoint (and one item) the frontend relies on."""
    problems: list[str] = []
    first_ids: dict[str, str] = {}

    for item in endpoints:
        path = str(item["path"])
        if item["method"] != "GET" or path.endswith("/{id}"):
            continue
        status, body = _get_json(base_url + path)
        if status != 200 or not isinstance(body, list):
            problems.append(f"GET {path} returned {status} / not a JSON list")
            continue
        if body and isinstance(body[0], dict) and body[0].get("id"):
            first_ids[path] = str(body[0]["id"])

    for item in endpoints:
        path = str(item["path"])
        if item["method"] != "GET" or not path.endswith("/{id}"):
            continue
        list_path = path[: -len("/{id}")]
        record_id = first_ids.get(list_path)
        if record_id is None:
            continue  # empty collection: nothing to fetch
        status, body = _get_json(base_url + list_path + "/" + record_id)
        if status != 200 or not isinstance(body, dict):
            problems.append(f"GET {list_path}/{record_id} returned {status}")

    return problems


def static_routes(frontend_plan: Any, limit: int = 8) -> list[str]:
    """Plain page routes (no parameters or wildcards) for the browser test."""
    routes = ["/"]
    for page in getattr(frontend_plan, "pages", []) or []:
        route = str(getattr(page, "route", "") or "")
        if (
            route.startswith("/")
            and ":" not in route
            and "*" not in route
            and route not in routes
        ):
            routes.append(route)
    return routes[:limit]


def validate_connected_runtime(
    frontend_dir: str | Path,
    backend_dir: str | Path,
    endpoints: list[dict[str, Any]],
    routes: list[str] | None = None,
    *,
    run_browser: bool = True,
) -> None:
    """Raise ValueError when the running pair does not work together."""
    with running_backend(backend_dir) as base_url:
        problems = smoke_check_api(base_url, endpoints)
        if problems:
            raise ValueError(
                "Generated backend failed API smoke checks:\n- "
                + "\n- ".join(problems)
            )
        print(f"Backend API smoke checks passed at {base_url}")

        if not run_browser:
            return

        # Imported lazily: it pulls in the whole frontend generator.
        from generation.frontend_generator import _validate_frontend_runtime

        previous = os.environ.get("VITE_API_PROXY_TARGET")
        os.environ["VITE_API_PROXY_TARGET"] = base_url
        try:
            _validate_frontend_runtime(
                Path(frontend_dir), routes=routes or ["/"]
            )
        finally:
            if previous is None:
                os.environ.pop("VITE_API_PROXY_TARGET", None)
            else:
                os.environ["VITE_API_PROXY_TARGET"] = previous
