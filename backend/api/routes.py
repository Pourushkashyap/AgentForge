"""Flask routes for the generation API."""
from __future__ import annotations

import io
import json
import mimetypes
import time
import zipfile
from pathlib import Path

from flask import (Blueprint, Response, current_app, jsonify, request,
                   stream_with_context)

from api.jobs import JOB_ID_RE, JobManager
from workflow.graph import mermaid, topology
from workflow.state import DEFAULT_OPTIONS, validate_options
from workflow.validators import iter_files

routes = Blueprint("routes", __name__)

MIN_REQUIREMENTS = 10
MAX_REQUIREMENTS = 8000
MAX_FILE_BYTES = 500_000


def _manager() -> JobManager:
    return current_app.config["JOB_MANAGER"]


def _error(message: str, status: int):
    return jsonify({"error": message}), status


def _job_or_404(job_id: str):
    if not JOB_ID_RE.match(job_id):
        return None
    return _manager().get(job_id)


@routes.get("/health")
def health():
    return jsonify({"status": "ok"})


@routes.get("/api/graph")
def graph_info():
    data = topology()
    data["mermaid"] = mermaid()
    data["default_options"] = DEFAULT_OPTIONS
    return jsonify(data)


@routes.post("/api/projects")
def create_project():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("Request body must be a JSON object.", 400)
    requirements = body.get("requirements")
    if not isinstance(requirements, str):
        return _error("'requirements' is required and must be a string.", 400)
    requirements = requirements.strip()
    if len(requirements) < MIN_REQUIREMENTS:
        return _error(f"'requirements' must be at least {MIN_REQUIREMENTS} characters.", 400)
    if len(requirements) > MAX_REQUIREMENTS:
        return _error(f"'requirements' must be at most {MAX_REQUIREMENTS} characters.", 400)
    unknown = set(body) - {"requirements", "options"}
    if unknown:
        return _error(f"Unknown field(s): {', '.join(sorted(unknown))}.", 400)
    try:
        options = validate_options(body.get("options"))
    except ValueError as exc:
        return _error(str(exc), 400)

    job = _manager().submit(requirements, options)
    response = jsonify(job)
    response.status_code = 202
    response.headers["Location"] = f"/api/projects/{job['id']}"
    return response


@routes.get("/api/projects")
def list_projects():
    return jsonify({"projects": _manager().list()})


@routes.get("/api/projects/<job_id>")
def get_project(job_id: str):
    job = _job_or_404(job_id)
    if job is None:
        return _error("Project not found.", 404)
    return jsonify(JobManager.public(job))


@routes.post("/api/projects/<job_id>/resume")
def resume_project(job_id: str):
    if _job_or_404(job_id) is None:
        return _error("Project not found.", 404)
    code = _manager().resume(job_id)
    if code == "not_failed":
        return _error("Only a failed or paused build can be resumed.", 409)
    if code == "no_checkpoint":
        return _error("No checkpoint saved for this project. Start a new build.", 409)
    return jsonify(JobManager.public(_manager().get(job_id))), 202


@routes.get("/api/projects/<job_id>/events")
def get_events(job_id: str):
    if not JOB_ID_RE.match(job_id):
        return _error("Project not found.", 404)
    try:
        after = max(0, int(request.args.get("after", "0")))
    except ValueError:
        return _error("'after' must be an integer.", 400)
    events = _manager().events_after(job_id, after)
    if events is None:
        return _error("Project not found.", 404)
    job = _manager().get(job_id)
    return jsonify({"status": job["status"], "events": events,
                    "next": after + len(events)})


def _root_or_error(job_id: str):
    if _job_or_404(job_id) is None:
        return None, _error("Project not found.", 404)
    root = _manager().project_root(job_id)
    if root is None:
        return None, _error("Project files are not available yet.", 409)
    return root, None


@routes.get("/api/projects/<job_id>/files")
def list_files(job_id: str):
    root, error = _root_or_error(job_id)
    if error:
        return error
    files = [p.relative_to(root).as_posix() for p in iter_files(root)]
    return jsonify({"files": files})


@routes.get("/api/projects/<job_id>/files/<path:relpath>")
def read_file(job_id: str, relpath: str):
    root, error = _root_or_error(job_id)
    if error:
        return error
    target = (root / relpath).resolve()
    if root not in target.parents or not target.is_file():
        return _error("File not found.", 404)
    if target.name == ".env":
        return _error("File not found.", 404)
    if target.stat().st_size > MAX_FILE_BYTES:
        return _error("File is too large to preview.", 413)
    mime = mimetypes.guess_type(target.name)[0] or "text/plain"
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error("File is not text.", 415)
    return Response(text, mimetype=mime if mime.startswith("text/") else "text/plain")


@routes.get("/api/projects/<job_id>/download")
def download(job_id: str):
    root, error = _root_or_error(job_id)
    if error:
        return error
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(root):
            if path.name == ".env":
                continue
            archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return Response(
        buffer.getvalue(), mimetype="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{root.name}.zip"'})


# ---- live preview ----------------------------------------------------------
@routes.post("/api/projects/<job_id>/preview")
def start_preview(job_id: str):
    if _job_or_404(job_id) is None:
        return _error("Project not found.", 404)
    state = _manager().start_preview(job_id)
    if state is None:
        return _error("Project files are not available yet.", 409)
    return jsonify(state), 202


@routes.get("/api/projects/<job_id>/preview")
def get_preview(job_id: str):
    if _job_or_404(job_id) is None:
        return _error("Project not found.", 404)
    return jsonify(_manager().preview.status(job_id) or {"status": "idle"})


@routes.delete("/api/projects/<job_id>/preview")
def stop_preview(job_id: str):
    if _job_or_404(job_id) is None:
        return _error("Project not found.", 404)
    return jsonify(_manager().preview.stop(job_id) or {"status": "idle"})


# ---- human-in-the-loop chat -------------------------------------------------
MAX_CHAT_MESSAGE = 2000


@routes.get("/api/projects/<job_id>/chat")
def get_chat(job_id: str):
    if not JOB_ID_RE.match(job_id):
        return _error("Project not found.", 404)
    history = _manager().chat_history(job_id)
    if history is None:
        return _error("Project not found.", 404)
    return jsonify({"messages": history})


@routes.post("/api/projects/<job_id>/chat")
def post_chat(job_id: str):
    if _job_or_404(job_id) is None:
        return _error("Project not found.", 404)
    body = request.get_json(silent=True)
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, str) or not message.strip():
        return _error("'message' is required and must be a non-empty string.", 400)
    if len(message) > MAX_CHAT_MESSAGE:
        return _error(f"'message' must be at most {MAX_CHAT_MESSAGE} characters.", 400)
    code, turn = _manager().submit_chat(job_id, message.strip())
    if code == "not_ready":
        return _error("The project must finish building before you can chat about it.", 409)
    if code == "busy":
        return _error("A change is already being applied. Please wait.", 409)
    return jsonify(turn), 202


# ---- server-sent events -----------------------------------------------------
@routes.get("/api/projects/<job_id>/stream")
def stream(job_id: str):
    if _job_or_404(job_id) is None:
        return _error("Project not found.", 404)
    manager = _manager()

    def generate():
        index, deadline = 0, time.time() + 30 * 60
        while time.time() < deadline:
            events = manager.events_after(job_id, index) or []
            for event in events:
                index = event["index"] + 1
                yield f"data: {json.dumps(event)}\n\n"
            job = manager.get(job_id)
            preview = (manager.preview.status(job_id) or {}).get("status")
            finished = job["status"] in ("completed", "completed_with_warnings", "failed")
            if finished and not events and preview not in ("installing", "starting"):
                final = JobManager.public(job, with_events=False)
                yield f"event: end\ndata: {json.dumps(final, default=str)}\n\n"
                return
            if not events:
                yield ": keep-alive\n\n"
            time.sleep(0.5)

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})