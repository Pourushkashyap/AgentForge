
import json

from core.groq_client import call_groq
from generation.api_endpoints import normalize_requirement
from planning.frontend_schemas import APIIntegration, FrontendPlan
from planning.prompts import (
    FRONTEND_PLANNER_SYSTEM_PROMPT,
    FRONTEND_PLANNER_USER_PROMPT,
)
from planning.schemas import MasterPlan


def normalize_api_integrations(plan: FrontendPlan) -> FrontendPlan:
    """Normalize CRUD APIs but preserve specialized authentication APIs."""

    cleaned: list[APIIntegration] = []
    seen: set[tuple[str, str]] = set()

    auth_actions = {
        "/api/auth/register",
        "/api/auth/signup",
        "/api/auth/login",
        "/api/auth/logout",
        "/api/auth/me",
        "/api/auth/refresh",
        "/api/users/register",
        "/api/users/login",
    }

    for integration in plan.api_integrations:
        method = str(integration.method or "").strip().upper()
        path = str(integration.endpoint or "").strip().rstrip("/")

        if not path.startswith("/"):
            path = "/" + path

        # Preserve explicitly supported authentication actions.
        if path in auth_actions:
            if method not in {"GET", "POST"}:
                continue

            key = (method, path)
            if key in seen:
                continue
            seen.add(key)

            cleaned.append(
                APIIntegration(
                    name=integration.name,
                    endpoint=path,
                    method=method,
                    purpose=integration.purpose,
                    request_fields=integration.request_fields,
                    response_fields=integration.response_fields,
                    authentication_required=(
                        False
                        if path.endswith(("/register", "/signup", "/login"))
                        else integration.authentication_required
                    ),
                )
            )
            continue

        # Existing CRUD normalization remains unchanged.
        item = normalize_requirement(
            {
                "name": integration.name,
                "method": method,
                "path": path,
                "purpose": integration.purpose,
                "request_fields": integration.request_fields,
                "response_fields": integration.response_fields,
                "authentication_required": (
                    integration.authentication_required
                ),
            }
        )

        if item is None:
            continue

        key = (item["method"], item["path"])
        if key in seen:
            continue
        seen.add(key)

        cleaned.append(
            APIIntegration(
                name=integration.name or item["name"],
                endpoint=item["path"],
                method=item["method"],
                purpose=integration.purpose,
                request_fields=item["request_fields"],
                response_fields=item["response_fields"],
                authentication_required=item["authentication_required"],
            )
        )

    plan.api_integrations = cleaned
    return plan


async def create_frontend_plan(
    master_plan: MasterPlan,
) -> FrontendPlan:
    if not master_plan.tasks:
        raise ValueError("Master plan must contain tasks.")

    payload = master_plan.model_dump()

    # Keep only the information needed by the frontend planner.
    frontend_payload = {
        "business_name": payload.get("business_name"),
        "project_name": payload.get("project_name"),
        "project_goal": payload.get("project_goal"),
        "architecture": payload.get("architecture"),
        "tech_stack": payload.get("tech_stack"),
        "features": payload.get("features", []),
        "tasks": [
            {
                "task_id": task.get("task_id"),
                "title": task.get("title"),
                "description": task.get("description"),
                "dependencies": task.get("dependencies", []),
            }
            for task in payload.get("tasks", [])
            # The frontend planner only needs frontend work items.
            if task.get("department", "frontend") == "frontend"
        ],
    }

    prompt = FRONTEND_PLANNER_USER_PROMPT.format(
        master_plan=json.dumps(frontend_payload, separators=(",", ":"))
    )

    response = await call_groq(
        prompt=prompt,
        system=FRONTEND_PLANNER_SYSTEM_PROMPT,
        max_tokens=4000,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(response)

        # Preserve the authoritative names from Master Planner.
        data["business_name"] = master_plan.business_name
        data["project_name"] = master_plan.project_name

        plan = FrontendPlan.model_validate(data)

    except json.JSONDecodeError as exc:
        raise ValueError(
            "Frontend Planner returned invalid JSON."
        ) from exc

    except Exception as exc:
        if exc.__class__.__name__ == "ValidationError":
            raise ValueError(
                f"Frontend Planner schema validation failed: {exc}"
            ) from exc
        raise

    task_ids = [task.task_id for task in plan.tasks]
    known_ids = set(task_ids)

    if len(task_ids) != len(known_ids):
        raise ValueError("Frontend plan contains duplicate task IDs.")

    for task in plan.tasks:
        missing = set(task.dependencies) - known_ids

        if missing:
            raise ValueError(
                f"Task {task.task_id} has unknown dependencies: "
                f"{sorted(missing)}"
            )

        if task.task_id in task.dependencies:
            raise ValueError(
                f"Task {task.task_id} cannot depend on itself."
            )

    # Detect circular task dependencies.
    graph = {
        task.task_id: task.dependencies
        for task in plan.tasks
    }

    visiting = set()
    visited = set()

    def has_cycle(task_id: str) -> bool:
        if task_id in visiting:
            return True

        if task_id in visited:
            return False

        visiting.add(task_id)

        for dependency in graph[task_id]:
            if has_cycle(dependency):
                return True

        visiting.remove(task_id)
        visited.add(task_id)
        return False

    for task_id in graph:
        if has_cycle(task_id):
            raise ValueError(
                f"Frontend plan contains a circular dependency "
                f"involving task {task_id}."
            )

    # The frontend owns the API contract: keep only servable calls.
    return normalize_api_integrations(plan)
