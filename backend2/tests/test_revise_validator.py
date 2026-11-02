
from workflow.revise import _validate_signup_feature


def create_signup_project(tmp_path):
    src = tmp_path / "frontend" / "src"

    (src / "pages").mkdir(parents=True)
    (src / "api").mkdir(parents=True)

    (src / "pages" / "Signup.jsx").write_text(
        """
        import { registerUser } from "../api/authApi.js";

        export default function Signup() {
            return <button onClick={() => registerUser("Test", "test@example.com", "password")}>
                Signup
            </button>;
        }
        """,
        encoding="utf-8",
    )

    (src / "pages" / "Login.jsx").write_text(
        """
        import { Link } from "react-router-dom";

        export default function Login() {
            return <Link to="/signup">Create an account</Link>;
        }
        """,
        encoding="utf-8",
    )

    (src / "App.jsx").write_text(
        """
        import { Routes, Route } from "react-router-dom";
        import Signup from "./pages/Signup.jsx";

        export default function App() {
            return (
                <Routes>
                    <Route path="/signup" element={<Signup />} />
                </Routes>
            );
        }
        """,
        encoding="utf-8",
    )

    return src


def test_complete_signup_feature_passes(tmp_path):
    create_signup_project(tmp_path)

    problems = _validate_signup_feature(
        tmp_path / "frontend"
    )

    assert problems == []


def test_missing_signup_component_is_rejected(tmp_path):
    src = create_signup_project(tmp_path)

    (src / "pages" / "Signup.jsx").unlink()

    problems = _validate_signup_feature(
        tmp_path / "frontend"
    )

    assert any("component is missing" in p for p in problems)


def test_missing_signup_route_is_rejected(tmp_path):
    src = create_signup_project(tmp_path)

    (src / "App.jsx").write_text(
        "export default function App() { return null; }",
        encoding="utf-8",
    )

    problems = _validate_signup_feature(
        tmp_path / "frontend"
    )

    assert any("route is missing" in p for p in problems)


def test_missing_login_navigation_is_rejected(tmp_path):
    src = create_signup_project(tmp_path)

    (src / "pages" / "Login.jsx").write_text(
        "export default function Login() { return null; }",
        encoding="utf-8",
    )

    problems = _validate_signup_feature(
        tmp_path / "frontend"
    )

    assert any("Login page does not link" in p for p in problems)


def test_missing_register_api_call_is_rejected(tmp_path):
    src = create_signup_project(tmp_path)

    (src / "pages" / "Signup.jsx").write_text(
        "export default function Signup() { return <div>Signup</div>; }",
        encoding="utf-8",
    )

    problems = _validate_signup_feature(
        tmp_path / "frontend"
    )

    assert any("registerUser" in p for p in problems)
