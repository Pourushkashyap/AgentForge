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
