"""Shared state and options for the generation graph."""
from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

# Keys whose values are appended to (not replaced) when a node returns them.
LIST_KEYS = ("events", "fixes")

DEFAULT_OPTIONS: dict[str, Any] = {
    "research": False,            # run market + competitor research first
    "market_scope": "Global",
    "seed_data": True,            # starter records for catalog collections
    "validate_connected": True,   # start the backend and smoke-test the API
    "validate_browser": True,     # open every page in a real browser (needs Playwright)
    "max_debug_attempts": 2,      # validate -> debug -> validate loops
    "strict_frontend_validation": False,
    "auto_preview": True,         # start the live preview when the build finishes
}

OPTION_TYPES: dict[str, type] = {
    "research": bool,
    "market_scope": str,
    "seed_data": bool,
    "validate_connected": bool,
    "validate_browser": bool,
    "max_debug_attempts": int,
    "strict_frontend_validation": bool,
    "auto_preview": bool,
}


class PipelineState(TypedDict, total=False):
    # inputs
    requirements: str
    output_dir: str
    options: dict[str, Any]

    # planning
    research_report: str
    master_plan: Any
    frontend_plan: Any
    backend_plan: Any
    planned_contract: list[dict[str, Any]]
    requirements_list: list[dict[str, Any]]
    actual_requirements: list[dict[str, Any]]
    resource_collections: Any

    # generated artifacts
    project_root: str
    frontend_dir: str
    backend_dir: str
    api_contract: list[dict[str, Any]]
    seed_summary: dict[str, int]

    # validation / debugging
    issues: list[dict[str, Any]]
    debug_attempts: int

    # bookkeeping
    status: str
    current_node: str
    error: str | None
    result: dict[str, Any]
    events: Annotated[list[dict[str, Any]], operator.add]
    fixes: Annotated[list[dict[str, Any]], operator.add]


def resolve_options(options: dict[str, Any] | None) -> dict[str, Any]:
    """Merge user options over the defaults."""
    merged = dict(DEFAULT_OPTIONS)
    merged.update(options or {})
    return merged


def validate_options(options: Any) -> dict[str, Any]:
    """Return a clean options dict or raise ValueError with a clear message."""
    if options is None:
        return {}
    if not isinstance(options, dict):
        raise ValueError("options must be a JSON object.")
    clean: dict[str, Any] = {}
    for key, value in options.items():
        expected = OPTION_TYPES.get(key)
        if expected is None:
            raise ValueError(f"Unknown option: {key!r}.")
        # bool is a subclass of int; reject True/False for int options
        if expected is int and isinstance(value, bool):
            raise ValueError(f"Option {key!r} must be an integer.")
        if not isinstance(value, expected):
            raise ValueError(f"Option {key!r} must be {expected.__name__}.")
        clean[key] = value
    if "max_debug_attempts" in clean and not 0 <= clean["max_debug_attempts"] <= 5:
        raise ValueError("max_debug_attempts must be between 0 and 5.")
    if "market_scope" in clean:
        scope = clean["market_scope"].strip()
        if not scope or len(scope) > 80:
            raise ValueError("market_scope must be 1-80 characters.")
        clean["market_scope"] = scope
    return clean