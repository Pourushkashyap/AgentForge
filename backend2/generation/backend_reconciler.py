"""Make the backend plan serve exactly the API the frontend calls.

The LLM backend planner is good at designing data models, but it also invents
extra endpoints and may spell field names differently from the frontend. In
the frontend-first pipeline the frontend is the source of truth, so after the
planner runs this module:

* replaces ``plan.endpoints`` with exactly the frontend requirements,
* guarantees a data model exists for every resource the frontend uses,
* makes model field names match the frontend's spelling (customerName),
* makes fields the frontend never sends optional, so real requests are not
  rejected with HTTP 422.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from generation.api_endpoints import (
    is_builtin,
    merge_requirements,
    resource_of,
)
from generation.backend_generator import build_planned_routes, safe_name
from planning.backend_schemas import (
    BackendEndpoint,
    BackendPlan,
    DatabaseField,
    DatabaseModel,
)

_WRITE_METHODS = {"POST", "PUT", "PATCH"}

# Data types the backend generator understands (see backend_generator.py).
_SUPPORTED_TYPES = {
    "str", "string", "text", "email", "url", "image", "uuid", "int",
    "integer", "float", "number", "decimal", "bool", "boolean", "datetime",
    "date", "list", "array", "dict", "object", "json",
}


def _supported_type(data_type: str) -> str:
    """Map planner types such as 'decimal(10,2)' or 'varchar' to a known one."""
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
    # resource name (as used in /api/<resource>) -> collection file name
    resource_collections: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _normal(name: str) -> str:
    """Spelling-insensitive key: customerName == customer_name."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _name_tokens(name: str) -> list[str]:
    """Split camelCase / snake_case / kebab-case into lowercase words."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return [tok for tok in re.split(r"[^A-Za-z0-9]+", spaced.lower()) if tok]


# Whole-word matches only. The previous version searched for substrings, so
# "imageUrl" matched "age", "message"/"page"/"language" matched "age",
# "country"/"account"/"discount" matched "count", and "feedback" matched
# "fee". That typed image URLs and text fields as numbers.
_STRING_WORDS = {
    "image", "img", "photo", "picture", "thumbnail", "banner", "cover",
    "avatar", "logo", "icon", "url", "uri", "link", "src", "path", "href",
    "email", "phone", "zip", "zipcode", "postal", "pincode", "message",
    "name", "title", "description", "address", "city", "country", "state",
    "alt", "text", "caption", "note", "notes", "comment", "slug", "label",
    "category", "type", "status", "language", "location",
}
_FLOAT_WORDS = {
    "price", "total", "amount", "cost", "rate", "rating", "subtotal", "fee",
    "fees", "balance", "salary", "discount",
}
_INT_WORDS = {
    "quantity", "qty", "count", "stock", "age", "year", "number", "duration",
    "spots", "seats", "slots", "capacity",
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
        "active",
        "available",
        "featured",
        "published",
        "subscribed",
    }:
        return "boolean"
    if key in {"items", "tags", "images", "ingredients", "categories"}:
        return "list"
    return "string"


def _model_for_resource(resource: str, models: list[DatabaseModel]):
    """Find the model the backend generator will route this resource to.

    Uses the generator's own resolution logic, so the two can never disagree.
    """
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


def reconcile_backend_plan(
    plan: BackendPlan,
    requirements: list[dict[str, Any]],
) -> ReconcileResult:
    """Return ``plan`` rewritten to serve exactly ``requirements``."""
    notes: list[str] = []
    merged = [
        item
        for item in merge_requirements(requirements)
        if not is_builtin(item)  # /api/uploads is provided by the template
    ]

    planner_endpoints = {
        (str(e.method).upper(), e.path.rstrip("/")): e for e in plan.endpoints
    }

    endpoints: list[BackendEndpoint] = []
    by_resource: dict[str, list[dict[str, Any]]] = {}
    for item in merged:
        by_resource.setdefault(resource_of(item["path"]), []).append(item)
        previous = planner_endpoints.get((item["method"], item["path"]))
        endpoints.append(
            BackendEndpoint(
                name=(previous.name if previous else item["name"]),
                method=item["method"],
                path=item["path"],
                purpose=item["purpose"]
                or (previous.purpose if previous else ""),
                authentication_required=False,
                request_fields=item["request_fields"],
                response_fields=item["response_fields"],
            )
        )

    planned_keys = {(e.method, e.path) for e in endpoints}
    dropped = [
        f"{method} {path}"
        for (method, path) in planner_endpoints
        if (method, path) not in planned_keys
    ]
    if dropped:
        notes.append(
            "Removed endpoints the frontend does not call: "
            + ", ".join(sorted(dropped))
        )

    models = list(plan.database_models)
    resource_collections: dict[str, str] = {}

    for resource, items in by_resource.items():
        request_names: list[str] = []
        response_names: list[str] = []
        for item in items:
            if item["method"] in _WRITE_METHODS:
                request_names += item["request_fields"]
            response_names += item["response_fields"]
        used = list(dict.fromkeys(request_names + response_names))
        sends_known_fields = bool(request_names)

        model = _model_for_resource(resource, models)
        if model is None:
            model = _new_model(resource, used)
            models.append(model)
            notes.append(
                f"Created model '{model.name}' for /api/{resource}."
            )

        existing = {_normal(f.name): f for f in model.fields}
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

        # Only fields the frontend really sends may stay required.
        sent = {_normal(n) for n in request_names}
        for model_field in model.fields:
            if _normal(model_field.name) == "id":
                model_field.required = False
            elif not sends_known_fields or _normal(model_field.name) not in sent:
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
