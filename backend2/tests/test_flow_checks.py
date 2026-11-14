"""Data-flow checks (auth call signatures, token reads, routes) and the chat edits built on them."""
import asyncio
import json
from pathlib import Path

from workflow import flow_checks as fc
from workflow import revise
from workflow.debugger import apply_deterministic_fixes

BACKEND = '''from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()


class RegisterInput(BaseModel):
    name: str = Field(default="User", min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)


class LoginInput(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


@app.post("/api/auth/register", status_code=201)
def register_user(payload: RegisterInput):
    return {"id": "1", "name": payload.name, "email": payload.email, "role": "user"}


@app.post("/api/auth/login")
def login_user(payload: LoginInput):
    return {
        "access_token": "x",
        "token_type": "bearer",
        "expires_in": 3600,
        "user": {"id": "1", "name": "A", "email": payload.email, "role": "user"},
    }
'''
API = '''export async function login({ email, password }) {
  const response = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  const data = await response.json();
  localStorage.setItem("token", data.token);
  return { id: data.id, name: data.name, email: data.email, token: data.token };
}

export async function register({ name, email, password }) {
  const response = await fetch("/api/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, password }),
  });
  return response.json();
}
'''
CONTEXT = '''import React, { createContext, useContext, useState } from "react";
import { login as apiLogin, register as apiRegister } from "../api/authApi.js";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const handleLogin = async (email, password) => {
    const data = await apiLogin(email, password);
    setUser({ id: data.id, name: data.name, email: data.email });
  };
  const handleRegister = async (name, email, password) => {
    return apiRegister(name, email, password);
  };
  const value = { user, login: handleLogin, register: handleRegister };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);
'''
LOGIN = '''import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext.jsx";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  async function onSubmit(e) {
    e.preventDefault();
    await login(email, password);
    navigate("/");
  }
  return (
    <form onSubmit={onSubmit}>
      <input value={email} onChange={(e) => setEmail(e.target.value)} />
      <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
      <button type="submit">Log in</button>
      <Link to="/signup">Sign up</Link>
    </form>
  );
}
'''
SIGNUP = '''import React, { useState } from "react";
import { useAuth } from "../context/AuthContext.jsx";

export default function Signup() {
  const { register } = useAuth();
  const [form, setForm] = useState({ name: "", email: "", password: "" });
  async function onSubmit(e) {
    e.preventDefault();
    await register(form.name, form.email, form.password);
  }
  return <form onSubmit={onSubmit}><input value={form.name} onChange={() => {}} /></form>;
}
'''
JOB = '''import React from "react";
import { useParams } from "react-router-dom";

export default function JobDetails() {
  const { jobId } = useParams();
  return <h1>{jobId}</h1>;
}
'''
APP = '''import React from "react";
import { Routes, Route, Link } from "react-router-dom";
import Home from "./pages/Home.jsx";
import Login from "./pages/Login.jsx";
import Signup from "./pages/Signup.jsx";
import JobDetails from "./pages/JobDetails.jsx";

export default function App() {
  return (
    <div>
      <Link to="/job/5">Job five</Link>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/jobs/:id" element={<JobDetails />} />
      </Routes>
    </div>
  );
}
'''


def project(tmp_path):
    fe, be = tmp_path / "frontend", tmp_path / "backend"
    for rel, text in {
        "src/App.jsx": APP, "src/pages/Home.jsx": "export default function Home(){return null}\n",
        "src/pages/Login.jsx": LOGIN, "src/pages/Signup.jsx": SIGNUP, "src/pages/JobDetails.jsx": JOB,
        "src/context/AuthContext.jsx": CONTEXT, "src/api/authApi.js": API,
    }.items():
        (fe / rel).parent.mkdir(parents=True, exist_ok=True)
        (fe / rel).write_text(text, encoding="utf-8")
    be.mkdir()
    (be / "main.py").write_text(BACKEND, encoding="utf-8")
    (be / "requirements.txt").write_text("fastapi\nuvicorn\n", encoding="utf-8")
    (be / "storage").mkdir()
    (be / "storage" / "json_store.py").write_text("x = 1\n", encoding="utf-8")
    return fe, be


def codes(issues):
    return sorted(i["code"] for i in issues)


def test_all_the_planted_bugs_are_detected(tmp_path):
    fe, be = project(tmp_path)
    found = codes(fc.all_flow_issues(fe, be, None))
    assert found.count("auth_call_mismatch") == 2          # context -> apiLogin, apiRegister
    assert "route_param_mismatch" in found                  # jobId vs :id
    assert "link_to_missing_route" in found                 # /job/5
    assert "auth_response_mismatch" in found                # data.token / flat id


def test_mechanical_fixes_repair_calls_params_and_token_reads(tmp_path):
    fe, be = project(tmp_path)
    fixes = fc.fix_flow(fe, be)
    assert fixes
    ctx = (fe / "src/context/AuthContext.jsx").read_text()
    assert "apiLogin({ email, password })" in ctx
    assert "apiRegister({ name, email, password })" in ctx
    assert "const { id: jobId } = useParams()" in (fe / "src/pages/JobDetails.jsx").read_text()
    api = (fe / "src/api/authApi.js").read_text()
    assert "data.access_token" in api and "data.token" not in api
    assert "data.user?.id" in api
    left = codes(fc.all_flow_issues(fe, be, None))
    assert "auth_call_mismatch" not in left and "route_param_mismatch" not in left
    assert left == ["link_to_missing_route"]  # not mechanical: handed to the model


def test_passing_the_result_of_an_api_call_is_flagged_but_not_guessed(tmp_path):
    fe, be = project(tmp_path)
    (fe / "src/pages/Login.jsx").write_text(LOGIN.replace("await login(email, password);",
        'const { token, user } = await login({ email, password });\n    await login(token, user);'),
        encoding="utf-8")
    fc.fix_flow(fe, be)
    left = [i for i in fc.all_flow_issues(fe, be, None) if i["code"] == "auth_call_mismatch"]
    assert left and "RESULT" in left[0]["message"]
    assert "login(token, user)" in (fe / "src/pages/Login.jsx").read_text()


def test_debugger_handles_flow_issues_without_the_model(tmp_path):
    fe, be = project(tmp_path)
    issues = fc.all_flow_issues(fe, be, None)
    fixes, handled = apply_deterministic_fixes(issues, be, fe)
    assert fixes and handled
    unhandled = [i for n, i in enumerate(issues) if n not in handled]
    assert {i["code"] for i in unhandled} <= {"link_to_missing_route"}


def test_a_clean_project_has_no_findings(tmp_path):
    fe, be = project(tmp_path)
    fc.fix_flow(fe, be)
    (fe / "src/App.jsx").write_text(APP.replace('/job/5', '/jobs/5'), encoding="utf-8")
    assert fc.all_flow_issues(fe, be, None) == []


def test_auth_contract_is_overwritten_with_what_the_backend_really_returns():
    from generation.api_endpoints import normalize_requirement

    login = normalize_requirement({"method": "POST", "path": "/api/auth/login",
                                   "request_fields": ["username"], "response_fields": ["token", "id"]})
    assert login["request_fields"] == ["email", "password"]
    assert login["response_fields"] == ["access_token", "token_type", "expires_in", "user"]
    assert "ONE object" in login["purpose"]


# ---- chat edits ---------------------------------------------------------------
def _fake_llm(reply_code, calls):
    async def llm(**kw):
        calls.append(kw["system"][:30])
        if "senior software engineer" in kw["system"]:                       # file selection
            return json.dumps({"files": ["frontend/src/pages/Home.jsx"]})
        if "software project planner" in kw["system"]:                       # new-file planning
            return json.dumps({"summary": "Made the header dark.", "new_files": []})
        return reply_code
    return llm


def test_a_bug_report_that_mentions_signup_is_not_rejected_as_incomplete_signup(tmp_path, monkeypatch):
    """The screenshot: 'preserve the existing signup and login' must not trigger the signup check."""
    fe, be = project(tmp_path)
    fc.fix_flow(fe, be)
    (fe / "src/App.jsx").write_text(APP.replace('/job/5', '/jobs/5'), encoding="utf-8")
    calls = []
    monkeypatch.setattr(revise, "_default_llm", _fake_llm("export default function Home(){return <h1>Hi</h1>}\n", calls))
    result = asyncio.run(revise.revise_project(
        fe, be, "Fix the runtime issue. Preserve the existing signup and login functionality.",
        build_check=lambda d: None))
    assert result["ok"], result
    assert "<h1>Hi</h1>" in (fe / "src/pages/Home.jsx").read_text()


def test_old_problems_do_not_block_an_unrelated_edit(tmp_path, monkeypatch):
    fe, be = project(tmp_path)                       # full of pre-existing flow problems
    calls = []
    monkeypatch.setattr(revise, "_default_llm", _fake_llm("export default function Home(){return <h1>Dark</h1>}\n", calls))
    result = asyncio.run(revise.revise_project(fe, be, "make the home header dark", build_check=lambda d: None))
    assert result["ok"], result


def test_login_bug_report_is_fixed_without_calling_the_model(tmp_path, monkeypatch):
    fe, be = project(tmp_path)
    fc.fix_flow(fe, be)                              # start clean, then re-break the login call
    (fe / "src/App.jsx").write_text(APP.replace('/job/5', '/jobs/5'), encoding="utf-8")
    ctx = (fe / "src/context/AuthContext.jsx").read_text().replace("apiLogin({ email, password })", "apiLogin(email, password)")
    (fe / "src/context/AuthContext.jsx").write_text(ctx, encoding="utf-8")

    async def boom(**_):
        raise AssertionError("the model must not be needed for a mechanical fix")

    monkeypatch.setattr(revise, "_default_llm", boom)
    result = asyncio.run(revise.revise_project(fe, be, "login gives 422, credentials are not sent",
                                               build_check=lambda d: None))
    assert result["ok"], result
    assert "apiLogin({ email, password })" in (fe / "src/context/AuthContext.jsx").read_text()
    assert "Fixed automatically" in result["summary"]


def _clean(tmp_path):
    fe, be = project(tmp_path)
    fc.fix_flow(fe, be)
    (fe / "src/App.jsx").write_text(APP.replace('/job/5', '/jobs/5'), encoding="utf-8")
    return fe, be


def _llm_returning(path_label, code):
    async def llm(**kw):
        if "senior software engineer" in kw["system"]:
            return json.dumps({"files": [path_label]})
        if "software project planner" in kw["system"]:
            return json.dumps({"summary": "Restyled.", "new_files": []})
        return code
    return llm


def test_a_wrong_call_shape_written_by_the_model_is_corrected_automatically(tmp_path, monkeypatch):
    fe, be = _clean(tmp_path)
    wrong = (LOGIN.replace("await login(email, password);", "await login({ email, password });")
             .replace("Log in</button>", "Welcome back</button>"))
    monkeypatch.setattr(revise, "_default_llm", _llm_returning("frontend/src/pages/Login.jsx", wrong))
    result = asyncio.run(revise.revise_project(fe, be, "restyle the login button", build_check=lambda d: None))
    assert result["ok"], result
    page = (fe / "src/pages/Login.jsx").read_text()
    assert "Welcome back" in page and "await login(email, password);" in page
    assert "corrected automatically" in result["summary"]


def test_a_change_that_breaks_the_build_is_rolled_back(tmp_path, monkeypatch):
    fe, be = _clean(tmp_path)
    before = {p: p.read_text() for p in fe.rglob("*.jsx")}
    monkeypatch.setattr(revise, "_default_llm",
                        _llm_returning("frontend/src/pages/Home.jsx", "export default function Home(){return <h1>x</h1>}\n"))
    monkeypatch.setattr(revise, "MAX_REPAIR_ROUNDS", 0)

    def bad_build(_):
        raise RuntimeError("vite build failed")

    result = asyncio.run(revise.revise_project(fe, be, "change the home title", build_check=bad_build))
    assert not result["ok"] and "unchanged" in result["error"]
    assert {p: p.read_text() for p in fe.rglob("*.jsx")} == before


def test_the_model_gets_a_second_chance_when_the_first_answer_breaks_the_build(tmp_path, monkeypatch):
    fe, be = _clean(tmp_path)
    attempts = []

    def flaky_build(_):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("Unexpected token")

    monkeypatch.setattr(revise, "_default_llm",
                        _llm_returning("frontend/src/pages/Home.jsx", "export default function Home(){return <h1>x</h1>}\n"))
    result = asyncio.run(revise.revise_project(fe, be, "change the home title", build_check=flaky_build))
    assert result["ok"] and len(attempts) == 2


def test_large_files_are_edited_with_patches(tmp_path):
    big = "// padding\n" * 900 + "export const value = 1;\n"
    async def llm(**kw):
        return json.dumps({"edits": [{"old": "export const value = 1;", "new": "export const value = 2;"}]})
    text = asyncio.run(revise._patch_file(llm, request="r", history="", label="x.js", original=big, extra=""))
    assert "value = 2" in text and text.count("// padding") == 900


def test_replace_once_tolerates_indentation_and_rejects_ambiguity():
    text = "function a() {\n    return 1;\n}\n"
    assert "return 2" in revise._replace_once(text, "function a() {\n  return 1;\n}", "function a() {\n  return 2;\n}")
    try:
        revise._replace_once("x\nx\n", "x", "y")
    except ValueError:
        return
    raise AssertionError("an ambiguous patch must be rejected")


# ---- "query.toLowerCase is not a function" --------------------------------------
SEARCH_PAGE = '''import React, { useState } from "react";
import SearchBar from "../components/SearchBar.jsx";

export default function Tutors({ tutors }) {
  const [query, setQuery] = useState("");
  const shown = tutors.filter((t) => t.name.toLowerCase().includes(query.toLowerCase()));
  return (
    <div>
      <input value={query} onChange={setQuery} />
      <SearchBar onSearch={setQuery} />
      {shown.length}
    </div>
  );
}
'''


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_event_stored_in_string_state_is_detected_and_fixed(tmp_path):
    fe = tmp_path / "frontend"
    page = _write(fe, "src/pages/Tutors.jsx", SEARCH_PAGE)
    issues = fc.check_event_setters(fe)
    assert [i["code"] for i in issues] == ["event_passed_to_state"] * 2
    fixes = fc.fix_event_setters(fe)
    assert fixes and fc.check_event_setters(fe) == []
    text = page.read_text()
    assert "onChange={(value) => setQuery(value && value.target ? value.target.value : value)}" in text
    assert fc.fix_event_setters(fe) == []                      # idempotent


def test_string_state_setters_are_the_only_ones_touched(tmp_path):
    fe = tmp_path / "frontend"
    _write(fe, "src/pages/A.jsx", 'const [items, setItems] = useState([]);\n<List onChange={setItems} />\n')
    assert fc.check_event_setters(fe) == [] and fc.fix_event_setters(fe) == []


def test_string_calls_cannot_crash_on_objects_any_more(tmp_path):
    fe = tmp_path / "frontend"
    page = _write(fe, "src/pages/B.jsx",
                  'const a = query.toLowerCase();\nconst b = filters.search.trim();\nconst c = user.name.toLowerCase();\n')
    assert fc.fix_string_calls(fe)
    text = page.read_text()
    assert 'String(query ?? "").toLowerCase()' in text
    assert 'String(filters.search ?? "").trim()' in text
    assert "user.name.toLowerCase()" in text                    # other names untouched
    assert fc.fix_string_calls(fe) == []                       # idempotent


def test_harden_frontend_repairs_the_whole_crash_class(tmp_path):
    fe = tmp_path / "frontend"
    page = _write(fe, "src/pages/Tutors.jsx", SEARCH_PAGE)
    assert fc.harden_frontend(fe)
    text = page.read_text()
    assert "onChange={setQuery}" not in text and 'String(query ?? "").toLowerCase()' in text


# ---- which pages the browser opens ----------------------------------------------
def test_browser_check_opens_sub_pages_and_pages_the_plan_did_not_list(tmp_path):
    from generation.runtime_routes import discover_routes

    fe, be = tmp_path / "frontend", tmp_path / "backend"
    _write(fe, "src/App.jsx", '''import { Routes, Route } from "react-router-dom";
export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/login" element={<Login />} />
      <Route path="/tutors" element={<Tutors />} />
      <Route path="/tutors/:id" element={<TutorDetails />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}
''')
    _write(be, "data/tutors.json", json.dumps([{"id": "t-42", "name": "Asha"}]))
    routes = discover_routes(fe, be, plan_routes=["/", "/tutors"])
    assert routes[0] == "/"
    assert "/tutors/t-42" in routes          # the real id from the seed data
    assert "/login" in routes                # not in the plan
    assert not any("*" in r or ":" in r for r in routes)
    assert routes.index("/tutors/t-42") < routes.index("/login")   # sub pages come first


def test_unknown_collections_fall_back_to_id_1_and_the_list_is_capped(tmp_path):
    from generation.runtime_routes import discover_routes

    fe = tmp_path / "frontend"
    many = "\n".join(f'<Route path="/p{i}" element={{<P{i} />}} />' for i in range(30))
    _write(fe, "src/App.jsx", f'<Routes>\n<Route path="/jobs/:id" element={{<J />}} />\n{many}\n</Routes>\n')
    routes = discover_routes(fe, None)
    assert "/jobs/1" in routes and len(routes) <= 12


def test_chat_fixes_the_toLowerCase_crash_without_the_model(tmp_path, monkeypatch):
    fe, be = project(tmp_path)
    fc.fix_flow(fe, be)
    (fe / "src/App.jsx").write_text(APP.replace('/job/5', '/jobs/5'), encoding="utf-8")
    _write(fe, "src/pages/Tutors.jsx", SEARCH_PAGE)

    async def boom(**_):
        raise AssertionError("the model must not be needed")

    monkeypatch.setattr(revise, "_default_llm", boom)
    result = asyncio.run(revise.revise_project(
        fe, be, "This page crashed: query.toLowerCase is not a function", build_check=lambda d: None))
    assert result["ok"], result
    text = (fe / "src/pages/Tutors.jsx").read_text()
    assert "onChange={setQuery}" not in text and 'String(query ?? "").toLowerCase()' in text
