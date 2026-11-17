from planning.backend_schemas import (
    BackendEndpoint,
    BackendPlan,
    BackendTask,
)
from generation.backend_reconciler import reconcile_backend_plan


def make_plan(endpoints=None, models=None):
    return BackendPlan(
        business_name="Test Business",
        project_name="Test Project",
        framework="FastAPI",
        architecture="REST API",
        endpoints=endpoints or [],
        database_models=models or [],
        tasks=[
            BackendTask(
                task_id="task-1",
                title="Build backend",
                description="Build API endpoints",
            )
        ],
    )


def requirement(method, path, **overrides):
    item = {
        "name": f"{method} {path}",
        "method": method,
        "path": path,
        "purpose": "Test endpoint",
        "request_fields": [],
        "response_fields": [],
    }
    item.update(overrides)
    return item


def test_authentication_endpoint_is_not_converted_to_crud_model():
    plan = make_plan()

    result = reconcile_backend_plan(
        plan,
        [
            requirement(
                "POST",
                "/api/auth/login",
                endpoint_type="authentication",
                request_fields=["email", "password"],
            )
        ],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.endpoint_type == "authentication"
    assert result.plan.database_models == []
    assert result.resource_collections == {}


def test_registration_endpoint_is_not_converted_to_crud_model():
    plan = make_plan()

    result = reconcile_backend_plan(
        plan,
        [
            requirement(
                "POST",
                "/api/auth/register",
                endpoint_type="registration",
                request_fields=["name", "email", "password"],
            )
        ],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.endpoint_type == "registration"
    assert result.plan.database_models == []
    assert result.resource_collections == {}


def test_authentication_requirement_is_preserved():
    existing = BackendEndpoint(
        name="Admin report",
        method="GET",
        path="/api/reports",
        purpose="Get admin reports",
        authentication_required=True,
    )
    plan = make_plan(endpoints=[existing])

    result = reconcile_backend_plan(
        plan,
        [requirement("GET", "/api/reports")],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.authentication_required is True
    assert endpoint.security.auth_scheme == "bearer"


def test_security_roles_and_permissions_are_preserved():
    existing = BackendEndpoint(
        name="Delete student",
        method="DELETE",
        path="/api/students/{id}",
        purpose="Delete a student",
        authentication_required=True,
        security={
            "roles": ["admin"],
            "permissions": ["students:delete"],
            "auth_scheme": "bearer",
        },
    )
    plan = make_plan(endpoints=[existing])

    result = reconcile_backend_plan(
        plan,
        [requirement("DELETE", "/api/students/{id}")],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.authentication_required is True
    assert endpoint.security.roles == ["admin"]
    assert endpoint.security.permissions == ["students:delete"]


def test_request_and_response_schemas_are_preserved():
    existing = BackendEndpoint(
        name="Login",
        method="POST",
        path="/api/auth/login",
        purpose="Authenticate user",
        endpoint_type="authentication",
        request_schema={
            "type": "object",
            "required": ["email", "password"],
        },
        response_schema={
            "type": "object",
            "properties": {"access_token": {"type": "string"}},
        },
    )
    plan = make_plan(endpoints=[existing])

    result = reconcile_backend_plan(
        plan,
        [requirement("POST", "/api/auth/login")],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.request_schema["required"] == ["email", "password"]
    assert "access_token" in endpoint.response_schema["properties"]


def test_custom_handler_metadata_is_preserved():
    existing = BackendEndpoint(
        name="Mark attendance",
        method="POST",
        path="/api/attendance/mark",
        purpose="Mark student attendance",
        endpoint_type="custom",
        handler={
            "operation": "mark_attendance",
            "service": "attendance_service",
            "custom_logic": True,
        },
    )
    plan = make_plan(endpoints=[existing])

    result = reconcile_backend_plan(
        plan,
        [requirement("POST", "/api/attendance/mark")],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.endpoint_type == "custom"
    assert endpoint.handler.operation == "mark_attendance"
    assert endpoint.handler.service == "attendance_service"
    assert endpoint.handler.custom_logic is True
    assert result.plan.database_models == []


def test_search_endpoint_is_not_treated_as_crud_collection():
    plan = make_plan()

    result = reconcile_backend_plan(
        plan,
        [
            requirement(
                "GET",
                "/api/students/search",
                endpoint_type="search",
            )
        ],
    )

    assert result.plan.endpoints[0].endpoint_type == "search"
    assert result.plan.database_models == []
    assert result.resource_collections == {}


def test_payment_endpoint_is_not_treated_as_crud_collection():
    plan = make_plan()

    result = reconcile_backend_plan(
        plan,
        [
            requirement(
                "POST",
                "/api/payments/checkout",
                endpoint_type="payment",
            )
        ],
    )

    assert result.plan.endpoints[0].endpoint_type == "payment"
    assert result.plan.database_models == []
    assert result.resource_collections == {}


def test_frontend_security_metadata_is_preserved():
    plan = make_plan()

    result = reconcile_backend_plan(
        plan,
        [
            requirement(
                "POST",
                "/api/auth/login",
                endpoint_type="authentication",
                authentication_required=False,
                security={
                    "notes": ["Public login endpoint"],
                },
                request_schema={
                    "type": "object",
                    "required": ["email", "password"],
                },
            )
        ],
    )

    endpoint = result.plan.endpoints[0]

    assert endpoint.endpoint_type == "authentication"
    assert endpoint.authentication_required is False
    assert endpoint.security.notes == ["Public login endpoint"]
    assert endpoint.request_schema["required"] == ["email", "password"]


def test_crud_endpoint_still_creates_database_model():
    plan = make_plan()

    result = reconcile_backend_plan(
        plan,
        [
            requirement(
                "POST",
                "/api/students",
                request_fields=["name", "email"],
            ),
            requirement(
                "GET",
                "/api/students",
                response_fields=["id", "name", "email"],
            ),
        ],
    )

    assert len(result.plan.database_models) == 1
    assert result.plan.database_models[0].name == "students"
    assert "students" in result.resource_collections
    assert all(endpoint.endpoint_type == "crud" for endpoint in result.plan.endpoints)