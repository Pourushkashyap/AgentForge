from generation.backend_reconciler import reconcile_backend_plan
from planning.backend_schemas import (
    BackendEndpoint,
    BackendPlan,
    BackendTask,
    DatabaseField,
    DatabaseModel,
)


def _plan():
    return BackendPlan(
        business_name="Demo",
        project_name="Demo",
        framework="FastAPI",
        architecture="REST",
        endpoints=[
            BackendEndpoint(name="List products", method="GET",
                            path="/api/products", purpose="x"),
            BackendEndpoint(name="Invented", method="POST",
                            path="/api/auth/login", purpose="x"),
        ],
        database_models=[
            DatabaseModel(
                name="Product",
                description="p",
                fields=[
                    DatabaseField(name="id", data_type="uuid"),
                    DatabaseField(name="name", data_type="string"),
                    DatabaseField(name="image_url", data_type="string"),
                ],
            ),
            DatabaseModel(
                name="Order",
                description="o",
                fields=[
                    DatabaseField(name="customer_name", data_type="string",
                                  required=True),
                    DatabaseField(name="notes", data_type="string",
                                  required=True),
                ],
            ),
        ],
        tasks=[BackendTask(task_id="BE1", title="t", description="d")],
    )


REQUIREMENTS = [
    {"method": "GET", "path": "/api/products",
     "response_fields": ["id", "name", "imageUrl"]},
    {"method": "POST", "path": "/api/orders",
     "request_fields": ["customerName", "total"]},
    {"method": "POST", "path": "/api/contact",
     "request_fields": ["email", "message"]},
]


def test_endpoints_are_exactly_the_frontend_requirements():
    result = reconcile_backend_plan(_plan(), REQUIREMENTS)
    got = {(e.method, e.path) for e in result.plan.endpoints}
    assert got == {
        ("GET", "/api/products"),
        ("POST", "/api/orders"),
        ("POST", "/api/contact"),
    }
    assert any("auth/login" in n for n in result.notes)


def test_field_names_follow_the_frontend_spelling():
    result = reconcile_backend_plan(_plan(), REQUIREMENTS)
    models = {m.name: m for m in result.plan.database_models}

    product_fields = {f.name for f in models["Product"].fields}
    assert "imageUrl" in product_fields and "image_url" not in product_fields

    order_fields = {f.name: f for f in models["Order"].fields}
    assert "customerName" in order_fields and "total" in order_fields
    # 'notes' is never sent by the frontend, so it must not be required.
    assert order_fields["notes"].required is False
    assert order_fields["customerName"].required is True


def test_missing_models_are_created_for_every_resource():
    result = reconcile_backend_plan(_plan(), REQUIREMENTS)
    names = {m.name for m in result.plan.database_models}
    assert "contact" in names
    assert set(result.resource_collections) == {"products", "orders", "contact"}


def test_unsupported_planner_types_are_mapped_to_supported_ones():
    plan = _plan()
    plan.database_models[0].fields[1].data_type = "VARCHAR(255)"   # name
    plan.database_models[0].fields[2].data_type = "DECIMAL(10,2)"  # image_url
    result = reconcile_backend_plan(plan, REQUIREMENTS)
    types = {f.name: f.data_type for f in result.plan.database_models[0].fields}
    assert types["name"] == "string"
    assert types["imageUrl"] == "float"
