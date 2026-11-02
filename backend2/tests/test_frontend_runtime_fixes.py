"""Provider wiring checks, browser-error repair and Playwright-missing handling."""
import asyncio
import sys
import tempfile
import types
from pathlib import Path

from workflow.debugger import _files_from_runtime_error, debug_project
from workflow.validators import check_frontend_static, check_runtime, has_errors

CONTEXT = """import { createContext, useContext } from 'react';
const GalleryContext = createContext(null);
export function GalleryProvider({ children }) {
  return <GalleryContext.Provider value={{ items: [] }}>{children}</GalleryContext.Provider>;
}
export function useGallery() {
  const ctx = useContext(GalleryContext);
  if (!ctx) throw new Error('useGallery must be used within a GalleryProvider');
  return ctx;
}
"""
PAGE = ("import { useGallery } from '../context/GalleryContext.jsx';\n"
        "export default function Gallery() { const { items } = useGallery(); return <div>{items.length}</div>; }\n")


def _frontend(root: Path) -> Path:
    fe = root / "frontend"
    files = {
        "src/context/GalleryContext.jsx": CONTEXT,
        "src/pages/Gallery.jsx": PAGE,
        "src/App.jsx": "export default function App() { return <div />; }\n",
        "src/main.jsx": "import App from './App.jsx';\n",
        "index.html": "<html/>",
        "package.json": '{"dependencies": {"react": "18"}, "scripts": {"build": "vite build"}}',
    }
    for rel, text in files.items():
        f = fe / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
    (root / "backend").mkdir()
    return fe


def test_unmounted_provider_is_a_static_error_and_fixed_without_llm():
    with tempfile.TemporaryDirectory() as tmp:
        fe = _frontend(Path(tmp))
        issues = check_frontend_static(fe, import_checker=lambda p: [])
        wiring = [i for i in issues if i["code"] == "unmounted_provider"]
        assert wiring and has_errors(issues)

        async def no_llm(**kw):
            raise AssertionError("LLM must not be used for provider wiring")

        fixes = asyncio.run(debug_project(issues, Path(tmp) / "backend", fe, llm=no_llm))
        assert [f["method"] for f in fixes] == ["deterministic"]
        assert "<GalleryProvider>" in (fe / "src" / "main.jsx").read_text()
        assert not has_errors(check_frontend_static(fe, import_checker=lambda p: []))


def test_browser_crash_with_provider_message_is_fixed_without_llm():
    with tempfile.TemporaryDirectory() as tmp:
        fe = _frontend(Path(tmp))
        issue = {"stage": "runtime", "code": "runtime_failure", "severity": "error",
                 "message": "Page crashed: useGallery must be used within a GalleryProvider",
                 "file": None, "auto_fixable": True}

        async def no_llm(**kw):
            raise AssertionError("LLM must not be used")

        fixes = asyncio.run(debug_project([issue], Path(tmp) / "backend", fe, llm=no_llm))
        assert fixes and fixes[0]["file"] == "src/main.jsx"
        assert "<GalleryProvider>" in (fe / "src" / "main.jsx").read_text()


def test_runtime_error_text_finds_the_right_files():
    with tempfile.TemporaryDirectory() as tmp:
        fe = _frontend(Path(tmp))
        (fe / "src" / "pages" / "Home.jsx").write_text(
            "const { fetchProducts } = useProducts(); fetchProducts();\n")
        (fe / "src" / "pages" / "Cart.jsx").write_text("<p>{item.price.toFixed(2)}</p>\n")
        found = _files_from_runtime_error(
            "TypeError: fetchProducts is not a function", fe, [])
        assert [p.name for p in found] == ["Home.jsx"]
        found = _files_from_runtime_error(
            "Cannot read properties of undefined (reading 'toFixed')", fe, [])
        assert [p.name for p in found] == ["Cart.jsx"]


def _fake_runtime(error: Exception | None):
    module = types.ModuleType("generation.connected_runtime")

    def validate_connected_runtime(*a, **k):
        if error:
            raise error
    module.validate_connected_runtime = validate_connected_runtime
    sys.modules["generation.connected_runtime"] = module


def test_missing_playwright_is_a_warning_not_a_failed_build():
    try:
        _fake_runtime(ValueError("Executable doesn't exist at C:\\x. Please run: npx playwright install"))
        issues = check_runtime("f", "b", [], ["/"], run_browser=True)
        assert issues[0]["severity"] == "warning" and issues[0]["code"] == "browser_unavailable"
    finally:
        sys.modules.pop("generation.connected_runtime", None)


def test_real_page_failure_is_still_an_error():
    try:
        _fake_runtime(ValueError(
            "Frontend browser runtime validation failed.\n"
            "route /gallery loads without runtime errors\nError: useGallery must be used within"))
        issues = check_runtime("f", "b", [], ["/"], run_browser=True)
        assert issues[0]["severity"] == "error" and issues[0]["code"] == "runtime_failure"
    finally:
        sys.modules.pop("generation.connected_runtime", None)
