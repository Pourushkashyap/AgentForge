
from pathlib import Path

import pytest

from generation.frontend_generator import _validate_manifest
from generation.frontend_generator import _validate_context_contracts
from generation.frontend_generator import _validate_project
def test_manifest_accepts_required_frontend_files():
    data = {
        "files": [
            {"path": "package.json", "purpose": "Dependencies and scripts"},
            {"path": "index.html", "purpose": "HTML entry"},
            {"path": "vite.config.js", "purpose": "Vite configuration"},
            {"path": "tailwind.config.js", "purpose": "Tailwind configuration"},
            {"path": "postcss.config.js", "purpose": "PostCSS configuration"},
            {"path": "src/main.jsx", "purpose": "Application entry"},
            {"path": "src/App.jsx", "purpose": "Application routes"},
            {"path": "src/styles.css", "purpose": "Global styles"},
            {"path": "src/pages/Home.jsx", "purpose": "Home page"},
        ]
    }

    manifest = _validate_manifest(data)

    assert len(manifest) == 9
    assert any(item["path"] == "src/App.jsx" for item in manifest)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../outside.js",
        "/outside.js",
        r"..\outside.js",
        "C:/outside.js",
    ],
)
def test_manifest_rejects_unsafe_paths(unsafe_path):
    data = {
        "files": [
            {"path": unsafe_path, "purpose": "Unsafe file"},
        ]
    }

    with pytest.raises(ValueError):
        _validate_manifest(data)


def test_manifest_rejects_missing_required_files():
    data = {
        "files": [
            {"path": "package.json", "purpose": "Dependencies"},
        ]
    }

    with pytest.raises(ValueError, match="omitted required files"):
        _validate_manifest(data)


def test_manifest_normalizes_typescript_extensions():
    data = {
        "files": [
            {"path": "package.json", "purpose": "Dependencies"},
            {"path": "index.html", "purpose": "HTML entry"},
            {"path": "vite.config.js", "purpose": "Vite configuration"},
            {"path": "tailwind.config.js", "purpose": "Tailwind configuration"},
            {"path": "postcss.config.js", "purpose": "PostCSS configuration"},
            {"path": "src/main.jsx", "purpose": "Application entry"},
            {"path": "src/App.tsx", "purpose": "Application routes"},
            {"path": "src/styles.css", "purpose": "Global styles"},
        ]
    }

    manifest = _validate_manifest(data)

    paths = {item["path"] for item in manifest}
    assert "src/App.jsx" in paths
    assert "src/App.tsx" not in paths



from generation.frontend_generator import validate_local_imports


def test_import_validator_accepts_existing_relative_imports(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text(
        'import Home from "./pages/Home";\n',
        encoding="utf-8",
    )
    (tmp_path / "src" / "pages").mkdir()
    (tmp_path / "src" / "pages" / "Home.jsx").write_text(
        "export default function Home() { return null; }\n",
        encoding="utf-8",
    )

    assert validate_local_imports(tmp_path) == []


def test_import_validator_detects_missing_relative_imports(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.jsx").write_text(
        'import Header from "./components/Header";\n',
        encoding="utf-8",
    )

    errors = validate_local_imports(tmp_path)

    assert len(errors) == 1
    assert "cannot resolve import" in errors[0]
    assert "./components/Header" in errors[0]



import subprocess
from unittest.mock import patch

from generation.frontend_generator import _validate_frontend_build


def create_package_json(project_dir, scripts=None):
    import json

    package = {
        "name": "test-frontend",
        "version": "1.0.0",
        "scripts": scripts if scripts is not None else {"build": "vite build"},
    }
    (project_dir / "package.json").write_text(
        json.dumps(package),
        encoding="utf-8",
    )



@patch("generation.frontend_generator.subprocess.run")
def test_build_validator_accepts_successful_build(mock_run, tmp_path):
    create_package_json(tmp_path)

    mock_run.side_effect = [
        subprocess.CompletedProcess(
            args=["npm", "install"],
            returncode=0,
            stdout="Dependencies installed",
            stderr="",
        ),
        subprocess.CompletedProcess(
            args=["npm", "run", "build"],
            returncode=0,
            stdout="Build successful",
            stderr="",
        ),
    ]

    _validate_frontend_build(tmp_path)

    assert mock_run.call_count == 2
    assert mock_run.call_args_list[0].args[0][1] == "install"
    assert mock_run.call_args_list[1].args[0][1:3] == ["run", "build"]


@patch("generation.frontend_generator.subprocess.run")
def test_build_validator_reports_build_failure(mock_run, tmp_path):
    create_package_json(tmp_path)

    mock_run.side_effect = [
        subprocess.CompletedProcess(
            args=["npm", "install"],
            returncode=0,
            stdout="Dependencies installed",
            stderr="",
        ),
        subprocess.CompletedProcess(
            args=["npm", "run", "build"],
            returncode=1,
            stdout="Vite compilation failed",
            stderr="Missing module",
        ),
    ]

    with pytest.raises(ValueError, match="production build failed") as exc:
        _validate_frontend_build(tmp_path)

    assert "Missing module" in str(exc.value)
    assert mock_run.call_count == 2


def test_build_validator_rejects_missing_package_json(tmp_path):
    with pytest.raises(ValueError, match="package.json is missing"):
        _validate_frontend_build(tmp_path)


def test_build_validator_handles_timeout(tmp_path):
    create_package_json(tmp_path)

    with patch(
        "generation.frontend_generator.subprocess.run",
        side_effect=subprocess.TimeoutExpired(
            cmd="npm run build",
            timeout=180,
        ),
    ):
        with pytest.raises(ValueError, match="timed out"):
            _validate_frontend_build(tmp_path)



def test_context_contract_detects_cart_items_mismatch(tmp_path):
    consumer = """
from "../context/CartContext.jsx";
const { cartItems } = useCart();
"""
    context = """
const value = { items, addToCart, removeFromCart, totalAmount };
export function useCart() {}
"""
    texts = {
        "src/components/Navbar.jsx": consumer,
        "src/context/CartContext.jsx": context,
    }

    problems = _validate_context_contracts(tmp_path, [], texts)

    assert "src/components/Navbar.jsx" in problems
    assert any("cartItems" in issue for issue in problems["src/components/Navbar.jsx"])


def test_context_contract_detects_add_item_mismatch(tmp_path):
    consumer = """
import { useCart } from "../context/CartContext.jsx";
const { addItem } = useCart();
"""
    context = """
const value = { items, addToCart, removeFromCart, totalAmount };
export function useCart() {}
"""
    texts = {
        "src/components/ProductCard.jsx": consumer,
        "src/context/CartContext.jsx": context,
    }

    problems = _validate_context_contracts(tmp_path, [], texts)

    assert any("addItem" in issue for issue in problems["src/components/ProductCard.jsx"])


def test_context_contract_detects_total_mismatch(tmp_path):
    consumer = """
import { useCart } from "../context/CartContext.jsx";
const { total = 0 } = useCart();
"""
    context = """
const value = { items, addToCart, removeFromCart, totalAmount };
export function useCart() {}
"""
    texts = {
        "src/pages/Cart.jsx": consumer,
        "src/context/CartContext.jsx": context,
    }

    problems = _validate_context_contracts(tmp_path, [], texts)

    assert any("totalAmount" in issue for issue in problems["src/pages/Cart.jsx"])


def test_context_contract_detects_remove_item_mismatch(tmp_path):
    consumer = """
import { useCart } from "../context/CartContext.jsx";
const { removeItem } = useCart();
"""
    context = """
const value = { items, addToCart, removeFromCart, totalAmount };
export function useCart() {}
"""
    texts = {
        "src/pages/Cart.jsx": consumer,
        "src/context/CartContext.jsx": context,
    }

    problems = _validate_context_contracts(tmp_path, [], texts)

    assert any("removeFromCart" in issue for issue in problems["src/pages/Cart.jsx"])



import asyncio
from unittest.mock import AsyncMock, patch

from planning.frontend_schemas import FrontendPlan


def make_test_frontend_plan():
    return FrontendPlan(
        business_name="Test Business",
        project_name="Test Frontend",
        framework="React",
        styling="Tailwind CSS",
        design_system={
            "description": "Minimal test design",
            "colors": {},
            "typography": {},
            "component_library": "",
        },
        pages=[
            {
                "name": "Home",
                "route": "/",
                "purpose": "Test home page",
            }
        ],
        state_management={
            "library": "React Context",
            "stores": [],
        },
        tasks=[
            {
                "task_id": "T1",
                "title": "Create home page",
                "description": "Create a simple home page",
            }
        ],
    )


@patch("generation.frontend_generator._validate_frontend_build")
@patch("generation.frontend_generator._write_scaffold", return_value=[])
@patch("generation.frontend_generator._validate_project", return_value={})
@patch("generation.frontend_generator._generate_file", new_callable=AsyncMock)
@patch(
    "generation.frontend_generator._generate_manifest",
    new_callable=AsyncMock,
)
@patch("generation.frontend_generator._validate_frontend_runtime")
def test_generate_frontend_runs_build_validation(
    mock_runtime,
    mock_manifest,
    mock_file,
    mock_validate,
    mock_scaffold,
    mock_build,
    tmp_path,
):
    from generation.frontend_generator import generate_frontend

    manifest = [
        {"path": "src/App.jsx", "purpose": "Application component"},
    ]
    mock_manifest.return_value = manifest
    mock_file.return_value = "export default function App() { return null; }"

    result = asyncio.run(
        generate_frontend(
            make_test_frontend_plan(),
            output_dir=tmp_path,
        )
    )

    assert result == (tmp_path / "test-frontend").resolve()
    mock_build.assert_called_once_with(result)
    mock_runtime.assert_called_once_with(result, routes=["/"])


@patch(
    "generation.frontend_generator._validate_frontend_build",
    side_effect=ValueError("Frontend production build failed."),
)
@patch("generation.frontend_generator._write_scaffold", return_value=[])
@patch("generation.frontend_generator._validate_project", return_value={})
@patch("generation.frontend_generator._generate_file", new_callable=AsyncMock)
@patch(
    "generation.frontend_generator._generate_manifest",
    new_callable=AsyncMock,
)
def test_generate_frontend_fails_when_build_fails(
    mock_manifest,
    mock_file,
    mock_validate,
    mock_scaffold,
    mock_build,
    tmp_path,
):
    from generation.frontend_generator import generate_frontend

    mock_manifest.return_value = [
        {"path": "src/App.jsx", "purpose": "Application component"},
    ]
    mock_file.return_value = "export default function App() { return null; }"

    with pytest.raises(ValueError, match="production build failed"):
        asyncio.run(
            generate_frontend(
                make_test_frontend_plan(),
                output_dir=tmp_path,
            )
        )

    mock_build.assert_called_once()



def test_context_contract_detects_missing_menu_function(tmp_path):
    consumer = """
import { useMenu } from "../context/MenuContext.jsx";

export default function PopularDrinks() {
    const { menu, loading, error, fetchMenu } = useMenu();
    return null;
}
"""
    context = """
const value = { menu, loading, error, reload: loadMenu };
export function useMenu() {}
"""

    texts = {
        "src/pages/PopularDrinks.jsx": consumer,
        "src/context/MenuContext.jsx": context,
    }

    problems = _validate_context_contracts(tmp_path, [], texts)

    assert "src/pages/PopularDrinks.jsx" in problems
    assert any(
        "fetchMenu" in issue
        for issue in problems["src/pages/PopularDrinks.jsx"]
    )


def test_context_contract_accepts_existing_menu_function(tmp_path):
    consumer = """
import { useMenu } from "../context/MenuContext.jsx";

export default function PopularDrinks() {
    const { menu, loading, error, reload } = useMenu();
    return null;
}
"""
    context = """
const value = { menu, loading, error, reload: loadMenu };
export function useMenu() {}
"""

    texts = {
        "src/pages/PopularDrinks.jsx": consumer,
        "src/context/MenuContext.jsx": context,
    }

    problems = _validate_context_contracts(tmp_path, [], texts)

    assert "src/pages/PopularDrinks.jsx" not in problems


def test_validate_project_detects_missing_context_property(tmp_path):
    from generation.frontend_generator import _validate_project

    context_dir = tmp_path / "src" / "context"
    pages_dir = tmp_path / "src" / "pages"
    context_dir.mkdir(parents=True)
    pages_dir.mkdir(parents=True)

    context_file = context_dir / "MenuContext.jsx"
    context_file.write_text(
        """
const value = { menu, loading, error, reload: loadMenu };
export function useMenu() {}
""",
        encoding="utf-8",
    )

    consumer_file = pages_dir / "PopularDrinks.jsx"
    consumer_file.write_text(
        """
import { useMenu } from "../context/MenuContext.jsx";
export default function PopularDrinks() {
    const { menu, loading, error, fetchMenu } = useMenu();
    return null;
}
""",
        encoding="utf-8",
    )

    manifest = [
        {
            "path": "src/context/MenuContext.jsx",
            "default_export": False,
            "named_exports": ["useMenu"],
        },
        {
            "path": "src/pages/PopularDrinks.jsx",
            "default_export": True,
            "named_exports": [],
        },
    ]

    problems = _validate_project(tmp_path, manifest)

    assert "src/pages/PopularDrinks.jsx" in problems
    assert any(
        "fetchMenu" in issue
        for issue in problems["src/pages/PopularDrinks.jsx"]
    )



@patch("generation.frontend_generator._validate_frontend_build")
@patch("generation.frontend_generator._write_scaffold", return_value=[])
@patch("generation.frontend_generator._validate_project")
@patch("generation.frontend_generator._generate_file", new_callable=AsyncMock)
@patch(
    "generation.frontend_generator._generate_manifest",
    new_callable=AsyncMock,
)
@patch("generation.frontend_generator._validate_frontend_runtime")
def test_generate_frontend_repairs_validation_error(
    mock_runtime,
    mock_manifest,
    mock_file,
    mock_validate,
    mock_scaffold,
    mock_build,
    tmp_path,
):
    from generation.frontend_generator import generate_frontend

    manifest = [
        {
            "path": "src/App.jsx",
            "purpose": "Application component",
            "default_export": True,
            "named_exports": [],
        }
    ]
    mock_manifest.return_value = manifest
    mock_file.side_effect = [
        "export default function App(){ return null; }",
        "export default function App(){ return <main>Fixed</main>; }",
    ]
    mock_validate.side_effect = [
        {"src/App.jsx": ["A generated contract is invalid."]},
        {},
    ]

    result = asyncio.run(
        generate_frontend(
            make_test_frontend_plan(),
            output_dir=tmp_path,
        )
    )

    assert result.exists()
    assert mock_file.await_count == 2
    assert mock_validate.call_count == 2
    mock_build.assert_called_once_with(result)
    mock_runtime.assert_called_once_with(result, routes=["/"])

    repaired_file = (result / "src" / "App.jsx").read_text(
        encoding="utf-8"
    )
    assert "Fixed" in repaired_file


@patch("generation.frontend_generator._validate_frontend_build")
@patch("generation.frontend_generator._write_scaffold", return_value=[])
@patch("generation.frontend_generator._validate_project")
@patch("generation.frontend_generator._generate_file", new_callable=AsyncMock)
@patch(
    "generation.frontend_generator._generate_manifest",
    new_callable=AsyncMock,
)
def test_generate_frontend_fails_if_repair_does_not_resolve_errors(
    mock_manifest,
    mock_file,
    mock_validate,
    mock_scaffold,
    mock_build,
    tmp_path,
):
    from generation.frontend_generator import generate_frontend

    mock_manifest.return_value = [
        {
            "path": "src/App.jsx",
            "purpose": "Application component",
            "default_export": True,
            "named_exports": [],
        }
    ]
    mock_file.return_value = (
        "export default function App(){ return null; }"
    )
    mock_validate.return_value = {
        "src/App.jsx": ["A generated contract is invalid."]
    }

    with pytest.raises(ValueError, match="validation failed"):
        asyncio.run(
            generate_frontend(
                make_test_frontend_plan(),
                output_dir=tmp_path,
                strict=True,
            )
        )

    assert mock_file.await_count > 1
    mock_build.assert_not_called()


def test_context_contract_detects_inline_provider_value():
    texts = {
        "src/context/SessionContext.jsx": """
            export function useSession() {}
            export function SessionProvider({ children }) {
                return (
                    <SessionContext.Provider
                        value={{ session, loading }}
                    >
                        {children}
                    </SessionContext.Provider>
                );
            }
        """,
        "src/pages/Profile.jsx": """
            import { useSession } from "../context/SessionContext.jsx";
            export default function Profile() {
                const { session, refresh } = useSession();
                return null;
            }
        """,
    }

    problems = _validate_context_contracts(Path("."), [], texts)

    assert "src/pages/Profile.jsx" in problems
    assert any("refresh" in issue for issue in problems["src/pages/Profile.jsx"])


def test_context_contract_accepts_inline_provider_properties():
    texts = {
        "src/context/SessionContext.jsx": """
            export function useSession() {}
            export function SessionProvider({ children }) {
                return (
                    <SessionContext.Provider
                        value={{ session, loading }}
                    >
                        {children}
                    </SessionContext.Provider>
                );
            }
        """,
        "src/pages/Profile.jsx": """
            import { useSession } from "../context/SessionContext.jsx";
            export default function Profile() {
                const { session, loading } = useSession();
                return null;
            }
        """,
    }

    problems = _validate_context_contracts(Path("."), [], texts)

    assert "src/pages/Profile.jsx" not in problems


def test_context_contract_accepts_destructuring_alias():
    texts = {
        "src/context/SessionContext.jsx": """
            const value = { session, loading };
            export function useSession() {}
        """,
        "src/pages/Profile.jsx": """
            import { useSession } from "../context/SessionContext.jsx";
            export default function Profile() {
                const { session: currentSession } = useSession();
                return null;
            }
        """,
    }

    problems = _validate_context_contracts(Path("."), [], texts)

    assert "src/pages/Profile.jsx" not in problems


def test_context_contract_supports_different_context_names():
    texts = {
        "src/context/ThemeContext.jsx": """
            const value = { theme, setTheme };
            export function useTheme() {}
        """,
        "src/components/ThemeButton.jsx": """
            import { useTheme } from "../context/ThemeContext.jsx";
            export default function ThemeButton() {
                const { theme, setTheme } = useTheme();
                return null;
            }
        """,
    }

    problems = _validate_context_contracts(Path("."), [], texts)

    assert "src/components/ThemeButton.jsx" not in problems


def test_context_contract_does_not_flag_unrelated_files():
    texts = {
        "src/context/SessionContext.jsx": """
            const value = { session, loading };
            export function useSession() {}
        """,
        "src/pages/About.jsx": """
            export default function About() {
                const refresh = "Refresh";
                return null;
            }
        """,
    }

    problems = _validate_context_contracts(Path("."), [], texts)

    assert problems == {}

