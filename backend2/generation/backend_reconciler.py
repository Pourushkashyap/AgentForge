"""Reconcile backend plans with the API requirements of the frontend.

CRUD endpoints are reconciled with database models.
Authentication, registration, search, upload, payment and custom endpoints
retain their own endpoint metadata and are not automatically converted into
CRUD database collections.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from generation.api_endpoints import (
    normalize_requirement,
    resource_of,
)
from generation.backend_generator import build_planned_routes, safe_name
from planning.backend_schemas import (
    BackendEndpoint,
    BackendPlan,
    DatabaseField,
    DatabaseModel,
    EndpointHandlerMetadata,
    EndpointSecurityMetadata,
)


_WRITE_METHODS = {"POST", "PUT", "PATCH"}

_ENDPOINT_TYPES = {
    "crud",
    "authentication",
    "registration",
    "search",
    "upload",
    "payment",
    "custom",
}

_SUPPORTED_TYPES = {
    "str", "string", "text", "email", "url", "image", "uuid", "int",
    "integer", "float", "number", "decimal", "bool", "boolean",
    "datetime", "date", "list", "array", "dict", "object", "json",
}


def _supported_type(data_type: str) -> str:
    """Map planner types to types understood by the backend generator."""
    value = (data_type or "").strip().lower()

    if value in _SUPPORTED_TYPES:
        return value
    if re.search(r"bool", value):
        return "boolean"
    if re.search(r"float|double|decimal|numeric|number|money|real", value):
        return "float"
    if re.search(r"int|serial|long|short", value):
        return "integer"
    if re.search(r"array|list|\[\]", value):
        return "list"
    if re.search(r"json|object|map|dict", value):
        return "dict"
    if re.search(r"date|time|stamp", value):
        return "datetime"

    return "string"


@dataclass
class ReconcileResult:
    plan: BackendPlan
    # CRUD resource name -> collection file name.
    resource_collections: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _normal(name: str) -> str:
    """Spelling-insensitive key: customerName == customer_name."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_tokens(name: str) -> list[str]:
    """Split camelCase, snake_case and kebab-case into lowercase words."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [
        token
        for token in re.split(r"[^A-Za-z0-9]+", spaced.lower())
        if token
    ]


_STRING_WORDS = {
    "image", "img", "photo", "picture", "thumbnail", "banner", "cover",
    "avatar", "logo", "icon", "url", "uri", "link", "src", "path", "href",
    "email", "phone", "zip", "zipcode", "postal", "pincode", "message",
    "name", "title", "description", "address", "city", "country", "state",
    "alt", "text", "caption", "note", "notes", "comment", "slug", "label",
    "category", "type", "status", "language", "location",
}

_FLOAT_WORDS = {
    "price", "total", "amount", "cost", "rate", "rating", "subtotal",
    "fee", "fees", "balance", "salary", "discount",
}

_INT_WORDS = {
    "quantity", "qty", "count", "stock", "age", "year", "number",
    "duration", "spots", "seats", "slots", "capacity",
}


def _guess_type(name: str) -> str:
    key = name.lower()
    tokens = _name_tokens(name)
    words = set(tokens)

    if key == "id" or (tokens and tokens[-1] == "id"):
        return "string"
    if words & _STRING_WORDS:
        return "string"
    if words & _FLOAT_WORDS:
        return "float"
    if words & _INT_WORDS:
        return "integer"
    if re.match(r"^(is|has|can|should)[A-Z_]", name) or key in {
        "active", "available", "featured", "published", "subscribed",
    }:
        return "boolean"
    if key in {"items", "tags", "images", "ingredients", "categories"}:
        return "list"

    return "string"


def _model_for_resource(
    resource: str,
    models: list[DatabaseModel],
) -> DatabaseModel | None:
    """Use the backend generator's model resolution logic."""
    probe = {"method": "GET", "path": f"/api/{resource}"}

    for model in models:
        spec = {"name": model.name, "collection": safe_name(model.name)}
        try:
            build_planned_routes([probe], [spec])
        except ValueError as exc:
            if "No planned model matches" in str(exc):
                continue
            raise
        return model

    return None


def _new_model(resource: str, fields: list[str]) -> DatabaseModel:
    ordered = ["id"] + [name for name in fields if name != "id"]

    return DatabaseModel(
        name=resource.replace("-", "_"),
        description=f"Records for /api/{resource}, derived from the frontend.",
        fields=[
            DatabaseField(
                name=name,
                data_type=_guess_type(name),
                required=False,
                description="Used by the generated frontend",
            )
            for name in ordered
        ],
    )


def _model_dump(value: Any) -> dict[str, Any]:
    """Convert a Pydantic model or dictionary to a safe dictionary."""
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _endpoint_type(
    item: dict[str, Any],
    previous: BackendEndpoint | None,
) -> str:
    """Resolve endpoint type, preferring explicit frontend/planner metadata."""
    explicit = item.get("endpoint_type")
    if explicit in _ENDPOINT_TYPES:
        return explicit

    if previous and previous.endpoint_type in _ENDPOINT_TYPES:
        return previous.endpoint_type

    path = str(item.get("path", "")).lower().rstrip("/")
    method = str(item.get("method", "GET")).upper()
    purpose = str(item.get("purpose", "")).lower()

    # Registration must be checked before generic /auth/ detection.
    if any(token in path for token in ("/register", "/signup", "/sign-up")):
        return "registration"
    if any(token in path for token in ("/auth/", "/login", "/logout", "/token")):
        return "authentication"
    if any(token in path for token in ("/upload", "/uploads")):
        return "upload"
    if any(token in path for token in ("/payment", "/payments", "/checkout")):
        return "payment"
    if "search" in path or "search" in purpose:
        return "search"
    if method in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return "crud"

    return "custom"


def _merge_metadata(
    item: dict[str, Any],
    previous: BackendEndpoint | None,
    field_name: str,
) -> dict[str, Any]:
    """Merge structured metadata without discarding planner information."""
    old = _model_dump(getattr(previous, field_name, None)) if previous else {}
    new = _model_dump(item.get(field_name))

    return {**old, **new}

def _canonical_requirement_path(path: str) -> str:
    """Normalize URL noise without rejecting custom action routes."""
    path = str(path).strip().split("?", 1)[0].split("#", 1)[0]
    path = "/" + path.strip("/")
    path = re.sub(r"/+", "/", path)

    # Normalize dynamic path parameters.
    path = re.sub(r"/:[A-Za-z_][A-Za-z0-9_]*", "/{id}", path)
    path = re.sub(r"/\$\{[^}]+\}", "/{id}", path)
    path = re.sub(r"/\{[A-Za-z_][A-Za-z0-9_]*\}", "/{id}", path)

    return path


def _merge_revision_requirements(
    requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge CRUD and custom API requirements without dropping action routes."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    for raw in requirements or []:
        if not isinstance(raw, dict):
            continue

        method = str(raw.get("method", "")).strip().upper()
        path = _canonical_requirement_path(raw.get("path", ""))

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            continue

        if not path.startswith("/api/"):
            continue

        # Preserve the existing CRUD canonicalization wherever supported.
        normalized = normalize_requirement(raw)
        if normalized is not None:
            item = {**raw, **normalized}
            path = normalized["path"]
        else:
            # Action routes are intentionally retained for specialized handlers.
            item = dict(raw)
            item["method"] = method
            item["path"] = path
            item.setdefault("name", f"{method} {path}")
            item.setdefault("purpose", "")
            item.setdefault("request_fields", [])
            item.setdefault("response_fields", [])
            item.setdefault("authentication_required", False)

        key = (method, path)

        if key not in merged:
            merged[key] = item
            continue

        current = merged[key]

        for field_name in ("request_fields", "response_fields"):
            current[field_name] = list(dict.fromkeys(
                list(current.get(field_name) or [])
                + list(item.get(field_name) or [])
            ))

        for field_name in (
            "request_schema",
            "response_schema",
            "security",
            "handler",
            "metadata",
        ):
            current[field_name] = {
                **(current.get(field_name) or {}),
                **(item.get(field_name) or {}),
            }

        current["authentication_required"] = (
            bool(current.get("authentication_required"))
            or bool(item.get("authentication_required"))
        )

        if not current.get("purpose"):
            current["purpose"] = item.get("purpose", "")

    return list(merged.values())


def _raw_requirement_metadata(
    requirements: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Retain structured fields that merge_requirements may not carry through."""
    result: dict[tuple[str, str], dict[str, Any]] = {}

    for item in requirements:
        method = str(item.get("method", "GET")).upper()
        path = str(item.get("path", "")).rstrip("/")
        key = (method, path)

        existing = result.setdefault(key, {})
        for field_name in (
            "endpoint_type",
            "request_schema",
            "response_schema",
            "security",
            "handler",
            "metadata",
            "authentication_required",
        ):
            if field_name in item:
                if isinstance(item[field_name], dict):
                    existing[field_name] = {
                        **existing.get(field_name, {}),
                        **item[field_name],
                    }
                else:
                    existing[field_name] = item[field_name]

    return result


def reconcile_backend_plan(
    plan: BackendPlan,
    requirements: list[dict[str, Any]],
) -> ReconcileResult:
    """Return a backend plan reconciled with the frontend API requirements.

    Only CRUD endpoints are mapped to database resources. Other endpoint
    types retain their metadata and are left for specialized handlers.
    """
    notes: list[str] = []
    raw_metadata = _raw_requirement_metadata(requirements or [])

    merged = [
        item
        for item in _merge_revision_requirements(requirements or [])
        if not (
            item["method"] == "POST"
            and item["path"] == "/api/uploads"
        )
    ]

    planner_endpoints = {
        (str(endpoint.method).upper(), endpoint.path.rstrip("/")): endpoint
        for endpoint in plan.endpoints
    }

    endpoints: list[BackendEndpoint] = []
    crud_by_resource: dict[str, list[dict[str, Any]]] = {}

    for item in merged:
        method = str(item.get("method", "GET")).upper()
        path = str(item.get("path", "")).rstrip("/")
        key = (method, path)

        previous = planner_endpoints.get(key)
        raw = raw_metadata.get(key, {})
        combined = {**item, **raw}

        endpoint_type = _endpoint_type(combined, previous)

        # Merge structured metadata from frontend requirements and planner.
        security_data = _merge_metadata(combined, previous, "security")
        handler_data = _merge_metadata(combined, previous, "handler")
        metadata = _merge_metadata(combined, previous, "metadata")

        authentication_required = bool(
            combined.get(
                "authentication_required",
                previous.authentication_required if previous else False,
            )
        )

        # Never discard an existing planner security requirement.
        if previous and previous.authentication_required:
            authentication_required = True

        if security_data.get("roles") or security_data.get("permissions"):
            authentication_required = True

        if authentication_required and not security_data.get("auth_scheme"):
            security_data["auth_scheme"] = "bearer"

        endpoint = BackendEndpoint(
            name=previous.name if previous else item.get("name", f"{method} {path}"),
            method=method,
            path=path,
            purpose=(
                item.get("purpose")
                or (previous.purpose if previous else "")
            ),
            authentication_required=authentication_required,
            request_fields=list(dict.fromkeys(
                (previous.request_fields if previous else [])
                + list(item.get("request_fields") or [])
            )),
            response_fields=list(dict.fromkeys(
                (previous.response_fields if previous else [])
                + list(item.get("response_fields") or [])
            )),
            endpoint_type=endpoint_type,
            request_schema=(
                combined.get("request_schema")
                or (previous.request_schema if previous else {})
            ),
            response_schema=(
                combined.get("response_schema")
                or (previous.response_schema if previous else {})
            ),
            security=EndpointSecurityMetadata(**security_data),
            handler=EndpointHandlerMetadata(**handler_data),
            metadata=metadata,
        )
        endpoints.append(endpoint)

        # Non-CRUD routes must not be treated as database collections.
        if endpoint_type != "crud":
            notes.append(
                f"Preserved {endpoint_type} endpoint {method} {path}; "
                "it requires a specialized handler."
            )
            continue

        try:
            resource = resource_of(path)
        except ValueError:
            # An explicitly CRUD endpoint with an unsupported path shape
            # must not silently become a collection.
            raise ValueError(
                f"CRUD endpoint has an unsupported resource path: "
                f"{method} {path}"
            )

        crud_by_resource.setdefault(resource, []).append({
            **item,
            "method": method,
            "path": path,
            "request_fields": endpoint.request_fields,
            "response_fields": endpoint.response_fields,
        })

    planned_keys = {
        (str(endpoint.method).upper(), endpoint.path.rstrip("/"))
        for endpoint in endpoints
    }

    dropped = [
        f"{method} {path}"
        for method, path in planner_endpoints
        if (method, path) not in planned_keys
    ]

    if dropped:
        notes.append(
            "Removed endpoints the frontend does not call: "
            + ", ".join(sorted(dropped))
        )

    models = list(plan.database_models)
    resource_collections: dict[str, str] = {}

    # Reconcile models only for CRUD resources.
    for resource, items in crud_by_resource.items():
        request_names: list[str] = []
        response_names: list[str] = []

        for item in items:
            if item["method"] in _WRITE_METHODS:
                request_names.extend(item["request_fields"])
            response_names.extend(item["response_fields"])

        used = list(dict.fromkeys(request_names + response_names))
        sends_known_fields = bool(request_names)

        model = _model_for_resource(resource, models)
        if model is None:
            model = _new_model(resource, used)
            models.append(model)
            notes.append(
                f"Created model '{model.name}' for /api/{resource}."
            )

        existing = {_normal(field.name): field for field in model.fields}

        for name in used:
            known = existing.get(_normal(name))

            if known is None:
                model.fields.append(
                    DatabaseField(
                        name=name,
                        data_type=_guess_type(name),
                        required=False,
                        description="Used by the generated frontend",
                    )
                )
                existing[_normal(name)] = model.fields[-1]
                notes.append(f"Added field '{name}' to '{model.name}'.")
            elif known.name != name:
                notes.append(
                    f"Renamed '{model.name}.{known.name}' to '{name}' "
                    "to match the frontend."
                )
                known.name = name

        sent = {_normal(name) for name in request_names}

        for model_field in model.fields:
            if _normal(model_field.name) == "id":
                model_field.required = False
            elif (
                not sends_known_fields
                or _normal(model_field.name) not in sent
            ):
                model_field.required = False

        resource_collections[resource] = safe_name(model.name)

    for model in models:
        for model_field in model.fields:
            model_field.data_type = _supported_type(model_field.data_type)

    new_plan = plan.model_copy(
        update={
            "endpoints": endpoints,
            "database_models": models,
            "assumptions": list(plan.assumptions)
            + ["API endpoints are derived from the generated frontend."],
        }
    )

    return ReconcileResult(
        plan=new_plan,
        resource_collections=resource_collections,
        notes=notes,
    )