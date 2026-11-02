"""Generate a connected project: frontend first, backend derived from it.

Order of work
-------------
1. Master plan, then frontend plan. The frontend plan declares the API calls
   its pages need (``api_integrations``).
2. Generate the frontend using that contract. Its browser smoke test is
   skipped for now because no backend exists yet.
3. Read the frontend's real API calls from its source code. Calls the backend
   generator cannot serve are repaired once; extra calls are fine.
4. Backend planner designs the data models from those calls, then the
   reconciler forces the backend to serve exactly the frontend's endpoints
   with the frontend's field names. Nothing else is generated.
5. Generate the backend, seed catalog-like collections with starter data.
6. Validate the frontend's calls against the backend's endpoints, then run
   both together (API smoke test + browser test through the Vite proxy).

Output: <output_dir>/<project-slug>/frontend and .../backend
"""
import re
from pathlib import Path
from typing import Any

from generation.api_contract_validator import validate_api_contract
from generation.api_endpoints import (
    BUILTIN_ENDPOINTS,
    contract_from_frontend_plan,
    merge_requirements,
    requirement_key,
    split_unsupported,
)
from generation.backend_generator import generate_backend_project
from generation.backend_reconciler import reconcile_backend_plan
from generation.connected_runtime import (
    static_routes,
    validate_connected_runtime,
)
from generation.frontend_api_analyzer import analyze_frontend_api_requirements
from generation.frontend_api_repair import repair_unsupported_api_calls
from generation.frontend_generator import (
    _validate_frontend_build,
    generate_frontend,
)
from generation.seed_data import seed_backend_data
from planning.backend_planner import create_backend_plan
from planning.frontend_planner import create_frontend_plan
from planning.master_planner import create_master_plan


def _slugify(value: str) -> str:
    """Create a safe, predictable directory name from a project name."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "generated-project"


def _endpoint_contract(backend_plan) -> list[dict[str, Any]]:
    """Serialize the backend endpoints for validation and seeding."""
    contract = []
    for endpoint in backend_plan.endpoints or []:
        item = (
            endpoint.model_dump()
            if hasattr(endpoint, "model_dump")
            else dict(endpoint)
        )
        contract.append({
            "name": item.get("name", ""),
            "method": str(item.get("method", "GET")).upper(),
            "path": item.get("path", ""),
            "purpose": item.get("purpose", ""),
            "request_fields": item.get("request_fields", []),
            "response_fields": item.get("response_fields", []),
            "authentication_required": item.get(
                "authentication_required", False
            ),
        })
    return contract


def _keys(items: list[dict[str, Any]]) -> set[tuple[str, str]]:
    return {key for key in (requirement_key(i) for i in items) if key}


async def generate_project_pipeline(
    requirements: str,
    output_dir: str | Path = "generated_projects",
    research_report: str = "No research report provided.",
    *,
    overwrite_frontend: bool = False,
    strict_frontend_validation: bool = True,
    seed_data: bool = True,
    validate_connected: bool = True,
    validate_browser: bool = True,
) -> dict[str, Any]:
    """Generate the frontend first, then a backend that serves only its API.

    Args:
        requirements: Business requirements for the project.
        output_dir: Parent directory for generated projects.
        research_report: Optional research text for the master planner.
        overwrite_frontend: Allow replacing an existing frontend folder.
        strict_frontend_validation: Passed through to generate_frontend.
        seed_data: Fill catalog-like collections with starter records.
        validate_connected: Start the backend and smoke-test the API.
        validate_browser: Also run the Playwright check against the live
            backend (only used when validate_connected is True).
    """
    if not isinstance(requirements, str) or not requirements.strip():
        raise ValueError("Project requirements cannot be empty.")

    # ---- 1. plans ---------------------------------------------------------
    master_plan = await create_master_plan(
        requirements=requirements,
        research_report=research_report,
    )
    frontend_plan = await create_frontend_plan(master_plan)

    project_root = Path(output_dir).resolve() / _slugify(master_plan.project_name)
    frontend_parent = project_root / "frontend"
    backend_dir = project_root / "backend"
    frontend_parent.mkdir(parents=True, exist_ok=True)

    # ---- 2. frontend (it defines the API) --------------------------------
    planned_contract = contract_from_frontend_plan(frontend_plan)
    print(f"\n[1/4] Generating frontend ({len(planned_contract)} planned API calls)")
    frontend_dir = await generate_frontend(
        frontend_plan=frontend_plan,
        output_dir=frontend_parent,
        overwrite=overwrite_frontend,
        strict=strict_frontend_validation,
        api_contract=planned_contract or None,
        validate_runtime=False,  # no backend yet; checked in step 4
    )

    # ---- 3. read the API calls the frontend really makes -----------------
    print("\n[2/4] Reading API calls from the generated frontend")
    detected = analyze_frontend_api_requirements(frontend_dir)
    servable, unsupported = split_unsupported(detected)

    if unsupported:
        listing = ", ".join(
            f"{c.get('method')} {c.get('path')}" for c in unsupported
        )
        print(f"Repairing unsupported API calls: {listing}")
        rewritten = await repair_unsupported_api_calls(
            frontend_dir,
            frontend_plan,
            merge_requirements(planned_contract, servable),
            unsupported,
        )
        if rewritten:
            _validate_frontend_build(frontend_dir)
            detected = analyze_frontend_api_requirements(frontend_dir)
            servable, unsupported = split_unsupported(detected)

    if unsupported:
        listing = "\n".join(
            f"- {c.get('method')} {c.get('path')} ({c.get('source_file')})"
            for c in unsupported
        )
        raise ValueError(
            "Generated frontend calls API routes the backend cannot serve:\n"
            + listing
        )

    actual = merge_requirements(servable)
    actual_keys = _keys(actual)
    if actual:
        # The code is the source of truth. Planned fields enrich the calls
        # that exist; planned calls the code never makes are not generated.
        used_plan = [p for p in planned_contract if requirement_key(p) in actual_keys]
        requirements_list = merge_requirements(actual, used_plan)
        unused = [
            f"{p['method']} {p['path']}"
            for p in planned_contract
            if requirement_key(p) not in actual_keys
        ]
        if unused:
            print("Planned but never called (not generated): " + ", ".join(unused))
    else:
        requirements_list = planned_contract
        if planned_contract:
            print(
                "WARNING: no API calls were detected in the frontend source; "
                "falling back to the planned API contract."
            )

    # ---- 4. backend from the frontend's requirements ---------------------
    print(f"\n[3/4] Generating backend ({len(requirements_list)} endpoints)")
    backend_plan = await create_backend_plan(
        master_plan=master_plan,
        frontend_requirements=requirements_list,
    )
    reconciled = reconcile_backend_plan(backend_plan, requirements_list)
    backend_plan = reconciled.plan
    for note in reconciled.notes:
        print(f"  reconcile: {note}")

    api_contract = _endpoint_contract(backend_plan)
    generated_backend_dir = generate_backend_project(
        plan=backend_plan,
        output_dir=backend_dir,
    )

    seed_summary: dict[str, int] = {}
    if seed_data:
        seed_summary = await seed_backend_data(
            generated_backend_dir,
            frontend_dir=frontend_dir,
            business_name=master_plan.business_name,
            project_goal=getattr(master_plan, "project_goal", ""),
            features=list(getattr(master_plan, "features", []) or []),
            endpoints=api_contract,
            resource_collections=reconciled.resource_collections,
        )
        if seed_summary:
            print("  seeded: " + ", ".join(f"{k}={v}" for k, v in seed_summary.items()))

    # ---- 5. contract + connected validation ------------------------------
    print("\n[4/4] Validating frontend against backend")
    contract_result = validate_api_contract(
        frontend_requirements=actual or requirements_list,
        backend_endpoints=api_contract + list(BUILTIN_ENDPOINTS),
    )
    if not contract_result["valid"]:
        missing = "\n".join(
            f"- {item['method']} {item['path']}"
            for item in contract_result["missing_endpoints"]
        )
        raise ValueError(
            "Generated frontend calls APIs that are missing from the backend:\n"
            + missing
        )

    if validate_connected:
        validate_connected_runtime(
            frontend_dir,
            generated_backend_dir,
            api_contract,
            routes=static_routes(frontend_plan),
            run_browser=validate_browser,
        )

    return {
        "project_name": master_plan.project_name,
        "business_name": master_plan.business_name,
        "master_plan": master_plan,
        "frontend_plan": frontend_plan,
        "backend_plan": backend_plan,
        "frontend_requirements": requirements_list,
        "api_contract": api_contract,
        "frontend_dir": Path(frontend_dir).resolve(),
        "backend_dir": Path(generated_backend_dir).resolve(),
        "project_root": project_root,
        "contract_result": contract_result,
        "reconcile_notes": reconciled.notes,
        "seed_summary": seed_summary,
    }
