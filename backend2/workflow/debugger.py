"""Debug node logic: fix what validation found.

Two passes, cheapest first:

1. Deterministic repairs (zero tokens): missing requirements, missing storage
   module, missing /health route, corrupt seed JSON.
2. LLM repair: send only the failing files and the error text to the model,
   accept full-file replacements, and keep a change only if it passes a
   syntax check and does not look truncated.

Nothing outside the generated project can be written, and secrets are never
sent to the model.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable

LLM = Callable[..., Awaitable[str]]

MAX_FILE_CHARS = 14_000
MAX_TOTAL_CHARS = 24_000
MAX_FILES = 3
WRITABLE_SUFFIXES = {".py", ".js", ".jsx", ".json"}

from workflow.flow_checks import FLOW_CODES  # noqa: E402

HEALTH_ROUTE = '\n\n@app.get("/health")\ndef health():\n    return {"status": "ok"}\n'

SYSTEM_PROMPT = (
    "You are a senior engineer repairing generated FastAPI and React code. "
    "Fix ONLY the problems listed. Do not rename routes, change API paths, or "
    "rename request/response fields. Return ONLY a JSON object: "
    '{"summary": "<one sentence>", "files": [{"path": "<path exactly as '
    'given>", "content": "<the complete corrected file>"}]}. '
    "Return complete files, never fragments. Do not touch files you were not given."
)


def _fix(issue: dict[str, Any], action: str, file: str | None, method: str) -> dict[str, Any]:
    return {"code": issue.get("code", ""), "file": file, "method": method, "action": action}


# --------------------------------------------------------------------------
# pass 1: deterministic
# --------------------------------------------------------------------------
def apply_deterministic_fixes(
    issues: list[dict[str, Any]], backend_dir: Path, frontend_dir: Path | None = None,
) -> tuple[list[dict[str, Any]], set[int]]:
    """Return (fixes applied, indexes of issues that were handled)."""
    fixes: list[dict[str, Any]] = []
    handled: set[int] = set()
    backend_dir = Path(backend_dir)
    wiring_done = False
    flow_done = False
    flow_left: set[tuple[str, str]] = set()

    for index, issue in enumerate(issues):
        if issue.get("severity") != "error":
            continue
        code = issue.get("code")
        try:
            if code == "missing_requirements":
                path = backend_dir / "requirements.txt"
                current = path.read_text(encoding="utf-8") if path.is_file() else ""
                lines = [l for l in current.splitlines() if l.strip()]
                lowered = current.lower()
                for needed in ("fastapi", "uvicorn[standard]", "python-multipart"):
                    if needed.split("[")[0] not in lowered:
                        lines.append(needed)
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                fixes.append(_fix(issue, "Added missing packages to requirements.txt",
                                  "requirements.txt", "deterministic"))
                handled.add(index)

            elif code == "missing_storage":
                source = Path(__file__).resolve().parent.parent / "storage" / "json_store.py"
                if source.is_file():
                    target = backend_dir / "storage"
                    target.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target / "json_store.py")
                    (target / "__init__.py").touch()
                    fixes.append(_fix(issue, "Restored storage/json_store.py",
                                      "storage/json_store.py", "deterministic"))
                    handled.add(index)

            elif code == "missing_health":
                main_py = backend_dir / "main.py"
                text = main_py.read_text(encoding="utf-8")
                if re.search(r"^app\s*=\s*FastAPI\(", text, re.MULTILINE):
                    main_py.write_text(text.rstrip() + HEALTH_ROUTE, encoding="utf-8")
                    fixes.append(_fix(issue, "Added GET /health route", "main.py",
                                      "deterministic"))
                    handled.add(index)

            elif (frontend_dir is not None and (
                    code == "unmounted_provider"
                    or (code == "runtime_failure"
                        and "must be used within" in issue.get("message", "")))):
                # a page uses a context hook whose provider is never mounted
                if not wiring_done:
                    from generation.provider_wiring import repair_project_main

                    mounted = repair_project_main(Path(frontend_dir))
                    wiring_done = True
                    fixes.append(_fix(issue, "Mounted providers in src/main.jsx: "
                                      + (", ".join(mounted) or "none needed"),
                                      "src/main.jsx", "deterministic"))
                handled.add(index)

            elif frontend_dir is not None and code in FLOW_CODES:
                # login/register call signatures, token/user reads, route params: mechanical
                if not flow_done:
                    from workflow.flow_checks import all_flow_issues, fix_flow

                    for applied in fix_flow(Path(frontend_dir), backend_dir):
                        fixes.append(_fix(issue, applied["action"], applied["file"], "deterministic"))
                    flow_done = True
                    flow_left = {(i["code"], i.get("file", ""))
                                 for i in all_flow_issues(Path(frontend_dir), backend_dir, None)}
                if (code, issue.get("file", "")) not in flow_left:
                    handled.add(index)

            elif code == "bad_data_json" and issue.get("file"):
                target = (backend_dir / issue["file"]).resolve()
                if backend_dir.resolve() in target.parents:
                    target.write_text("[]", encoding="utf-8")
                    fixes.append(_fix(issue, "Reset corrupt data file to []",
                                      issue["file"], "deterministic"))
                    handled.add(index)
        except OSError:
            continue
    return fixes, handled


# --------------------------------------------------------------------------
# pass 2: LLM
# --------------------------------------------------------------------------
def _candidate_files(
    issue: dict[str, Any], backend_dir: Path, frontend_dir: Path,
) -> list[Path]:
    """Files that could contain the cause of one issue."""
    found: list[Path] = []
    names = []
    if issue.get("file"):
        names.append(str(issue["file"]))
    names += re.findall(r"[\w./\\-]+\.(?:py|jsx?|json)", issue.get("message", ""))
    auth_new_files: list[Path] = []
    if issue.get("stage") == "authentication":
        message = issue.get("message", "").lower()
        if "signup" in message or "registration" in message:
            names.append("src/pages/Signup.jsx")
            auth_new_files.append((frontend_dir / "src/pages/Signup.jsx").resolve())
        if "login" in message or "sign in" in message:
            names.append("src/pages/Login.jsx")
            auth_new_files.append((frontend_dir / "src/pages/Login.jsx").resolve())
        if "route" in message or "import" in message:
            names.append("src/App.jsx")

    for name in names:
        name = name.replace("\\\\", "/").lstrip("./")
        for base in (backend_dir, frontend_dir):
            candidate = (base / name).resolve()
            if (base.resolve() in candidate.parents and candidate.suffix in WRITABLE_SUFFIXES
                    and (candidate.is_file() or candidate in auth_new_files)):
                found.append(candidate)
                break
    if issue.get("stage") == "runtime":
        message = issue.get("message", "")
        found += _files_from_runtime_error(message, frontend_dir, found)
        # API smoke diagnostics include the failing route. Search backend route
        # declarations and route-registration files before considering main.py.
        route_match = re.search(r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s:'\"]+)", message, re.I)
        if route_match:
            route = route_match.group(1).rstrip("/")
            for path in sorted(backend_dir.rglob("*.py")):
                if any(part in {".venv", "venv", "__pycache__", "site-packages"} for part in path.parts):
                    continue
                try:
                    source = path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                # Route may be declared directly or split across router prefix + decorator.
                route_leaf = route.rsplit("/", 1)[-1]
                decorator_match = re.search(
                    r"@(?:\w+\.)?(?:get|post|put|patch|delete)\s*\(\s*['\"]"
                    + re.escape(route) + r"['\"]",
                    source,
                    flags=re.I,
                )
                leaf_decorator = re.search(
                    r"@(?:\w+\.)?(?:get|post|put|patch|delete)\s*\(\s*['\"][^'\"]*/"
                    + re.escape(route_leaf) + r"['\"]",
                    source,
                    flags=re.I,
                )
                if decorator_match or leaf_decorator:
                    found.append(path.resolve())
        # Keep stable order and only use main.py as last-resort context.
        unique = []
        for path in found:
            if path not in unique and path.is_file():
                unique.append(path)
        if not unique:
            main_py = backend_dir / "main.py"
            if main_py.is_file():
                unique.append(main_py.resolve())
        return unique[:MAX_FILES]
    return found


def _files_from_runtime_error(message: str, frontend_dir: Path, already: list[Path]) -> list[Path]:
    """Map common browser diagnostics to likely source files without grepping for generic methods."""
    src = Path(frontend_dir) / "src"
    if not src.is_dir():
        return []

    # Examples: "hotels.map is not a function", "Cannot read properties of
    # undefined (reading 'map')", and "Cannot read properties of undefined
    # (reading 'length')".
    identifiers: list[str] = []
    for pattern in (
        r"\b([A-Za-z_$][\w$]*)\s*\.\s*(?:map|filter|reduce|forEach)\s+is not a function",
        r"\b([A-Za-z_$][\w$]*)\s*\[\s*\d+\s*\].{0,80}(?:undefined|null)",
        r"\b([A-Za-z_$][\w$]*)\s+is not a function",
        r"\b([A-Za-z_$][\w$]*)\s+is not defined",
    ):
        identifiers.extend(re.findall(pattern, message, flags=re.I))

    hooks = re.findall(r"\b(use[A-Z]\w*)\b", message)
    providers = re.findall(r"\b([A-Z]\w*Provider)\b", message)

    # Extract the property named in browser errors, e.g. reading 'toFixed'.
    # Generic properties such as map and length are filtered below.
    read_properties = re.findall(
        r"""reading\s+['"]([A-Za-z_$][\w$]*)['"]""",
        message,
        flags=re.I,
    )

    identifiers.extend(hooks + providers + read_properties)

    # A property such as "map" or "length" alone is not a useful identifier.
    identifiers = [n for n in dict.fromkeys(identifiers)
                   if n.lower() not in {"map", "filter", "reduce", "foreach", "length"}]

    candidates: list[tuple[int, Path]] = []
    for path in sorted(src.rglob("*")):
        if (path.suffix not in {".js", ".jsx", ".ts", ".tsx"} or not path.is_file()
                or path.resolve() in {p.resolve() for p in already}):
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        score = sum(
            3 if re.search(rf"\b(?:const|let|var)\s+{re.escape(name)}\b", source)
            else 1 if re.search(rf"\b{re.escape(name)}\b", source)
            else 0
            for name in identifiers
        )
        if score:
            candidates.append((score, path.resolve()))

    candidates.sort(key=lambda item: (-item[0], str(item[1])))
    return [path for _, path in candidates[:MAX_FILES]]

def _label(path: Path, backend_dir: Path, frontend_dir: Path) -> str:
    for prefix, base in (("backend", backend_dir), ("frontend", frontend_dir)):
        if base.resolve() in path.parents:
            return f"{prefix}/{path.relative_to(base.resolve()).as_posix()}"
    return path.name


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM repair response must be a JSON object.")
    return data


def _content_is_sound(path: Path, content: str, original: str) -> str | None:
    """Return a rejection reason, or None when the replacement is acceptable."""
    if not content.strip():
        return "empty content"
    if len(original) > 400 and len(content) < len(original) * 0.3:
        return "replacement looks truncated"
    try:
        if path.suffix == ".py":
            ast.parse(content)
        elif path.suffix == ".json":
            json.loads(content)
    except (SyntaxError, json.JSONDecodeError) as exc:
        return f"replacement is not valid {path.suffix[1:]}: {exc}"
    return None


_SECRET_VALUE_PATTERNS = (
    re.compile(r"gsk_[A-Za-z0-9]{20,}"),
    re.compile(r"tvly-[A-Za-z0-9_-]{16,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in _SECRET_VALUE_PATTERNS)



async def llm_repair(
    issues: list[dict[str, Any]],
    backend_dir: Path,
    frontend_dir: Path,
    llm: LLM,
) -> list[dict[str, Any]]:
    """Ask the model to repair the files the remaining issues point at."""
    backend_dir, frontend_dir = Path(backend_dir), Path(frontend_dir)
    targets: dict[str, Path] = {}
    relevant: list[dict[str, Any]] = []
    total = 0

    for issue in issues:
        if issue.get("severity") != "error" or not issue.get("auto_fixable", True):
            continue
        if issue.get("code") == "secret_found":
            continue
        files = _candidate_files(issue, backend_dir, frontend_dir)
        if not files:
            continue
        relevant.append(issue)
        for path in files:
            label = _label(path, backend_dir, frontend_dir)
            if label in targets or len(targets) >= MAX_FILES:
                continue
            original_text = (path.read_text(encoding="utf-8", errors="ignore")
                             if path.is_file() else "")
            # Never transmit a file containing a recognizable credential, even
            # when the current issue is unrelated to security.
            if _contains_secret(original_text):
                continue
            size = len(original_text)
            if size > MAX_FILE_CHARS or total + size > MAX_TOTAL_CHARS:
                continue
            targets[label] = path
            total += size

    if not targets:
        return []

    blocks = []
    for label, path in targets.items():
        current = (path.read_text(encoding="utf-8", errors="ignore")
                   if path.is_file() else "(MISSING FILE: create this file)")
        blocks.append(f"=== {label} ===\n{current}")
    problems = "\n".join(f"- {i['message'][:1500]}" for i in relevant)
    prompt = f"PROBLEMS:\n{problems}\n\nFILES:\n" + "\n\n".join(blocks)

    response = await llm(
        prompt=prompt,
        system=SYSTEM_PROMPT,
        max_tokens=4096,
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    data = _parse_json(response)

    fixes: list[dict[str, Any]] = []
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        label, content = item.get("path"), item.get("content")
        path = targets.get(label) if isinstance(label, str) else None
        if path is None or not isinstance(content, str):
            continue  # never write a file we did not offer
        original = path.read_text(encoding="utf-8", errors="ignore") if path.is_file() else ""
        reason = _content_is_sound(path, content, original)
        if reason:
            fixes.append({"code": "llm_rejected", "file": label,
                          "method": "llm", "action": f"Rejected patch: {reason}"})
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content if content.endswith("\n") else content + "\n",
                        encoding="utf-8")
        fixes.append({"code": "llm_patch", "file": label, "method": "llm",
                      "action": str(data.get("summary") or "Patched file")[:300]})
    return fixes


async def _default_llm(**kwargs: Any) -> str:
    from core.groq_client import call_groq

    return await call_groq(**kwargs)


async def debug_project(
    issues: list[dict[str, Any]],
    backend_dir: str | Path,
    frontend_dir: str | Path,
    llm: LLM | None = None,
) -> list[dict[str, Any]]:
    """Apply deterministic fixes, then LLM fixes for what is left."""
    backend_dir, frontend_dir = Path(backend_dir), Path(frontend_dir)
    fixes, handled = apply_deterministic_fixes(issues, backend_dir, frontend_dir)
    remaining = [i for n, i in enumerate(issues) if n not in handled]
    if any(i.get("severity") == "error" and i.get("auto_fixable", True)
           and i.get("code") != "secret_found" for i in remaining):
        try:
            fixes += await llm_repair(remaining, backend_dir, frontend_dir,
                                      llm or _default_llm)
        except Exception as exc:  # a failed repair must not crash the graph
            fixes.append({"code": "llm_error", "file": None, "method": "llm",
                          "action": f"LLM repair failed: {exc}"[:300]})
    return fixes
