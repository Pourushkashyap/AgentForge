"""Run a generated project (backend + frontend) so it can be shown in a browser."""
from __future__ import annotations

import atexit
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

Notify = Callable[[str, str, str, str], None]  # (job_id, node, message, level)
Change = Callable[[str, dict[str, Any]], None]  # (job_id, public_state)

INSTALL_TIMEOUT = 900
BACKEND_READY_SECONDS = 45
FRONTEND_READY_SECONDS = 120


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_http(url: str, seconds: float) -> bool:
    """True as soon as the server answers at all (any status code)."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return True
        except urllib.error.HTTPError:
            return True  # it answered; 404 is fine
        except Exception:
            time.sleep(0.5)
    return False


def kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, timeout=15)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _popen(cmd: list[str], cwd: Path, log_path: Path, env: dict[str, str] | None = None):
    log = open(log_path, "ab")
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, **(env or {})}, **kwargs)


class PreviewManager:
    def __init__(
        self,
        notify: Notify | None = None,
        on_change: Change | None = None,
        backend_cmd: Callable[[int], list[str]] | None = None,
        frontend_cmd: Callable[[int], list[str]] | None = None,
        install: bool = True,
    ):
        self._notify = notify or (lambda *a: None)
        self._on_change = on_change or (lambda *a: None)
        self._backend_cmd = backend_cmd or self._default_backend_cmd
        self._frontend_cmd = frontend_cmd or self._default_frontend_cmd
        self._install = install
        self._items: dict[str, dict[str, Any]] = {}
        self._procs: dict[str, dict[str, subprocess.Popen]] = {}
        self._dirs: dict[str, tuple[Path, Path]] = {}
        self._lock = threading.Lock()
        atexit.register(self.stop_all)

    # ---- default commands -------------------------------------------------
    @staticmethod
    def _default_backend_cmd(port: int) -> list[str]:
        return [sys.executable, "-m", "uvicorn", "main:app",
                "--host", "127.0.0.1", "--port", str(port)]

    @staticmethod
    def _default_frontend_cmd(port: int) -> list[str]:
        npm = shutil.which("npm") or "npm"
        return [npm, "run", "dev", "--", "--port", str(port),
                "--strictPort", "--host", "127.0.0.1"]

    # ---- state ------------------------------------------------------------
    def _set(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            state = self._items.setdefault(job_id, {"status": "idle"})
            state.update(fields)
            public = dict(state)
        self._on_change(job_id, public)

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._items.get(job_id)
            return dict(state) if state else None

    # ---- lifecycle --------------------------------------------------------
    def start(self, job_id: str, backend_dir: str | Path, frontend_dir: str | Path) -> dict[str, Any]:
        current = self.status(job_id)
        if current and current["status"] in ("installing", "starting", "ready"):
            return current
        self._dirs[job_id] = (Path(backend_dir), Path(frontend_dir))
        self._set(job_id, status="installing", url=None, error=None)
        threading.Thread(target=self._boot, args=(job_id,), daemon=True).start()
        return self.status(job_id)

    def _boot(self, job_id: str) -> None:
        backend_dir, frontend_dir = self._dirs[job_id]
        log = frontend_dir.parent.parent / "preview.log"
        try:
            if self._install and not (frontend_dir / "node_modules").is_dir():
                self._notify(
                    job_id, "preview",
                    "Installing dependencies (temporary registry errors are retried)",
                    "info",
                )
                from core.npm_utils import install_npm_dependencies
                install_npm_dependencies(
                    frontend_dir, attempts=4, timeout=INSTALL_TIMEOUT
                )

            self._set(job_id, status="starting")
            self._notify(job_id, "preview", "Starting servers", "info")
            backend_port, frontend_port = free_port(), free_port()
            procs = {
                "backend": _popen(self._backend_cmd(backend_port), backend_dir, log),
                "frontend": _popen(
                    self._frontend_cmd(frontend_port), frontend_dir, log,
                    {"VITE_API_PROXY_TARGET": f"http://127.0.0.1:{backend_port}"}),
            }
            with self._lock:
                self._procs[job_id] = procs

            if not wait_for_http(f"http://127.0.0.1:{backend_port}/health", BACKEND_READY_SECONDS):
                raise RuntimeError(f"Backend did not start. See {log}")
            if not wait_for_http(f"http://127.0.0.1:{frontend_port}/", FRONTEND_READY_SECONDS):
                raise RuntimeError(f"Frontend did not start. See {log}")

            url = f"http://127.0.0.1:{frontend_port}"
            self._set(job_id, status="ready", url=url, backend_port=backend_port,
                      frontend_port=frontend_port)
            self._notify(job_id, "preview", f"Preview ready at {url}", "info")
        except Exception as exc:
            self._kill(job_id)
            self._set(job_id, status="failed", error=str(exc)[:1000])
            self._notify(job_id, "preview", f"Preview failed: {exc}"[:500], "error")

    def _kill(self, job_id: str) -> None:
        with self._lock:
            procs = self._procs.pop(job_id, {})
        for proc in procs.values():
            kill_tree(proc)

    def stop(self, job_id: str) -> dict[str, Any] | None:
        if self.status(job_id) is None:
            return None
        self._kill(job_id)
        self._set(job_id, status="stopped", url=None)
        return self.status(job_id)

    def restart_backend(self, job_id: str) -> None:
        """Reload backend code after an edit (Vite hot-reloads the frontend itself)."""
        state = self.status(job_id)
        if not state or state["status"] != "ready" or job_id not in self._dirs:
            return
        backend_dir, _ = self._dirs[job_id]
        with self._lock:
            old = self._procs.get(job_id, {}).get("backend")
        if old:
            kill_tree(old)
        port = state["backend_port"]
        proc = _popen(self._backend_cmd(port), backend_dir,
                      backend_dir.parent / "preview.log")
        with self._lock:
            self._procs.setdefault(job_id, {})["backend"] = proc
        wait_for_http(f"http://127.0.0.1:{port}/health", BACKEND_READY_SECONDS)

    def stop_all(self) -> None:
        for job_id in list(self._procs):
            self._kill(job_id)
