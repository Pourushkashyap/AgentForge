"""Human-in-the-loop: change a finished project from a chat message.

Flow: pick the files that matter -> ask the model for full replacements ->
apply -> verify (build + backend checks) -> keep, or roll everything back.
A failed revision never leaves the project broken.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path
from typing import Any, Awaitable, Callable

LLM = Callable[..., Awaitable[str]]
BuildCheck = Callable[[Path], None]  # raises on failure

MAX_FILES = 4
MAX_TOTAL_CHARS = 18_000
MAX_REPAIR_ROUNDS = 2
LARGE_FILE_CHARS = 7_000   # above this a file is edited with small patches, not rewritten
MAX_MESSAGE = 2000
EDITABLE = {".jsx", ".js", ".css", ".py"}
NEW_FILE_SUFFIXES = {".jsx", ".js", ".css"}

SELECT_SYSTEM = (
    "You are a senior software engineer modifying an existing project. "
    "Analyze the user's request and identify ALL existing files required "
    "to implement the complete feature, not just one part of it. "

    "For a new page or feature, consider all dependencies, including: "
    "- Application routing files "
    "- Existing pages and components "
    "- Navigation and layout components "
    "- API service files "
    "- Authentication context or state management "
    "- Related backend files when required "

    "Do not select only an API file when the request requires a UI. "
    "Do not select only a page when routing and navigation also need changes. "

    f"Choose up to {MAX_FILES} relevant existing files. "
    "New files can be created separately in the editing step. "

    "Return ONLY valid JSON in this format: "
    '{"files": ["exact file label from the provided list"]}'
)

EDIT_SYSTEM = (
    "You are a senior full-stack software engineer. "
    "You modify existing React (Vite, Tailwind, react-router) + FastAPI projects. "

    "Return COMPLETE corrected files, never partial fragments. "
    "Preserve existing functionality, exports, props, API contracts and routes. "
    "Keep imports valid and include file extensions. "

    "You may use only these frontend packages: react, react-dom, "
    "react-router-dom, lucide-react, clsx, framer-motion, axios. "

    "IMPORTANT FEATURE COMPLETENESS RULES: "

    "Implement the complete user-requested feature, not just one supporting file. "

    "For a new page or feature: "
    "- Create the actual page component using new_files if it does not exist. "
    "- Register the page in the application's router. "
    "- Add navigation links from relevant existing pages. "
    "- Integrate the required API functions. "
    "- Reuse existing authentication context and components where appropriate. "

    "Never claim a page was created unless its component file is included "
    "in new_files or already exists and is correctly modified. "

    "Never claim routing was added unless the actual routing file is updated. "

    "Ensure all imports point to real files and all routes are valid. "
 
    "BACKEND FEATURE COMPLETENESS RULES: "
"- You may modify existing backend Python files. "
"- You may create new backend Python modules under backend/. "
"- Keep FastAPI imports and application wiring valid. "
"- Register new routers in the application when needed. "
"- Never create fake authentication or payment functionality. "
"- Do not replace secure authentication with generic CRUD. "
"- Keep frontend API URLs and backend routes consistent. "
 
    "Return ONLY valid JSON in this exact structure: "
    '{"summary": "<one or two plain sentences>", '
    '"files": [{"path": "<existing file label>", "content": "<complete file>"}], '
    '"new_files": [{"path": "frontend/src/... or backend/...", '
    '"content": "<complete file>"}]}'
)

def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("The model did not return a JSON object.")
    return data


def _clean_generated_file(content: str) -> str:
    """Remove optional Markdown fences from a generated source file."""
    content = (content or "").strip()

    match = re.match(
        r"^```(?:python|jsx|javascript|js|css|typescript|tsx)?\s*\n(.*?)\n```$",
        content,
        re.DOTALL | re.IGNORECASE,
    )
    if match:
        content = match.group(1)

    if not content.strip():
        raise ValueError("The model generated an empty file.")

    return content.rstrip() + "\n"


async def _generate_one_file(
    llm: LLM,
    *,
    request: str,
    history: str,
    label: str,
    original: str | None,
    max_tokens: int = 3000,
    extra: str = "",
) -> str:
    """Generate one complete file without wrapping its contents in JSON."""

    if original is None:
        task = "Create this new file."
        source = ""
    else:
        task = "Update this existing file. Return its complete replacement."
        source = f"\nEXISTING FILE CONTENT:\n{original}"

    system = (
        "You are an expert full-stack software engineer. "
        "Return only the complete source code for the requested file. "
        "Do not return JSON, Markdown explanations, or code fences. "
        "Preserve existing functionality and use valid imports. "
        "Do not use placeholder implementations or omit required logic. "
        "Keep frontend API paths consistent with the backend."
    )

    prompt = (
        f"USER REQUEST:\n{request}\n\n"
        f"RECENT CONVERSATION:\n{history}\n\n"
        f"{extra}"
        f"FILE TO GENERATE: {label}\n"
        f"TASK: {task}\n"
        f"{source}\n\n"
        "Return the complete file content only."
    )

    response = await llm(
        prompt=prompt,
        system=system,
        max_tokens=max_tokens,
        temperature=0.1,
    )

    return _clean_generated_file(response)


async def _call_json_with_retry(
    llm: LLM,
    *,
    prompt: str,
    system: str,
    max_tokens: int,
    temperature: float,
    label: str,
) -> dict[str, Any]:
    """Call the LLM and retry once with corrective JSON instructions."""

    last_error = ""

    for attempt in range(2):
        retry_prompt = prompt

        if attempt > 0:
            retry_prompt += (
                "\n\nIMPORTANT CORRECTION:\n"
                "Your previous response failed JSON validation.\n"
                "Return one complete, strictly valid JSON object.\n"
                "Escape all newlines, quotes and backslashes inside "
                "string values correctly.\n"
                "Do not use Markdown fences, comments or trailing commas.\n"
                "Do not omit or truncate any file content.\n"
                "Previous error: " + last_error[:500]
            )

        try:
            response = await llm(
                prompt=retry_prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format={"type": "json_object"},
            )

            return _parse_json(response)

        except Exception as exc:
            error_text = str(exc)
            is_json_error = (
                "json_validate_failed" in error_text.lower()
                or isinstance(exc, (ValueError, json.JSONDecodeError))
            )

            if not is_json_error:
                raise

            last_error = error_text

            if attempt == 0:
                print(
                    f"{label}: invalid JSON. Retrying with corrective instructions."
                )

    raise ValueError(
        f"{label} failed after corrective retry: {last_error[:500]}"
    )



def inventory(frontend_dir: Path, backend_dir: Path) -> dict[str, Path]:
    """Return editable frontend and backend source files."""
    files: dict[str, Path] = {}

    ignored = {
        "node_modules", "dist", ".git", "__pycache__",
        ".venv", "venv", "site-packages",
    }

    frontend_src = frontend_dir / "src"

    if frontend_src.is_dir():
        for path in sorted(frontend_src.rglob("*")):
            if (
                path.is_file()
                and path.suffix in {".jsx", ".js", ".tsx", ".ts", ".css"}
                and not any(part in ignored for part in path.parts)
            ):
                label = (
                    "frontend/src/"
                    + path.relative_to(frontend_src).as_posix()
                )
                files[label] = path

    if backend_dir.is_dir():
        for path in sorted(backend_dir.rglob("*.py")):
            if (
                path.is_file()
                and not any(part in ignored for part in path.parts)
            ):
                label = (
                    "backend/"
                    + path.relative_to(backend_dir).as_posix()
                )
                files[label] = path

    return files


def _history_text(history: list[dict[str, Any]] | None) -> str:
    lines = []
    for turn in (history or [])[-6:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        lines.append(f"{role}: {str(turn.get('content', ''))[:300]}")
    return "\n".join(lines)


async def _default_llm(**kwargs: Any) -> str:
    from core.groq_client import call_groq

    return await call_groq(**kwargs)


def _default_build_check(frontend_dir: Path) -> None:
    from generation.frontend_generator import _validate_frontend_build, validate_local_imports

    problems = validate_local_imports(frontend_dir)
    if problems:
        raise ValueError("Broken imports: " + "; ".join(problems[:3]))
    _validate_frontend_build(frontend_dir)


def _sound(path_suffix: str, content: str, original: str | None) -> str | None:
    if not content.strip():
        return "empty file"
    if original and len(original) > 400 and len(content) < len(original) * 0.3:
        return "looks truncated"
    if path_suffix == ".py":
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return f"python syntax error line {exc.lineno}"
    return None

def _validate_revision_api_contract(
    frontend_dir: Path,
    backend_dir: Path,
) -> None:
    from generation.frontend_api_analyzer import (
        analyze_frontend_api_requirements,
    )
    from generation.backend_route_analyzer import (
        analyze_backend_routes,
    )
    from generation.api_contract_validator import (
        validate_api_contract,
    )

    frontend_requirements = analyze_frontend_api_requirements(
        frontend_dir
    )

    # No detectable frontend API calls means nothing to compare.
    if not frontend_requirements:
        return

    backend_endpoints = analyze_backend_routes(backend_dir)

    result = validate_api_contract(
        frontend_requirements=frontend_requirements,
        backend_endpoints=backend_endpoints,
    )

    missing = result.get("missing_endpoints", [])

    if missing:
        details = [
            f"{item.get('method', '?')} {item.get('path', '?')}"
            for item in missing
        ]

        raise ValueError(
            "Frontend API calls are missing from the backend: "
            + ", ".join(details[:8])
        )

    # Request-field mismatches are checked only when the backend
    # analyzer can provide reliable request schemas. It currently
    # extracts routes, not Pydantic model schemas.

def _safe_new_path(
    label: str,
    frontend_dir: Path,
    backend_dir: Path | None = None,
) -> Path | None:
    if label.startswith("frontend/src/"):
        root = (frontend_dir / "src").resolve()
        relative = label[len("frontend/src/"):]

        if Path(relative).suffix not in {
            ".jsx", ".js", ".tsx", ".ts", ".css"
        }:
            return None

    elif label.startswith("backend/") and backend_dir is not None:
        root = backend_dir.resolve()
        relative = label[len("backend/"):]

        if Path(relative).suffix != ".py":
            return None

    else:
        return None

    path = (root / relative).resolve()

    if root not in path.parents:
        return None

    if path.exists():
        return None

    return path

def _validate_signup_feature(frontend_dir: Path) -> list[str]:
    """What is still missing for a working Sign up feature (shared with the pipeline)."""
    from workflow.auth_feature import signup_problems

    return signup_problems(frontend_dir)


# ---------------------------------------------------------------------------
# intent detection
# ---------------------------------------------------------------------------
ADD_SIGNUP_RE = re.compile(
    r"\b(?:add|create|build|make|need|want|include|implement|missing|without)\b[^.\n]{0,80}"
    r"\b(?:sign[\s-]?up|registration|register(?: page)?|create (?:an )?account)\b", re.IGNORECASE)
AUTH_INTENT_RE = re.compile(
    r"log\s*-?in|sign\s*-?in|sign\s*-?up|register|registration|credential|authenticat|"
    r"\b4(?:01|22)\b|unprocessable|password|session|token|account", re.IGNORECASE)
FLOW_INTENT_RE = re.compile(
    AUTH_INTENT_RE.pattern + r"|sub\s*-?page|route|\blinks?\b|navigat|\b404\b|not found|blank|"
    r"doesn.?t open|not open|detail|crash|not a function|undefined|cannot read|tolowercase|"
    r"white screen|runtime", re.IGNORECASE)
BUG_RE = re.compile(
    r"not working|doesn.?t work|isn.?t working|error|fix|fail|crash|bug|broken|can.?t|cannot|"
    r"unable|issue|problem|\b4\d\d\b|wrong|not (?:able|moving|opening)|not a function|"
    r"not defined|undefined|cannot read|typeerror|referenceerror|exception|white screen|blank",
    re.IGNORECASE)


IMAGE_FIX_RE = re.compile(
    r"(?=.*\b(?:images?|photos?|pictures?|thumbnails?|img)\b)"
    r"(?=.*(?:same|identical|duplicate|repeat|missing|broken|not\s+(?:show|load|display|appear|there)|"
    r"no\s+(?:image|photo|picture)|dummy|placeholder|add|put|generate|fix|show))", re.IGNORECASE | re.DOTALL)
NEW_FEATURE_RE = re.compile(
    r"\b(?:add|create|build|make|new)\s+(?:a|an|another|new)\s+[\w\s-]{0,25}\b"
    r"(?:page|section|component|carousel|slider|slideshow|form|feature)\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# snapshots (one rollback for everything a request touched)
# ---------------------------------------------------------------------------
def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _snapshot(files: dict[str, Path]) -> dict[Path, str]:
    return {path: _read(path) for path in files.values()}


def _restore(snapshot: dict[Path, str], created: list[Path]) -> None:
    for path, original in snapshot.items():
        if path.exists() and _read(path) != original:
            path.write_text(original, encoding="utf-8")
    for path in created:
        path.unlink(missing_ok=True)


def _changed_labels(frontend_dir: Path, backend_dir: Path, snapshot: dict[Path, str]) -> list[str]:
    return [label for label, path in inventory(frontend_dir, backend_dir).items()
            if path not in snapshot or _read(path) != snapshot[path]]


# ---------------------------------------------------------------------------
# verification: only problems the request INTRODUCED count
# ---------------------------------------------------------------------------
def _flow_issues(frontend_dir: Path, backend_dir: Path) -> list[dict[str, Any]]:
    from workflow.flow_checks import all_flow_issues

    return all_flow_issues(frontend_dir, backend_dir, None)


def _wiring_problems(frontend_dir: Path) -> list[str]:
    try:
        from generation.provider_wiring import validate_provider_wiring

        return list(validate_provider_wiring(frontend_dir))
    except Exception:
        return []


def _missing_endpoints(frontend_dir: Path, backend_dir: Path) -> set[str]:
    try:
        from generation.api_contract_validator import validate_api_contract
        from generation.backend_route_analyzer import analyze_backend_routes
        from generation.frontend_api_analyzer import analyze_frontend_api_requirements

        requirements = analyze_frontend_api_requirements(frontend_dir)
        if not requirements:
            return set()
        result = validate_api_contract(
            frontend_requirements=requirements,
            backend_endpoints=analyze_backend_routes(backend_dir))
        return {f"{i.get('method', '?')} {i.get('path', '?')}"
                for i in result.get("missing_endpoints", [])}
    except Exception:
        return set()


def _backend_errors(backend_dir: Path) -> set[str]:
    from workflow.validators import check_backend_static

    return {i["message"] for i in check_backend_static(backend_dir) if i.get("severity") == "error"}


def _baseline(frontend_dir: Path, backend_dir: Path) -> dict[str, Any]:
    counts: dict[tuple[str, str], int] = {}
    for issue in _flow_issues(frontend_dir, backend_dir):
        key = (issue["code"], issue.get("file", ""))
        counts[key] = counts.get(key, 0) + 1
    return {"flow": counts, "wiring": set(_wiring_problems(frontend_dir)),
            "missing": _missing_endpoints(frontend_dir, backend_dir),
            "backend": _backend_errors(backend_dir)}


def _runtime_auth_check(backend_dir: Path) -> list[str]:
    """Start the backend the way the preview does and really register + log in against it."""
    main = Path(backend_dir) / "main.py"
    if not main.is_file() or "/auth/login" not in _read(main):
        return []
    from generation.connected_runtime import auth_roundtrip, running_backend

    try:
        with running_backend(Path(backend_dir)) as base_url:
            return auth_roundtrip(base_url)
    except Exception as exc:
        return [f"The backend no longer starts: {str(exc)[:400]}"]


async def _refresh_images(frontend_dir: Path, backend_dir: Path,
                          build_check: BuildCheck) -> dict[str, Any] | None:
    """Every item its own, fitting picture; also draws pictures the code points at but are missing."""
    from generation.placeholder_art import fix_missing_images, refresh_placeholder_images

    sources = {p: _read(p) for p in (Path(frontend_dir) / "src").rglob("*") if p.is_file()}
    refreshed = refresh_placeholder_images(Path(backend_dir).parent)
    fixes = fix_missing_images(Path(frontend_dir))
    if not refreshed["changed"] and not fixes:
        return None
    if fixes:                                   # source files changed: they must still build
        try:
            await asyncio.to_thread(build_check, frontend_dir)
        except Exception:
            for path, text in sources.items():
                if path.exists() and _read(path) != text:
                    path.write_text(text, encoding="utf-8")
            fixes = []
            if not refreshed["changed"]:
                return None
    changed = ([f"frontend/public/images/catalog ({refreshed['changed']} pictures)"]
               if refreshed["changed"] else []) + [f"frontend/{f['file']}" for f in fixes]
    summary = (f"Gave {refreshed['changed']} items their own picture (theme: {refreshed['category']}). "
               if refreshed["changed"] else "") + (
        f"Drew {len(fixes)} missing image file(s). " if fixes else "")
    return {"ok": True, "changed": changed, "summary": (
        summary + "These are generated offline illustrations, different for every item. To use "
        "real photos, put them in frontend/public/images and tell me which item uses which file.")}


async def _find_problems(
    frontend_dir: Path, backend_dir: Path, labels: list[str], baseline: dict[str, Any],
    build_check: BuildCheck, check_signup: bool,
    runtime_check: Callable[[Path], list[str]] | None = None, ran: list[str] | None = None,
) -> list[str]:
    """Problems this change introduced. Pre-existing ones never block an unrelated edit."""
    ran = ran if ran is not None else []
    problems: list[str] = []
    if check_signup:
        problems += _validate_signup_feature(frontend_dir)

    ran.append("login / route / context / import-name checks")
    issues = _flow_issues(frontend_dir, backend_dir)
    counts: dict[tuple[str, str], int] = {}
    for issue in issues:
        key = (issue["code"], issue.get("file", ""))
        counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        if count > baseline["flow"].get(key, 0):
            problems += [i["message"] for i in issues
                         if (i["code"], i.get("file", "")) == key][:2]

    problems += [w for w in _wiring_problems(frontend_dir) if w not in baseline["wiring"]]
    problems += sorted(_backend_errors(backend_dir) - baseline["backend"])[:3]
    new_missing = _missing_endpoints(frontend_dir, backend_dir) - baseline["missing"]
    if new_missing:
        problems.append("Frontend API calls are missing from the backend: "
                        + ", ".join(sorted(new_missing)[:8]))

    ran.append("backend code + API contract")
    if not problems and any(label.startswith("frontend/") for label in labels):
        try:
            await asyncio.to_thread(build_check, frontend_dir)
            ran.append("frontend build")
        except Exception as exc:
            problems.append(f"Frontend build failed: {str(exc)[:300]}")
    if (not problems and runtime_check is not None
            and any(label.startswith("backend/") for label in labels)):
        found = await asyncio.to_thread(runtime_check, backend_dir)
        ran.append("backend start + register/login round-trip")
        problems += found
    return problems


# ---------------------------------------------------------------------------
# context + large-file patches
# ---------------------------------------------------------------------------
def _flow_context(frontend_dir: Path, backend_dir: Path) -> str:
    """Project facts a model cannot see when it rewrites ONE file in isolation."""
    from workflow import flow_checks as fc

    lines: list[str] = []
    shapes = fc.backend_auth_shapes(backend_dir)
    if "login" in shapes and shapes["login"].get("response"):
        lines.append("- Backend POST /api/auth/login takes { email, password } and returns "
                     f"{{ {', '.join(sorted(shapes['login']['response']))} }}. Save access_token and send it "
                     "as 'Authorization: Bearer <access_token>'. There is NO top-level 'token', 'id' or 'email'.")
    if "register" in shapes:
        lines.append("- Backend POST /api/auth/register takes { name, email, password } and returns the "
                     "public user only (no token): log in afterwards.")
    for label, path in fc.src_files(frontend_dir).items():
        for name, shape in fc.find_definitions(fc.read(path)).items():
            if shape["kind"] == "object":
                lines.append(f"- {name} is defined in src/{label} as {name}({{ {', '.join(shape['names'])} }}): call it with ONE object.")
            elif shape["kind"] in ("positional", "single"):
                lines.append(f"- {name} is defined in src/{label} as {name}({', '.join(shape['names'])}).")
    routes = sorted({r["path"] for r in fc.collect_routes(frontend_dir)})
    if routes:
        lines.append("- Defined routes: " + ", ".join(routes))
    if not lines:
        return ""
    return ("PROJECT FACTS (keep every call consistent with these):\n" + "\n".join(lines[:14])
            + "\n- Never call the API from a page AND from the context: call the context function once.\n\n")


def _replace_once(text: str, old: str, new: str) -> str:
    """Replace `old` (which must be unique) tolerating whitespace differences."""
    if old and text.count(old) == 1:
        return text.replace(old, new, 1)
    if old and text.count(old) > 1:
        raise ValueError("a patch matched several places; it must be unique")
    old_lines = [l.strip() for l in old.strip("\n").splitlines()]
    if not old_lines:
        raise ValueError("an empty patch")
    lines = text.split("\n")
    hits = [i for i in range(len(lines) - len(old_lines) + 1)
            if [l.strip() for l in lines[i:i + len(old_lines)]] == old_lines]
    if len(hits) != 1:
        raise ValueError("a patch did not match the file")
    i = hits[0]
    return "\n".join(lines[:i] + new.strip("\n").split("\n") + lines[i + len(old_lines):])


async def _patch_file(llm: LLM, *, request: str, history: str, label: str, original: str,
                      extra: str) -> str:
    """Edit a LARGE file with small exact patches instead of rewriting all of it."""
    data = await _call_json_with_retry(
        llm,
        prompt=(f"USER REQUEST:\n{request}\n\nRECENT CONVERSATION:\n{history}\n\n{extra}"
                f"FILE: {label}\n=== CONTENT ===\n{original}\n=== END ==="),
        system=(
            "You change one existing source file with minimal exact patches. Return ONLY JSON: "
            '{"edits":[{"old":"<text copied EXACTLY from the file>","new":"<replacement>"}]}. '
            "Each `old` must appear exactly once (3-12 lines is ideal), keep patches small, do not "
            "rewrite the file, keep existing behaviour and imports valid."),
        max_tokens=2200, temperature=0.1, label=f"Patch {label}")
    text = original
    for edit in data.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("old"), str) and isinstance(edit.get("new"), str):
            text = _replace_once(text, edit["old"], edit["new"])
    if text == original:
        raise ValueError(f"The model produced no usable patch for {label}.")
    return text if text.endswith("\n") else text + "\n"


async def _rewrite_file(llm: LLM, *, request: str, history: str, label: str, original: str | None,
                        extra: str, max_tokens: int = 3000) -> str:
    if original is not None and len(original) > LARGE_FILE_CHARS:
        return await _patch_file(llm, request=request, history=history, label=label,
                                 original=original, extra=extra)
    return await _generate_one_file(llm, request=request, history=history, label=label,
                                    original=original, max_tokens=max_tokens, extra=extra)


def _augment_selection(files: dict[str, Path], chosen: list[str],
                       issues: list[dict[str, Any]]) -> list[str]:
    """Files that contain a known login/signup problem are always part of the edit.

    A request that merely mentions "login" never pulls auth files in."""
    named: list[str] = []
    for issue in issues:
        if not str(issue.get("code", "")).startswith("auth_"):
            continue
        label = "frontend/" + str(issue.get("file", "")).lstrip("/")
        if label in files:
            named.append(label)   # only the file with the problem; signatures go in PROJECT FACTS
    return list(dict.fromkeys(named + chosen))[:MAX_FILES]


# ---------------------------------------------------------------------------
# the entry point
# ---------------------------------------------------------------------------
async def revise_project(
    frontend_dir: str | Path,
    backend_dir: str | Path,
    message: str,
    history: list[dict[str, Any]] | None = None,
    llm: LLM | None = None,
    build_check: BuildCheck | None = None,
    runtime_check: Callable[[Path], list[str]] | None = _runtime_auth_check,
) -> dict[str, Any]:
    """Apply one chat request. Returns {ok, summary, changed, error}.

    Order of work (cheapest first, each step verified, any failure rolls back):
      0. "add sign up"      -> deterministic page + route + API + backend endpoints
      1. login/route bugs   -> deterministic repair of call signatures / response reads / params
      2. the model          -> edits the files (patches for large files)
      3. deterministic repair again on whatever the model wrote
      4. verification, and up to MAX_REPAIR_ROUNDS corrections by the model
    """
    from workflow import auth_feature
    from workflow.flow_checks import fix_flow

    frontend_dir, backend_dir = Path(frontend_dir), Path(backend_dir)
    llm = llm or _default_llm
    build_check = build_check or _default_build_check
    message = (message or "").strip()
    if not message:
        return {"ok": False, "error": "Message is empty.", "changed": []}
    message = message[:MAX_MESSAGE]

    files = inventory(frontend_dir, backend_dir)
    if not files:
        return {"ok": False, "error": "No editable project files found.", "changed": []}

    had_signup = auth_feature.signup_file(frontend_dir) is not None
    adding_signup = (not had_signup) and bool(ADD_SIGNUP_RE.search(message))
    baseline = _baseline(frontend_dir, backend_dir)
    snapshot = _snapshot(files)
    created: list[Path] = []
    notes: list[str] = []
    ran: list[str] = []

    def checked() -> str:
        """What was really verified, so a summary never claims more than that."""
        return (f" Checked: {', '.join(dict.fromkeys(ran))}. Not tested in a browser."
                if ran else "")

    def fail(error: str) -> dict[str, Any]:
        _restore(snapshot, created)
        return {"ok": False, "changed": [],
                "error": "Your project was left unchanged because the edit did not pass "
                         f"checks: {error[:400]}"}

    # 0) sign up: deterministic and always complete
    if adding_signup:
        outcome = await auth_feature.apply_signup(frontend_dir, backend_dir, build_check)
        if outcome["ok"]:
            return outcome
        print(f"Sign up fast path failed ({outcome.get('error')}); falling back to the model.")

    # 0b) pictures: "every card has the same image" / "no images": draw a fitting one per item
    if IMAGE_FIX_RE.search(message) and not NEW_FEATURE_RE.search(message):
        outcome = await _refresh_images(frontend_dir, backend_dir, build_check)
        if outcome is not None:
            return outcome

    # 1) login / route bugs: mechanical repairs, zero tokens
    if FLOW_INTENT_RE.search(message):
        applied = fix_flow(frontend_dir, backend_dir)
        if applied:
            problems = await _find_problems(
                frontend_dir, backend_dir,
                _changed_labels(frontend_dir, backend_dir, snapshot), baseline, build_check, False,
                runtime_check, ran)
            if problems:
                _restore(snapshot, created)
                applied = []
            else:
                notes += [f"{a['file']}: {a['action']}" for a in applied]
                if BUG_RE.search(message) and not _flow_issues(frontend_dir, backend_dir):
                    return {"ok": True,
                            "changed": _changed_labels(frontend_dir, backend_dir, snapshot),
                            "summary": "Fixed automatically: " + "; ".join(notes)[:500]
                                       + "." + checked()
                                       + " If something is still wrong, describe it."}

    # 2) choose files
    files = inventory(frontend_dir, backend_dir)
    listing = "\n".join(f"{label} ({len(_read(p))} chars)" for label, p in files.items())
    chosen_data = await _call_json_with_retry(
        llm,
        prompt=(f"CONVERSATION:\n{_history_text(history)}\n\n"
                f"REQUEST: {message}\n\nFILES:\n{listing}"),
        system=SELECT_SYSTEM, max_tokens=300, temperature=0.1, label="File selection")
    chosen = [l for l in chosen_data.get("files", []) if isinstance(l, str) and l in files][:MAX_FILES]
    flow_now = _flow_issues(frontend_dir, backend_dir)
    chosen = _augment_selection(files, chosen, flow_now) if BUG_RE.search(message) else chosen
    if not chosen:
        if notes:  # the mechanical repairs are already in place and verified
            return {"ok": True, "changed": _changed_labels(frontend_dir, backend_dir, snapshot),
                    "summary": "Fixed automatically: " + "; ".join(notes)[:500]}
        return {"ok": False, "changed": [],
                "error": "I could not tell which files to change. Try describing the page or section."}

    sources = {label: _read(files[label]) for label in chosen}
    production = llm is _default_llm

    # 3) ask the model for the changes
    if production:
        history_text = _history_text(history)
        facts = _flow_context(frontend_dir, backend_dir)
        known = [i["message"] for i in flow_now][:3] if BUG_RE.search(message) else []
        if known:
            facts += "KNOWN PROBLEMS FOUND BY STATIC ANALYSIS (fix them):\n- " + "\n- ".join(known) + "\n\n"
        data: dict[str, Any] = {"summary": "Project changes generated.", "files": [], "new_files": []}
        try:
            for label, original in sources.items():
                content = await _rewrite_file(llm, request=message, history=history_text,
                                              label=label, original=original, extra=facts)
                data["files"].append({"path": label, "content": content})

            plan = await _call_json_with_retry(
                llm,
                prompt=(f"USER REQUEST:\n{message}\n\nEXISTING FILES:\n{chr(10).join(sources)}\n\n"
                        "Identify only the additional source files required. Do not list existing "
                        "files. Use frontend/src/... for frontend files and backend/... for backend "
                        'Python files. Return JSON: {"summary":"short summary","new_files":["path1"]}'),
                system=("You are a software project planner. Return only a small valid JSON object. "
                        "List only genuinely necessary new files. Do not include file contents."),
                max_tokens=800, temperature=0.1, label="New file planning")
            data["summary"] = str(plan.get("summary") or "Project changes generated.")
            for label in (plan.get("new_files") or [])[:5]:
                if isinstance(label, str) and _safe_new_path(label, frontend_dir, backend_dir):
                    content = await _generate_one_file(
                        llm, request=message, history=history_text, label=label,
                        original=None, max_tokens=2500, extra=facts)
                    data["new_files"].append({"path": label, "content": content})
        except Exception as exc:
            return fail(str(exc))
    else:
        blocks = "\n\n".join(f"=== {label} ===\n{src}" for label, src in sources.items())
        data = await _call_json_with_retry(
            llm,
            prompt=(f"CONVERSATION:\n{_history_text(history)}\n\n"
                    f"REQUEST: {message}\n\nFILES:\n{blocks}"),
            system=EDIT_SYSTEM, max_tokens=4500, temperature=0.2, label="File editing")

    # 4) apply, repair mechanically, verify, let the model correct what is still wrong
    try:
        _write_changes(data, files, set(sources), frontend_dir, backend_dir, created)
        notes += [f"{a['file']}: {a['action']}" for a in fix_flow(frontend_dir, backend_dir)]
        labels = _changed_labels(frontend_dir, backend_dir, snapshot)
        if not labels:
            return {"ok": False, "changed": [],
                    "error": "The assistant did not produce any usable changes."}

        for round_no in range(MAX_REPAIR_ROUNDS + 1):
            problems = await _find_problems(
                frontend_dir, backend_dir, labels, baseline, build_check,
                adding_signup, runtime_check, ran)
            if not problems:
                break
            if round_no == MAX_REPAIR_ROUNDS or not production:
                raise ValueError("; ".join(problems[:4]))
            print(f"Revision incomplete ({len(problems)} problem(s)); repair round {round_no + 1}.")
            await _repair_round(llm, message, history, problems, frontend_dir, backend_dir, labels)
            notes += [f"{a['file']}: {a['action']}" for a in fix_flow(frontend_dir, backend_dir)]
            labels = _changed_labels(frontend_dir, backend_dir, snapshot)
    except Exception as exc:
        return fail(str(exc))

    summary = str(data.get("summary") or "Changes applied.")[:500]
    if notes:
        summary += " Also corrected automatically: " + "; ".join(dict.fromkeys(notes))[:300]
    return {"ok": True, "changed": labels, "summary": (summary + checked())[:1100]}


def _write_changes(
    data: dict[str, Any], files: dict[str, Path], allowed: set[str],
    frontend_dir: Path, backend_dir: Path, created: list[Path],
) -> None:
    """Write the model's answer. Only files that were offered to it may be changed."""
    for item in data.get("files") or []:
        if not isinstance(item, dict):
            continue
        label, content = item.get("path"), item.get("content")
        if label not in allowed or not isinstance(content, str):
            continue
        path = files[label]
        problem = _sound(path.suffix, content, _read(path))
        if problem:
            raise ValueError(f"Rejected edit to {label}: {problem}.")
        path.write_text(content.rstrip() + "\n", encoding="utf-8")

    for item in data.get("new_files") or []:
        if not isinstance(item, dict):
            continue
        label, content = str(item.get("path", "")), item.get("content")
        path = _safe_new_path(label, frontend_dir, backend_dir)
        if path is None or not isinstance(content, str):
            continue
        problem = _sound(path.suffix, content, None)
        if problem:
            raise ValueError(f"Rejected new file {label}: {problem}.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        created.append(path)


async def _repair_round(
    llm: LLM, message: str, history: list[dict[str, Any]] | None, problems: list[str],
    frontend_dir: Path, backend_dir: Path, labels: list[str],
) -> None:
    """Send the remaining problems back to the model, file by file."""
    current = inventory(frontend_dir, backend_dir)
    named = ["frontend/" + m for text in problems for m in re.findall(r"src/[\w./-]+\.[jt]sx?", text)]
    targets = [l for l in dict.fromkeys(named + labels) if l in current][:3]
    facts = _flow_context(frontend_dir, backend_dir)
    request = (f"{message}\n\nYour previous change is INCOMPLETE. Fix every problem below and "
               "keep everything else exactly as it is:\n- " + "\n- ".join(problems[:6]))
    for label in targets:
        original = _read(current[label])
        content = await _rewrite_file(llm, request=request, history=_history_text(history),
                                      label=label, original=original, extra=facts)
        problem = _sound(current[label].suffix, content, original)
        if problem:
            raise ValueError(f"Rejected repair of {label}: {problem}.")
        current[label].write_text(content.rstrip() + "\n", encoding="utf-8")
