import re
from typing import Any


def validate_api_contract(
    frontend_requirements: list[dict[str, Any]],
    backend_endpoints: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate endpoint coverage and report known field mismatches."""
    planned = [
        (
            str(endpoint.get("method", "GET")).upper(),
            _normalize_path(str(endpoint.get("path", ""))),
            endpoint,
        )
        for endpoint in backend_endpoints
    ]

    missing = []
    field_mismatches = []
    matched_count = 0

    for requirement in frontend_requirements:
        method = str(requirement.get("method", "GET")).upper()
        path = _normalize_path(str(requirement.get("path", "")))

        if not path:
            missing.append({
                "method": method,
                "path": path,
                "source_file": requirement.get("source_file"),
                "reason": "Missing API path",
            })
            continue

        matches = [
            endpoint
            for planned_method, planned_path, endpoint in planned
            if planned_method == method
            and _paths_match(path, planned_path)
        ]

        if not matches:
            missing.append({
                "method": method,
                "path": path,
                "source_file": requirement.get("source_file"),
                "reason": "No matching backend endpoint",
            })
            continue

        matched_count += 1
        backend = matches[0]

        # Compare only non-empty field lists. Empty lists may mean
        # "unknown/not detected", rather than "no fields expected".
        frontend_request = set(requirement.get("request_fields") or [])
        backend_request = set(backend.get("request_fields") or [])

        if frontend_request and backend_request:
            absent = sorted(frontend_request - backend_request)
            if absent:
                field_mismatches.append({
                    "method": method,
                    "path": path,
                    "kind": "request",
                    "fields": absent,
                    "reason": "Frontend fields absent from backend plan",
                })

        frontend_response = set(requirement.get("response_fields") or [])
        backend_response = set(backend.get("response_fields") or [])

        if frontend_response and backend_response:
            absent = sorted(frontend_response - backend_response)
            if absent:
                field_mismatches.append({
                    "method": method,
                    "path": path,
                    "kind": "response",
                    "fields": absent,
                    "reason": "Frontend-used fields absent from backend plan",
                })

    return {
        "valid": not missing and not field_mismatches,
        "missing_endpoints": missing,
        "field_mismatches": field_mismatches,
        "frontend_endpoint_count": len(frontend_requirements),
        "backend_endpoint_count": len(backend_endpoints),
        "matched_endpoint_count": matched_count,
    }


def _normalize_path(path: str) -> str:
    path = path.strip()
    if not path:
        return ""

    if not path.startswith("/"):
        path = "/" + path

    if path != "/":
        path = path.rstrip("/")

    return path


def _paths_match(frontend_path: str, backend_path: str) -> bool:
    """Match concrete frontend path segments to backend {parameter} segments."""
    frontend_parts = frontend_path.strip("/").split("/")
    backend_parts = backend_path.strip("/").split("/")

    if len(frontend_parts) != len(backend_parts):
        return False

    for frontend_part, backend_part in zip(frontend_parts, backend_parts):
        is_parameter = (
            backend_part.startswith("{")
            and backend_part.endswith("}")
            and len(backend_part) > 2
        )

        if is_parameter:
            if not frontend_part:
                return False
        elif frontend_part != backend_part:
            return False

    return True