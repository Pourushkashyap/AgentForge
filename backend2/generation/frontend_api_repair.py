"""Repair generated frontend files that call unsupported API routes.

The backend generator serves only JSON CRUD routes. If the generated frontend
calls something else (for example /api/auth/login or /api/orders/{id}/items),
the files containing those calls are regenerated once with the exact problem
and the list of endpoints that are allowed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _describe(contract: list[dict[str, Any]]) -> str:
    return "; ".join(f"{c['method']} {c['path']}" for c in contract) or "none"


async def repair_unsupported_api_calls(
    frontend_dir: str | Path,
    frontend_plan: Any,
    api_contract: list[dict[str, Any]],
    unsupported: list[dict[str, Any]],
) -> list[str]:
    """Regenerate the files that contain unsupported calls.

    Returns the relative paths of the files that were rewritten.
    """
    # Imported lazily: it pulls in the whole frontend generator.
    from generation.frontend_generator import _generate_file

    frontend_dir = Path(frontend_dir).resolve()
    metadata = json.loads(
        (frontend_dir / "agentforge-manifest.json").read_text(encoding="utf-8")
    )
    manifest = metadata["generated_files"]
    by_path = {item["path"]: item for item in manifest}

    problems: dict[str, list[str]] = {}
    for call in unsupported:
        path = str(call.get("source_file", ""))
        if path in by_path:
            problems.setdefault(path, []).append(
                f"Calls {call.get('method')} {call.get('path')}, which the "
                "backend cannot serve. The backend supports only "
                "/api/<resource> and /api/<resource>/{id} with GET, POST, "
                "PUT, PATCH and DELETE: no nested paths and no login or "
                "action routes. Remove this call and do the work in the "
                "browser, or use only these endpoints: "
                + _describe(api_contract)
            )

    rewritten: list[str] = []
    for path, issues in problems.items():
        current = (frontend_dir / path).read_text(encoding="utf-8")
        fixed = await _generate_file(
            frontend_plan,
            manifest,
            by_path[path],
            previous_code=current,
            issues=issues,
            api_contract=api_contract,
        )
        (frontend_dir / path).write_text(fixed.rstrip() + "\n", encoding="utf-8")
        rewritten.append(path)

    return rewritten
