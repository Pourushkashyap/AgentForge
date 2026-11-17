"""'loginUser is not a function': names that are read but never provided, and honest chat results."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from generation.connected_runtime import auth_roundtrip
from workflow import flow_checks as fc
from workflow import revise

CONTEXT = '''import React, { createContext, useState } from "react";

export const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const login = async ({ email, password }) => { setUser({ email }); };
  const register = async ({ name, email, password }) => ({ name });
  const logout = () => setUser(null);
  const value = { user, login, register, logout };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
'''
USE_AUTH = '''import { useContext } from "react";
import { AuthContext } from "./AuthContext.jsx";

export function useAuth() {
  return useContext(AuthContext);
}
'''
LOGIN = '''import React from "react";
import { useAuth } from "../context/useAuth.js";

export default function Login() {
  const { loginUser } = useAuth();
  async function onSubmit(e) {
    e.preventDefault();
    await loginUser({ email: "a@b.c", password: "x" });
  }
  return <form onSubmit={onSubmit}>login</form>;
}
'''
API = '''export async function login({ email, password }) {
  return fetch("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
}

export async function register({ name, email, password }) {
  return fetch("/api/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) });
}
'''


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def project(tmp_path):
    fe, be = tmp_path / "frontend", tmp_path / "backend"
    _write(fe, "src/context/AuthContext.jsx", CONTEXT)
    _write(fe, "src/context/useAuth.js", USE_AUTH)
    _write(fe, "src/pages/Login.jsx", LOGIN)
    _write(fe, "src/api/authApi.js", API)
    _write(fe, "src/main.jsx", 'import { AuthProvider } from "./context/AuthContext.jsx";\n<AuthProvider><App /></AuthProvider>\n')
    _write(be, "main.py", 'from fastapi import FastAPI\napp = FastAPI()\n\n@app.post("/api/auth/login")\ndef login(payload: dict):\n    return {}\n')
    _write(be, "requirements.txt", "fastapi\nuvicorn\n")
    return fe, be


def test_a_name_the_hook_never_provides_is_found_even_when_hook_and_provider_are_separate_files(tmp_path):
    fe, _ = project(tmp_path)
    issues = fc.check_context_properties(fe)
    assert [i["code"] for i in issues] == ["context_property_missing"]
    assert "loginUser is not a function" in issues[0]["message"] and "always undefined" in issues[0]["message"]


def test_the_missing_name_is_provided_as_an_alias_of_the_function_that_exists(tmp_path):
    fe, _ = project(tmp_path)
    fixes = fc.fix_context_properties(fe)
    assert fixes and fc.check_context_properties(fe) == []
    assert "loginUser: login" in (fe / "src/context/AuthContext.jsx").read_text()
    assert fc.fix_context_properties(fe) == []                      # idempotent


def test_a_name_with_no_same_role_function_is_left_for_the_model(tmp_path):
    fe, _ = project(tmp_path)
    (fe / "src/pages/Login.jsx").write_text(LOGIN.replace("loginUser", "signInAnonymously"), encoding="utf-8")
    ctx = (fe / "src/context/AuthContext.jsx").read_text().replace("const login =", "const doSomething =").replace(
        "value = { user, login,", "value = { user, doSomething,")
    (fe / "src/context/AuthContext.jsx").write_text(ctx, encoding="utf-8")
    assert fc.check_context_properties(fe) and fc.fix_context_properties(fe) == []


def test_calling_a_member_a_module_does_not_export_is_found_and_aliased(tmp_path):
    fe, _ = project(tmp_path)
    page = _write(fe, "src/pages/Signup.jsx", '''import * as authApi from "../api/authApi.js";
export default function Signup() {
  return <button onClick={() => authApi.registerUser({ name: "a" })}>go</button>;
}
''')
    issues = fc.check_module_members(fe)
    assert [i["code"] for i in issues] == ["import_member_missing"]
    assert fc.fix_module_members(fe) and fc.check_module_members(fe) == []
    assert "export const registerUser = register;" in (fe / "src/api/authApi.js").read_text()
    assert page.exists()


def test_a_default_exported_api_object_is_handled_too(tmp_path):
    fe, _ = project(tmp_path)
    _write(fe, "src/api/authApi.js", "const login = async () => 1;\nexport default { login };\n")
    _write(fe, "src/pages/L.jsx", 'import authApi from "../api/authApi.js";\nauthApi.loginUser({});\n')
    assert fc.fix_module_members(fe) and "loginUser: login" in (fe / "src/api/authApi.js").read_text()


def test_names_unrelated_to_auth_are_never_touched(tmp_path):
    fe, _ = project(tmp_path)
    _write(fe, "src/pages/P.jsx", 'import * as authApi from "../api/authApi.js";\nauthApi.getPets();\n')
    assert fc.check_module_members(fe) == []


def test_chat_fixes_login_user_is_not_a_function_without_the_model_and_says_what_it_checked(tmp_path, monkeypatch):
    fe, be = project(tmp_path)

    async def boom(**_):
        raise AssertionError("the model must not be needed")

    monkeypatch.setattr(revise, "_default_llm", boom)
    result = asyncio.run(revise.revise_project(
        fe, be, "login page shows: loginUser is not a function", build_check=lambda d: None,
        runtime_check=None))
    assert result["ok"], result
    assert "loginUser: login" in (fe / "src/context/AuthContext.jsx").read_text()
    assert "Checked:" in result["summary"] and "Not tested in a browser" in result["summary"]


def test_a_backend_change_that_stops_login_working_is_rolled_back(tmp_path, monkeypatch):
    fe, be = project(tmp_path)
    original = (be / "main.py").read_text()

    async def llm(**kw):
        if "senior software engineer" in kw["system"]:
            return json.dumps({"files": ["backend/main.py"]})
        if "software project planner" in kw["system"]:
            return json.dumps({"summary": "Switched the JWT configuration.", "new_files": []})
        return original + "\n# changed\n"

    monkeypatch.setattr(revise, "_default_llm", llm)
    monkeypatch.setattr(revise, "MAX_REPAIR_ROUNDS", 0)
    result = asyncio.run(revise.revise_project(
        fe, be, "make the JWT secret generated automatically", build_check=lambda d: None,
        runtime_check=lambda d: ["The backend no longer starts: ModuleNotFoundError: dotenv"]))
    assert not result["ok"] and "dotenv" in result["error"]
    assert (be / "main.py").read_text() == original


# ---- the real round-trip against a (stub) HTTP server ------------------------------------
def _serve(behaviour):
    users = {}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path.endswith("/register"):
                if behaviour == "no-auth":
                    return self._send(404, {"detail": "Not Found"})
                users[body["email"]] = body["password"]
                return self._send(201, {"id": "1", "email": body["email"]})
            if self.path.endswith("/login"):
                if behaviour == "login-500":
                    return self._send(500, {"detail": "JWT_SECRET_KEY missing"})
                ok = users.get(body["email"]) == body["password"] or behaviour == "accepts-any"
                return self._send(200, {"access_token": "t", "user": {}}) if ok else self._send(401, {})
            self._send(404, {})

        def do_GET(self):
            self._send(200, {"id": "1"} if self.headers.get("Authorization") else {})

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _roundtrip(behaviour):
    server, url = _serve(behaviour)
    try:
        return auth_roundtrip(url)
    finally:
        server.shutdown()


def test_roundtrip_passes_for_a_backend_where_login_really_works():
    assert _roundtrip("ok") == []


def test_roundtrip_reports_a_backend_that_cannot_log_in():
    problems = _roundtrip("login-500")
    assert problems and "login" in problems[0] and "JWT_SECRET_KEY" in problems[0]


def test_roundtrip_catches_a_login_that_accepts_any_password():
    assert any("WRONG password" in p for p in _roundtrip("accepts-any"))


def test_roundtrip_skips_projects_without_auth():
    assert _roundtrip("no-auth") == []
