"""Which pages the browser check opens.

It used to open only "/" and the pages named in the plan, and it skipped every route with a
parameter. So detail / sub pages (``/tutors/:id``), and pages added later such as /login or
/signup, were never opened and their crashes shipped. The routes now come from the real router,
and ``:id`` is replaced by an id that really exists in the project's seed data.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

MAX_ROUTES = 12


def _first_id(backend_dir: Path | None, segment: str) -> str:
    """The id of the first record of the collection a path segment refers to, else "1"."""
    if backend_dir is None:
        return "1"
    data_dir = Path(backend_dir) / "data"
    base = re.sub(r"[^a-z0-9]+", "_", segment.lower()).strip("_")
    names = {base, base.rstrip("s"), base + "s", base.replace("ies", "y")}
    for name in sorted(names):
        file = data_dir / f"{name}.json"
        if not file.is_file():
            continue
        try:
            records = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(records, dict):
            records = next((v for v in records.values() if isinstance(v, list)), [])
        if isinstance(records, list) and records and isinstance(records[0], dict):
            value = records[0].get("id")
            if value not in (None, ""):
                return str(value)
    return "1"


def _fill_params(path: str, backend_dir: Path | None) -> str:
    segments = path.strip("/").split("/")
    for i, seg in enumerate(segments):
        if seg.startswith(":"):
            before = segments[i - 1] if i > 0 and not segments[i - 1].startswith(":") else ""
            segments[i] = _first_id(backend_dir, before)
    return "/" + "/".join(segments)


def discover_routes(
    frontend_dir: Path, backend_dir: Path | None = None,
    plan_routes: list[str] | None = None, limit: int = MAX_ROUTES,
) -> list[str]:
    """"/" first, then the static pages, then one concrete URL per parameterised page."""
    from workflow.flow_checks import collect_routes

    static: list[str] = ["/"]
    dynamic: list[str] = []

    def add(bucket: list[str], route: str) -> None:
        if route and route not in static and route not in dynamic:
            bucket.append(route)

    for route in plan_routes or []:
        add(static, route)
    try:
        found = collect_routes(Path(frontend_dir))
    except Exception:
        found = []
    for entry in found:
        path = str(entry.get("path", ""))
        if not path.startswith("/") or "*" in path:
            continue
        if ":" in path:
            add(dynamic, _fill_params(path, backend_dir))
        else:
            add(static, path)
    # sub pages first: they are the ones that were never opened before
    ordered = static[:1] + dynamic + static[1:]
    return list(dict.fromkeys(ordered))[:limit]
