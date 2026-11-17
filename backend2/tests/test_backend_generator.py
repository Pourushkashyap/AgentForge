
import json

from planning.backend_schemas import BackendPlan
from generation.backend_generator import generate_backend_project


def make_plan():
    return BackendPlan(
        business_name="Sweet Oven",
        project_name="Sweet Oven E-Commerce Platform",
        framework="FastAPI",
        architecture="FastAPI with JSON file storage",
        storage_type="json",
        storage_path="data/",
        database_models=[
            {
                "name": "Product",
                "description": "Bakery products",
                "fields": [
                    {
                        "name": "id",
                        "data_type": "string",
                        "required": True,
                        "description": "Product ID",
                    },
                    {
                        "name": "name",
                        "data_type": "string",
                        "required": True,
                        "description": "Product name",
                    },
                ],
                "relationships": [],
                "indexes": [],
            }
        ],
        tasks=[
            {
                "task_id": "BE1",
                "title": "Initialize backend",
                "description": "Create the backend project",
                "dependencies": [],
            }
        ],
    )


def test_generates_project_files(tmp_path):
    output = generate_backend_project(make_plan(), tmp_path / "generated")

    assert (output / "main.py").is_file()
    assert (output / "requirements.txt").is_file()
    assert (output / "README.md").is_file()
    assert (output / "storage" / "json_store.py").is_file()


def test_initializes_json_collection(tmp_path):
    output = generate_backend_project(make_plan(), tmp_path / "generated")

    products_file = output / "data" / "product.json"

    assert products_file.is_file()
    assert json.loads(products_file.read_text(encoding="utf-8")) == []


def test_manifest_declares_json_storage(tmp_path):
    output = generate_backend_project(make_plan(), tmp_path / "generated")

    manifest = json.loads(
        (output / "generation_manifest.json").read_text(encoding="utf-8")
    )

    assert manifest["storage_type"] == "json"
    assert manifest["storage_path"] == "data/"
    assert manifest["business_name"] == "Sweet Oven"


def test_existing_data_is_not_overwritten(tmp_path):
    output = tmp_path / "generated"
    data_dir = output / "data"
    data_dir.mkdir(parents=True)
    existing = data_dir / "product.json"
    existing.write_text(
        '[{"id": "p1", "name": "Chocolate Cake"}]',
        encoding="utf-8",
    )

    generate_backend_project(make_plan(), output)

    assert json.loads(existing.read_text(encoding="utf-8")) == [
        {"id": "p1", "name": "Chocolate Cake"}
    ]


import ast


def test_generated_manifest_includes_planned_endpoints(tmp_path):
    plan = make_plan()
    
    
    plan.endpoints = [
        {
            "name": "List products",
            "method": "GET",
            "path": "/api/products",
            "purpose": "List available bakery products",
            "response_fields": ["id", "name"],
        },
        {
            "name": "Create product",
            "method": "POST",
            "path": "/api/products",
            "purpose": "Create a bakery product",
            "response_fields": ["id", "name"],
        },
    ]



    output = generate_backend_project(plan, tmp_path / "generated")
    manifest = json.loads(
        (output / "generation_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    assert manifest["endpoints"] == [
        {
            "name": "List products",
            "method": "GET",
            "path": "/api/products",
            "purpose": "List available bakery products",
            "authentication_required": False,
            "request_fields": [],
            "response_fields": ["id", "name"],
        },
        {
            "name": "Create product",
            "method": "POST",
            "path": "/api/products",
            "purpose": "Create a bakery product",
            "authentication_required": False,
            "request_fields": [],
            "response_fields": ["id", "name"],
        },
    ]


def test_generated_backend_python_is_valid(tmp_path):
    output = generate_backend_project(
        make_plan(), tmp_path / "generated"
    )

    ast.parse(
        (output / "main.py").read_text(encoding="utf-8")
    )



def test_generated_routes_follow_backend_plan(tmp_path):
    plan = make_plan()
    plan.endpoints = [
    {
        "name": "List products",
        "method": "GET",
        "path": "/api/products",
        "purpose": "List available bakery products",
        "response_fields": ["id", "name"],
    },
    {
        "name": "Create product",
        "method": "POST",
        "path": "/api/products",
        "purpose": "Create a bakery product",
        "response_fields": ["id", "name"],
    },
]

    output = generate_backend_project(
        plan, tmp_path / "generated"
    )
    source = (output / "main.py").read_text(encoding="utf-8")

    assert '@app.get("/api/products")' in source
    assert '@app.post("/api/products"' in source
    assert '@app.get("/api/{collection}")' not in source
    assert '@app.delete("/api/{collection}/{record_id}")' not in source
