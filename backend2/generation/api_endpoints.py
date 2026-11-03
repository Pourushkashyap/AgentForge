
"""Shared helpers for the frontend-first API contract.

CRUD endpoints are normalized separately from action endpoints such as
registration and authentication. Action endpoints must never be interpreted
as generic JSON-store CRUD operations.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

BUILTIN_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {
        "name": "Upload image",
        "method": "POST",
        "path": "/api/uploads",
        "purpose": "Upload an image file",
        "request_fields": [],
        "response_fields": ["image_url"],
        "authentication_required": False,
        "endpoint_type": "upload",
    },
)
_BUILTIN_KEYS = {("POST", "/api/uploads")}

_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
_PARAM_RE = re.compile(
    r"^(?:\{[A-Za-z_][A-Za-z0-9_]*\}|:[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]*\})$"
)
_FIELD_RE = re.compile(r"^\s*([A-Za-z_$][\w$]*)")

RESERVED_RESOURCES = {
    "auth", "authenticate", "login", "logout", "signin", "signout",
    "signup", "register", "token", "tokens", "refresh", "search",
    "checkout", "payment", "payments",
}

# Only these action routes are currently supported by the auth-aware
# backend generator. Add a route here only when its handler is implemented.
AUTH_ACTION_ENDPOINTS = {
    ("POST", "/api/auth/register"): "registration",
    ("POST", "/api/auth/signup"): "registration",
    ("POST", "/api/auth/login"): "authentication",
    ("POST", "/api/auth/logout"): "authentication",
    ("GET", "/api/auth/me"): "authentication",
    ("POST", "/api/auth/refresh"): "authentication",
    ("POST", "/api/users/register"): "registration",
    ("POST", "/api/users/login"): "authentication",
}

ACTION_ENDPOINT_TYPES = {
    "authentication",
    "registration",
}


def field_name(value: Any) -> str | None:
    """Return a plain field name from 'name', 'name (string): text', etc."""
    if isinstance(value, dict):
        value = value.get("name", "")
    if not isinstance(value, str):
        return None
    match = _FIELD_RE.match(value)
    return match.group(1) if match else None


def field_names(values: Iterable[Any] | None) -> list[str]:
    names: list[str] = []
    for value in values or []:
        name = field_name(value)
        if name and name not in names:
            names.append(name)
    return names


def _clean_path(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None

    path = raw.strip().split("?", 1)[0].split("#", 1)[0]
    if not path:
        return None

    if not path.startswith("/"):
        path = "/" + path

    if path == "/api":
        return None

    if not path.startswith("/api/"):
        path = "/api" + path

    parts = [part for part in path.strip("/").split("/") if part]
    return "/" + "/".join(parts) if parts else None


def normalize_path(raw: Any) -> str | None:
    """Normalize only collection/item CRUD paths."""
    path = _clean_path(raw)
    if path is None:
        return None

    parts = path.strip("/").split("/")
    if len(parts) not in (2, 3) or parts[0] != "api":
        return None

    resource = parts[1].lower()
    if not _RESOURCE_RE.match(resource) or resource in RESERVED_RESOURCES:
        return None

    if len(parts) == 2:
        return f"/api/{resource}"

    if not _PARAM_RE.match(parts[2]):
        return None

    return f"/api/{resource}/{{id}}"


def normalize_action_endpoint(
    method: Any,
    raw_path: Any,
    endpoint_type: Any = None,
) -> tuple[str, str, str] | None:
    """Validate a specifically supported action endpoint."""
    verb = str(method or "").strip().upper()
    path = _clean_path(raw_path)

    if verb not in HTTP_METHODS or path is None:
        return None

    inferred_type = AUTH_ACTION_ENDPOINTS.get((verb, path))
    declared_type = str(endpoint_type or "").strip().lower()

    if inferred_type is None:
        return None

    if declared_type and declared_type not in ACTION_ENDPOINT_TYPES | {"crud"}:
        return None

    if declared_type and declared_type != inferred_type:
        return None

    return verb, path, inferred_type


def normalize_endpoint(method: Any, raw_path: Any) -> tuple[str, str] | None:
    """Return (METHOD, canonical CRUD path), or None."""
    verb = str(method or "").strip().upper()
    if verb not in HTTP_METHODS:
        return None

    path = normalize_path(raw_path)
    if path is None:
        return None

    is_item = path.endswith("/{id}")

    if verb == "POST" and is_item:
        return None

    if verb in {"PUT", "PATCH", "DELETE"} and not is_item:
        return None

    return verb, path


def is_valid_endpoint(method: Any, raw_path: Any) -> bool:
    return normalize_endpoint(method, raw_path) is not None


def resource_of(path: str) -> str:
    return path.strip("/").split("/")[1]


def requirement_key(item: dict[str, Any]) -> tuple[str, str] | None:
    """Return the canonical method/path key for CRUD or supported actions."""
    method = str(item.get("method", "")).strip().upper()
    endpoint_type = item.get("endpoint_type")

    action = normalize_action_endpoint(
        method, item.get("path"), endpoint_type
    )
    if action:
        return action[0], action[1]

    crud = normalize_endpoint(method, item.get("path"))
    if crud:
        return crud

    return None


def _copy_metadata(item: dict[str, Any]) -> dict[str, Any]:
    """Preserve structured planner/analyzer metadata."""
    result: dict[str, Any] = {}

    for key in (
        "endpoint_type",
        "request_schema",
        "response_schema",
        "security",
        "handler",
        "metadata",
    ):
        value = item.get(key)
        if value is not None:
            if hasattr(value, "model_dump"):
                value = value.model_dump()
            result[key] = value

    return result


def normalize_requirement(item: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a CRUD or supported auth requirement."""
    if not isinstance(item, dict):
        return None

    method = str(item.get("method", "")).strip().upper()
    action = normalize_action_endpoint(
        method,
        item.get("path"),
        item.get("endpoint_type"),
    )

    if action:
        method, path, endpoint_type = action
    else:
        crud = normalize_endpoint(method, item.get("path"))
        if crud is None:
            return None

        method, path = crud
        endpoint_type = str(item.get("endpoint_type") or "crud").lower()

        # An explicitly non-CRUD action cannot pass through CRUD validation.
        if endpoint_type not in {"crud", "upload"}:
            return None

    result = {
        "source_file": item.get("source_file", "frontend_plan"),
        "name": item.get("name") or f"{method} {path}",
        "method": method,
        "path": path,
        "purpose": item.get("purpose", "") or "",
        "request_fields": field_names(item.get("request_fields")),
        "response_fields": field_names(item.get("response_fields")),
        "authentication_required": bool(
            item.get("authentication_required", False)
        ),
        "endpoint_type": endpoint_type,
    }
    result.update(_copy_metadata(item))

    # Public endpoints must not accidentally inherit a protected flag.
    if endpoint_type == "registration" or path in {
        "/api/auth/login",
        "/api/users/login",
    }:
        result["authentication_required"] = False

    # The current-user endpoint always requires a valid access token.
    if path == "/api/auth/me":
        result["authentication_required"] = True

    return result


def is_builtin(item: dict[str, Any]) -> bool:
    return requirement_key(item) in _BUILTIN_KEYS


def merge_requirements(
    *groups: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ordered union; merge fields and preserve structured metadata."""
    merged: dict[tuple[str, str], dict[str, Any]] = {}

    for group in groups:
        for raw in group or []:
            item = normalize_requirement(raw)
            if item is None:
                continue

            key = (item["method"], item["path"])

            if key not in merged:
                merged[key] = item
                continue

            current = merged[key]

            for field in ("request_fields", "response_fields"):
                current[field] = list(
                    dict.fromkeys(current[field] + item[field])
                )

            if not current["purpose"] and item["purpose"]:
                current["purpose"] = item["purpose"]

            if current["source_file"] == "frontend_plan":
                current["source_file"] = item["source_file"]

            current["authentication_required"] = (
                current["authentication_required"]
                or item["authentication_required"]
            )

            for field in (
                "request_schema",
                "response_schema",
                "security",
                "handler",
                "metadata",
            ):
                if field in item:
                    if field not in current:
                        current[field] = item[field]
                    elif isinstance(current[field], dict) and isinstance(
                        item[field], dict
                    ):
                        current[field] = {
                            **current[field],
                            **item[field],
                        }

            # Keep the more specific action classification.
            if item["endpoint_type"] != "crud":
                current["endpoint_type"] = item["endpoint_type"]

    return list(merged.values())



def split_unsupported(
    calls: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split detected API calls into (servable, unsupported).

    Action endpoints require an explicit supported endpoint_type.
    Ordinary CRUD endpoints retain their existing behavior.
    """
    servable: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    for call in calls or []:
        path = str(call.get("path", ""))
        if not (path.startswith("/api/") or path == "/api"):
            continue

        method = str(call.get("method", "")).strip().upper()
        action_type = AUTH_ACTION_ENDPOINTS.get((method, path))

        if action_type:
            declared_type = str(
                call.get("endpoint_type") or ""
            ).strip().lower()

            if declared_type != action_type:
                unsupported.append(call)
                continue

        item = normalize_requirement(call)

        if item is None:
            unsupported.append(call)
        else:
            servable.append(item)

    return servable, unsupported

def contract_from_frontend_plan(frontend_plan: Any) -> list[dict[str, Any]]:
    """Build the normalized API contract declared by the frontend plan."""
    result: list[dict[str, Any]] = []

    for integration in getattr(frontend_plan, "api_integrations", None) or []:
        item = (
            integration.model_dump()
            if hasattr(integration, "model_dump")
            else dict(integration)
        )

        result.append({
            **item,
            "source_file": "frontend_plan",
            "path": item.get("endpoint") or item.get("path", ""),
        })

    return merge_requirements(result)
