"""Validation checks for a generated project.

Every check returns a list of *issues* (plain dicts, JSON-serialisable):

    {"stage": "backend-static", "code": "syntax_error", "severity": "error",
     "message": "...", "file": "main.py" | None, "auto_fixable": True}

``severity == "error"`` blocks completion and triggers the debug node;
``"warning"`` is reported but never blocks.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Callable

SKIP_DIRS = {
    "node_modules", "dist", "build", ".git", "__pycache__", ".venv", "venv",
    ".pytest_cache", "test-results",
}
TEXT_SUFFIXES = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".json", ".md", ".txt", ".html",
    ".css", ".env", ".yml", ".yaml", ".toml", ".cfg", ".ini",
}

SECRET_PATTERNS = [
    ("Groq API key", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("Tavily API key", re.compile(r"tvly-[A-Za-z0-9_-]{16,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_\-]{30,}")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("Private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]


def make_issue(
    stage: str,
    message: str,
    *,
    code: str = "",
    severity: str = "error",
    file: str | None = None,
    auto_fixable: bool = True,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "code": code,
        "severity": severity,
        "message": message,
        "file": file,
        "auto_fixable": auto_fixable,
    }


def iter_files(root: Path):
    """Yield project files, skipping dependency/build/cache folders."""
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def has_errors(issues: list[dict[str, Any]]) -> bool:
    return any(i.get("severity") == "error" for i in issues)


# --------------------------------------------------------------------------
# backend
# --------------------------------------------------------------------------
def check_backend_static(backend_dir: str | Path) -> list[dict[str, Any]]:
    stage = "backend-static"
    backend = Path(backend_dir)
    if not backend.is_dir():
        return [make_issue(stage, f"Backend directory not found: {backend}",
                           code="missing_backend", auto_fixable=False)]

    issues: list[dict[str, Any]] = []
    main_py = backend / "main.py"
    if not main_py.is_file():
        issues.append(make_issue(stage, "main.py is missing.",
                                 code="missing_main", file="main.py",
                                 auto_fixable=False))

    for path in iter_files(backend):
        if path.suffix != ".py":
            continue
        rel = path.relative_to(backend).as_posix()
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        except SyntaxError as exc:
            issues.append(make_issue(
                stage, f"SyntaxError in {rel} line {exc.lineno}: {exc.msg}",
                code="syntax_error", file=rel))
        except UnicodeDecodeError:
            issues.append(make_issue(stage, f"{rel} is not valid UTF-8.",
                                     code="bad_encoding", file=rel))

    requirements = backend / "requirements.txt"
    wanted = ("fastapi", "uvicorn")
    text = requirements.read_text(encoding="utf-8").lower() if requirements.is_file() else ""
    missing = [name for name in wanted if name not in text]
    if missing:
        issues.append(make_issue(
            stage, "requirements.txt is missing: " + ", ".join(missing),
            code="missing_requirements", file="requirements.txt"))

    if not (backend / "storage" / "json_store.py").is_file():
        issues.append(make_issue(stage, "storage/json_store.py is missing.",
                                 code="missing_storage",
                                 file="storage/json_store.py"))

    if main_py.is_file() and "/health" not in main_py.read_text(encoding="utf-8"):
        issues.append(make_issue(stage, "main.py has no /health route.",
                                 code="missing_health", file="main.py"))

    data_dir = backend / "data"
    if data_dir.is_dir():
        for path in sorted(data_dir.glob("*.json")):
            rel = path.relative_to(backend).as_posix()
            try:
                value = json.loads(path.read_text(encoding="utf-8") or "null")
            except json.JSONDecodeError as exc:
                issues.append(make_issue(
                    stage, f"{rel} is not valid JSON: {exc.msg}",
                    code="bad_data_json", file=rel))
                continue
            if not isinstance(value, list):
                issues.append(make_issue(
                    stage, f"{rel} must contain a JSON list.",
                    code="bad_data_json", file=rel))
    return issues


# --------------------------------------------------------------------------
# frontend
# --------------------------------------------------------------------------
def _default_import_checker(frontend_dir: Path) -> list[str]:
    from generation.frontend_generator import validate_local_imports

    return validate_local_imports(frontend_dir)


def check_frontend_static(
    frontend_dir: str | Path,
    import_checker: Callable[[Path], list[str]] | None = None,
) -> list[dict[str, Any]]:
    stage = "frontend-static"
    frontend = Path(frontend_dir)
    if not frontend.is_dir():
        return [make_issue(stage, f"Frontend directory not found: {frontend}",
                           code="missing_frontend", auto_fixable=False)]

    issues: list[dict[str, Any]] = []
    package = frontend / "package.json"
    if not package.is_file():
        issues.append(make_issue(stage, "package.json is missing.",
                                 code="missing_package", file="package.json",
                                 auto_fixable=False))
    else:
        try:
            data = json.loads(package.read_text(encoding="utf-8"))
            if "react" not in (data.get("dependencies") or {}):
                issues.append(make_issue(
                    stage, "package.json does not depend on react.",
                    code="no_react", file="package.json", auto_fixable=False))
            if "build" not in (data.get("scripts") or {}):
                issues.append(make_issue(
                    stage, "package.json has no build script.",
                    code="no_build_script", file="package.json",
                    auto_fixable=False))
        except json.JSONDecodeError as exc:
            issues.append(make_issue(
                stage, f"package.json is not valid JSON: {exc.msg}",
                code="bad_package_json", file="package.json",
                auto_fixable=False))

    if not (frontend / "index.html").is_file():
        issues.append(make_issue(stage, "index.html is missing.",
                                 code="missing_index", file="index.html",
                                 auto_fixable=False))
    if not any((frontend / "src" / f"main{ext}").is_file()
               for ext in (".jsx", ".js", ".tsx")):
        issues.append(make_issue(stage, "src/main entry file is missing.",
                                 code="missing_entry", auto_fixable=False))

    # Every context hook used by a page needs its provider mounted somewhere.
    try:
        from generation.provider_wiring import validate_provider_wiring

        for message in validate_provider_wiring(frontend):
            issues.append(make_issue(
                stage, message, code="unmounted_provider", file="src/main.jsx"))
    except ImportError:
        pass

    checker = import_checker or _default_import_checker
    try:
        for message in checker(frontend):
            file_match = re.match(r"^([\w./\\-]+\.(?:jsx?|tsx?))", message)
            issues.append(make_issue(
                stage, message, code="bad_import",
                file=file_match.group(1).replace("\\", "/") if file_match else None))
    except ImportError:
        pass  # checker unavailable in this environment
    return issues


# --------------------------------------------------------------------------
# secrets, contract, runtime
# --------------------------------------------------------------------------
def scan_for_secrets(root: str | Path) -> list[dict[str, Any]]:
    """Flag API keys/private keys that leaked into generated files."""
    stage = "security"
    base = Path(root)
    issues: list[dict[str, Any]] = []
    if not base.is_dir():
        return issues
    for path in iter_files(base):
        rel = path.relative_to(base).as_posix()
        if path.name == ".env":
            issues.append(make_issue(
                stage, "A .env file was generated; never ship secrets.",
                code="env_file", severity="warning", file=rel,
                auto_fixable=False))
        if path.suffix not in TEXT_SUFFIXES and path.name != ".env":
            continue
        try:
            if path.stat().st_size > 1_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for label, pattern in SECRET_PATTERNS:
            if pattern.search(text):
                issues.append(make_issue(
                    stage, f"Possible {label} found in {rel}. Remove and rotate it.",
                    code="secret_found", file=rel, auto_fixable=False))
                break
    return issues


def check_contract(
    frontend_requirements: list[dict[str, Any]],
    backend_endpoints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    from generation.api_contract_validator import validate_api_contract
    from generation.api_endpoints import BUILTIN_ENDPOINTS

    stage = "api-contract"
    result = validate_api_contract(
        frontend_requirements=frontend_requirements,
        backend_endpoints=list(backend_endpoints) + list(BUILTIN_ENDPOINTS),
    )
    issues = [
        make_issue(
            stage,
            f"Frontend calls {m['method']} {m['path']} but the backend does not serve it.",
            code="missing_endpoint", file=m.get("source_file"),
            auto_fixable=False)
        for m in result["missing_endpoints"]
    ]
    issues += [
        make_issue(
            stage,
            f"{m['method']} {m['path']} {m['kind']} fields not in backend plan: "
            + ", ".join(m["fields"]),
            code="field_mismatch", severity="warning", auto_fixable=False)
        for m in result["field_mismatches"]
    ]
    return issues



def _auth_page_requirements(plan: Any, endpoints: list[dict[str, Any]] | None) -> tuple[bool, bool]:
    """Infer whether login/registration pages are part of this project."""
    pages = getattr(plan, "pages", []) or []
    signals = []
    for page in pages:
        if isinstance(page, dict):
            name, route = page.get("name", ""), page.get("route", "")
        else:
            name, route = getattr(page, "name", ""), getattr(page, "route", "")
        signals.append((str(name).strip().lower(), str(route).strip().lower().rstrip("/")))

    paths = {
        str(item.get("path") or item.get("endpoint") or "").lower().rstrip("/")
        for item in (endpoints or []) if isinstance(item, dict)
    }
    login = any(route == "/login" or name in {"login", "sign in", "signin"}
                for name, route in signals)
    signup = any(route in {"/signup", "/sign-up", "/register"}
                 or name in {"signup", "sign up", "register", "registration"}
                 for name, route in signals)
    login = login or any(path.endswith("/auth/login") for path in paths)
    signup = signup or any(path.endswith(("/auth/register", "/auth/signup", "/auth/sign-up"))
                           for path in paths)
    return login or signup, signup


def check_authentication(
    frontend_dir: str | Path,
    plan: Any,
    endpoints: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Validate the generated auth pages and wiring without assuming a UI library.

    This is intentionally static: API availability and real credential flows are
    verified by connected-runtime/browser tests when those options are enabled.
    """
    frontend = Path(frontend_dir)
    login_required, signup_required = _auth_page_requirements(plan, endpoints)
    if not login_required and not signup_required:
        return []

    stage = "authentication"
    issues: list[dict[str, Any]] = []
    src = frontend / "src"
    app_file = src / "App.jsx"
    app_text = app_file.read_text(encoding="utf-8", errors="ignore") if app_file.is_file() else ""

    def add(message: str, file: str, code: str) -> None:
        issues.append(make_issue(stage, message, code=code, file=file, auto_fixable=True))

    required_pages = []
    if login_required:
        required_pages.append(("Login", "/login", "email", "password", "/signup"))
    if signup_required:
        required_pages.append(("Signup", "/signup", "name", "email", "/login"))

    for component, route, first_field, second_field, opposite_route in required_pages:
        rel = f"src/pages/{component}.jsx"
        page = src / "pages" / f"{component}.jsx"
        if not page.is_file():
            add(f"Required authentication page is missing: {rel}.", "src/App.jsx", "auth_page_missing")
            continue
        content = page.read_text(encoding="utf-8", errors="ignore")
        # Verify App imports the dedicated component and maps the exact route.
        if not re.search(r"import\s+" + component + r"\s+from\s+['\"]\.?/[^'\"]*" + component + r"\.jsx?['\"]", app_text):
            add(f"App.jsx must import {component} from {rel}.", "src/App.jsx", "auth_import_missing")
        route_ok = False
        for tag in re.findall(r"<Route\b[^>]*>", app_text, flags=re.S):
            path_match = re.search(r"\bpath\s*=\s*['\"]([^'\"]+)", tag)
            element_match = re.search(r"element\s*=\s*\{\s*<([A-Za-z_$][\w$]*)", tag)
            if path_match and element_match and path_match.group(1).rstrip("/") == route:
                route_ok = element_match.group(1) == component
                break
        if not route_ok:
            add(f"Route {route} must render <{component} />.", "src/App.jsx", "auth_route_mismatch")

        lowered = content.lower()
        has_form = re.search(r"<(?:form|AuthForm)\b", content, re.I)
        has_submit = re.search(r"onSubmit\s*=", content, re.I)
        if not has_form or not has_submit:
            add(f"{component} must render a form/AuthForm with an onSubmit handler.", rel, "auth_form_missing")
        for field in (first_field, second_field):
            field_pattern = (r"(?:name|type|id)\s*=\s*['\"]" + re.escape(field) + r"['\"]")
            if not re.search(field_pattern, content, re.I):
                add(f"{component} must contain a {field} field.", rel, "auth_field_missing")
        if not re.search(r"(?:to|href)\s*=\s*['\"]" + re.escape(opposite_route) + r"['\"]", content):
            add(f"{component} must link to {opposite_route}.", rel, "auth_cross_link_missing")

    return issues

def _runtime_file_hint(message: str) -> str | None:
    """Extract a generated source-file path from runtime diagnostics, if present."""
    if not message:
        return None

    # Prefer explicit project-relative paths emitted by backend/browser diagnostics.
    patterns = (
        r"(?i)(?:^|[\s\[(])((?:backend|frontend)[/\\][^\s\]:)]+\.(?:py|js|jsx|ts|tsx))",
        r"(?i)(?:file|source)\s*[:=]\s*['\"]?([^'\"\s]+\.(?:py|js|jsx|ts|tsx))",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1).replace("\\", "/")
    return None


def check_runtime(
    frontend_dir: str | Path,
    backend_dir: str | Path,
    endpoints: list[dict[str, Any]],
    routes: list[str] | None,
    *,
    run_browser: bool,
) -> list[dict[str, Any]]:
    """Start the backend, smoke-test the API (and optionally the browser)."""
    from generation.connected_runtime import validate_connected_runtime

    try:
        validate_connected_runtime(
            frontend_dir, backend_dir, endpoints, routes=routes,
            run_browser=run_browser)
    except Exception as exc:  # ValueError, subprocess/Playwright errors
        message = str(exc)
        if run_browser and _browser_tooling_missing(message):
            return [make_issue(
                "runtime",
                "Browser check skipped: Playwright is not installed. Run "
                "'npm install' and 'npx playwright install chromium' in the "
                "backend2 folder to catch page crashes automatically.",
                code="browser_unavailable", severity="warning",
                auto_fixable=False)]
        return [make_issue(
            "runtime", message[-8000:], code="runtime_failure",
            file=_runtime_file_hint(message),
            auto_fixable=True,
        )]
    return []


_TOOLING_MARKERS = (
    "executable doesn't exist", "playwright install", "could not determine executable",
    "npx canceled", "cannot find module '@playwright", "command not found",
    "is not recognized as an internal or external command",
    "no such file or directory: 'npx", "winerror 2", "errno 2",
)


def _browser_tooling_missing(message: str) -> bool:
    lowered = message.lower()
    # a real page failure always carries the spec's own output
    if "route " in lowered and "loads without runtime errors" in lowered:
        return False
    return any(marker in lowered for marker in _TOOLING_MARKERS)


def summarize_issues(issues: list[dict[str, Any]], limit: int = 5) -> str:
    errors = [i for i in issues if i.get("severity") == "error"]
    lines = [f"- [{i['stage']}] {i['message'].splitlines()[0]}" for i in errors[:limit]]
    if len(errors) > limit:
        lines.append(f"- ... and {len(errors) - limit} more")
    return "\n".join(lines)
