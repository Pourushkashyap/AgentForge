"""useAuth() is called but defined nowhere: the debugger must fix it, not loop on a no-op."""
from generation.auth_context import ensure_auth_context, exports_hook
from generation.provider_wiring import validate_provider_wiring
from workflow.debugger import apply_deterministic_fixes
from workflow.validators import make_issue

APP = '''import React from "react";
import { Routes, Route } from "react-router-dom";
import Navbar from "./components/Navbar.jsx";
import Login from "./pages/Login.jsx";

export default function App() {
  return (
    <div>
      <Navbar />
      <Routes>
        <Route path="/login" element={<Login />} />
      </Routes>
    </div>
  );
}
'''
NAVBAR = '''import React from "react";
import { useAuth } from "../context/AuthContext.jsx";

export default function Navbar() {
  const { user, logout } = useAuth();
  return <nav>{user ? <button onClick={logout}>Log out</button> : "Guest"}</nav>;
}
'''
LOGIN = '''import React from "react";
import { useAuth } from "../context/AuthContext.jsx";

export default function Login() {
  const { login, error } = useAuth();
  return <form onSubmit={(e) => { e.preventDefault(); login({ email: "a", password: "b" }); }}>{error}</form>;
}
'''


def make_project(tmp_path):
    fe, be = tmp_path / "frontend", tmp_path / "backend"
    for rel, text in {"src/App.jsx": APP, "src/components/Navbar.jsx": NAVBAR,
                      "src/pages/Login.jsx": LOGIN}.items():
        path = fe / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    be.mkdir()
    return fe, be


def wiring_issues(fe):
    return [make_issue("frontend-static", m, code="unmounted_provider", file="src/main.jsx")
            for m in validate_provider_wiring(fe)]


def test_the_reported_failure_is_detected(tmp_path):
    fe, _ = make_project(tmp_path)
    problems = validate_provider_wiring(fe)
    assert any("calls useAuth(), which no context file or local file exports" in p for p in problems)


def test_missing_auth_context_is_created_and_mounted_without_the_model(tmp_path):
    fe, be = make_project(tmp_path)
    issues = wiring_issues(fe)
    fixes, handled = apply_deterministic_fixes(issues, be, fe)
    assert len(handled) == len(issues) and issues
    assert (fe / "src/context/AuthContext.jsx").is_file()
    assert "export function useAuth" in (fe / "src/context/AuthContext.jsx").read_text()
    assert "AuthProvider" in (fe / "src/main.jsx").read_text()
    assert validate_provider_wiring(fe) == []
    assert any("auth context" in f["action"] for f in fixes)


def test_a_no_op_is_never_reported_as_a_fix(tmp_path):
    """The old debugger marked every wiring issue 'handled' even when nothing was fixed."""
    fe, be = make_project(tmp_path)
    (fe / "src/components/Navbar.jsx").write_text(
        NAVBAR.replace("useAuth", "useCart").replace("AuthContext", "CartContext"), encoding="utf-8")
    (fe / "src/pages/Login.jsx").write_text("export default function Login(){return null}\n", encoding="utf-8")
    issues = wiring_issues(fe)
    assert issues
    _, handled = apply_deterministic_fixes(issues, be, fe)
    assert handled == set()                       # left for the model, not swallowed


def test_a_context_that_forgot_to_export_the_hook_gets_it(tmp_path):
    fe, be = make_project(tmp_path)
    (fe / "src/context").mkdir()
    (fe / "src/context/AuthContext.jsx").write_text('''import React, { createContext, useState } from "react";

export const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  return <AuthContext.Provider value={{ user, logout: () => setUser(null) }}>{children}</AuthContext.Provider>;
}
''', encoding="utf-8")
    assert ensure_auth_context(fe) == ["src/context/AuthContext.jsx"]
    assert exports_hook((fe / "src/context/AuthContext.jsx").read_text())
    assert ensure_auth_context(fe) == []          # idempotent


def test_a_custom_hook_outside_src_context_is_not_a_false_positive(tmp_path):
    fe, _ = make_project(tmp_path)
    (fe / "src/hooks").mkdir()
    (fe / "src/hooks/useAuth.js").write_text(
        'import { useContext } from "react";\nexport function useAuth() { return useContext(null) || {}; }\n',
        encoding="utf-8")
    assert not any("no context file" in p for p in validate_provider_wiring(fe))


def test_a_hook_that_already_exists_is_never_overwritten(tmp_path):
    fe, _ = make_project(tmp_path)
    (fe / "src/context").mkdir()
    custom = "export function useAuth() { return { user: null }; }\n"
    (fe / "src/context/AuthContext.jsx").write_text(custom, encoding="utf-8")
    assert ensure_auth_context(fe) == []
    assert (fe / "src/context/AuthContext.jsx").read_text() == custom
