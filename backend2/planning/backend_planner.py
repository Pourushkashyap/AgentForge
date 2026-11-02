
import json

from pydantic import ValidationError

from core.groq_client import call_groq
from planning.backend_schemas import BackendPlan
from planning.prompts import (
    BACKEND_PLANNER_SYSTEM_PROMPT,
    BACKEND_PLANNER_USER_PROMPT,
)
from planning.schemas import MasterPlan


def normalize_endpoint_fields(data: dict) -> dict:
    """Convert endpoint field objects into the strings expected by the schema."""
    endpoints = data.get("endpoints", [])

    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue

        for key in ("request_fields", "response_fields"):
            fields = endpoint.get(key, [])

            if not isinstance(fields, list):
                continue

            normalized = []
            for field in fields:
                if isinstance(field, dict):
                    name = str(field.get("name", "")).strip()
                    field_type = str(field.get("type", "")).strip()
                    description = str(field.get("description", "")).strip()

                    if name:
                        value = f"{name} ({field_type})" if field_type else name
                        if description:
                            value += f": {description}"
                        normalized.append(value)
                elif isinstance(field, str):
                    normalized.append(field)

            endpoint[key] = normalized

    return data


async def create_backend_plan(
    master_plan: MasterPlan,
    frontend_requirements: list[dict] | None = None,
) -> BackendPlan:

    if not master_plan.tasks:
        raise ValueError("Master plan must contain tasks.")

    
    
    # Keep the prompt focused on information required to build the backend.
    master_data = master_plan.model_dump()

    prompt = BACKEND_PLANNER_USER_PROMPT.format(
        master_plan=json.dumps(
            master_data,
            separators=(",", ":"),
        ),
        frontend_requirements=json.dumps(
            frontend_requirements or [],
            separators=(",", ":"),
        ),
    )

    response = await call_groq(
        prompt=prompt,
        system=BACKEND_PLANNER_SYSTEM_PROMPT,
        max_tokens=4500,
        temperature=0.2,
        response_format={"type": "json_object"},
    )


    try:
        data = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Backend Planner returned invalid plan JSON. "
            "The response may be truncated."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError(
            "Backend Planner response must be a JSON object."
        )

    # Master Plan is authoritative for project identity.
    data["business_name"] = master_plan.business_name
    data["project_name"] = master_plan.project_name
    data = normalize_endpoint_fields(data)
    # AgentForge always uses JSON file-based storage.
    # Ignore any external database choice returned by the LLM.
    data["storage_type"] = "json"
    data["storage_path"] = "data/"

    try:
        plan = BackendPlan.model_validate(data)
    except ValidationError as exc:
        raise ValueError(
            f"Backend Planner schema validation failed: {exc}"
        ) from exc

    # Validate task IDs.
    task_ids = [task.task_id for task in plan.tasks]
    known_ids = set(task_ids)

    if len(task_ids) != len(known_ids):
        raise ValueError(
            "Backend plan contains duplicate task IDs."
        )

    # Validate dependencies.
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

    # Detect cycles in the dependency graph.
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
                "Backend plan contains a circular dependency "
                f"involving task {task_id}."
            )

    return plan
