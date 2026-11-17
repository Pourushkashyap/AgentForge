
import json
from unittest.mock import AsyncMock, patch

import pytest

from planning.frontend_planner import create_frontend_plan
from planning.schemas import MasterPlan


MASTER_PLAN_DATA = {
    "project_name": "Premium Bakery Web Platform",
    "business_name": "Golden Crust Bakery",
    "project_goal": "Sell bakery products online.",
    "architecture": "React frontend with Node.js backend.",
    "tech_stack": {
        "frontend": ["React", "TypeScript", "Tailwind CSS"],
        "backend": ["Node.js", "Express"],
        "database": ["PostgreSQL"],
        "devops": ["Docker"],
    },
    "features": ["Product listings", "Shopping cart"],
    "tasks": [
        {
            "task_id": "T1",
            "title": "Build product API",
            "description": "Provide product data.",
            "department": "backend",
            "dependencies": [],
            "priority": "high",
        }
    ],
    "assumptions": [],
}



FRONTEND_RESPONSE = {
    "business_name": "Wrong Name",
    "project_name": "Wrong Project",
    "framework": "React",
    "styling": "Tailwind CSS",
    "design_system": {
        "description": "Warm bakery theme",
        "colors": {
            "primary": "#D2691E",
            "secondary": "#F5E0C3",
        },
        "typography": {
            "heading": "Poppins",
            "body": "Open Sans",
        },
        "component_library": "Custom components",
    },
    "pages": [
        {
            "name": "Home",
            "route": "/",
            "purpose": "Showcase bakery products.",
            "components": ["Header", "ProductGrid"],
            "requires_auth": False,
        }
    ],
    "shared_components": ["Header", "Footer", "ProductCard"],
    "state_management": {
        "library": "React Context",
        "stores": ["CartContext"],
    },
    "api_integrations": [
        {
            "name": "Fetch Products",
            "endpoint": "",
            "method": "GET",
            "purpose": "Fetch product listings.",
        }
    ],
    "tasks": [
        {
            "task_id": "FE1",
            "title": "Build homepage",
            "description": "Create responsive homepage.",
            "dependencies": [],
        }
    ],
}


@pytest.mark.asyncio
async def test_frontend_plan_preserves_names():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    with patch(
        "planning.frontend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(FRONTEND_RESPONSE),
    ):
        result = await create_frontend_plan(master_plan)

    assert result.business_name == "Golden Crust Bakery"
    assert result.project_name == "Premium Bakery Web Platform"
    assert result.pages[0].route == "/"
    assert result.tasks[0].task_id == "FE1"


@pytest.mark.asyncio
async def test_duplicate_task_ids_rejected():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    response = {
        **FRONTEND_RESPONSE,
        "tasks": [
            FRONTEND_RESPONSE["tasks"][0],
            {
                **FRONTEND_RESPONSE["tasks"][0],
                "title": "Another task",
            },
        ],
    }

    with patch(
        "planning.frontend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(response),
    ):
        with pytest.raises(ValueError, match="duplicate task IDs"):
            await create_frontend_plan(master_plan)


@pytest.mark.asyncio
async def test_unknown_dependency_rejected():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    response = {
        **FRONTEND_RESPONSE,
        "tasks": [
            {
                **FRONTEND_RESPONSE["tasks"][0],
                "dependencies": ["MISSING"],
            }
        ],
    }

    with patch(
        "planning.frontend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(response),
    ):
        with pytest.raises(ValueError, match="unknown dependencies"):
            await create_frontend_plan(master_plan)


@pytest.mark.asyncio
async def test_circular_dependencies_rejected():
    master_plan = MasterPlan.model_validate(MASTER_PLAN_DATA)

    response = {
        **FRONTEND_RESPONSE,
        "tasks": [
            {
                "task_id": "FE1",
                "title": "Build homepage",
                "description": "Create homepage.",
                "dependencies": ["FE2"],
            },
            {
                "task_id": "FE2",
                "title": "Build product page",
                "description": "Create product page.",
                "dependencies": ["FE1"],
            },
        ],
    }

    with patch(
        "planning.frontend_planner.call_groq",
        new_callable=AsyncMock,
        return_value=json.dumps(response),
    ):
        with pytest.raises(ValueError, match="circular dependency"):
            await create_frontend_plan(master_plan)
            