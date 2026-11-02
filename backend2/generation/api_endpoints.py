"""Shared helpers for the API contract that the FRONTEND defines.

In the frontend-first pipeline the generated frontend decides which API calls
it needs. The backend generator can only serve simple JSON-store CRUD routes:

    GET    /api/<resource>          list
    POST   /api/<resource>          create
    GET    /api/<resource>/{id}     read one
    PUT    /api/<resource>/{id}     replace
    PATCH  /api/<resource>/{id}     update
    DELETE /api/<resource>/{id}     delete

Every helper here normalizes an API call into that shape (or rejects it), so
the frontend plan, the frontend code analyzer, the backend plan and the
backend generator all agree on exactly the same endpoints.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Endpoints the generated backend template already provides by itself.
BUILTIN_ENDPOINTS: tuple[dict[str, Any], ...] = (
    {
        "name": "Upload image",
        "method": "POST",
        "path": "/api/uploads",
        "purpose": "Upload an image file",
        "request_fields": [],
        "response_fields": ["image_url"],
        "authentication_required": False,
    },
)
_BUILTIN_KEYS = {("POST", "/api/uploads")}

_RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# Action-style names are not data collections: a JSON CRUD backend cannot
# implement their behavior (sessions, tokens, search, payments).
RESERVED_RESOURCES = {
    "auth", "authenticate", "login", "logout", "signin", "signout",
    "signup", "register", "token", "tokens", "refresh", "search",
    "checkout", "payment", "payments",
}
_PARAM_RE = re.compile(
    r"^(?:\{[A-Za-z_][A-Za-z0-9_]*\}|:[A-Za-z_][A-Za-z0-9_]*|\$\{[^}]*\})$"
)
_FIELD_RE = re.compile(r"^\s*([A-Za-z_$][\w$]*)")


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


def normalize_path(raw: Any) -> str | None:
    """Return a canonical '/api/<resource>[/{id}]' path, or None if the
    backend generator cannot serve it."""
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


def is_valid_endpoint(method: Any, raw_path: Any) -> bool:
    return normalize_endpoint(method, raw_path) is not None


def normalize_endpoint(method: Any, raw_path: Any) -> tuple[str, str] | None:
    """Return (METHOD, canonical path) when the backend can serve it."""
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


def resource_of(path: str) -> str:
    return path.strip("/").split("/")[1]


def requirement_key(item: dict[str, Any]) -> tuple[str, str] | None:
    return normalize_endpoint(item.get("method"), item.get("path"))


def normalize_requirement(item: dict[str, Any]) -> dict[str, Any] | None:
    """Canonical requirement dict, or None when it cannot be served."""
    key = requirement_key(item)
    if key is None:
        return None
    method, path = key
    return {
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
    }


def is_builtin(item: dict[str, Any]) -> bool:
    return requirement_key(item) in _BUILTIN_KEYS


def merge_requirements(
    *groups: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Ordered union of requirements; field lists are merged, not replaced."""
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

    return list(merged.values())


def split_unsupported(
    calls: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split detected frontend calls into (servable, unsupported).

    Only calls under /api are considered; other fetches (images, external
    files) are ignored on purpose.
    """
    servable: list[dict[str, Any]] = []
    unsupported: list[dict[str, Any]] = []

    for call in calls or []:
        path = str(call.get("path", ""))
        if not (path.startswith("/api/") or path == "/api"):
            continue
        item = normalize_requirement(call)
        if item is None:
            unsupported.append(call)
        else:
            servable.append(call)
    return servable, unsupported


def contract_from_frontend_plan(frontend_plan: Any) -> list[dict[str, Any]]:
    """Servable API contract declared by the frontend plan."""
    result: list[dict[str, Any]] = []

    for integration in getattr(frontend_plan, "api_integrations", None) or []:
        item = (
            integration.model_dump()
            if hasattr(integration, "model_dump")
            else dict(integration)
        )
        result.append(
            {
                "source_file": "frontend_plan",
                "name": item.get("name", ""),
                "method": item.get("method", ""),
                "path": item.get("endpoint") or item.get("path", ""),
                "purpose": item.get("purpose", ""),
                "request_fields": item.get("request_fields", []),
                "response_fields": item.get("response_fields", []),
                "authentication_required": item.get(
                    "authentication_required", False
                ),
            }
        )

    return merge_requirements(result)
