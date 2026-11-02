
import ast
from pathlib import Path
from typing import Any

HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
IGNORED_DIRS = {
    "__pycache__", ".venv", "venv", "site-packages",
    "node_modules", ".git",
}


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _join_paths(*parts: str) -> str:
    segments = [p.strip("/") for p in parts if p and p.strip("/")]
    return "/" + "/".join(segments)


def analyze_backend_routes(backend_dir: str | Path) -> list[dict[str, Any]]:
    """Extract statically declared FastAPI routes from backend Python files."""
    root = Path(backend_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Backend directory not found: {root}")

    python_files = [
        path for path in root.rglob("*.py")
        if not any(part in IGNORED_DIRS for part in path.parts)
    ]

    trees: dict[Path, ast.Module] = {}
    router_prefixes: dict[str, str] = {}
    included_prefixes: dict[str, str] = {}

    # First pass: parse files and discover APIRouter prefixes.
    for path in python_files:
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (OSError, UnicodeError, SyntaxError):
            continue

        trees[path] = tree

        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue

            value = node.value
            if not isinstance(value, ast.Call):
                continue

            func = value.func
            if not (
                isinstance(func, ast.Name)
                and func.id == "APIRouter"
            ):
                continue

            prefix = ""
            for keyword in value.keywords:
                if keyword.arg == "prefix":
                    prefix = _literal_string(keyword.value) or ""

            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            for target in targets:
                if isinstance(target, ast.Name):
                    router_prefixes[target.id] = prefix

    # Discover app.include_router(router, prefix="/something").
    for tree in trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "include_router"
            ):
                continue

            if not node.args or not isinstance(node.args[0], ast.Name):
                continue

            router_name = node.args[0].id
            extra_prefix = ""

            for keyword in node.keywords:
                if keyword.arg == "prefix":
                    extra_prefix = _literal_string(keyword.value) or ""

            included_prefixes[router_name] = _join_paths(
                extra_prefix,
                router_prefixes.get(router_name, ""),
            )

    routes: dict[tuple[str, str], dict[str, Any]] = {}

    # Second pass: extract route decorators.
    for file_path, tree in trees.items():
        relative = file_path.relative_to(root).as_posix()

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            for decorator in node.decorator_list:
                if not isinstance(decorator, ast.Call):
                    continue

                func = decorator.func
                if not isinstance(func, ast.Attribute):
                    continue

                method_name = func.attr.lower()
                methods: list[str] = []

                if method_name.upper() in HTTP_METHODS:
                     methods = [method_name.upper()]
                elif method_name in {"api_route", "route"}:
                    for keyword in decorator.keywords:
                        if keyword.arg == "methods":
                            if isinstance(keyword.value, (ast.List, ast.Tuple)):
                                methods = [
                                    item.value.upper()
                                    for item in keyword.value.elts
                                    if isinstance(item, ast.Constant)
                                    and isinstance(item.value, str)
                                    and item.value.upper() in HTTP_METHODS
                                ]

                if not methods:
                    continue

                raw_path = (
                    _literal_string(decorator.args[0])
                    if decorator.args else None
                )

                if raw_path is None:
                    for keyword in decorator.keywords:
                        if keyword.arg == "path":
                            raw_path = _literal_string(keyword.value)
                            break

                if raw_path is None:
                    continue

                base_name = (
                    func.value.id
                    if isinstance(func.value, ast.Name)
                    else ""
                )

                prefix = included_prefixes.get(
                    base_name,
                    router_prefixes.get(base_name, ""),
                )
                full_path = _join_paths(prefix, raw_path)
                full_path = full_path.rstrip("/") or "/"

                if not full_path.startswith("/api/"):
                    continue

                for method in methods:
                    key = (method, full_path)
                    routes[key] = {
                        "source_file": relative,
                        "method": method,
                        "path": full_path,
                        "purpose": f"Backend route: {node.name}",
                        "request_fields": [],
                        "response_fields": [],
                        "authentication_required": False,
                    }

    return list(routes.values())
