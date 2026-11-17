
from generation.api_contract_validator import validate_api_contract


def test_valid_contract_when_frontend_routes_exist():
    frontend = [
        {"method": "GET", "path": "/api/products"},
        {"method": "POST", "path": "/api/products"},
    ]
    backend = [
        {"method": "GET", "path": "/api/products"},
        {"method": "POST", "path": "/api/products"},
    ]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is True
    assert result["missing_endpoints"] == []
    assert result["field_mismatches"] == []



def test_detects_response_field_mismatch():
    frontend = [{
        "method": "GET",
        "path": "/api/products",
        "response_fields": ["id", "name", "imageUrl"],
    }]

    backend = [{
        "method": "GET",
        "path": "/api/products",
        "response_fields": ["id", "name"],
    }]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is False
    assert len(result["field_mismatches"]) == 1

    mismatch = result["field_mismatches"][0]

    assert mismatch["method"] == "GET"
    assert mismatch["path"] == "/api/products"
    assert mismatch["kind"] == "response"
    assert mismatch["fields"] == ["imageUrl"]


def test_detects_missing_frontend_endpoint():
    frontend = [{
        "method": "DELETE",
        "path": "/api/products/123",
        "source_file": "src/api.js",
    }]
    backend = [
        {"method": "GET", "path": "/api/products"},
    ]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is False
    assert result["missing_endpoints"][0]["method"] == "DELETE"
    assert result["missing_endpoints"][0]["path"] == "/api/products/123"


def test_normalizes_trailing_slashes():
    frontend = [{"method": "GET", "path": "/api/products/"}]
    backend = [{"method": "GET", "path": "/api/products"}]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is True


def test_matches_dynamic_backend_path():
    frontend = [
        {"method": "GET", "path": "/api/products/123"}
    ]
    backend = [
        {"method": "GET", "path": "/api/products/{id}"}
    ]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is True
    assert result["missing_endpoints"] == []


def test_matches_nested_dynamic_backend_path():
    frontend = [
        {"method": "GET", "path": "/api/users/42/orders"}
    ]
    backend = [
        {"method": "GET", "path": "/api/users/{user_id}/orders"}
    ]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is True


def test_does_not_match_different_static_segments():
    frontend = [
        {"method": "GET", "path": "/api/categories/123"}
    ]
    backend = [
        {"method": "GET", "path": "/api/products/{id}"}
    ]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is False


def test_dynamic_path_still_requires_matching_method():
    frontend = [
        {"method": "DELETE", "path": "/api/products/123"}
    ]
    backend = [
        {"method": "GET", "path": "/api/products/{id}"}
    ]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is False

def test_detects_request_field_mismatch():
    frontend = [{
        "method": "POST",
        "path": "/api/products",
        "request_fields": ["name", "price"],
    }]

    backend = [{
        "method": "POST",
        "path": "/api/products",
        "request_fields": ["name"],
    }]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is False
    assert len(result["field_mismatches"]) == 1

    mismatch = result["field_mismatches"][0]

    assert mismatch["method"] == "POST"
    assert mismatch["path"] == "/api/products"
    assert mismatch["kind"] == "request"
    assert mismatch["fields"] == ["price"]


def test_unknown_fields_do_not_create_false_mismatch():
    frontend = [{
        "method": "POST",
        "path": "/api/products",
        "request_fields": [],
    }]
    backend = [{
        "method": "POST",
        "path": "/api/products",
        "request_fields": ["name"],
    }]

    result = validate_api_contract(frontend, backend)

    assert result["valid"] is True
    assert result["field_mismatches"] == []
