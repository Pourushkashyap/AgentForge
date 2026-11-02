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
    """Verify that a Signup feature is actually integrated into the frontend."""

    src = frontend_dir / "src"
    problems: list[str] = []

    if not src.is_dir():
        return ["Frontend src directory does not exist."]

    files = {
        path.relative_to(src).as_posix(): path
        for path in src.rglob("*")
        if path.is_file() and path.suffix in {".jsx", ".js"}
    }

    contents = {
        label: path.read_text(encoding="utf-8", errors="ignore")
        for label, path in files.items()
    }

    # 1. Signup page component must exist.
    signup_files = {
        label: content
        for label, content in contents.items()
        if Path(label).stem.lower() == "signup"
    }

    if not signup_files:
        problems.append("Signup page component is missing.")
        signup_content = ""
    else:
        signup_content = "\n".join(signup_files.values())

    # 2. Application must register the /signup route.
    all_content = "\n".join(contents.values())

    if not re.search(
        r"""(?:path\s*=\s*["']/signup["']|["']/signup["']\s*:)""",
        all_content,
        re.IGNORECASE,
    ):
        problems.append("The /signup route is missing.")

    # 3. Signup page should connect to the registration API.
    if signup_content and not re.search(
        r"\bregisterUser\b",
        signup_content,
    ):
        problems.append(
            "Signup page does not call the registerUser API function."
        )

    # 4. Login page should provide navigation to Signup.
    login_content = "\n".join(
        content
        for label, content in contents.items()
        if Path(label).stem.lower() == "login"
    )

    if not re.search(
        r"""(?:to|href)\s*=\s*["']/signup["']""",
        login_content,
        re.IGNORECASE,
    ):
        problems.append("Login page does not link to Signup.")

    return problems


async def revise_project(
    frontend_dir: str | Path,
    backend_dir: str | Path,
    message: str,
    history: list[dict[str, Any]] | None = None,
    llm: LLM | None = None,
    build_check: BuildCheck | None = None,
) -> dict[str, Any]:
    """Apply one chat request. Returns {ok, summary, changed, error}."""
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

    # 1) choose files
    listing = "\n".join(f"{label} ({len(p.read_text(encoding='utf-8', errors='ignore'))} chars)"
                        for label, p in files.items())
    chosen_data = await _call_json_with_retry(
        llm,
        prompt=(
            f"CONVERSATION:\n{_history_text(history)}\n\n"
            f"REQUEST: {message}\n\nFILES:\n{listing}"
        ),
        system=SELECT_SYSTEM,
        max_tokens=300,
        temperature=0.1,
        label="File selection",
    )

    chosen = [
        l for l in chosen_data.get("files", [])
        if isinstance(l, str) and l in files
    ][:MAX_FILES]
    if not chosen:
        return {"ok": False, "changed": [],
                "error": "I could not tell which files to change. Try describing the page or section."}

    sources = {label: files[label].read_text(encoding="utf-8") for label in chosen}
    if sum(len(s) for s in sources.values()) > MAX_TOTAL_CHARS:
        return {"ok": False, "changed": [],
                "error": "That change touches files that are too large to edit in one step. Try a smaller request."}

    # 2) ask for edits
    blocks = "\n\n".join(f"=== {label} ===\n{src}" for label, src in sources.items())
    data = await _call_json_with_retry(
    llm,
        prompt=(
            f"CONVERSATION:\n{_history_text(history)}\n\n"
            f"REQUEST: {message}\n\nFILES:\n{blocks}"
        ),
        system=EDIT_SYSTEM,
        max_tokens=4500,
        temperature=0.2,
        label="File editing",
    )

    # 3) apply with backups
    backups: dict[Path, str | None] = {}
    changed: list[str] = []

    def rollback() -> None:
        for path, original in backups.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(original, encoding="utf-8")

    
    try:
        # 1. Update existing files selected and provided to the LLM.
        for item in data.get("files") or []:
            if not isinstance(item, dict):
                continue

            label = item.get("path")
            content = item.get("content")

            # Never modify files that were not offered to the LLM.
            if label not in sources or not isinstance(content, str):
                continue

            path = files[label]

            problem = _sound(
                path.suffix,
                content,
                sources[label],
            )

            if problem:
                raise ValueError(
                    f"Rejected edit to {label}: {problem}."
                )

            backups[path] = sources[label]

            path.write_text(
                content.rstrip() + "\n",
                encoding="utf-8",
            )

            changed.append(label)

        # 2. Create new frontend/backend files.
        for item in data.get("new_files") or []:
            if not isinstance(item, dict):
                continue

            label = str(item.get("path", ""))
            content = item.get("content")

            path = _safe_new_path(
                label,
                frontend_dir,
                backend_dir,
            )

            if path is None or not isinstance(content, str):
                continue

            problem = _sound(path.suffix, content, None)

            if problem:
                raise ValueError(
                    f"Rejected new file {label}: {problem}."
                )

            path.parent.mkdir(parents=True, exist_ok=True)

            backups[path] = None

            path.write_text(
                content.rstrip() + "\n",
                encoding="utf-8",
            )

            changed.append(label)

        if not changed:
            return {
                "ok": False,
                "changed": [],
                "error": "The assistant did not produce any usable changes.",
            }

        
        
        # 4) Verify requested Signup feature completeness.
        signup_request = bool(
            re.search(
                r"\b(signup|sign up|registration|register|create an account)\b",
                message,
                re.IGNORECASE,
            )
        )

        if signup_request:
            problems = _validate_signup_feature(frontend_dir)

            if problems:
                raise ValueError(
                    "Signup feature is incomplete: "
                    + "; ".join(problems)
                )

        
        # 4) verify
                # Validate backend regardless of which side was edited.
        from workflow.validators import check_backend_static, has_errors

        issues = check_backend_static(backend_dir)
        if has_errors(issues):
            raise ValueError(issues[0]["message"])

        # Frontend/backend API compatibility must be checked
        # even when only frontend files changed.
        _validate_revision_api_contract(
            frontend_dir,
            backend_dir,
        )

        if any(c.startswith("frontend/") for c in changed):
            await asyncio.to_thread(build_check, frontend_dir)
    except Exception as exc:
        rollback()
        return {"ok": False, "changed": [],
                "error": f"Your project was left unchanged because the edit did not pass checks: {str(exc)[:400]}"}

    return {"ok": True, "changed": changed,
            "summary": str(data.get("summary") or "Changes applied.")[:600]}
