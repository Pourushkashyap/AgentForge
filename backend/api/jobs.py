"""Job manager: runs the graph, the live preview and chat revisions."""
from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from api.preview import PreviewManager
from workflow.state import resolve_options

JOB_ID_RE = re.compile(r"^[0-9a-f]{12}$")
MAX_EVENTS = 1000
MAX_CHAT = 200
TERMINAL = ("completed", "completed_with_warnings", "failed")

# UI progress steps -> graph nodes (preview steps come from the preview state)
STEPS = [
    ("planning", "Planning", ["research", "master_plan", "frontend_plan"]),
    ("generating", "Generating Files",
     ["frontend_build", "api_analysis", "backend_plan", "backend_build"]),
    ("validating", "Validating & Debugging", ["validate", "debug", "finalize"]),
    ("installing", "Installing Dependencies", None),
    ("preview", "Launching Preview", None),
]
NODE_ORDER = ["research", "master_plan", "frontend_plan", "frontend_build",
              "api_analysis", "backend_plan", "backend_build", "validate",
              "debug", "finalize", "fail"]


def compute_steps(job: dict[str, Any]) -> list[dict[str, str]]:
    status, current = job["status"], job.get("current_node")
    if current == "fail":  # the fail node hides where the build actually stopped
        current = next((e["node"] for e in reversed(job.get("events", []))
                        if e.get("node") in NODE_ORDER and e["node"] != "fail"), current)
    preview = (job.get("preview") or {}).get("status")
    failed = status == "failed"
    index = NODE_ORDER.index(current) if current in NODE_ORDER else -1
    steps = []
    for key, label, nodes in STEPS:
        if nodes is not None:
            positions = [NODE_ORDER.index(n) for n in nodes]
            if status in ("completed", "completed_with_warnings") or index > max(positions):
                state = "done"
            elif current in nodes or (index == -1 and key == "planning" and status == "running"):
                state = "failed" if failed else "active"
            elif failed and current == "fail" and key == "validating":
                state = "failed"
            else:
                state = "pending"
        elif key == "installing":
            state = {"installing": "active", "starting": "done", "ready": "done",
                     "failed": "failed"}.get(preview, "pending")
        else:  # preview
            state = {"starting": "active", "ready": "done",
                     "failed": "failed"}.get(preview, "pending")
        steps.append({"key": key, "label": label, "status": state})
    return steps


class JobManager:
    def __init__(
        self,
        output_root: str | Path = "generated_projects_api",
        workers: int | None = None,
        runner: Callable[..., Any] | None = None,
        preview: PreviewManager | None = None,
        reviser: Callable[..., Any] | None = None,
    ):
        self.output_root = Path(output_root).resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        workers = workers or int(os.getenv("PIPELINE_WORKERS", "1"))
        self._pool = ThreadPoolExecutor(max_workers=max(1, workers))
        self._chat_pool = ThreadPoolExecutor(max_workers=2)
        self._runner = runner  # async (requirements, output_dir, options, on_event) -> state
        self._reviser = reviser  # async (frontend_dir, backend_dir, message, history) -> dict
        self.preview = preview or PreviewManager(
            notify=self._notify, on_change=self._preview_changed)
        self._load_saved()

    # ---- persistence ------------------------------------------------------
    def _job_file(self, job_id: str) -> Path:
        return self.output_root / job_id / "job.json"

    def _save(self, job: dict[str, Any]) -> None:
        path = self._job_file(job["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(job, indent=2, default=str), encoding="utf-8")

    def _load_saved(self) -> None:
        for path in self.output_root.glob("*/job.json"):
            try:
                job = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not JOB_ID_RE.match(str(job.get("id", ""))):
                continue
            if job.get("status") in ("queued", "running"):
                job["status"] = "failed"
                job["error"] = "Server restarted while the job was running."
            job["preview"] = None  # processes do not survive a restart
            job["revising"] = False
            job.setdefault("chat", [])
            self._jobs[job["id"]] = job

    # ---- events -----------------------------------------------------------
    def _notify(self, job_id: str, node: str, message: str, level: str = "info") -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        with self._lock:
            if len(job["events"]) < MAX_EVENTS:
                job["events"].append({"ts": time.time(), "node": node,
                                      "level": level, "message": message})

    def _preview_changed(self, job_id: str, state: dict[str, Any]) -> None:
        job = self._jobs.get(job_id)
        if job is not None:
            job["preview"] = state

    # ---- public API -------------------------------------------------------
    def submit(self, requirements: str, options: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id, "status": "queued", "requirements": requirements,
            "options": options, "created_at": time.time(), "finished_at": None,
            "current_node": None, "error": None, "result": None, "events": [],
            "preview": None, "chat": [], "revising": False,
        }
        with self._lock:
            self._jobs[job_id] = job
        self._save(job)
        self._pool.submit(self._run, job_id)
        return self.public(job)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
        return [self.public(j, with_events=False) for j in jobs]

    def project_root(self, job_id: str) -> Path | None:
        job = self.get(job_id)
        root = ((job or {}).get("result") or {}).get("project_root")
        if not root:
            return None
        path = Path(root).resolve()
        if self.output_root in path.parents and path.is_dir():
            return path
        return None

    def project_dirs(self, job_id: str) -> tuple[Path, Path] | None:
        """(backend_dir, frontend_dir) for a finished job, inside the output root."""
        result = (self.get(job_id) or {}).get("result") or {}
        try:
            backend, frontend = Path(result["backend_dir"]), Path(result["frontend_dir"])
        except KeyError:
            return None
        for path in (backend, frontend):
            if self.output_root not in path.resolve().parents or not path.is_dir():
                return None
        return backend.resolve(), frontend.resolve()

    @staticmethod
    def public(job: dict[str, Any], with_events: bool = True) -> dict[str, Any]:
        hidden = ("events", "requirements", "chat")
        data = {k: v for k, v in job.items() if k not in hidden}
        data["requirements_preview"] = job["requirements"][:200]
        data["event_count"] = len(job["events"])
        data["steps"] = compute_steps(job)
        data["chat_count"] = len(job.get("chat", []))
        if with_events:
            data["events"] = list(job["events"][-50:])
        return data

    def events_after(self, job_id: str, after: int) -> list[dict[str, Any]] | None:
        job = self.get(job_id)
        if job is None:
            return None
        return [dict(e, index=i) for i, e in enumerate(job["events"]) if i >= after]

    # ---- resume -----------------------------------------------------------
    def resume(self, job_id: str) -> str:
        """Continue a failed build from its last checkpoint.
        Returns: ok | not_found | not_failed | no_checkpoint"""
        job = self.get(job_id)
        if job is None:
            return "not_found"
        if job["status"] != "failed":
            return "not_failed"
        if not (self.output_root / job_id / "checkpoint.json").is_file():
            return "no_checkpoint"
        with self._lock:
            job.update(status="queued", error=None, finished_at=None)
        try:
            node = json.loads((self.output_root / job_id / "checkpoint.json")
                              .read_text(encoding="utf-8")).get("resume_node", "?")
        except (OSError, ValueError):
            node = "?"
        self._notify(job_id, "resume", f"Resuming from '{node}' (saved files are reused)")
        self._save(job)
        self._pool.submit(self._run, job_id, True)
        return "ok"

    # ---- preview ----------------------------------------------------------
    def start_preview(self, job_id: str) -> dict[str, Any] | None:
        dirs = self.project_dirs(job_id)
        if dirs is None:
            return None
        return self.preview.start(job_id, *dirs)

    # ---- chat (human in the loop) ----------------------------------------
    def chat_history(self, job_id: str) -> list[dict[str, Any]] | None:
        job = self.get(job_id)
        return None if job is None else list(job["chat"])

    def submit_chat(self, job_id: str, message: str) -> tuple[str, dict[str, Any] | None]:
        """Returns (code, turn). code: ok | not_found | not_ready | busy."""
        job = self.get(job_id)
        if job is None:
            return "not_found", None
        if self.project_dirs(job_id) is None or job["status"] not in (
                "completed", "completed_with_warnings"):
            return "not_ready", None
        with self._lock:
            if job["revising"] or len(job["chat"]) >= MAX_CHAT:
                return "busy", None
            job["revising"] = True
            turn_id = len(job["chat"]) // 2 + 1
            user = {"id": f"u{turn_id}", "role": "user", "content": message,
                    "ts": time.time()}
            reply = {"id": f"a{turn_id}", "role": "assistant", "content": "",
                     "status": "working", "changed": [], "ts": time.time()}
            job["chat"] += [user, reply]
        self._chat_pool.submit(self._revise, job_id, reply)
        return "ok", dict(reply)

    def _revise(self, job_id: str, reply: dict[str, Any]) -> None:
        job = self._jobs[job_id]
        backend, frontend = self.project_dirs(job_id)
        self._notify(job_id, "chat", "Applying your change", "info")
        try:
            reviser = self._reviser
            if reviser is None:
                from workflow.revise import revise_project as reviser
            history = [t for t in job["chat"][:-2]]
            result = asyncio.run(reviser(frontend, backend, job["chat"][-2]["content"], history))
        except Exception as exc:
            result = {"ok": False, "error": f"Unexpected error: {exc}", "changed": []}

        with self._lock:
            if result.get("ok"):
                reply["status"] = "done"
                reply["content"] = result.get("summary", "Done.")
                reply["changed"] = result.get("changed", [])
            else:
                reply["status"] = "failed"
                reply["content"] = result.get("error", "The change could not be applied.")
            reply["ts"] = time.time()
            job["revising"] = False
        if result.get("ok") and any(c.startswith("backend/") for c in result.get("changed", [])):
            try:
                self.preview.restart_backend(job_id)
            except Exception:
                pass
        self._notify(job_id, "chat", "Change applied" if result.get("ok") else "Change rejected",
                     "info" if result.get("ok") else "warning")
        self._save(job)

    # ---- worker -----------------------------------------------------------
    def _run(self, job_id: str, resume: bool = False) -> None:
        job = self._jobs[job_id]
        
       
        
        def on_event(event: dict[str, Any]) -> None:
            with self._lock:
                if len(job["events"]) < MAX_EVENTS:
                    job["events"].append(event)
                job["current_node"] = event.get("node")

        job["status"] = "running"
        try:
            runner = self._runner
            if runner is None:
                from workflow.checkpoint import run_checkpointed as runner
            extra = {"resume": True} if resume else {}
            final = asyncio.run(runner(
                requirements=job["requirements"],
                output_dir=str(self.output_root / job_id),
                options=job["options"],
                on_event=on_event,
                **extra,
            ))
            job["error"] = final.get("error")
            job["result"] = final.get("result")
            if final.get("status", "failed") == "failed" and not job["result"]:
                job["result"] = {
                    "issues": final.get("issues") or [],
                    "fixes": final.get("fixes") or [],
                    "project_root": final.get("project_root"),
                }
            job["status"] = final.get("status", "failed")
            
            # Preserve the actual failed stage instead of exposing "fail".
            if job["status"] == "failed" and job.get("current_node") == "fail":
                previous_node = next(
                    (
                        event.get("node")
                        for event in reversed(job.get("events", []))
                        if event.get("node") in NODE_ORDER
                        and event.get("node") != "fail"
                    ),
                    None,
                )

                if previous_node:
                    job["current_node"] = previous_node
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
        finally:
            job["finished_at"] = time.time()
            self._save(job)

        if (job["status"] in ("completed", "completed_with_warnings")
                and resolve_options(job["options"])["auto_preview"]):
            try:
                self.start_preview(job_id)
            except Exception as exc:
                self._notify(job_id, "preview", f"Preview could not start: {exc}", "error")