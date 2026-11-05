"""Graph nodes. Heavy imports are lazy so the graph can be built and tested
without the LLM stack installed.

Every node takes (state, config) and returns a *partial* state update.
Exceptions never escape: they become ``error`` + ``status="failed"`` and the
graph routes to the ``fail`` node.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from workflow.debugger import debug_project
from workflow.state import resolve_options
from workflow.validators import (
    check_authentication,
    check_backend_static,
    check_contract,
    check_frontend_static,
    check_runtime,
    has_errors,
    scan_for_secrets,
    summarize_issues,
)

logger = logging.getLogger("agentforge.workflow")

DEFAULT_RESEARCH = "No research report provided."
MAX_LIST_ITEMS = 8


def _event(node: str, message: str, level: str = "info", **data: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "ts": time.time(), "node": node, "level": level, "message": message,
    }
    if data:
        event["data"] = data
    return event


def _emit(config: Any, event: dict[str, Any]) -> None:
    callback = ((config or {}).get("configurable") or {}).get("on_event")
    if callback:
        try:
            callback(event)
        except Exception:  # a broken listener must not break the build
            logger.exception("on_event callback failed")


def graph_node(name: str):
    """Wrap a node: start event, error capture, event emission."""
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(state, config=None):
            start = _event(name, f"{name} started")
            _emit(config, start)
            try:
                update = dict(await fn(state, config) or {})
            except Exception as exc:
                logger.exception("node %s failed", name)
                failure = _event(name, f"{name} failed: {exc}", "error")
                _emit(config, failure)
                return {
                    "error": f"{name}: {exc}", "status": "failed",
                    "current_node": name, "events": [start, failure],
                }
            extra = list(update.pop("events", []))
            for event in extra:
                _emit(config, event)
            update["events"] = [start] + extra
            update["current_node"] = name
            return update
        return wrapper
    return decorator


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "generated-project"


def _research_text(report: Any) -> str:
    def block(title: str, items: list[str]) -> str:
        items = [str(i)[:200] for i in (items or [])[:MAX_LIST_ITEMS]]
        return f"{title}:\n" + "\n".join(f"- {i}" for i in items) if items else ""

    parts = [
        block("Opportunities", getattr(report, "combined_opportunities", [])),
        block("Risks", getattr(report, "combined_risks", [])),
        block("Limitations", getattr(report, "limitations", [])),
    ]
    return "\n\n".join(p for p in parts if p) or DEFAULT_RESEARCH


# --------------------------------------------------------------------------
@graph_node("research")
async def research_node(state, config=None):
    options = resolve_options(state.get("options"))
    if not options["research"]:
        return {"research_report": DEFAULT_RESEARCH,
                "events": [_event("research", "Research disabled; skipped")]}
    from research.research_department import run_research_department

    try:
        report = await run_research_department(
            business_idea=state["requirements"],
            market_scope=options["market_scope"],
        )
    except Exception as exc:  # research is helpful, never required
        return {"research_report": DEFAULT_RESEARCH,
                "events": [_event("research", f"Research failed, continuing: {exc}",
                                  "warning")]}
    return {"research_report": _research_text(report),
            "events": [_event("research", "Research complete")]}


@graph_node("master_plan")
async def master_plan_node(state, config=None):
    from planning.master_planner import create_master_plan

    plan = await create_master_plan(
        requirements=state["requirements"],
        research_report=state.get("research_report") or DEFAULT_RESEARCH,
    )
    root = Path(state["output_dir"]).resolve() / _slugify(plan.project_name)
    (root / "frontend").mkdir(parents=True, exist_ok=True)
    return {"master_plan": plan, "project_root": str(root),
            "events": [_event("master_plan", f"Planned {plan.project_name}")]}


@graph_node("frontend_plan")
async def frontend_plan_node(state, config=None):
    from generation.api_endpoints import contract_from_frontend_plan
    from planning.frontend_planner import create_frontend_plan

    plan = await create_frontend_plan(state["master_plan"])
    contract = contract_from_frontend_plan(plan)
    return {"frontend_plan": plan, "planned_contract": contract,
            "events": [_event("frontend_plan",
                              f"{len(plan.pages)} pages, {len(contract)} planned API calls")]}


@graph_node("frontend_build")
async def frontend_build_node(state, config=None):
    from generation.frontend_generator import generate_frontend

    options = resolve_options(state.get("options"))
    contract = state.get("planned_contract") or None
    frontend_dir = await generate_frontend(
        frontend_plan=state["frontend_plan"],
        output_dir=Path(state["project_root"]) / "frontend",
        overwrite=True,  # job directories are unique, so this is safe
        strict=options["strict_frontend_validation"],
        api_contract=contract,
        validate_runtime=False,  # no backend yet; checked in `validate`
        resume=bool(state.get("resuming")),  # reuse manifest + files already on disk
    )
    return {"frontend_dir": str(Path(frontend_dir).resolve()),
            "events": [_event("frontend_build", "Frontend generated")]}


@graph_node("api_analysis")
async def api_analysis_node(state, config=None):
    """Read the API calls the generated frontend really makes."""
    from generation.api_endpoints import (
        merge_requirements, requirement_key, split_unsupported)
    from generation.frontend_api_analyzer import analyze_frontend_api_requirements
    from generation.frontend_api_repair import repair_unsupported_api_calls
    from generation.frontend_generator import _validate_frontend_build

    frontend_dir = Path(state["frontend_dir"])
    planned = state.get("planned_contract") or []
    detected = analyze_frontend_api_requirements(frontend_dir)
    servable, unsupported = split_unsupported(detected)
    events = []

    if unsupported:
        listing = ", ".join(f"{c.get('method')} {c.get('path')}" for c in unsupported)
        events.append(_event("api_analysis", f"Repairing unsupported calls: {listing}",
                             "warning"))
        rewritten = await repair_unsupported_api_calls(
            frontend_dir, state["frontend_plan"],
            merge_requirements(planned, servable), unsupported)
        if rewritten:
            _validate_frontend_build(frontend_dir)
            detected = analyze_frontend_api_requirements(frontend_dir)
            servable, unsupported = split_unsupported(detected)

    if unsupported:
        listing = "\n".join(f"- {c.get('method')} {c.get('path')} ({c.get('source_file')})"
                            for c in unsupported)
        raise ValueError("Frontend calls API routes the backend cannot serve:\n" + listing)

    actual = merge_requirements(servable)
    keys = {requirement_key(i) for i in actual}
    if actual:
        used = [p for p in planned if requirement_key(p) in keys]
        requirements_list = merge_requirements(actual, used)
    else:
        requirements_list = planned
        events.append(_event("api_analysis",
                             "No API calls detected; using the planned contract",
                             "warning"))
    events.append(_event("api_analysis", f"{len(requirements_list)} endpoints required"))
    return {"actual_requirements": actual, "requirements_list": requirements_list,
            "events": events}


@graph_node("backend_plan")
async def backend_plan_node(state, config=None):
    from generation.backend_reconciler import reconcile_backend_plan
    from planning.backend_planner import create_backend_plan

    plan = await create_backend_plan(
        master_plan=state["master_plan"],
        frontend_requirements=state["requirements_list"],
    )
    reconciled = reconcile_backend_plan(plan, state["requirements_list"])
    events = [_event("backend_plan", f"reconcile: {n}") for n in reconciled.notes]
    events.append(_event("backend_plan", "Backend planned"))
    return {"backend_plan": reconciled.plan,
            "resource_collections": reconciled.resource_collections,
            "events": events}


def _endpoint_contract(backend_plan) -> list[dict[str, Any]]:
    contract = []
    for endpoint in backend_plan.endpoints or []:
        item = endpoint.model_dump() if hasattr(endpoint, "model_dump") else dict(endpoint)
        contract.append({
            "name": item.get("name", ""),
            "method": str(item.get("method", "GET")).upper(),
            "path": item.get("path", ""),
            "purpose": item.get("purpose", ""),
            "request_fields": item.get("request_fields", []),
            "response_fields": item.get("response_fields", []),
            "authentication_required": item.get("authentication_required", False),
        })
    return contract


@graph_node("backend_build")
async def backend_build_node(state, config=None):
    from generation.backend_generator import generate_backend_project
    from generation.seed_data import seed_backend_data

    options = resolve_options(state.get("options"))
    plan, master = state["backend_plan"], state["master_plan"]
    contract = _endpoint_contract(plan)
    backend_dir = generate_backend_project(
        plan=plan, output_dir=Path(state["project_root"]) / "backend")

    seed_summary: dict[str, int] = {}
    if options["seed_data"]:
        seed_summary = await seed_backend_data(
            backend_dir,
            frontend_dir=state["frontend_dir"],
            business_name=master.business_name,
            project_goal=getattr(master, "project_goal", ""),
            features=list(getattr(master, "features", []) or []),
            endpoints=contract,
            resource_collections=state.get("resource_collections"),
        )
    return {"backend_dir": str(Path(backend_dir).resolve()),
            "api_contract": contract, "seed_summary": seed_summary,
            "events": [_event("backend_build",
                              f"Backend generated with {len(contract)} endpoints")]}


@graph_node("validate")
async def validate_node(state, config=None):
    options = resolve_options(state.get("options"))
    frontend_dir, backend_dir = state["frontend_dir"], state["backend_dir"]

    issues = check_backend_static(backend_dir)
    issues += check_frontend_static(frontend_dir)
    issues += check_authentication(
        frontend_dir, state.get("frontend_plan"), state.get("api_contract") or []
    )
    issues += scan_for_secrets(state["project_root"])
    issues += check_contract(
        state.get("actual_requirements") or state.get("requirements_list") or [],
        state.get("api_contract") or [])

    if not has_errors(issues) and options["validate_connected"]:
        from generation.connected_runtime import static_routes

        issues += await asyncio.to_thread(
            check_runtime, frontend_dir, backend_dir, state.get("api_contract") or [],
            static_routes(state["frontend_plan"]),
            run_browser=options["validate_browser"])

    errors = sum(1 for i in issues if i["severity"] == "error")
    warnings = len(issues) - errors
    level = "error" if errors else "info"
    return {"issues": issues,
            "events": [_event("validate", f"{errors} errors, {warnings} warnings",
                              level, errors=errors, warnings=warnings)]}


@graph_node("debug")
async def debug_node(state, config=None):
    attempt = int(state.get("debug_attempts", 0)) + 1
    fixes = await debug_project(
        state.get("issues") or [], state["backend_dir"], state["frontend_dir"])
    previous_fixes = list(state.get("fixes") or [])
    all_fixes = previous_fixes + fixes
    applied = sum(1 for item in fixes if item.get("method") in {"llm", "deterministic"})
    rejected = [item for item in fixes if item.get("code") in {"llm_rejected", "llm_error"}]
    detail = f"Attempt {attempt}: {applied} repair(s) applied"
    if rejected:
        detail += f", {len(rejected)} repair issue(s)"
    return {"debug_attempts": attempt, "fixes": all_fixes,
            "events": [_event("debug", detail,
                              "warning" if not applied or rejected else "info",
                              fixes=fixes)]}


@graph_node("finalize")
async def finalize_node(state, config=None):
    issues = state.get("issues") or []
    status = "completed_with_warnings" if issues else "completed"
    master = state["master_plan"]
    result = {
        "project_name": master.project_name,
        "business_name": master.business_name,
        "project_root": state["project_root"],
        "frontend_dir": state["frontend_dir"],
        "backend_dir": state["backend_dir"],
        "api_contract": state.get("api_contract") or [],
        "seed_summary": state.get("seed_summary") or {},
        "issues": issues,
        "fixes": state.get("fixes") or [],
        "debug_attempts": state.get("debug_attempts", 0),
    }
    Path(state["project_root"], "pipeline_report.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8")
    return {"status": status, "result": result,
            "events": [_event("finalize", f"Project ready ({status})")]}


@graph_node("fail")
async def fail_node(state, config=None):
    error = state.get("error")
    if not error:
        attempts = state.get("debug_attempts", 0)
        error = (f"Validation failed after {attempts} debug attempt(s):\n"
                 + summarize_issues(state.get("issues") or []))
    return {"status": "failed", "error": error,
            "events": [_event("fail", "Pipeline failed", "error")]}


NODES = {
    "research": research_node, "master_plan": master_plan_node,
    "frontend_plan": frontend_plan_node, "frontend_build": frontend_build_node,
    "api_analysis": api_analysis_node, "backend_plan": backend_plan_node,
    "backend_build": backend_build_node, "validate": validate_node,
    "debug": debug_node, "finalize": finalize_node, "fail": fail_node,
}