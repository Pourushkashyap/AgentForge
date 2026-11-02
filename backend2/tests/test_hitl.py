"""Preview, chat revision, steps and streaming tests (no network, no LLM)."""
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from api.jobs import JobManager, compute_steps
from api.preview import PreviewManager
from workflow.revise import revise_project


# ---- helpers ----------------------------------------------------------------
def _project(root: Path):
    backend, frontend = root / "backend", root / "frontend" / "site"
    (frontend / "src" / "pages").mkdir(parents=True)
    (backend / "storage").mkdir(parents=True)
    (backend / "storage" / "json_store.py").write_text("x = 1\n")
    (backend / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (backend / "main.py").write_text(
        "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/health')\ndef h():\n    return {}\n")
    (frontend / "src" / "App.jsx").write_text("export default function App(){return <div>hi</div>}\n" + "// x\n" * 30)
    (frontend / "src" / "pages" / "Home.jsx").write_text("export default function Home(){return <h1>Old title</h1>}\n" + "// y\n" * 30)
    return backend, frontend


def _llm(select, edit):
    calls = []

    async def llm(**kw):
        calls.append(kw)
        return json.dumps(select if len(calls) == 1 else edit)
    llm.calls = calls
    return llm


def _ok_build(frontend):
    return None


# ---- revise -----------------------------------------------------------------
def test_revise_applies_change():
    with tempfile.TemporaryDirectory() as tmp:
        backend, frontend = _project(Path(tmp))
        new = "export default function Home(){return <h1>New title</h1>}\n" + "// y\n" * 30
        llm = _llm({"files": ["frontend/src/pages/Home.jsx", "bogus"]},
                   {"summary": "Changed the title.",
                    "files": [{"path": "frontend/src/pages/Home.jsx", "content": new},
                              {"path": "frontend/src/App.jsx", "content": "NOT OFFERED"}],
                    "new_files": [{"path": "frontend/src/components/Badge.jsx", "content": "export default function B(){return null}"},
                                  {"path": "frontend/../../evil.js", "content": "x"}]})
        result = asyncio.run(revise_project(frontend, backend, "make title New", llm=llm, build_check=_ok_build))
        print("\nREVISION RESULT:", result)
        assert result["ok"], result
        assert "New title" in (frontend / "src" / "pages" / "Home.jsx").read_text()
        assert "NOT OFFERED" not in (frontend / "src" / "App.jsx").read_text()
        assert (frontend / "src" / "components" / "Badge.jsx").is_file()
        assert not (Path(tmp) / "evil.js").exists()
        assert sorted(result["changed"]) == ["frontend/src/components/Badge.jsx",
                                             "frontend/src/pages/Home.jsx"]


def test_revise_rolls_back_when_build_fails():
    with tempfile.TemporaryDirectory() as tmp:
        backend, frontend = _project(Path(tmp))
        home = frontend / "src" / "pages" / "Home.jsx"
        original = home.read_text()
        llm = _llm({"files": ["frontend/src/pages/Home.jsx"]},
                   {"summary": "x", "files": [{"path": "frontend/src/pages/Home.jsx",
                                               "content": "broken " * 100}],
                    "new_files": [{"path": "frontend/src/components/New.jsx", "content": "export default 1"}]})

        def bad_build(_):
            raise RuntimeError("vite build failed")

        result = asyncio.run(revise_project(frontend, backend, "do it", llm=llm, build_check=bad_build))
        assert not result["ok"] and "unchanged" in result["error"]
        assert home.read_text() == original
        assert not (frontend / "src" / "components" / "New.jsx").exists()


def test_revise_rejects_bad_python_and_empty_message():
    with tempfile.TemporaryDirectory() as tmp:
        backend, frontend = _project(Path(tmp))
        main = backend / "main.py"
        original = main.read_text()
        llm = _llm({"files": ["backend/main.py"]},
                   {"summary": "x", "files": [{"path": "backend/main.py", "content": "def broken(:\n"}]})
        result = asyncio.run(revise_project(frontend, backend, "x", llm=llm, build_check=_ok_build))
        assert not result["ok"] and main.read_text() == original
        empty = asyncio.run(revise_project(frontend, backend, "   ", llm=llm))
        assert not empty["ok"]


def test_revise_no_files_chosen():
    with tempfile.TemporaryDirectory() as tmp:
        backend, frontend = _project(Path(tmp))
        llm = _llm({"files": []}, {})
        result = asyncio.run(revise_project(frontend, backend, "?", llm=llm))
        assert not result["ok"] and len(llm.calls) == 1


# ---- preview ----------------------------------------------------------------
def test_preview_start_ready_stop():
    server = lambda port: [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
    with tempfile.TemporaryDirectory() as tmp:
        backend, frontend = _project(Path(tmp))
        notes, changes = [], []
        manager = PreviewManager(
            notify=lambda *a: notes.append(a), on_change=lambda j, s: changes.append(s["status"]),
            backend_cmd=server, frontend_cmd=server, install=False)
        manager.start("abc", backend, frontend)
        end = time.time() + 20
        while time.time() < end and (manager.status("abc") or {}).get("status") not in ("ready", "failed"):
            time.sleep(0.1)
        state = manager.status("abc")
        assert state["status"] == "ready", state
        assert state["url"].startswith("http://127.0.0.1:")
        assert "installing" in changes and "starting" in changes
        stopped = manager.stop("abc")
        assert stopped["status"] == "stopped" and stopped["url"] is None


def test_preview_failure_reported():
    with tempfile.TemporaryDirectory() as tmp:
        backend, frontend = _project(Path(tmp))
        import api.preview as pv
        old = pv.BACKEND_READY_SECONDS
        pv.BACKEND_READY_SECONDS = 1
        try:
            dead = lambda port: [sys.executable, "-c", "raise SystemExit(1)"]
            manager = PreviewManager(backend_cmd=dead, frontend_cmd=dead, install=False)
            manager.start("x1", backend, frontend)
            end = time.time() + 15
            while time.time() < end and manager.status("x1")["status"] not in ("ready", "failed"):
                time.sleep(0.1)
            assert manager.status("x1")["status"] == "failed"
        finally:
            pv.BACKEND_READY_SECONDS = old


# ---- steps ------------------------------------------------------------------
def _job(status, node, preview=None):
    return {"status": status, "current_node": node, "preview": {"status": preview} if preview else None}


def test_steps_progress():
    s = {x["key"]: x["status"] for x in compute_steps(_job("running", "frontend_build"))}
    assert s["planning"] == "done" and s["generating"] == "active" and s["installing"] == "pending"
    s = {x["key"]: x["status"] for x in compute_steps(_job("completed", "finalize", "installing"))}
    assert s["validating"] == "done" and s["installing"] == "active"
    s = {x["key"]: x["status"] for x in compute_steps(_job("completed", "finalize", "ready"))}
    assert s["preview"] == "done"
    s = {x["key"]: x["status"] for x in compute_steps(_job("failed", "master_plan"))}
    assert s["planning"] == "failed" and s["generating"] == "pending"


# ---- API: chat, preview, stream ---------------------------------------------
def _client(runner, reviser, preview):
    from app import create_app
    tmp = tempfile.mkdtemp()
    manager = JobManager(tmp, workers=1, runner=runner, reviser=reviser, preview=preview)
    return create_app(manager).test_client(), manager, Path(tmp)


def _wait(client, job_id, field="status", values=("completed", "failed"), seconds=8):
    end = time.time() + seconds
    while time.time() < end:
        data = client.get(f"/api/projects/{job_id}").get_json()
        if data[field] in values:
            return data
        time.sleep(0.05)
    raise AssertionError("timeout")


def test_chat_and_stream_flow():
    revised = []

    async def runner(requirements, output_dir, options, on_event):
        root = Path(output_dir) / "proj"
        backend, frontend = _project(root)
        on_event({"node": "validate", "message": "ok", "level": "info", "ts": 0})
        return {"status": "completed", "result": {
            "project_root": str(root), "backend_dir": str(backend), "frontend_dir": str(frontend)}}

    async def reviser(frontend, backend, message, history):
        revised.append((message, len(history)))
        if "fail" in message:
            return {"ok": False, "error": "nope", "changed": []}
        return {"ok": True, "summary": "Done!", "changed": ["frontend/src/App.jsx"]}

    server = lambda port: [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"]
    preview = PreviewManager(backend_cmd=server, frontend_cmd=server, install=False)
    client, manager, _ = _client(runner, reviser, preview)

    job_id = client.post("/api/projects", json={"requirements": "Build a bakery website"}).get_json()["id"]
    done = _wait(client, job_id)
    assert done["status"] == "completed"

    # preview auto-started
    end = time.time() + 20
    while time.time() < end and (client.get(f"/api/projects/{job_id}/preview").get_json().get("status") != "ready"):
        time.sleep(0.1)
    prev = client.get(f"/api/projects/{job_id}/preview").get_json()
    assert prev["status"] == "ready" and prev["url"]

    # chat validation
    assert client.post(f"/api/projects/{job_id}/chat", json={}).status_code == 400
    assert client.post(f"/api/projects/{job_id}/chat", json={"message": "x" * 2001}).status_code == 400
    assert client.post("/api/projects/" + "0" * 12 + "/chat", json={"message": "hi"}).status_code == 404

    first = client.post(f"/api/projects/{job_id}/chat", json={"message": "make header blue"})
    assert first.status_code == 202
    end = time.time() + 5
    while time.time() < end:
        msgs = client.get(f"/api/projects/{job_id}/chat").get_json()["messages"]
        if msgs[-1]["status"] != "working":
            break
        time.sleep(0.05)
    assert msgs[-1]["status"] == "done" and msgs[-1]["content"] == "Done!"
    assert msgs[-1]["changed"] == ["frontend/src/App.jsx"]

    client.post(f"/api/projects/{job_id}/chat", json={"message": "please fail"})
    time.sleep(0.5)
    msgs = client.get(f"/api/projects/{job_id}/chat").get_json()["messages"]
    assert msgs[-1]["status"] == "failed" and msgs[-1]["content"] == "nope"
    assert revised[1][1] == 2  # history passed to the reviser (previous turn)

    # SSE ends with a final event
    stream = client.get(f"/api/projects/{job_id}/stream")
    body = stream.get_data(as_text=True)
    assert "event: end" in body and '"steps"' in body
    assert client.delete(f"/api/projects/{job_id}/preview").get_json()["status"] == "stopped"


def test_chat_requires_finished_project():
    async def runner(**kw):
        raise RuntimeError("boom")

    client, _, _ = _client(runner, None, PreviewManager(install=False))
    job_id = client.post("/api/projects", json={"requirements": "Build a bakery website"}).get_json()["id"]
    _wait(client, job_id)
    assert client.post(f"/api/projects/{job_id}/chat", json={"message": "hi"}).status_code == 409
    assert client.post(f"/api/projects/{job_id}/preview").status_code == 409


def test_failed_build_points_at_the_failing_step():
    async def runner(requirements, output_dir, options, on_event):
        on_event({"node": "master_plan", "message": "ok", "level": "info", "ts": 0})
        on_event({"node": "frontend_plan", "message": "failed", "level": "error", "ts": 0})
        on_event({"node": "fail", "message": "Pipeline failed", "level": "error", "ts": 0})
        return {"status": "failed", "error": "frontend_plan: Groq daily limit"}

    client, _, _ = _client(runner, None, PreviewManager(install=False))
    job_id = client.post("/api/projects", json={"requirements": "Build a bakery website"}).get_json()["id"]
    data = _wait(client, job_id)
    steps = {s["key"]: s["status"] for s in data["steps"]}
    assert data["status"] == "failed" and data["current_node"] == "frontend_plan"
    assert steps["planning"] == "failed" and steps["generating"] == "pending"
    assert steps["validating"] == "pending"
