
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from planning.master_planner import create_master_plan
from planning.schemas import MasterPlan


VALID_PLAN = {
    "project_name": "Handmade E-commerce",
    "business_name": "ArtisanNest",
    "project_goal": "Sell handmade products online.",
    "architecture": "React frontend with FastAPI backend.",
    "tech_stack": {
        "frontend": "React",
        "backend": "FastAPI",
        "database": "PostgreSQL",
    },
    "features": [
        "User authentication",
        "Product catalog",
        "Shopping cart",
        "Checkout",
    ],
    "tasks": [
        {
            "task_id": "TASK-1",
            "title": "Create database schema",
            "description": "Define users and products tables.",
            "department": "database",
            "dependencies": [],
            "priority": "high",
        },
        {
            "task_id": "TASK-2",
            "title": "Build product API",
            "description": "Create product listing endpoints.",
            "department": "backend",
            "dependencies": ["TASK-1"],
            "priority": "high",
        },
    ],
    "assumptions": ["Payment provider will be selected later."],
}


@pytest.mark.asyncio
async def test_create_master_plan_success():
    with patch(
        "planning.master_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(VALID_PLAN),
    ) as mock_groq:
        result = await create_master_plan(
            requirements="Build an e-commerce website.",
            research_report="Handmade products have several competitors.",
        )

    assert isinstance(result, MasterPlan)
    assert result.project_name == "Handmade E-commerce"
    assert len(result.tasks) == 2
    assert result.tasks[1].dependencies == ["TASK-1"]
    mock_groq.assert_awaited_once()


@pytest.mark.asyncio
async def test_empty_requirements():
    with pytest.raises(ValueError, match="cannot be empty"):
        await create_master_plan("   ")


@pytest.mark.asyncio
async def test_invalid_json():
    with patch(
        "planning.master_planner.call_groq",
        new_callable=AsyncMock,
        return_value="not valid json",
    ):
        with pytest.raises(ValueError, match="invalid plan JSON"):
            await create_master_plan("Build a portfolio website.")


@pytest.mark.asyncio
async def test_unknown_dependency():
    invalid_plan = {
        **VALID_PLAN,
        "tasks": [
            {
                **VALID_PLAN["tasks"][0],
                "dependencies": ["TASK-UNKNOWN"],
            }
        ],
    }

    with patch(
        "planning.master_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(invalid_plan),
    ):
        with pytest.raises(ValueError, match="unknown dependencies"):
            await create_master_plan("Build a website.")


@pytest.mark.asyncio
async def test_duplicate_task_ids():
    duplicate_plan = {
        **VALID_PLAN,
        "tasks": [
            VALID_PLAN["tasks"][0],
            {
                **VALID_PLAN["tasks"][1],
                "task_id": "TASK-1",
            },
        ],
    }

    with patch(
        "planning.master_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(duplicate_plan),
    ):
        with pytest.raises(ValueError, match="duplicate task IDs"):
            await create_master_plan("Build a website.")


@pytest.mark.asyncio
async def test_self_dependency():
    invalid_plan = {
        **VALID_PLAN,
        "tasks": [
            {
                **VALID_PLAN["tasks"][0],
                "dependencies": ["TASK-1"],
            }
        ],
    }

    with patch(
        "planning.master_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(invalid_plan),
    ):
        with pytest.raises(ValueError, match="cannot depend on itself"):
            await create_master_plan("Build a website.")


async def run_live_test():
    plan = await create_master_plan(
        requirements=(
            "Build a premium bakery website with product listings, "
            "shopping cart, admin dashboard and user authentication."
        ),
        research_report=(
            "The business sells artisan bakery products online."
        ),
    )

    print(json.dumps(plan.model_dump(), indent=2))


if __name__ == "__main__":
    asyncio.run(run_live_test())
