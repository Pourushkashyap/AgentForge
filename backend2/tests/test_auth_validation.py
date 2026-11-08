from types import SimpleNamespace

from workflow.validators import check_authentication, has_errors


def _write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _valid_project(tmp_path):
    _write(
        tmp_path,
        "src/App.jsx",
        '''import Login from "./pages/Login.jsx";
import Signup from "./pages/Signup.jsx";
export default function App() {
  return <Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/signup" element={<Signup />} />
  </Routes>;
}
''',
    )
    _write(
        tmp_path,
        "src/pages/Login.jsx",
        '''export default function Login() {
  const submit = async (event) => { event.preventDefault(); };
  return <form onSubmit={submit}>
    <input type="email" name="email" />
    <input type="password" name="password" />
    <Link to="/signup">Create account</Link>
  </form>;
}
''',
    )
    _write(
        tmp_path,
        "src/pages/Signup.jsx",
        '''export default function Signup() {
  const submit = async (event) => { event.preventDefault(); };
  return <form onSubmit={submit}>
    <input type="text" name="name" />
    <input type="email" name="email" />
    <input type="password" name="password" />
    <Link to="/login">Sign in</Link>
  </form>;
}
''',
    )


def test_valid_login_and_signup_pages_pass_auth_validation(tmp_path):
    _valid_project(tmp_path)
    plan = SimpleNamespace(pages=[])
    endpoints = [
        {"method": "POST", "path": "/api/auth/login"},
        {"method": "POST", "path": "/api/auth/register"},
    ]

    issues = check_authentication(tmp_path, plan, endpoints)

    assert issues == []


def test_signup_is_required_when_register_endpoint_exists(tmp_path):
    _write(
        tmp_path,
        "src/App.jsx",
        '''import Login from "./pages/Login.jsx";
export default function App() {
 return <Routes><Route path="/login" element={<Login />} /></Routes>;
}
''',
    )
    _write(
        tmp_path,
        "src/pages/Login.jsx",
        '''export default function Login() {
 return <form onSubmit={submit}>
  <input type="email" name="email" />
  <input type="password" name="password" />
  <Link to="/signup">Sign up</Link>
 </form>;
}
''',
    )
    plan = SimpleNamespace(pages=[])

    issues = check_authentication(
        tmp_path, plan, [{"method": "POST", "path": "/api/auth/register"}]
    )

    assert has_errors(issues)
    assert any(issue["code"] == "auth_page_missing" for issue in issues)


def test_non_authenticated_project_is_not_flagged(tmp_path):
    plan = SimpleNamespace(pages=[SimpleNamespace(name="Home", route="/")])
    assert check_authentication(tmp_path, plan, []) == []


def test_auth_form_wrapper_is_supported(tmp_path):
    _write(
        tmp_path,
        "src/App.jsx",
        '''import Login from "./pages/Login.jsx";
export default function App() {
 return <Routes><Route path="/login" element={<Login />} /></Routes>;
}
''',
    )
    _write(
        tmp_path,
        "src/pages/Login.jsx",
        '''export default function Login() {
 const submit = () => {};
 return <AuthForm onSubmit={submit}>
  <Input type="email" name="email" />
  <Input type="password" name="password" />
  <Link to="/signup">Create account</Link>
 </AuthForm>;
}
''',
    )
    plan = SimpleNamespace(pages=[SimpleNamespace(name="Login", route="/login")])
    issues = check_authentication(tmp_path, plan, [])
    assert not any(issue["code"] == "auth_form_missing" for issue in issues)


def test_auth_validator_accepts_dict_plan(tmp_path):
    _valid_project(tmp_path)
    issues = check_authentication(
        tmp_path,
        {"pages": [{"name": "Login", "route": "/login"},
                   {"name": "Signup", "route": "/signup"}]},
        [],
    )
    assert issues == []


def test_auth_validator_rejects_identical_login_and_signup(tmp_path):
    _valid_project(tmp_path)
    login = (tmp_path / "src/pages/Login.jsx").read_text(encoding="utf-8")
    _write(tmp_path, "src/pages/Signup.jsx", login)
    issues = check_authentication(
        tmp_path, SimpleNamespace(pages=[SimpleNamespace(name="Signup", route="/signup")]), []
    )
    assert any(issue["code"] == "auth_pages_duplicated" for issue in issues)


def test_auth_validator_rejects_same_component_for_both_routes(tmp_path):
    _valid_project(tmp_path)
    app = """import Login from "./pages/Login.jsx";
import Signup from "./pages/Signup.jsx";
export default function App() {
 return <Routes>
  <Route path="/login" element={<Login />} />
  <Route path="/signup" element={<Login />} />
 </Routes>;
}
"""
    _write(tmp_path, "src/App.jsx", app)
    issues = check_authentication(
        tmp_path, SimpleNamespace(pages=[]),
        [{"method": "POST", "path": "/api/auth/register"}],
    )
    assert any(issue["code"] == "auth_routes_share_component" for issue in issues)


def test_auth_validator_checks_default_export_name(tmp_path):
    _valid_project(tmp_path)
    _write(tmp_path, "src/pages/Signup.jsx", """function Register() {
 const submit = () => {};
 return <form onSubmit={submit}>
  <input name="name" /><input name="email" /><input name="password" />
  <Link to="/login">Sign in</Link>
 </form>;
}
export default Register;
""")
    issues = check_authentication(
        tmp_path, SimpleNamespace(pages=[SimpleNamespace(name="Signup", route="/signup")]), []
    )
    assert any(issue["code"] == "auth_export_mismatch" for issue in issues)


def test_auth_validator_checks_contract_endpoint_usage(tmp_path):
    _valid_project(tmp_path)
    _write(tmp_path, "src/api/authApi.js", 'export const login = (data) => api.post("/api/auth/signin", data);')
    issues = check_authentication(
        tmp_path, SimpleNamespace(pages=[]),
        [{"method": "POST", "path": "/api/auth/login"}],
    )
    assert any(issue["code"] == "auth_login_api_missing" for issue in issues)



def test_debugger_can_recreate_a_missing_signup_page(tmp_path):
    import asyncio
    import json
    from workflow.debugger import llm_repair

    frontend = tmp_path / "frontend"
    backend = tmp_path / "backend"
    (frontend / "src/pages").mkdir(parents=True)
    backend.mkdir()
    _write(frontend, "src/pages/Login.jsx", "export default function Login() { return <div />; }")
    issue = {
        "stage": "authentication",
        "severity": "error",
        "auto_fixable": True,
        "code": "auth_page_missing",
        "file": "src/pages/Signup.jsx",
        "message": "Required authentication page is missing: src/pages/Signup.jsx.",
    }

    async def fake_llm(**kwargs):
        assert "MISSING FILE" in kwargs["prompt"]
        return json.dumps({"summary": "Created Signup page", "files": [{
            "path": "frontend/src/pages/Signup.jsx",
            "content": "export default function Signup() { return <div>Sign up</div>; }",
        }]})

    fixes = asyncio.run(llm_repair([issue], backend, frontend, fake_llm))
    assert fixes and fixes[0]["code"] == "llm_patch"
    assert (frontend / "src/pages/Signup.jsx").is_file()



def test_auth_validator_detects_token_response_name_mismatch(tmp_path):
    _valid_project(tmp_path)
    _write(tmp_path, "src/api/authApi.js", """
export async function login(credentials) {
  const response = await api.post("/api/auth/login", credentials);
  const token = response.data.token;
  return token;
}
""")
    issues = check_authentication(
        tmp_path,
        SimpleNamespace(pages=[]),
        [{
            "method": "POST",
            "path": "/api/auth/login",
            "response_fields": ["access_token", "user"],
        }],
    )
    assert any(issue["code"] == "auth_token_field_mismatch" for issue in issues)
