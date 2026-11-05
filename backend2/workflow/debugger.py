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
    if issue.get("stage") == "authentication":
        message = issue.get("message", "").lower()
        if "signup" in message or "registration" in message:
            names.append("src/pages/Signup.jsx")
        if "login" in message or "sign in" in message:
            names.append("src/pages/Login.jsx")
        if "route" in message or "import" in message:
            names.append("src/App.jsx")

    for name in names:
        name = name.replace("\\", "/").lstrip("./")
        for base in (backend_dir, frontend_dir):
            candidate = (base / name).resolve()
            if (base.resolve() in candidate.parents and candidate.is_file()
                    and candidate.suffix in WRITABLE_SUFFIXES):
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
                if (route in source or route_leaf in source
                        or re.search(r"include_router\\s*\\(", source)):
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
    """Browser errors name identifiers, not always paths: grep the source for them."""
    src = Path(frontend_dir) / "src"
    if not src.is_dir():
        return []
    names = set(re.findall(r"\b([A-Za-z_$][\w$]*) is not a function", message))
    names |= set(re.findall(r"\b([A-Za-z_$][\w$]*) is not defined", message))
    names |= set(re.findall(r"\buse[A-Z]\w*\b", message))
    names |= {n for n in re.findall(r"\b[A-Z]\w*Provider\b", message)}
    props = re.findall(r"\(reading '([A-Za-z_$][\w$]*)'\)", message)
    if not names and not props:
        return []
    result: list[Path] = []
    for path in sorted(src.rglob("*")):
        if path.suffix not in {".js", ".jsx"} or not path.is_file() or path in already:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if (any(re.search(r"\b%s\b" % re.escape(n), text) for n in names)
                or any(re.search(r"\.%s\b" % re.escape(p), text) for p in props)):
            result.append(path.resolve())
    return result[:MAX_FILES]


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
            size = len(path.read_text(encoding="utf-8", errors="ignore"))
            if size > MAX_FILE_CHARS or total + size > MAX_TOTAL_CHARS:
                continue
            targets[label] = path
            total += size

    if not targets:
        return []

    blocks = []
    for label, path in targets.items():
        blocks.append(f"=== {label} ===\n{path.read_text(encoding='utf-8')}")
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
        original = path.read_text(encoding="utf-8")
        reason = _content_is_sound(path, content, original)
        if reason:
            fixes.append({"code": "llm_rejected", "file": label,
                          "method": "llm", "action": f"Rejected patch: {reason}"})
            continue
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
