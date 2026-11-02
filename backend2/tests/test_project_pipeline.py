"""Frontend-first pipeline: the backend must serve exactly the frontend's API."""
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from generation.connected_runtime import running_backend
from generation.project_pipeline import generate_project_pipeline
from planning.backend_schemas import (
    BackendEndpoint,
    BackendPlan,
    BackendTask,
    DatabaseField,
    DatabaseModel,
)
from planning.frontend_schemas import FrontendPlan

GOOD_API = """
export async function getProducts() {
  const res = await fetch('/api/products');
  if (!res.ok) throw new Error('failed');
  return res.json();
}
export async function getProduct(id) {
  const res = await fetch(`/api/products/${id}`);
  if (!res.ok) throw new Error('failed');
  return res.json();
}
export async function createOrder(customerName, total, items) {
  const res = await fetch('/api/orders', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ customerName: customerName, total: total, items: items }),
  });
  if (!res.ok) throw new Error('failed');
  return res.json();
}
"""

BAD_API = GOOD_API + """
export const addItem = (id) => fetch(`/api/orders/${id}/items`, { method: 'POST' });
"""


def _frontend_plan():
    return FrontendPlan.model_validate({
        "business_name": "Demo",
        "project_name": "Demo Bakery",
        "framework": "React",
        "styling": "Tailwind",
        "design_system": {"description": "warm", "colors": {"primary": "#aa5500"}},
        "pages": [
            {"name": "Home", "route": "/", "purpose": "home"},
            {"name": "Menu", "route": "/menu", "purpose": "menu"},
            {"name": "Item", "route": "/menu/:id", "purpose": "item"},
        ],
        "state_management": {"library": "React Context", "stores": ["cart"]},
        "api_integrations": [
            {"name": "List products", "endpoint": "/api/products", "method": "GET",
             "response_fields": ["id", "name", "price", "imageUrl"]},
            {"name": "Create order", "endpoint": "/api/orders", "method": "POST",
             "request_fields": ["customerName", "total", "items"]},
            {"name": "Login", "endpoint": "/api/login", "method": "POST"},
        ],
        "tasks": [{"task_id": "FE1", "title": "t", "description": "d"}],
    })


def _backend_plan():
    return BackendPlan(
        business_name="Demo",
        project_name="Demo Bakery",
        framework="FastAPI",
        architecture="REST",
        endpoints=[
            BackendEndpoint(name="x", method="POST", path="/api/auth/login", purpose="x"),
            BackendEndpoint(name="y", method="GET", path="/api/categories", purpose="y"),
        ],
        database_models=[
            DatabaseModel(name="Product", description="p", fields=[
                DatabaseField(name="id", data_type="uuid"),
                DatabaseField(name="name", data_type="string"),
                DatabaseField(name="price", data_type="float"),
                DatabaseField(name="image_url", data_type="string"),
            ]),
            DatabaseModel(name="Order", description="o", fields=[
                DatabaseField(name="customer_name", data_type="string", required=True),
                DatabaseField(name="total", data_type="float", required=True),
                DatabaseField(name="notes", data_type="string", required=True),
            ]),
        ],
        tasks=[BackendTask(task_id="BE1", title="t", description="d")],
    )


SEED = {"product": [
    {
        "id": "p1",
        "name": "Croissant",
        "price": 3.5,
        "imageUrl": "/images/croissant.jpg",
    },
    {
        "id": "p2",
        "name": "Sourdough",
        "price": "6",
        "imageUrl": "/images/sourdough.jpg",
        "bogus": 1,
    },
]}


def _master():
    return SimpleNamespace(
        project_name="Demo Bakery", business_name="Demo",
        project_goal="Sell bread online", features=["catalog", "orders"], tasks=[],
    )


def _fake_frontend(api_source):
    async def fake(*, frontend_plan, output_dir, overwrite, strict,
                   api_contract, validate_runtime):
        assert validate_runtime is False, "no backend exists yet"
        assert api_contract, "frontend must receive its own API contract"
        assert {(c["method"], c["path"]) for c in api_contract} == {
            ("GET", "/api/products"), ("POST", "/api/orders")}  # login dropped
        directory = Path(output_dir) / "demo-bakery"
        (directory / "src" / "api").mkdir(parents=True)
        (directory / "src" / "api" / "productsApi.js").write_text(api_source)
        return directory
    return fake


def _patches(api_source, tmp_path):
    return (
        patch("generation.project_pipeline.create_master_plan",
              new=AsyncMock(return_value=_master())),
        patch("generation.project_pipeline.create_frontend_plan",
              new=AsyncMock(return_value=_frontend_plan())),
        patch("generation.project_pipeline.generate_frontend",
              new=_fake_frontend(api_source)),
        patch("generation.project_pipeline.create_backend_plan",
              new=AsyncMock(return_value=_backend_plan())),
        patch("generation.seed_data.call_groq",
              new=AsyncMock(return_value=json.dumps(SEED))),
    )


def _request(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as response:
        return response.status, json.loads(response.read())


@pytest.mark.asyncio
async def test_backend_serves_exactly_what_the_frontend_calls(tmp_path):
    p1, p2, p3, p4, p5 = _patches(GOOD_API, tmp_path)
    with p1, p2, p3, p4, p5:
        result = await generate_project_pipeline(
            "Build a bakery site", output_dir=tmp_path,
            validate_connected=True, validate_browser=False)

    served = {(e["method"], e["path"]) for e in result["api_contract"]}
    assert served == {
        ("GET", "/api/products"),
        ("GET", "/api/products/{id}"),
        ("POST", "/api/orders"),
    }  # login / categories invented by the planner are gone
    assert result["contract_result"]["valid"]
    assert result["seed_summary"] == {"product": 2}

    with running_backend(result["backend_dir"]) as base:
        _, products = _request("GET", base + "/api/products")
        assert [p["name"] for p in products] == ["Croissant", "Sourdough"]
        assert products[1]["price"] == 6.0 and "bogus" not in products[1]

        _, one = _request("GET", base + "/api/products/p1")
        assert one["imageUrl"] == "/images/croissant.jpg"

        status, order = _request("POST", base + "/api/orders", {
            "customerName": "Asha", "total": 9.5, "items": [{"id": "p1", "qty": 2}]})
        assert status == 201 and order["customerName"] == "Asha"
        # The frontend never lists orders, so the backend has no such route.
        with pytest.raises(urllib.error.HTTPError) as error:
            _request("GET", base + "/api/orders")
        assert error.value.code == 405

    saved = json.loads((result["backend_dir"] / "data" / "order.json").read_text())
    assert len(saved) == 1 and saved[0]["customerName"] == "Asha"  # 'notes' not required


@pytest.mark.asyncio
async def test_unsupported_frontend_calls_are_repaired_once(tmp_path):
    def repair(frontend_dir, plan, contract, unsupported):
        (Path(frontend_dir) / "src" / "api" / "productsApi.js").write_text(GOOD_API)
        return ["src/api/productsApi.js"]

    p1, p2, p3, p4, p5 = _patches(BAD_API, tmp_path)
    repair_mock = AsyncMock(side_effect=repair)
    with p1, p2, p3, p4, p5, \
         patch("generation.project_pipeline.repair_unsupported_api_calls", new=repair_mock), \
         patch("generation.project_pipeline._validate_frontend_build") as build:
        result = await generate_project_pipeline(
            "Build a bakery site", output_dir=tmp_path, validate_connected=False)

    repair_mock.assert_awaited_once()
    assert [c["path"] for c in repair_mock.await_args.args[3]] == ["/api/orders/{id}/items"]
    build.assert_called_once()
    assert result["contract_result"]["valid"]


@pytest.mark.asyncio
async def test_pipeline_fails_when_repair_does_not_help(tmp_path):
    p1, p2, p3, p4, p5 = _patches(BAD_API, tmp_path)
    with p1, p2, p3, p4, p5, \
         patch("generation.project_pipeline.repair_unsupported_api_calls",
               new=AsyncMock(return_value=[])):
        with pytest.raises(ValueError, match="cannot serve"):
            await generate_project_pipeline(
                "Build a bakery site", output_dir=tmp_path, validate_connected=False)


@pytest.mark.asyncio
async def test_empty_requirements_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        await generate_project_pipeline("   ", output_dir=tmp_path)
