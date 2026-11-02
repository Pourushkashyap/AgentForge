import json

from core.groq_client import call_groq
from planning.prompts import (
    MASTER_PLANNER_SYSTEM_PROMPT,
    MASTER_PLANNER_USER_PROMPT,
)
from planning.schemas import MasterPlan


async def create_master_plan(
    requirements: str,
    research_report: str = "No research report provided.",
) -> MasterPlan:
    if not requirements.strip():
        raise ValueError("Project requirements cannot be empty.")

    prompt = MASTER_PLANNER_USER_PROMPT.format(
        requirements=requirements,
        research_report=research_report,
    )

    response = await call_groq(
        prompt=prompt,
        system=MASTER_PLANNER_SYSTEM_PROMPT,
        max_tokens=6000,
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    try:
        data = json.loads(response)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Master Planner returned invalid plan JSON. "
            "The response may be truncated."
        ) from exc

    if not isinstance(data, dict):
        raise ValueError("Master Planner response must be a JSON object.")
    
    print("DEBUG: Master Planner response keys:", list(data.keys()))
    print("DEBUG: business_name =", repr(data.get("business_name")))
    print("DEBUG: project_name =", repr(data.get("project_name")))
    
    
    if not isinstance(data.get("business_name"), str) or not data[
        "business_name"
    ].strip():
        project_name = data.get("project_name")

        if isinstance(project_name, str) and project_name.strip():
            # Example: "Sweet Oven E-commerce Platform" -> "Sweet Oven"
            suffixes = (
                " E-commerce Platform",
                " Ecommerce Platform",
                " E-commerce Website",
                " Ecommerce Website",
                " Website",
                " Web Application",
                " Application",
                " Platform",
            )

            business_name = project_name.strip()

            for suffix in suffixes:
                if business_name.lower().endswith(suffix.lower()):
                    business_name = business_name[:-len(suffix)].strip()
                    break

            if business_name:
                data["business_name"] = business_name
                print(
                    "WARNING: business_name missing; inferred from "
                    f"project_name: {business_name!r}"
                )
            else:
                raise ValueError(
                    "Master Planner omitted business_name and it could "
                    "not be inferred from project_name."
                )
        else:
            raise ValueError(
                "Master Planner omitted business_name and returned no "
                "usable project_name for fallback."
            )

    try:
        plan = MasterPlan.model_validate(data)
    except Exception as exc:
        if exc.__class__.__name__ == "ValidationError":
            raise ValueError(
                f"Master Planner schema validation failed: {exc}"
            ) from exc
        raise

    task_ids = {task.task_id for task in plan.tasks}

    if len(task_ids) != len(plan.tasks):
        raise ValueError("Master plan contains duplicate task IDs.")

    for task in plan.tasks:
        missing = set(task.dependencies) - task_ids

        if missing:
            raise ValueError(
                f"Task {task.task_id} has unknown dependencies: "
                f"{sorted(missing)}"
            )

        if task.task_id in task.dependencies:
            raise ValueError(
                f"Task {task.task_id} cannot depend on itself."
            )

    return plan