"""The preview must start generated backends that insist on JWT_SECRET_KEY."""
import sys
import time

from api import preview as pv
from api.preview import PreviewManager, failure_reason
from core import dev_env

SERVER = """
import os, sys, http.server
if not os.environ.get('JWT_SECRET_KEY'):
    raise RuntimeError('JWT_SECRET_KEY environment variable is required.')
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
"""
OPEN_SERVER = SERVER.replace("if not os.environ.get('JWT_SECRET_KEY'):\n    raise RuntimeError('JWT_SECRET_KEY environment variable is required.')\n", "")


def _backend(port):
    return [sys.executable, "-c", SERVER, str(port)]


def _frontend(port):
    return [sys.executable, "-c", OPEN_SERVER, str(port)]


def _dirs(tmp_path):
    backend, frontend = tmp_path / "proj" / "backend", tmp_path / "proj" / "frontend" / "app"
    backend.mkdir(parents=True)
    frontend.mkdir(parents=True)
    return backend, frontend


def _wait(manager, job_id, seconds=25):
    end = time.time() + seconds
    while time.time() < end:
        state = manager.status(job_id)
        if state and state["status"] in ("ready", "failed"):
            return state
        time.sleep(0.2)
    return manager.status(job_id)


def test_dev_secret_is_stable_per_project_and_never_overrides_the_users(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_env, "_KEY_FILE", tmp_path / ".dev_secret")
    monkeypatch.setattr(dev_env, "_cached", None)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    a1, a2 = dev_env.backend_env("job-a"), dev_env.backend_env("job-a")
    b = dev_env.backend_env("job-b")
    assert a1 == a2 and a1 != b and len(a1["JWT_SECRET_KEY"]) >= 32
    monkeypatch.setenv("JWT_SECRET_KEY", "mine")
    assert dev_env.backend_env("job-a") == {}


def test_preview_starts_a_backend_that_requires_the_jwt_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(dev_env, "_KEY_FILE", tmp_path / ".dev_secret")
    monkeypatch.setattr(dev_env, "_cached", None)
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    backend, frontend = _dirs(tmp_path)
    manager = PreviewManager(backend_cmd=_backend, frontend_cmd=_frontend, install=False)
    manager.start("job1", backend, frontend)
    try:
        state = _wait(manager, "job1")
        assert state["status"] == "ready", state
    finally:
        manager.stop("job1")


def test_a_crashing_backend_fails_fast_and_shows_the_real_cause(tmp_path, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    monkeypatch.setattr(pv, "backend_env", lambda key="": {})      # the old behaviour
    monkeypatch.setattr(pv, "BACKEND_READY_SECONDS", 40)
    backend, frontend = _dirs(tmp_path)
    manager = PreviewManager(backend_cmd=_backend, frontend_cmd=_frontend, install=False)
    started = time.time()
    manager.start("job2", backend, frontend)
    try:
        state = _wait(manager, "job2")
        assert state["status"] == "failed"
        assert "JWT_SECRET_KEY environment variable is required" in state["error"]
        assert time.time() - started < 15          # did not wait the full 40 seconds
    finally:
        manager.stop("job2")


def test_failure_reason_reads_only_the_latest_attempt(tmp_path):
    log = tmp_path / "preview.log"
    log.write_text("=== preview start 10:00:00 ===\nValueError: old problem\n"
                   "=== preview start 10:05:00 ===\nTraceback...\nRuntimeError: new problem\n")
    assert failure_reason(log) == "RuntimeError: new problem"
