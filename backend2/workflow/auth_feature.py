"""Add a complete "Sign up" feature to a generated project.

Page + API call + route + link on the Login page + secure backend endpoints.
It is deterministic on purpose: it is always the same small shape, and it does not
depend on an LLM fitting four files into one answer. Used by

* the chat (HITL) when the user asks for sign up / registration, and
* the pipeline after a build: a project that has a Login page but no Sign up page
  gets one automatically.
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path
from typing import Any, Callable

SRC_SUFFIXES = {".jsx", ".js", ".tsx", ".ts"}
IGNORED = {"node_modules", "dist", ".git", "__pycache__"}
LOGIN_STEMS = {"login", "signin", "loginpage", "signinpage"}
SIGNUP_STEMS = {"signup", "register", "registration", "signuppage", "registerpage", "createaccount"}
BACKEND_MARK = "# --- AgentForge auth"
WANTS_SIGNUP_RE = re.compile(
    r"\b(sign[\s-]?up|registration|register|create (?:an |a )?account)\b", re.IGNORECASE)


def wants_signup(message: str) -> bool:
    return bool(WANTS_SIGNUP_RE.search(message or ""))


# ---- discovery ---------------------------------------------------------------
def src_files(frontend_dir: Path) -> dict[str, Path]:
    src = Path(frontend_dir) / "src"
    if not src.is_dir():
        return {}
    return {
        p.relative_to(src).as_posix(): p
        for p in sorted(src.rglob("*"))
        if p.is_file() and p.suffix in SRC_SUFFIXES
        and not any(part in IGNORED for part in p.parts)
    }


def _find(frontend_dir: Path, stems: set[str]) -> Path | None:
    for path in src_files(frontend_dir).values():
        if path.stem.lower() in stems:
            return path
    return None


def login_file(frontend_dir: Path) -> Path | None:
    return _find(frontend_dir, LOGIN_STEMS)


def signup_file(frontend_dir: Path) -> Path | None:
    return _find(frontend_dir, SIGNUP_STEMS)


def router_file(frontend_dir: Path) -> Path | None:
    for path in src_files(frontend_dir).values():
        if "<Routes" in path.read_text(encoding="utf-8", errors="ignore"):
            return path
    return None


def backend_main(backend_dir: Path) -> Path | None:
    main = Path(backend_dir) / "main.py"
    if main.is_file() and "FastAPI(" in main.read_text(encoding="utf-8", errors="ignore"):
        return main
    return None


_SIGNUP_ROUTE_RE = re.compile(r"""path\s*=\s*["']/(?:signup|register)["']""", re.IGNORECASE)


def has_signup(frontend_dir: Path) -> bool:
    """A Sign up page exists AND is reachable through a route."""
    if signup_file(frontend_dir) is None:
        return False
    return any(_SIGNUP_ROUTE_RE.search(p.read_text(encoding="utf-8", errors="ignore"))
               for p in src_files(frontend_dir).values())


# ---- verification (also used by the chat flow) ----------------------------------
def signup_problems(frontend_dir: Path) -> list[str]:
    """Return what is still missing for a working Sign up feature."""
    frontend_dir = Path(frontend_dir)
    if not (frontend_dir / "src").is_dir():
        return ["Frontend src directory does not exist."]
    files = src_files(frontend_dir)
    contents = {label: p.read_text(encoding="utf-8", errors="ignore") for label, p in files.items()}
    problems: list[str] = []

    signup = {l: c for l, c in contents.items() if Path(l).stem.lower() in SIGNUP_STEMS}
    if not signup:
        problems.append("Signup page component is missing.")
    signup_text = "\n".join(signup.values())

    if not any(_SIGNUP_ROUTE_RE.search(c) or re.search(r"""["']/signup["']\s*:""", c)
               for c in contents.values()):
        problems.append("The /signup route is missing.")

    # a call, not the component's own declaration (`function Signup() {`)
    calls_only = re.sub(r"(?:function\s+\w+|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?)\s*\(", "(", signup_text)
    if signup_text and not re.search(
            r"(?:register|sign_?up|create_?(?:user|account))\w*\s*\(|fetch\s*\(|axios|\bapi\.", calls_only, re.IGNORECASE):
        problems.append("Signup page does not call the registerUser API function.")

    login = "\n".join(c for l, c in contents.items() if Path(l).stem.lower() in LOGIN_STEMS)
    if login and not re.search(r"""(?:to|href)\s*=\s*["']/signup["']""", login, re.IGNORECASE):
        problems.append("Login page does not link to Signup.")
    return problems


# ---- templates ---------------------------------------------------------------
AUTH_API = '''export async function registerUser({ name, email, password }) {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, password }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : "Could not create your account.");
  }
  return data;
}
'''

AUTH_API_LOGIN = '''
export async function loginUser({ email, password }) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data && data.detail;
    throw new Error(typeof detail === "string" ? detail : "Invalid email or password.");
  }
  return data;
}
'''

SIGNUP_PAGE = '''import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { registerUser } from "__API_IMPORT__";

export default function Signup() {
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", confirm: "" });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const onChange = (event) => setForm({ ...form, [event.target.name]: event.target.value });

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    if (form.password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    if (form.password !== form.confirm) {
      setError("Passwords do not match.");
      return;
    }
    setLoading(true);
    try {
      await registerUser({ name: form.name.trim(), email: form.email.trim(), password: form.password });
      navigate("__LOGIN_PATH__", { state: { registered: true } });
    } catch (err) {
      setError(err.message || "Could not create your account.");
    } finally {
      setLoading(false);
    }
  }

  const input = "w-full rounded-lg border border-gray-300 bg-white px-3 py-2 text-gray-900 focus:outline-none focus:ring-2 focus:ring-blue-500";

  return (
    <div className="flex min-h-[70vh] items-center justify-center px-4 py-12">
      <form onSubmit={onSubmit} className="w-full max-w-md space-y-4 rounded-2xl bg-white p-8 shadow-lg">
        <h1 className="text-2xl font-bold text-gray-900">Create your account</h1>
        {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        <div>
          <label htmlFor="name" className="mb-1 block text-sm font-medium text-gray-700">Full name</label>
          <input id="name" name="name" type="text" required value={form.name} onChange={onChange} className={input} />
        </div>
        <div>
          <label htmlFor="email" className="mb-1 block text-sm font-medium text-gray-700">Email</label>
          <input id="email" name="email" type="email" required value={form.email} onChange={onChange} className={input} />
        </div>
        <div>
          <label htmlFor="password" className="mb-1 block text-sm font-medium text-gray-700">Password</label>
          <input id="password" name="password" type="password" required minLength={8} value={form.password} onChange={onChange} className={input} />
        </div>
        <div>
          <label htmlFor="confirm" className="mb-1 block text-sm font-medium text-gray-700">Confirm password</label>
          <input id="confirm" name="confirm" type="password" required value={form.confirm} onChange={onChange} className={input} />
        </div>
        <button type="submit" disabled={loading} className="w-full rounded-lg bg-blue-600 px-4 py-2 font-semibold text-white hover:bg-blue-700 disabled:opacity-60">
          {loading ? "Creating account..." : "Sign up"}
        </button>
        <p className="text-center text-sm text-gray-600">
          Already have an account?{" "}
          <Link to="__LOGIN_PATH__" className="font-medium text-blue-600 underline">Log in</Link>
        </p>
      </form>
    </div>
  );
}
'''

BACKEND_SNIPPET = '''# --- AgentForge auth (sign up / log in) ---------------------------------------
# Passwords are salted + hashed (PBKDF2) and kept in their own "auth_account"
# collection, so they are never returned by the generic CRUD endpoints.
import hashlib as _hashlib
import hmac as _hmac
import os as _os
import re as _re
import uuid as _uuid

from fastapi import HTTPException as _HTTPException

_EMAIL_RE = _re.compile(r"^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$")
_PBKDF2_ROUNDS = 120_000


def _hash_password(password: str) -> str:
    salt = _os.urandom(16)
    digest = _hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS)
    return salt.hex() + "$" + digest.hex()


def _check_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        actual = _hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _PBKDF2_ROUNDS)
        return _hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except ValueError:
        return False


def _find_account(email: str):
    email = email.strip().lower()
    for record in store.list_records("auth_account"):
        if str(record.get("email", "")).strip().lower() == email:
            return record
    return None


def _public_account(record: dict) -> dict:
    return {"id": record["id"], "name": record.get("name", ""), "email": record["email"]}


@app.post("/api/auth/register", status_code=201)
def register_account(payload: dict):
    name = str(payload.get("name") or "").strip()
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not name:
        raise _HTTPException(status_code=422, detail="Name is required")
    if not _EMAIL_RE.match(email):
        raise _HTTPException(status_code=422, detail="Enter a valid email address")
    if len(password) < 8:
        raise _HTTPException(status_code=422, detail="Password must be at least 8 characters")
    if _find_account(email) is not None:
        raise _HTTPException(status_code=409, detail="An account with this email already exists")
    record = {
        "id": str(_uuid.uuid4()),
        "name": name,
        "email": email,
        "password_hash": _hash_password(password),
    }
    store.create_record("auth_account", record)
    return _public_account(record)


@app.post("/api/auth/login")
def login_account(payload: dict):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    record = _find_account(email) if email else None
    if record is None or not _check_password(password, str(record.get("password_hash", ""))):
        raise _HTTPException(status_code=401, detail="Invalid email or password")
    return {"user": _public_account(record)}
'''


# ---- file helpers ------------------------------------------------------------
def _write(path: Path, text: str, backups: dict[Path, str | None]) -> None:
    if path not in backups:
        backups[path] = path.read_text(encoding="utf-8") if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def rollback(backups: dict[Path, str | None]) -> None:
    for path, original in backups.items():
        if original is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(original, encoding="utf-8")


_IMPORT_RE = re.compile(r"""^import\s+(?:[^;]*?\s+from\s+)?["'][^"']+["'];?[ \t]*$""", re.MULTILINE)


def _add_import(text: str, line: str) -> str:
    last = None
    for last in _IMPORT_RE.finditer(text):
        pass
    if last is None:
        return line + "\n" + text
    return text[:last.end()] + "\n" + line + text[last.end():]


def _rel_import(target: Path, start_dir: Path) -> str:
    rel = os.path.relpath(target, start_dir).replace("\\", "/")
    return rel if rel.startswith(".") else "./" + rel


def _patch_router(router: Path, signup: Path, backups: dict) -> None:
    text = router.read_text(encoding="utf-8")
    if _SIGNUP_ROUTE_RE.search(text):
        return
    if "</Routes>" not in text:
        raise ValueError("Could not find <Routes> ... </Routes> in the router file.")
    if not re.search(r"\bimport\s+Signup\b", text):
        text = _add_import(text, f'import Signup from "{_rel_import(signup, router.parent)}";')
    lines = text.split("\n")
    close = next(i for i, l in enumerate(lines) if "</Routes>" in l)
    indent = ""
    for l in reversed(lines[:close]):
        if "<Route" in l:
            indent = l[: len(l) - len(l.lstrip())]
            break
    lines.insert(close, f'{indent}<Route path="/signup" element={{<Signup />}} />')
    _write(router, "\n".join(lines), backups)


def _patch_login(login: Path, backups: dict) -> None:
    text = login.read_text(encoding="utf-8")
    if re.search(r"""["']/signup["']""", text):
        return
    if "</form>" not in text:
        raise ValueError("Could not find the login <form> to add a Sign up link to.")
    link = ('<p className="mt-4 text-center text-sm">New here?{" "}'
            '<Link to="/signup" className="font-medium underline">Create an account</Link></p>\n')
    head, _, tail = text.rpartition("</form>")
    text = head + link + "</form>" + tail
    m = re.search(r"""import\s*\{([^}]*)\}\s*from\s*["']react-router-dom["']""", text)
    if m:
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
        if "Link" not in names:
            names.append("Link")
            text = text[:m.start()] + "import { " + ", ".join(names) + ' } from "react-router-dom"' + text[m.end():]
    else:
        text = _add_import(text, 'import { Link } from "react-router-dom";')
    _write(login, text, backups)


def _login_path(router: Path) -> str:
    m = re.search(r"""<Route\s+path=["']([^"']+)["']\s+element=\{\s*<(?:Login|SignIn|Signin)\b""",
                  router.read_text(encoding="utf-8", errors="ignore"))
    return m.group(1) if m else "/login"


def _api_dir(src: Path) -> Path:
    for name in ("api", "services", "utils"):
        if (src / name).is_dir():
            return src / name
    return src / "api"


# ---- the feature -------------------------------------------------------------
def add_signup(frontend_dir: Path, backend_dir: Path, backups: dict[Path, str | None]) -> list[str]:
    """Write everything needed for Sign up. Returns changed labels. Raises on failure
    (the caller rolls back through `backups`)."""
    frontend_dir, backend_dir = Path(frontend_dir), Path(backend_dir)
    src = frontend_dir / "src"
    router = router_file(frontend_dir)
    if router is None:
        raise ValueError("Could not find the router (<Routes>) of the app.")
    main = backend_main(backend_dir)
    if main is None:
        raise ValueError("Could not find the FastAPI backend (backend/main.py).")
    main_text = main.read_text(encoding="utf-8")
    has_backend_auth = "/auth/register" in main_text and "/auth/login" in main_text
    if not has_backend_auth and not re.search(r"^store\s*=", main_text, re.MULTILINE):
        raise ValueError("The backend has no JSON store to keep accounts in.")

    login = login_file(frontend_dir)
    pages = login.parent if login else (src / "pages" if (src / "pages").is_dir() else router.parent)
    changed: list[str] = []

    def label(path: Path) -> str:
        return "frontend/src/" + path.relative_to(src).as_posix()

    # API client
    api_file = _api_dir(src) / "authApi.js"
    if api_file.exists():
        text = api_file.read_text(encoding="utf-8")
        if "registerUser" not in text:
            extra = AUTH_API + ("" if "loginUser" in text else AUTH_API_LOGIN)
            _write(api_file, text.rstrip() + "\n\n" + extra, backups)
            changed.append(label(api_file))
    else:
        _write(api_file, AUTH_API + AUTH_API_LOGIN, backups)
        changed.append(label(api_file))

    # page
    signup = signup_file(frontend_dir)
    if signup is None:
        signup = pages / "Signup.jsx"
        page = (SIGNUP_PAGE.replace("__API_IMPORT__", _rel_import(api_file, signup.parent))
                .replace("__LOGIN_PATH__", _login_path(router)))
        _write(signup, page, backups)
        changed.append(label(signup))

    # router + login link
    before = router.read_text(encoding="utf-8")
    _patch_router(router, signup, backups)
    if router.read_text(encoding="utf-8") != before:
        changed.append(label(router))
    if login is not None:
        before = login.read_text(encoding="utf-8")
        _patch_login(login, backups)
        if login.read_text(encoding="utf-8") != before:
            changed.append(label(login))

    # backend
    text = main.read_text(encoding="utf-8")
    if not has_backend_auth and BACKEND_MARK not in text:
        _write(main, text.rstrip() + "\n\n\n" + BACKEND_SNIPPET, backups)
        changed.append("backend/" + main.relative_to(backend_dir).as_posix())
    return changed


async def apply_signup(frontend_dir: Path, backend_dir: Path,
                       build_check: Callable[[Path], None] | None = None) -> dict[str, Any]:
    """add_signup + verification. On any failure the project is restored untouched."""
    from workflow.revise import _default_build_check, _missing_endpoints
    from workflow.validators import check_backend_static, has_errors

    build_check = build_check or _default_build_check
    backups: dict[Path, str | None] = {}
    missing_before = _missing_endpoints(Path(frontend_dir), Path(backend_dir))
    try:
        changed = add_signup(frontend_dir, backend_dir, backups)
        problems = signup_problems(frontend_dir)
        if problems:
            raise ValueError("; ".join(problems))
        issues = check_backend_static(Path(backend_dir))
        if has_errors(issues):
            raise ValueError(issues[0]["message"])
        new_missing = _missing_endpoints(Path(frontend_dir), Path(backend_dir)) - missing_before
        if new_missing:
            raise ValueError("Frontend API calls are missing from the backend: "
                             + ", ".join(sorted(new_missing)))
        await asyncio.to_thread(build_check, Path(frontend_dir))
    except Exception as exc:
        rollback(backups)
        return {"ok": False, "changed": [], "error": str(exc)[:400]}
    return {"ok": True, "changed": changed, "summary": (
        "Added a Sign up page (/signup) with name, email and password, a link to it on the "
        "Login page, and /api/auth/register + /api/auth/login in the backend. Passwords are "
        "salted and hashed.")}


async def ensure_auth(frontend_dir: Path, backend_dir: Path,
                      build_check: Callable[[Path], None] | None = None) -> dict[str, Any]:
    """After a build: a project with a Login page must also have a Sign up page."""
    if login_file(frontend_dir) is None or has_signup(frontend_dir):
        return {"ok": True, "changed": []}
    return await apply_signup(frontend_dir, backend_dir, build_check)
