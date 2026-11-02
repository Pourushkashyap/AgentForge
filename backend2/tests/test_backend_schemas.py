import pytest
from pydantic import ValidationError

from planning.backend_schemas import (
    BackendEndpoint,
    BackendPlan,
    BackendTask,
    EndpointHandlerMetadata,
    EndpointSecurityMetadata,
    FrontendAPIRequirement,
)


def make_endpoint(**overrides):
    data = {
        "name": "List students",
        "method": "GET",
        "path": "/api/students",
        "purpose": "Retrieve all students",
    }
    data.update(overrides)
    return BackendEndpoint(**data)


def test_legacy_endpoint_fields_still_work():
    endpoint = make_endpoint(
        authentication_required=True,
        request_fields=["name"],
        response_fields=["id", "name"],
    )

    assert endpoint.authentication_required is True
    assert endpoint.request_fields == ["name"]
    assert endpoint.response_fields == ["id", "name"]
    assert endpoint.endpoint_type == "crud"


def test_legacy_endpoint_gets_default_structured_metadata():
    endpoint = make_endpoint()

    assert endpoint.request_schema == {}
    assert endpoint.response_schema == {}
    assert endpoint.security.roles == []
    assert endpoint.handler.custom_logic is False
    assert endpoint.metadata == {}


@pytest.mark.parametrize(
    ("method", "path", "expected_type"),
    [
        ("POST", "/api/auth/login", "authentication"),
        ("POST", "/api/auth/logout", "authentication"),
        ("POST", "/api/users/register", "registration"),
        ("POST", "/api/signup", "registration"),
        ("POST", "/api/uploads", "upload"),
        ("POST", "/api/payments/checkout", "payment"),
        ("GET", "/api/students/search", "search"),
        ("GET", "/api/students", "crud"),
    ],
)
def test_legacy_endpoint_type_inference(method, path, expected_type):
    endpoint = make_endpoint(method=method, path=path)

    assert endpoint.endpoint_type == expected_type


def test_explicit_endpoint_type_is_not_overridden():
    endpoint = make_endpoint(
        method="POST",
        path="/api/custom-action",
        endpoint_type="custom",
    )

    assert endpoint.endpoint_type == "custom"


def test_structured_request_and_response_schemas():
    endpoint = make_endpoint(
        method="POST",
        path="/api/auth/login",
        endpoint_type="authentication",
        request_schema={
            "type": "object",
            "required": ["email", "password"],
            "properties": {
                "email": {"type": "string", "format": "email"},
                "password": {"type": "string"},
            },
        },
        response_schema={
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "token_type": {"type": "string"},
            },
        },
    )

    assert endpoint.request_schema["required"] == ["email", "password"]
    assert endpoint.response_schema["properties"]["access_token"]["type"] == "string"


def test_security_metadata_is_preserved():
    endpoint = make_endpoint(
        authentication_required=True,
        security=EndpointSecurityMetadata(
            roles=["admin"],
            auth_scheme="bearer",
            permissions=["students:read"],
            notes=["Only administrators can access this endpoint"],
        ),
    )

    assert endpoint.authentication_required is True
    assert endpoint.security.roles == ["admin"]
    assert endpoint.security.auth_scheme == "bearer"
    assert endpoint.security.permissions == ["students:read"]


def test_roles_and_permissions_enable_authentication():
    endpoint = make_endpoint(
        security={
            "roles": ["admin"],
            "permissions": ["students:delete"],
        }
    )

    assert endpoint.authentication_required is True
    assert endpoint.security.auth_scheme == "bearer"


def test_handler_metadata_is_preserved():
    endpoint = make_endpoint(
        endpoint_type="custom",
        handler=EndpointHandlerMetadata(
            operation="mark_attendance",
            service="attendance_service",
            resource="attendance",
            custom_logic=True,
            notes=["Check student enrollment before marking attendance"],
        ),
    )

    assert endpoint.handler.operation == "mark_attendance"
    assert endpoint.handler.service == "attendance_service"
    assert endpoint.handler.resource == "attendance"
    assert endpoint.handler.custom_logic is True


def test_endpoint_serializes_with_new_fields():
    endpoint = make_endpoint(
        endpoint_type="search",
        metadata={"feature": "student-search"},
    )

    data = endpoint.model_dump()

    assert data["endpoint_type"] == "search"
    assert data["metadata"]["feature"] == "student-search"
    assert "authentication_required" in data
    assert "request_fields" in data
    assert "response_fields" in data


def test_frontend_requirement_accepts_old_analyzer_output():
    requirement = FrontendAPIRequirement(
        source_file="src/api/studentApi.js",
        method="GET",
        path="/api/students",
    )

    assert requirement.endpoint_type == "crud"
    assert requirement.request_schema == {}
    assert requirement.response_schema == {}


def test_frontend_requirement_accepts_structured_metadata():
    requirement = FrontendAPIRequirement(
        source_file="src/api/authApi.js",
        method="POST",
        path="/api/auth/login",
        endpoint_type="authentication",
        request_schema={
            "type": "object",
            "required": ["email", "password"],
        },
    )

    assert requirement.endpoint_type == "authentication"
    assert requirement.request_schema["required"] == ["email", "password"]


def test_backend_plan_still_accepts_legacy_endpoint():
    plan = BackendPlan(
        business_name="Student Management",
        project_name="Student Management System",
        framework="FastAPI",
        architecture="REST API",
        endpoints=[
            {
                "name": "List students",
                "method": "GET",
                "path": "/api/students",
                "purpose": "List all students",
            }
        ],
        tasks=[
            BackendTask(
                task_id="backend-1",
                title="Create API",
                description="Create student API",
            )
        ],
    )

    assert len(plan.endpoints) == 1
    assert plan.endpoints[0].endpoint_type == "crud"


def test_invalid_endpoint_type_is_rejected():
    with pytest.raises(ValidationError):
        make_endpoint(endpoint_type="unknown")
        
@pytest.mark.parametrize(
    "path, expected",
    [
        ("/api/auth/register", "registration"),
        ("/api/signup", "registration"),
        ("/api/auth/login", "authentication"),
        ("/api/logout", "authentication"),
        ("/api/products/search", "search"),
        ("/api/uploads", "upload"),
        ("/api/payments/checkout", "payment"),
    ],
)
def test_endpoint_type_inference(path, expected):
    endpoint = BackendEndpoint(
        name="Test endpoint",
        method="POST",
        path=path,
        purpose="Test",
    )

    assert endpoint.endpoint_type == expected        