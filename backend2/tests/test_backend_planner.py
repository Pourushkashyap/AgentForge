
import json
from unittest.mock import AsyncMock, patch

import pytest

from planning.backend_planner import create_backend_plan
from planning.schemas import MasterPlan


MASTER_PLAN_DATA = {
    "project_name": "Sweet Oven E-Commerce Platform",
    "business_name": "Sweet Oven",
    "project_goal": "Sell bakery products online.",
    "architecture": "React frontend with backend API.",
    "tech_stack": {
        "frontend": ["React", "TypeScript", "Tailwind CSS"],
        "backend": ["FastAPI"],
        "database": ["PostgreSQL"],
        "devops": ["Docker"],
    },
    "features": ["Product listings", "Shopping cart"],
    "tasks": [
        {
            "task_id": "T1",
            "title": "Implement product feature",
            "description": "Support product listings.",
            "department": "backend",
            "dependencies": [],
            "priority": "high",
        }
    ],
    "assumptions": [],
}


BACKEND_RESPONSE = {
    "business_name": "Wrong Name",
    "project_name": "Wrong Project",
    "framework": "FastAPI",
    "architecture": "REST API with PostgreSQL",
    "authentication": ["JWT authentication"],
    "security_requirements": ["Validate request input"],
    "endpoints": [
        {
            "name": "List Products",
            "method": "GET",
            "path": "/api/products",
            "purpose": "Return product listings.",
            "authentication_required": False,
            "request_fields": [],
            "response_fields": ["id", "name", "price"],
        }
    ],
    "database_models": [
        {
            "name": "Product",
            "description": "A bakery product.",
            "fields": [
                {
                    "name": "id",
                    "data_type": "UUID",
                    "required": True,
                    "description": "Unique identifier.",
                },
                {
                    "name": "name",
                    "data_type": "string",
                    "required": True,
                    "description": "Product name.",
                },
            ],
            "relationships": [
                {
                    "type": "many-to-one",
                    "related_model": "Category",
                    "field": "category_id",
                }
            ],
            "indexes": [
                {
                    "fields": ["id"],
                    "unique": True,
                }
            ],
        }
    ],
    "tasks": [
        {
            "task_id": "BE1",
            "title": "Create product model",
            "description": "Define the Product model.",
            "dependencies": [],
        },
        {
            "task_id": "BE2",
            "title": "Implement product endpoint",
            "description": "Create the product listing API.",
            "dependencies": ["BE1"],
        },
    ],
    "assumptions": [],
}


@pytest.mark.asyncio
async def test_backend_plan_preserves_names():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    with patch(
        "planning.backend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(BACKEND_RESPONSE),
    ):
        result = await create_backend_plan(master_plan)

    assert result.business_name == "Sweet Oven"
    assert result.project_name == "Sweet Oven E-Commerce Platform"
    assert result.framework == "FastAPI"
    assert result.endpoints[0].path == "/api/products"
    assert result.tasks[1].dependencies == ["BE1"]


@pytest.mark.asyncio
async def test_duplicate_task_ids_rejected():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    response = {
        **BACKEND_RESPONSE,
        "tasks": [
            BACKEND_RESPONSE["tasks"][0],
            {
                **BACKEND_RESPONSE["tasks"][0],
                "title": "Duplicate task",
            },
        ],
    }

    with patch(
        "planning.backend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(response),
    ):
        with pytest.raises(ValueError, match="duplicate task IDs"):
            await create_backend_plan(master_plan)


@pytest.mark.asyncio
async def test_unknown_dependency_rejected():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    response = {
        **BACKEND_RESPONSE,
        "tasks": [
            {
                **BACKEND_RESPONSE["tasks"][0],
                "dependencies": ["MISSING"],
            }
        ],
    }

    with patch(
        "planning.backend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(response),
    ):
        with pytest.raises(ValueError, match="unknown dependencies"):
            await create_backend_plan(master_plan)


@pytest.mark.asyncio
async def test_circular_dependencies_rejected():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    response = {
        **BACKEND_RESPONSE,
        "tasks": [
            {
                **BACKEND_RESPONSE["tasks"][0],
                "dependencies": ["BE2"],
            },
            {
                **BACKEND_RESPONSE["tasks"][1],
                "dependencies": ["BE1"],
            },
        ],
    }

    with patch(
        "planning.backend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(response),
    ):
        with pytest.raises(ValueError, match="circular dependency"):
            await create_backend_plan(master_plan)
