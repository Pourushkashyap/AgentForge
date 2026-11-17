"""Graph routing, validation, debugging and API tests (no network, no LLM)."""
import asyncio
import json
import tempfile
import time
from pathlib import Path

from workflow.debugger import debug_project
from workflow.graph import build_graph, mermaid, route_after, run_workflow, topology
from workflow.state import validate_options
from workflow.validators import (
    check_backend_static, check_frontend_static, has_errors, scan_for_secrets)


def _issue(code="syntax_error", severity="error", fixable=True):
    return {"stage": "t", "code": code, "severity": severity,
            "message": "m", "file": None, "auto_fixable": fixable}


# ---- routing --------------------------------------------------------------
def test_routing_paths():
    assert route_after("research", {}) == "master_plan"
    assert route_after("master_plan", {"error": "boom"}) == "fail"
    assert route_after("validate", {"issues": []}) == "finalize"
    state = {"issues": [_issue()], "debug_attempts": 0, "options": {}}
    assert route_after("validate", state) == "debug"
    state["debug_attempts"] = 2
    assert route_after("validate", state) == "fail"
    assert route_after("validate", {"issues": [_issue(fixable=False)]}) == "fail"
    assert route_after("validate", {"issues": [_issue(severity="warning")]}) == "finalize"
    assert route_after("debug", {}) == "validate"


def test_topology_and_mermaid():
    data = topology()
    assert data["entry"] == "research"
    assert {"debug", "validate", "fail", "finalize"} <= set(data["nodes"])
    assert "validate -->" in mermaid()


# ---- graph with fake nodes ------------------------------------------------
def _fake_nodes(validate_results):
    results = list(validate_results)
    calls = {"debug": 0}

    def passthrough(name):
        async def node(state, config=None):
            return {"events": [{"node": name, "message": name}], "current_node": name}
        return node

    async def validate(state, config=None):
        return {"issues": results.pop(0) if len(results) > 1 else results[0],
                "events": [{"node": "validate", "message": "v"}]}

    async def debug(state, config=None):
        calls["debug"] += 1
        return {"debug_attempts": state.get("debug_attempts", 0) + 1,
                "fixes": [{"code": "x"}], "events": [{"node": "debug", "message": "d"}]}

    async def finalize(state, config=None):
        return {"status": "completed", "result": {"ok": True}}

    async def fail(state, config=None):
        return {"status": "failed"}

    from workflow.graph import NODE_NAMES
    nodes = {n: passthrough(n) for n in NODE_NAMES}
    nodes.update(validate=validate, debug=debug, finalize=finalize, fail=fail)
    return nodes, calls


def _run(nodes, options=None, use_langgraph=False):
    seen = []
    graph = build_graph(nodes, use_langgraph=use_langgraph)
    final = asyncio.run(run_workflow(
        "Build a bakery website with a shop", "out", options,
        on_event=seen.append, graph=graph))
    return final, seen


def test_graph_happy_path():
    nodes, calls = _fake_nodes([[]])
    final, _ = _run(nodes)
    assert final["status"] == "completed" and calls["debug"] == 0


def test_graph_debug_loop_recovers():
    nodes, calls = _fake_nodes([[_issue()], []])
    final, _ = _run(nodes)
    assert final["status"] == "completed"
    assert calls["debug"] == 1 and len(final["fixes"]) == 1


def test_graph_gives_up_after_max_attempts():
    nodes, calls = _fake_nodes([[_issue()]])
    final, _ = _run(nodes, {"max_debug_attempts": 2})
    assert final["status"] == "failed" and calls["debug"] == 2


def test_graph_node_error_goes_to_fail():
    nodes, _ = _fake_nodes([[]])

    async def broken(state, config=None):
        return {"error": "master_plan: boom", "status": "failed"}

    nodes["master_plan"] = broken
    final, _ = _run(nodes)
    assert final["status"] == "failed" and "boom" in final["error"]


def test_langgraph_matches_fallback_when_installed():
    try:
        import langgraph  # noqa: F401
    except ImportError:
        return
    nodes, calls = _fake_nodes([[_issue()], []])
    final, _ = _run(nodes, use_langgraph=True)
    assert final["status"] == "completed" and calls["debug"] == 1


def test_empty_requirements_rejected():
    try:
        asyncio.run(run_workflow("   ", "out"))
    except ValueError:
        return
    raise AssertionError("expected ValueError")


# ---- options --------------------------------------------------------------
def test_option_validation():
    assert validate_options(None) == {}
    assert validate_options({"research": True})["research"] is True
    for bad in ({"nope": 1}, {"research": "yes"}, {"max_debug_attempts": 9},
                {"max_debug_attempts": True}, "x"):
        try:
            validate_options(bad)
        except ValueError:
            continue
        raise AssertionError(f"accepted {bad!r}")


# ---- validators -----------------------------------------------------------
def _make_backend(root: Path, main="from fastapi import FastAPI\napp = FastAPI()\n"
                  "@app.get('/health')\ndef health():\n    return {}\n"):
    (root / "storage").mkdir(parents=True)
    (root / "storage" / "json_store.py").write_text("x = 1\n")
    (root / "data").mkdir()
    (root / "main.py").write_text(main)
    (root / "requirements.txt").write_text("fastapi\nuvicorn[standard]\n")


def test_backend_static_clean_and_broken():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_backend(root)
        assert check_backend_static(root) == []
        (root / "main.py").write_text("def broken(:\n")
        (root / "data" / "p.json").write_text("{bad")
        (root / "requirements.txt").write_text("flask\n")
        codes = {i["code"] for i in check_backend_static(root)}
        assert {"syntax_error", "bad_data_json", "missing_requirements"} <= codes


def test_frontend_static():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        issues = check_frontend_static(root, import_checker=lambda p: [])
        assert has_errors(issues)
        (root / "src").mkdir()
        (root / "src" / "main.jsx").write_text("")
        (root / "index.html").write_text("<html/>")
        (root / "package.json").write_text(json.dumps(
            {"dependencies": {"react": "18"}, "scripts": {"build": "vite build"}}))
        assert check_frontend_static(root, import_checker=lambda p: []) == []
        bad = check_frontend_static(
            root, import_checker=lambda p: ["src/App.jsx: cannot resolve ./Nope"])
        assert bad[0]["file"] == "src/App.jsx"


def test_secret_scan_and_node_modules_skipped():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.py").write_text('KEY = "gsk_' + "a" * 30 + '"\n')
        (root / "node_modules").mkdir()
        (root / "node_modules" / "b.js").write_text('"gsk_' + "b" * 30 + '"')
        issues = [i for i in scan_for_secrets(root) if i["code"] == "secret_found"]
        assert len(issues) == 1 and issues[0]["file"] == "a.py"
        assert issues[0]["auto_fixable"] is False


# ---- debugger -------------------------------------------------------------
def test_deterministic_fixes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_backend(root, main="from fastapi import FastAPI\napp = FastAPI()\n")
        (root / "requirements.txt").write_text("flask\n")
        (root / "data" / "p.json").write_text("{bad")
        issues = check_backend_static(root)
        fixes = asyncio.run(debug_project(issues, root, root / "fe"))
        assert {f["method"] for f in fixes} == {"deterministic"}
        assert check_backend_static(root) == []
        assert (root / "data" / "p.json").read_text() == "[]"


def test_llm_repair_accepts_good_patch_and_rejects_bad():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_backend(root, main="def broken(:\n" + "# pad\n" * 100)
        issues = check_backend_static(root)
        seen = {}

        async def good_llm(**kwargs):
            seen["prompt"] = kwargs["prompt"]
            content = ("from fastapi import FastAPI\napp = FastAPI()\n"
                       "@app.get('/health')\ndef health():\n    return {}\n"
                       + "# pad\n" * 100)
            return json.dumps({"summary": "fixed", "files": [
                {"path": "backend/main.py", "content": content},
                {"path": "backend/../../etc/passwd", "content": "x"}]})

        fixes = asyncio.run(debug_project(issues, root, root / "fe", llm=good_llm))
        assert [f["code"] for f in fixes] == ["llm_patch"]
        assert "backend/main.py" in seen["prompt"]
        assert check_backend_static(root) == []

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_backend(root, main="def broken(:\n" + "# pad\n" * 100)

        async def bad_llm(**kwargs):
            return json.dumps({"files": [
                {"path": "backend/main.py", "content": "def still(:\n"}]})

        fixes = asyncio.run(debug_project(
            check_backend_static(root), root, root / "fe", llm=bad_llm))
        assert fixes[0]["code"] == "llm_rejected"
        assert "def broken(:" in (root / "main.py").read_text()


def test_secrets_never_sent_to_llm():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _make_backend(root)
        (root / "leak.py").write_text('K = "gsk_' + "a" * 30 + '"\n')
        called = []

        async def llm(**kwargs):
            called.append(1)
            return "{}"

        issues = scan_for_secrets(root)
        asyncio.run(debug_project(issues, root, root / "fe", llm=llm))
        assert not called


# ---- API ------------------------------------------------------------------
def _client(runner):
    from app import create_app
    from api.jobs import JobManager

    tmp = tempfile.mkdtemp()
    manager = JobManager(tmp, workers=1, runner=runner)
    return create_app(manager).test_client(), manager, Path(tmp)


def _wait(client, job_id, seconds=5):
    end = time.time() + seconds
    while time.time() < end:
        data = client.get(f"/api/projects/{job_id}").get_json()
        if data["status"] not in ("queued", "running"):
            return data
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_api_validation_errors():
    async def runner(**kw):
        return {"status": "completed"}

    client, _, _ = _client(runner)
    assert client.get("/health").get_json() == {"status": "ok"}
    assert client.post("/api/projects", data="x").status_code == 400
    assert client.post("/api/projects", json={}).status_code == 400
    assert client.post("/api/projects", json={"requirements": "short"}).status_code == 400
    assert client.post("/api/projects", json={"requirements": "x" * 9000}).status_code == 400
    ok = {"requirements": "Build a bakery website"}
    assert client.post("/api/projects", json={**ok, "extra": 1}).status_code == 400
    assert client.post("/api/projects", json={**ok, "options": {"bad": 1}}).status_code == 400
    assert client.get("/api/projects/nothex").status_code == 404
    assert client.get("/api/projects/" + "0" * 12).status_code == 404
    assert client.get("/api/graph").get_json()["mermaid"].startswith("flowchart")


def test_api_job_lifecycle_files_and_traversal():
    holder = {}

    async def runner(requirements, output_dir, options, on_event):
        root = Path(output_dir) / "proj"
        (root / "backend").mkdir(parents=True)
        (root / "backend" / "main.py").write_text("print('hi')\n")
        (root / "backend" / ".env").write_text("SECRET=1\n")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "x.js").write_text("x")
        on_event({"node": "validate", "message": "ok", "level": "info", "ts": 0})
        return {"status": "completed", "result": {"project_root": str(root)}}

    client, manager, _ = _client(runner)
    response = client.post("/api/projects", json={
        "requirements": "Build a bakery website", "options": {"research": False}})
    assert response.status_code == 202
    job_id = response.get_json()["id"]
    assert response.headers["Location"].endswith(job_id)

    data = _wait(client, job_id)
    assert data["status"] == "completed" and data["event_count"] == 1
    events = client.get(f"/api/projects/{job_id}/events?after=0").get_json()
    assert events["next"] == 1
    assert client.get(f"/api/projects/{job_id}/events?after=x").status_code == 400

    files = client.get(f"/api/projects/{job_id}/files").get_json()["files"]
    assert "backend/main.py" in files and not any("node_modules" in f for f in files)
    assert client.get(f"/api/projects/{job_id}/files/backend/main.py").status_code == 200
    assert client.get(f"/api/projects/{job_id}/files/backend/.env").status_code == 404
    assert client.get(f"/api/projects/{job_id}/files/../job.json").status_code == 404
    assert client.get(f"/api/projects/{job_id}/files/%2e%2e/job.json").status_code == 404
    zipped = client.get(f"/api/projects/{job_id}/download")
    assert zipped.status_code == 200 and zipped.mimetype == "application/zip"
    assert len(client.get("/api/projects").get_json()["projects"]) == 1


def test_api_failed_job_reports_error():
    async def runner(**kw):
        raise RuntimeError("groq down")

    client, _, _ = _client(runner)
    job_id = client.post("/api/projects",
                         json={"requirements": "Build a bakery website"}).get_json()["id"]
    data = _wait(client, job_id)
    assert data["status"] == "failed" and "groq down" in data["error"]
    assert client.get(f"/api/projects/{job_id}/files").status_code == 409
