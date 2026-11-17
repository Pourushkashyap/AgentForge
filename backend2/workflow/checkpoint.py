"""Per-node checkpoints so a failed / interrupted build can resume where it stopped.

Copy to backend2/workflow/checkpoint.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from workflow.graph import END, MAX_STEPS, _merge, build_graph, route_after
from workflow.state import resolve_options

CKPT = "checkpoint.json"


def _models() -> dict[str, Any]:
    from planning.backend_schemas import BackendPlan
    from planning.frontend_schemas import FrontendPlan
    from planning.schemas import MasterPlan
    return {"master_plan": MasterPlan, "frontend_plan": FrontendPlan, "backend_plan": BackendPlan}


def save(output_dir: str | Path, state: dict[str, Any], resume_node: str) -> None:
    data = {k: (v.model_dump() if hasattr(v, "model_dump") else v)
            for k, v in state.items() if k not in ("events", "error", "status")}
    path = Path(output_dir) / CKPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"resume_node": resume_node, "state": data}, default=str),
                    encoding="utf-8")


def load(output_dir: str | Path) -> tuple[dict[str, Any], str] | None:
    path = Path(output_dir) / CKPT
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    state = raw["state"]
    for key, model in _models().items():
        if isinstance(state.get(key), dict):
            state[key] = model.model_validate(state[key])
    return state, raw["resume_node"]


def wrap_nodes(nodes: dict[str, Callable], output_dir: str) -> dict[str, Callable]:
    """Save a checkpoint after every node (also when a node fails, so it can be retried)."""
    def wrap(name: str, fn: Callable):
        async def wrapper(state, config=None):
            update = await fn(state, config)
            if name == "fail":
                return update
            if update.get("error"):                      # node crashed -> retry this node
                save(output_dir, state, name)
                return update
            merged = dict(state)
            _merge(merged, update)
            nxt = route_after(name, merged)
            if nxt == "fail":                            # validation exhausted -> retry validate
                save(output_dir, merged, "validate")
            elif nxt != END:
                save(output_dir, merged, nxt)
            return update
        return wrapper
    return {n: wrap(n, f) for n, f in nodes.items()}


async def run_checkpointed(requirements: str, output_dir: str, options: dict | None = None,
                           on_event: Callable | None = None, resume: bool = False,
                           nodes: dict | None = None) -> dict[str, Any]:
    """Drop-in replacement for run_workflow(); resume=True continues from the checkpoint."""
    if nodes is None:
        from workflow.nodes import NODES as nodes
    wrapped = wrap_nodes(nodes, output_dir)
    config = {"configurable": {"on_event": on_event}, "recursion_limit": MAX_STEPS}

    if not resume:
        initial = {"requirements": requirements.strip(), "output_dir": str(output_dir),
                   "options": resolve_options(options), "issues": [], "fixes": [], "events": [],
                   "debug_attempts": 0, "status": "running", "error": None}
        return await build_graph(wrapped).ainvoke(initial, config)

    loaded = load(output_dir)
    if loaded is None:
        raise RuntimeError("No checkpoint saved for this project.")
    state, current = loaded
    state.update(status="running", error=None, events=[], resuming=True)
    if current in ("validate", "debug"):
        state["debug_attempts"] = 0                      # give the auto-fixer a fresh budget
    for _ in range(MAX_STEPS):
        _merge(state, await wrapped[current](state, config))
        current = route_after(current, state)
        if current == END:
            return state
    raise RuntimeError("Graph exceeded the maximum number of steps.")