import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from generation.backend_generator import generate_backend_project
from tests.test_backend_generator import make_plan


def test_generated_backend_api_supports_planned_routes(tmp_path):
    plan = make_plan()
    
    plan.endpoints = [
        {
            "name": "List products",
            "method": "GET",
            "path": "/api/products",
            "purpose": "List products",
            "response_fields": ["id", "name"],
        },
        {
            "name": "Create product",
            "method": "POST",
            "path": "/api/products",
            "purpose": "Create a product",
            "response_fields": ["id", "name"],
        },
    ]


    output = generate_backend_project(
        plan, tmp_path / "generated"
    )

    module_name = "generated_backend_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name, output / "main.py"
    )
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module

    try:
        spec.loader.exec_module(module)
        client = TestClient(module.app)

        # The collection starts empty.
        response = client.get("/api/products")
        assert response.status_code == 200
        assert response.json() == []

        # Create a product through the generated HTTP endpoint.
        response = client.post(
            "/api/products",
            json={"name": "Chocolate Cake"},
        )
        assert response.status_code in (200, 201)
        created = response.json()
        assert created["name"] == "Chocolate Cake"
        assert created["id"]

        # Confirm that the created record is persisted and returned.
        response = client.get("/api/products")
        assert response.status_code == 200
        assert any(
            product["id"] == created["id"]
            for product in response.json()
        )

        # Generic CRUD routes should not be present.
        assert client.get("/api/unplanned_collection").status_code == 404

    finally:
        sys.modules.pop(module_name, None)
