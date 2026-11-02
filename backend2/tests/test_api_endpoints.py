from generation.api_endpoints import (
    contract_from_frontend_plan,
    merge_requirements,
    normalize_endpoint,
    normalize_path,
    split_unsupported,
)
from planning.frontend_schemas import APIIntegration


def test_normalize_path_accepts_crud_shapes():
    assert normalize_path("/api/products") == "/api/products"
    assert normalize_path("products") == "/api/products"
    assert normalize_path("/api/Products/") == "/api/products"
    assert normalize_path("/api/products/${id}") == "/api/products/{id}"
    assert normalize_path("/api/products/:productId") == "/api/products/{id}"
    assert normalize_path("/api/products/{productId}") == "/api/products/{id}"
    assert normalize_path("/api/products?category=cake") == "/api/products"


def test_normalize_path_rejects_unservable_shapes():
    assert normalize_path("/api/orders/{id}/items") is None
    assert normalize_path("/api/auth/login") is None  # 3rd part not a param
    assert normalize_path("/api") is None
    assert normalize_path("/api/products/featured") is None
    assert normalize_path("") is None


def test_normalize_endpoint_enforces_method_rules():
    assert normalize_endpoint("get", "/api/products") == ("GET", "/api/products")
    assert normalize_endpoint("POST", "/api/products/{id}") is None
    assert normalize_endpoint("DELETE", "/api/products") is None
    assert normalize_endpoint("PUT", "/api/products/{id}") == (
        "PUT",
        "/api/products/{id}",
    )
    assert normalize_endpoint("OPTIONS", "/api/products") is None


def test_merge_requirements_unions_fields_and_dedupes():
    merged = merge_requirements(
        [{"method": "POST", "path": "/api/orders",
          "request_fields": ["customerName"], "source_file": "src/a.js"}],
        [{"method": "post", "path": "/api/orders/",
          "request_fields": ["total", "customerName (string): who"]}],
        [{"method": "GET", "path": "/api/auth/login"}],  # dropped
    )
    assert len(merged) == 1
    assert merged[0]["request_fields"] == ["customerName", "total"]


def test_split_unsupported_only_looks_at_api_calls():
    servable, unsupported = split_unsupported([
        {"method": "GET", "path": "/api/products"},
        {"method": "POST", "path": "/api/auth/login"},
        {"method": "GET", "path": "/images/logo.png"},
    ])
    assert [c["path"] for c in servable] == ["/api/products"]
    assert [c["path"] for c in unsupported] == ["/api/auth/login"]


def test_contract_from_frontend_plan_uses_integrations():
    class Plan:
        api_integrations = [
            APIIntegration(name="List", endpoint="/api/products", method="GET",
                           response_fields=["id", "name"]),
            APIIntegration(name="Login", endpoint="/api/login", method="POST"),
            APIIntegration(name="No endpoint", endpoint="", method=""),
        ]

    contract = contract_from_frontend_plan(Plan())
    assert [(c["method"], c["path"]) for c in contract] == [
        ("GET", "/api/products")
    ]
    assert contract[0]["response_fields"] == ["id", "name"]


def test_action_style_resources_are_rejected():
    for path in ("/api/login", "/api/auth", "/api/checkout", "/api/search"):
        assert normalize_path(path) is None
