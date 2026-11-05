from __future__ import annotations

import asyncio
import html
import json
import os
import random
import re
from pathlib import Path, PurePosixPath
import shutil
from core.groq_client import call_groq
from planning.frontend_schemas import FrontendPlan
from generation.component_contracts import validate_component_props
from generation.provider_wiring import (
    build_main_jsx,
    validate_context_properties,
    validate_provider_wiring,
)

import json
import subprocess

from pathlib import Path
import re



def _validate_frontend_build(project_dir: Path) -> None:
    """Install frontend dependencies and validate the production build."""
    project_dir = Path(project_dir).resolve()
    package_file = project_dir / "package.json"

    if not package_file.is_file():
        raise ValueError(
            f"package.json is missing from {project_dir}"
        )

    try:
        with package_file.open("r", encoding="utf-8") as file:
            package = json.load(file)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Could not read package.json: {exc}"
        ) from exc

    scripts = package.get("scripts", {})
    if not isinstance(scripts, dict) or "build" not in scripts:
        raise ValueError(
            "package.json does not define a production build script."
        )

    # Keep this node running during transient registry failures. A temporary
    # ECONNRESET must not mark the whole AgentForge job as failed.
    from core.npm_utils import install_npm_dependencies

    try:
        install_npm_dependencies(project_dir, attempts=4, timeout=600)
    except RuntimeError as exc:
        raise ValueError(
            "Frontend dependency installation failed after network retries.\\n"
            f"Project: {project_dir}\\n{exc}"
        ) from exc

    # Build only after dependencies have installed successfully.
    
   
    try:
        npm_command = shutil.which("npm.cmd") or shutil.which("npm")

        if not npm_command:
            raise FileNotFoundError(
                "npm was not found. Please install Node.js "
                "and ensure npm is available in PATH."
            )

        result = subprocess.run(
            [npm_command, "run", "build"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
            shell=False
        )

        if result.returncode != 0:
            raise ValueError(
                "Frontend production build failed:\n"
                f"STDOUT:\n{result.stdout[-5000:]}\n"
                f"STDERR:\n{result.stderr[-5000:]}"
            )

    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(
            f"Frontend production build could not complete: {exc}"
        ) from exc


    if result.returncode != 0:
        details = (
            result.stderr or result.stdout
        )[-12000:]
        raise ValueError(
            "Frontend production build failed.\n"
            f"Project: {project_dir}\n{details}"
        )




import socket
import time
import urllib.request


def _validate_frontend_runtime(
    project_dir: Path,
    routes: list[str] | None = None,
) -> None:
    """Run Playwright smoke tests against a generated Vite project."""
    project_dir = Path(project_dir).resolve()
    repo_root = Path(__file__).resolve().parents[1]
    test_file = repo_root / "tests" / "browser" / "runtime.spec.js"

    if not test_file.is_file():
        raise ValueError(
            f"Playwright test file not found: {test_file}"
        )

    package_file = project_dir / "package.json"
    if not package_file.is_file():
        raise ValueError("Cannot run browser tests without package.json.")

    package = json.loads(package_file.read_text(encoding="utf-8"))
    dependencies = {
        **package.get("dependencies", {}),
        **package.get("devDependencies", {}),
    }

    if "vite" not in dependencies:
        raise ValueError(
            "Browser runtime validation currently requires a Vite project."
        )

    npm_command = "npm.cmd" if os.name == "nt" else "npm"
    npx_command = "npx.cmd" if os.name == "nt" else "npx"

    # Select an available port for the preview server.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    base_url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env["APP_URL"] = base_url
    env["APP_ROUTES"] = ",".join(routes or ["/"])

    server = None

    try:
        server = subprocess.Popen(
            [
                npm_command,
                "run",
                "preview",
                "--",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--strictPort",
            ],
            cwd=project_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
        )

        # Wait until Vite responds or the startup deadline expires.
        deadline = time.monotonic() + 30
        ready = False

        while time.monotonic() < deadline:
            if server.poll() is not None:
                raise ValueError(
                    "Vite preview server exited before becoming ready."
                )

            try:
                with urllib.request.urlopen(
                    base_url, timeout=1
                ) as response:
                    if response.status < 400:
                        ready = True
                        break
            except Exception:
                time.sleep(0.25)

        if not ready:
            raise ValueError(
                "Vite preview server did not become ready within 30 seconds."
            )

        test_path = test_file.relative_to(repo_root).as_posix()

        result = subprocess.run(
            [
                npx_command,
                "--no-install",
                "playwright",
                "test",
                test_path,
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

        if result.returncode != 0:
            details = (result.stdout + "\n" + result.stderr)[-12000:]
            raise ValueError(
                "Frontend browser runtime validation failed.\n"
                f"Project: {project_dir}\n{details}"
            )

        print("Frontend browser runtime validation passed.")
        print(result.stdout[-4000:])

    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "Frontend browser runtime validation timed out."
        ) from exc

    finally:
        if server is not None and server.poll() is None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()



MAX_BUILD_REPAIR_ROUNDS = 3     # build errors (syntax etc.) -> fix agent
MAX_RUNTIME_REPAIR_ROUNDS = 2   # browser crashes -> fix agent
MAX_REPAIR_TARGETS = 3          # files rewritten per round (Groq TPM limit)


def _find_error_files(
    error_text: str,
    manifest: list[dict],
    project_dir: Path | None = None,
) -> list[str]:
    """Map a build/browser error to the generated files that likely caused it.

    The old version of this function used a double-escaped regex that never
    matched anything. Three strategies are used, in order:
      1. explicit source paths (vite/esbuild output, dev-server stack traces,
         Windows or POSIX separators);
      2. component names from stack frames ("at Gallery (...)") mapped to the
         manifest's default exports;
      3. identifiers inside the message ("useGallery", "Foo is not defined",
         "XProvider") mapped to the files that export or use them.
    """
    by_path = {item["path"]: item for item in manifest}
    source_paths = [
        p for p in by_path
        if p.startswith("src/") and p.endswith(SOURCE_EXTS)
    ]
    targets: list[str] = []

    def add(path: str) -> None:
        if path in source_paths and path not in targets:
            targets.append(path)

    normalized = error_text.replace("\\", "/")

    for m in re.finditer(
        r"(src/[^\s():\"'?\[\]]+?\.(?:jsx|js))(?![\w])", normalized
    ):
        add(m.group(1))

    for m in re.finditer(r"\bat\s+([A-Z][\w$]*)\s*\(", error_text):
        for item in manifest:
            if item.get("default_export") == m.group(1):
                add(item["path"])

    identifiers = set(re.findall(r"\buse[A-Z]\w*\b", error_text))
    identifiers |= set(re.findall(r"\b[A-Z]\w*Provider\b", error_text))
    identifiers |= set(
        re.findall(r"\b([A-Za-z_$][\w$]*) is not defined", error_text)
    )
    for ident in sorted(identifiers):
        for item in manifest:
            if (
                item.get("default_export") == ident
                or ident in (item.get("named_exports") or [])
            ):
                add(item["path"])
        if project_dir is not None:
            for path in source_paths:
                file = Path(project_dir) / path
                if file.is_file() and re.search(
                    r"\b%s\b" % re.escape(ident),
                    file.read_text(encoding="utf-8", errors="ignore"),
                ):
                    add(path)

    return targets


# Backwards-compatible name.
_find_runtime_repair_targets = _find_error_files


def validate_local_imports(project_dir: Path) -> list[str]:
    """Validate relative imports in JavaScript and JSX files."""
    project_dir = Path(project_dir).resolve()
    errors = []

    source_extensions = {".js", ".jsx", ".ts", ".tsx"}
    extensions = ["", ".js", ".jsx", ".ts", ".tsx", ".json"]
    source_dir = project_dir / "src"

    if not source_dir.exists():
        return errors

    import_pattern = re.compile(
        r"""(?:import\s+(?:.*?\s+from\s+)?|export\s+.*?\s+from\s+)["']([^"']+)["']"""
    )

    for file_path in source_dir.rglob("*"):
        if not file_path.is_file() or file_path.suffix not in source_extensions:
            continue

        source = file_path.read_text(encoding="utf-8")

        for match in import_pattern.finditer(source):
            import_path = match.group(1)

            if not import_path.startswith("."):
                continue

            target = (file_path.parent / import_path).resolve()

            candidates = [
                target.with_suffix(ext) if ext else target
                for ext in extensions
            ]
            candidates.extend(
                target / f"index{ext}"
                for ext in [".js", ".jsx", ".ts", ".tsx"]
            )

            if not any(candidate.is_file() for candidate in candidates):
                errors.append(
                    f"{file_path.relative_to(project_dir)}: "
                    f"cannot resolve import '{import_path}'"
                )

    return errors

# --------------------------------------------------------------------------
# Tunables
# --------------------------------------------------------------------------
FORCE_CONTEXT_STATE = True      # True: React Context instead of Redux/Zustand
MAX_FILES = 24                 # files the LLM may plan (excluding scaffold)
MANIFEST_ATTEMPTS = 3
MAX_REPAIR_PASSES = 2
FILE_MAX_TOKENS = 2500          # output budget per file (reasoning models spend
                                # part of this on thinking, so keep headroom)

# Files written by code, never by the LLM.
SCAFFOLD_PATHS = {
    "package.json",
    "index.html",
    "vite.config.js",
    "postcss.config.js",
    "tailwind.config.js",
    "src/main.jsx",
    "src/styles.css",
}

# Packages generated code may import, with pinned versions.
PINNED_PACKAGES = {
    "react": "18.3.1",
    "react-dom": "18.3.1",
    "react-router-dom": "6.28.0",
    "lucide-react": "0.468.0",
    "clsx": "2.1.1",
    "axios": "1.7.9",
    "framer-motion": "11.15.0",
}
CORE_DEPENDENCIES = ("react", "react-dom", "react-router-dom")
DEV_DEPENDENCIES = {
    "vite": "5.4.11",
    "@vitejs/plugin-react": "4.3.4",
    "tailwindcss": "3.4.17",
    "postcss": "8.4.49",
    "autoprefixer": "10.4.20",
}

SOURCE_EXTS = (".js", ".jsx")
RESOLVE_SUFFIXES = ("", ".js", ".jsx", ".css", ".json", "/index.js", "/index.jsx")


# --------------------------------------------------------------------------
# LLM call with retry (unchanged logic, now actually used)
# --------------------------------------------------------------------------
async def call_groq_with_retry(
    *,
    prompt: str,
    system: str,
    max_tokens: int = 3000,
    temperature: float = 0.2,
    response_format: dict | None = None,
    max_attempts: int = 6,
):
    """
    Retry temporary Groq rate limits and transient server/network errors.
    Never retry permanent authentication or billing errors indefinitely.
    """
    for attempt in range(max_attempts):
        try:
            return await call_groq(
                prompt=prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
                response_format=response_format,
            )
        except Exception as exc:
            chain = []
            current = exc
            seen = set()
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                chain.append(current)
                current = current.__cause__ or current.__context__
            details = " ".join(
                f"{type(err).__name__}: {err}" for err in chain
            ).lower()
            if "output was truncated" in details:
                raise
            retryable = any(
                marker in details
                for marker in (
                    "ratelimiterror",
                    "rate_limit_exceeded",
                    "rate limit reached",
                    "tokens per minute",
                    "requests per minute",
                    "status code: 429",
                    " 429",
                    "service unavailable",
                    "internal server error",
                    "bad gateway",
                    "gateway timeout",
                    "timed out",
                    "timeout",
                    "connection error",
                    "empty content",
                )
            )
            permanent = any(
                marker in details
                for marker in (
                    "insufficient_quota",
                    "invalid_api_key",
                    "authenticationerror",
                    "permission denied",
                    "billing",
                )
            )
            if permanent or not retryable or attempt == max_attempts - 1:
                print(f"\nGroq request failed after {attempt + 1} attempt(s).")
                raise
            match = re.search(r"try again in\s+([\d.]+)\s*s", details)
            delay = (
                float(match.group(1)) + 0.5
                if match
                else min(2 ** (attempt + 1), 60)
            )
            delay += random.uniform(0.2, 1.0)
            print(
                f"\nTemporary Groq error ({type(exc).__name__}). "
                f"Retrying in {delay:.1f}s ({attempt + 2}/{max_attempts})..."
            )
            await asyncio.sleep(delay)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
FRONTEND_FILE_PLANNER_PROMPT = """
You are the file-planning agent of AgentForge. Plan the SOURCE FILES of a
project-specific React frontend from the supplied plan.

The project may run in connected mode with a generated backend. Follow the API contract in FRONTEND PLAN when provided.
Package.json, index.html, vite.config.js, tailwind.config.js, postcss.config.js,
src/main.jsx and src/styles.css are created automatically. DO NOT list them.

Return ONLY a JSON object in exactly this shape:
{
  "files": [
    {
      "path": "src/pages/Home.jsx",
      "purpose": "One sentence: what this file renders or does",
      "default_export": "Home",
      "named_exports": []
    },
    {
      "path": "src/context/CartContext.jsx",
      "purpose": "Cart state shared across pages",
      "default_export": null,
      "named_exports": ["CartProvider", "useCart"]
    }
  ]
}
PRODUCT IMAGE REQUIREMENTS

- Use the image path supplied by the product data.
- Support both image_url and imageUrl field names.
- Do not replace a valid supplied image with a hardcoded URL.
- Do not generate guessed Unsplash or Picsum URLs.
- Use a local project image when one has been validated and supplied.
- Display a consistent fallback when no valid image exists.
- Keep image loading errors from breaking the product card.


PRODUCT DATA IMAGE RULES

- Product image data may use either imageUrl or image_url.
- Read the supplied value using:
  product.imageUrl || product.image_url || ""
- Do not replace a valid supplied path with a hardcoded image URL.
- Do not invent Unsplash or Picsum URLs.
- Local paths such as /images/croissant.jpg must refer to files
  actually included in the generated frontend's public/images folder.
- When an image is missing or fails to load, display a consistent fallback.
- Do not claim an image was downloaded or validated unless it was checked.

Rules:
1. Every path starts with "src/" and ends in .jsx (components/pages/providers)
   or .js (data, helpers). Never TypeScript.
2. "src/App.jsx" is REQUIRED and its default_export MUST be "App". App.jsx
   defines the <Routes>. main.jsx already provides the router.
3. Every file MUST declare its exports. A component file normally has exactly
   one default_export (PascalCase, equal to the file name) and no named exports.
   A context/hook/data/helper file uses named_exports.
4. This contract is binding: other files will import exactly these names and
   nothing else. List every name another file will need.

4.1. Every component listed in PROJECT PLAN's shared_components MUST have
     its own file in the manifest, unless an equivalent component file is
     already included. Use paths such as src/components/Button.jsx,
     src/components/Input.jsx, src/components/LoadingSpinner.jsx,
     src/components/ErrorMessage.jsx and src/components/Modal.jsx.
4.2. Every generated page and component must import ONLY files present in
     the manifest. Never invent paths such as shared_components/Input.jsx.
4.3. Reuse shared components instead of recreating them. Every import path
     must match the actual manifest path and declared export.
4.4. Count shared component files within MAX_FILES. If the limit is reached,
     merge small related components into an existing component file and
     update its export contract. Never omit a required shared component.
4.5. The generator uses JavaScript and JSX, not TypeScript. Use .js/.jsx
     paths and JavaScript-compatible exports only.

5. If FRONTEND PLAN contains a backend_api_contract, use real API calls for server-owned data and do not create mock modules for that data. Use local static data only for genuinely static content. Without a contract, local mock modules may be used.
5.2. AUTHENTICATION FILE CONTRACT (MANDATORY when authentication is required):
     - Include src/pages/Login.jsx with default export Login.
     - Include src/pages/Signup.jsx with default export Signup whenever registration is supported.
     - These must be two separate files and two separate page components. Never reuse Login as Signup or render the same component for both routes.
     - Include src/App.jsx routes: path="/login" renders <Login /> and path="/signup" renders <Signup />.
     - Include the correct imports for both page components in App.jsx.
     - Login form fields: email and password. Signup form fields: name, email and password.
     - Use distinct headings ("Log In" / "Sign In" for login and "Create Account" / "Sign Up" for signup).
     - The signup page must contain a link to /login; the login page must contain a link to /signup.
     - Follow the exact backend API contract for request fields and response fields. Do not assume token field names.
     - Do not place both forms in one component and switch only the heading or button label.
5.1. When a contract is provided, plan one src/api/<resource>Api.js file per
     resource (named exports only, for example getProducts and createOrder)
     and let pages, hooks and context providers import those functions.
6. Shared state uses React Context in src/context/ (a Provider plus a
   useXxx hook as named exports). No Redux, Zustand or other state library.
   main.jsx mounts every Provider automatically, so App.jsx must not.
7. Pick pages and components from the REAL business requirements in the plan.
   Skip generic filler. Reuse components instead of duplicating them.
8. Keep it buildable in one pass: {MAX_FILES} files or fewer in total. Prefer
   several small components over one giant file. Merge tiny related pieces.
9. Do not list tests, configs, README files, .env files or backups.
10. Return JSON only, with no Markdown fences and no commentary.
""".replace("{MAX_FILES}", str(MAX_FILES))


FRONTEND_FILE_CODE_PROMPT = """
You are the code-generation agent inside AgentForge. Generate the COMPLETE
source code for exactly ONE frontend file.

OUTPUT FORMAT
- Return ONLY the file's source code as plain text.
- No JSON wrapper, no Markdown fences, no explanations before or after.

PROJECT SETUP (already decided, do not fight it)
- React 18 + plain JavaScript (ES modules) + Vite + Tailwind CSS 3.
- src/main.jsx ALREADY wraps <App /> in <BrowserRouter>.
  So NEVER render <BrowserRouter>, <HashRouter>, <MemoryRouter> or <Router>
  anywhere. App.jsx renders <Routes> and <Route> directly.
- src/main.jsx ALSO mounts every <XProvider> exported from src/context/
  around <App />, in the right order. Do NOT render context providers in
  App.jsx or anywhere else, and never mount the same provider twice. Every
  useXxx hook you call must come from a Provider that exists in src/context/.
- Tailwind is configured. Use utility classes. Theme colors from the project
  are available as classes such as bg-primary, text-primary, border-secondary,
  bg-primary-light (the exact color names are listed in the context).
  Fonts: font-heading and font-body.
- If a BACKEND API CONTRACT is provided, a backend will be generated from the
  calls you write, so follow these API rules exactly:
  * Use only the contract's endpoints and HTTP methods for server-owned data.
    Do not invent endpoints. Never use mock data for server-owned data and
    never silently fall back to mock data when a real API call fails.
  * Write the URL directly inside the fetch call, as a string literal or a
    template literal: fetch('/api/products') or fetch(`/api/products/${id}`).
    Never build the URL in a variable, a base-URL constant or a helper.
  * For requests with a JSON body, write every key explicitly as `key: value`
    inside JSON.stringify({ ... }), using exactly the contract's
    request_fields, for example
    fetch('/api/orders', { method: 'POST', headers: { 'Content-Type':
    'application/json' }, body: JSON.stringify({ customerName: name, total: total }) }).
    Do not use shorthand keys, spreads or a pre-built body variable.
  * Read from responses only the fields listed in the contract's
    response_fields, spelled exactly as listed.
  * Check response.ok and throw an Error when it is false. Show loading,
    empty, success and error states in the UI.
  * Put all network calls in src/api/ modules (for example
    src/api/productsApi.js) that export small named functions, and call those
    functions from pages, hooks and context providers.
- If no BACKEND API CONTRACT is provided, use local mock modules as before.

EXPORT / IMPORT CONTRACT (most important rule)
- The context lists the contract for every file in the project.
- This file MUST export exactly what its contract says: the default export
  name, and every named export, spelled identically.
- Import from other project files ONLY what their contract lists, using the
  same style: a default export is imported as `import Name from '...'`, a
  named export as `import { name } from '...'`.
- Use relative paths WITH the file extension, e.g. '../components/Header.jsx'.
- Never import a project file that is not in the contract.
- Third-party imports are limited to: react, react-dom, react-router-dom,
  lucide-react, clsx, framer-motion, axios. Nothing else.


CREATIVE FREEDOM AND VISUAL QUALITY

- Treat the user's requirements as the starting point, not a limit.
- Improve the website with useful, relevant features that enhance the
  user experience, even when they were not explicitly requested.
- Use creative judgment for layout, color palettes, typography,
  animations, transitions, illustrations, and responsive behavior.
- Add polished empty states, loading states, helpful feedback,
  search, filters, and interactive details wherever appropriate.
- Use realistic mock data only when no backend API contract is provided.
- Prefer visually rich websites with relevant imagery, clear hierarchy,
  reusable components, and consistent design.
- Do not make every generated website look like the same template.
- Do not avoid useful features merely because they were not explicitly
  listed in the requirements.

SHARED LAYOUT (no duplicates)

- App.jsx renders <Navbar /> and <Footer /> once around all routes.
- Page components must NOT render Navbar, Header or Footer themselves.
- Every navigation destination appears exactly once in the navbar. If a
  cart link shows an item-count badge, put the badge inside that single
  Cart link; never add a second Cart link.

IMAGE HANDLING

- Prefer relevant, valid image URLs when external images are appropriate.
- Do not assume that a local image file exists unless it is included
  in the generated project or explicitly created by the build process.
- If local images are used, create the corresponding image assets or
  provide a graceful fallback.
- Add an onError fallback for important images so broken URLs do not
  leave broken-image icons in the interface.
- Use meaningful alt text for accessibility.
- Keep hero images, destination images, cards, and avatars visually
  consistent with the website's purpose.

IMPLEMENTATION QUALITY

- Preserve valid imports, exports, component props, and routing.
- AUTHENTICATION: if this file is src/pages/Login.jsx, implement ONLY the login page
  with email/password fields and a link to /signup. If this file is
  src/pages/Signup.jsx, implement ONLY the registration page with name/email/password
  fields and a link to /login. Never render the Login form from Signup.jsx or vice versa.
  In src/App.jsx, map /login to <Login /> and /signup to <Signup /> using separate imports.
- Reuse existing shared components where practical.
- Keep interactions functional, not merely decorative.
- When adding a new feature, include its required components and
  dependencies in the implementation.
- If an external service or asset is unavailable, provide a reasonable
  fallback rather than pretending it succeeded.
- Prefer a complete, polished implementation over a minimal mockup.


CODE QUALITY
- Functional components and hooks. No TypeScript syntax of any kind.
- Responsive, accessible, polished UI that fits the business described.
- Real working interactions (add to cart, filter, form validation, etc.),
  with loading, empty and error states where they make sense.
- Keep the file focused: aim for under 150 lines. No TODOs, no placeholder
  pages, no pseudocode.
- Complete, syntactically valid code: every JSX tag, bracket, string and
  import closed. Finish the file; never stop mid-way.
- No secrets, no API keys.
- Prefer concise, production-style code over lengthy explanations.
- Keep simple component files under 80 lines whenever practical.
- Keep complex component files under 120 lines.
- Split large UI sections into separate files only when those files
  already exist in the file contract.
- Avoid excessive mock data, repeated markup, and unnecessary helpers.
- Every requested export must be implemented.
- Always return a complete file; never stop mid-statement.
"""


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "generated-frontend"


def _parse_json_object(response: str, label: str) -> dict:
    try:
        data = json.loads(response)
    except json.JSONDecodeError as exc:
        cleaned = re.sub(
            r"^\s*```(?:json)?\s*|\s*```\s*$",
            "",
            response,
            flags=re.IGNORECASE,
        )
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            raise ValueError(f"{label} returned invalid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{label} must return a JSON object.")
    return data


def _validate_relative_path(raw_path: str) -> str:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ValueError("Generated file path must be a non-empty string.")
    normalized = raw_path.strip().replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or re.match(r"^[a-zA-Z]:", normalized)
    ):
        raise ValueError(f"Unsafe generated file path: {raw_path}")
    return str(path)


def _clean_code(response: str) -> str:
    """Strip Markdown fences / surrounding prose if the model added them."""
    content = response.strip()
    fenced = re.findall(r"```[a-zA-Z0-9]*\n(.*?)```", content, flags=re.S)
    if fenced:
        content = max(fenced, key=len).strip()
    elif content.startswith("```"):
        content = re.sub(r"^```[^\n]*\n", "", content, count=1)
        content = re.sub(r"\n```\s*$", "", content, count=1).strip()
    return content


_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def _theme_colors(plan: FrontendPlan) -> dict[str, str]:
    """Only valid hex colors survive; primary/secondary/accent always exist."""
    colors = {"primary": "#2563eb", "secondary": "#7c3aed", "accent": "#f59e0b"}
    for key, value in (plan.design_system.colors or {}).items():
        name = re.sub(r"[^a-z0-9-]+", "-", str(key).strip().lower()).strip("-")
        if (
            _NAME_RE.match(name)
            and isinstance(value, str)
            and _HEX_RE.match(value.strip())
        ):
            colors[name] = value.strip()
    return colors


def _clean_font(value: str | None, fallback: str) -> str:
    name = re.sub(r"[^A-Za-z0-9 ]+", "", value or "").strip()
    return name if 2 <= len(name) <= 40 else fallback


# --------------------------------------------------------------------------
# Manifest (file plan with export contract)
# --------------------------------------------------------------------------

def _validate_manifest(data: dict) -> list[dict]:
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("Frontend file planner returned no files.")

    manifest: list[dict] = []
    seen: set[str] = set()

    for item in files:
        if not isinstance(item, dict):
            raise ValueError("Every manifest entry must be a JSON object.")

        path = _validate_relative_path(item.get("path", ""))

        if path.endswith(".tsx"):
            path = path[:-4] + ".jsx"
        elif path.endswith(".ts") and not path.endswith(".d.ts"):
            path = path[:-3] + ".js"

        
        # Keep all valid planned paths in the manifest.
        # Scaffold files are filtered when generating source code,
        # not when validating the complete manifest.
        if path.endswith((".test.js", ".test.jsx", ".bak")):
            continue

        if not (
            path in SCAFFOLD_PATHS
            or path.startswith("src/")
        ):
            continue

        if (
            path not in SCAFFOLD_PATHS
            and not path.endswith(SOURCE_EXTS)
        ):
            continue

        if path.endswith((".test.js", ".test.jsx", ".bak")):
            continue
        if path in seen:
            continue

        purpose = item.get("purpose", "")
        if not isinstance(purpose, str) or not purpose.strip():
            raise ValueError(f"Missing purpose for generated file: {path}")

        default_export = item.get("default_export")
        if not isinstance(default_export, str) or not default_export.strip():
            default_export = None
        else:
            default_export = default_export.strip()

        named_raw = item.get("named_exports") or []
        if not isinstance(named_raw, list):
            named_raw = []

        named_exports = []
        for name in named_raw:
            name = str(name).strip()
            if (
                re.match(r"^[A-Za-z_$][\w$]*$", name)
                and name not in named_exports
            ):
                named_exports.append(name)

        if path == "src/App.jsx":
            default_export = "App"
        elif not default_export and not named_exports:
            default_export = Path(path).stem

        seen.add(path)
        manifest.append({
            "path": path,
            "purpose": purpose.strip(),
            "default_export": default_export,
            "named_exports": named_exports,
        })

    if "src/App.jsx" not in seen:
        raise ValueError(
            "Frontend file planner omitted required files: src/App.jsx"
        )

    if len(manifest) > MAX_FILES:
        raise ValueError(
            f"Frontend manifest has {len(manifest)} files; the limit is "
            f"{MAX_FILES}. Merge or drop files."
        )

    return manifest



def _authentication_requirements(
    plan: FrontendPlan,
    api_contract: list[dict] | None,
) -> tuple[bool, bool]:
    """Return (login_required, signup_required) from routes and API contract."""
    page_signals = []
    for page in getattr(plan, "pages", []) or []:
        name = str(getattr(page, "name", "") or "").lower()
        route = str(getattr(page, "route", "") or "").lower().rstrip("/")
        page_signals.append((name, route))

    contract_paths = set()
    for item in api_contract or []:
        if not isinstance(item, dict):
            continue
        contract_paths.add(str(item.get("path") or item.get("endpoint") or "").lower())

    login_api = any(path.endswith("/auth/login") for path in contract_paths)
    signup_api = any(
        path.endswith(("/auth/register", "/auth/signup", "/auth/sign-up"))
        for path in contract_paths
    )
    login_page = any(
        route == "/login" or name in {"login", "sign in", "signin"}
        for name, route in page_signals
    )
    signup_page = any(
        route in {"/signup", "/sign-up", "/register"}
        or name in {"signup", "sign up", "register", "registration"}
        for name, route in page_signals
    )

    login_required = login_api or login_page
    signup_required = signup_api or signup_page
    # A registration endpoint implies that the app also needs a login page.
    if signup_required:
        login_required = True
    return login_required, signup_required


def _ensure_auth_manifest(
    plan: FrontendPlan,
    manifest: list[dict],
    api_contract: list[dict] | None,
) -> list[dict]:
    """Make essential auth page files deterministic instead of trusting the LLM."""
    login_required, signup_required = _authentication_requirements(plan, api_contract)
    required = []
    if login_required:
        required.append({
            "path": "src/pages/Login.jsx",
            "purpose": "Dedicated login page with email and password fields",
            "default_export": "Login",
            "named_exports": [],
        })
    if signup_required:
        required.append({
            "path": "src/pages/Signup.jsx",
            "purpose": "Dedicated registration page with name, email and password fields",
            "default_export": "Signup",
            "named_exports": [],
        })

    by_path = {item["path"]: item for item in manifest}
    for item in required:
        existing = by_path.get(item["path"])
        if existing:
            existing.update(item)
        else:
            manifest.append(item)
            by_path[item["path"]] = item

    if len(manifest) > MAX_FILES:
        raise ValueError(
            "Authentication requires dedicated Login/Signup files, but the "
            f"frontend manifest exceeds the {MAX_FILES}-file limit. "
            "Merge non-essential files or increase MAX_FILES."
        )
    return manifest


async def _generate_manifest(
    plan: FrontendPlan,
    api_contract: list[dict] | None = None,
) -> list[dict]:
    plan_data = _plan_for_llm(plan, api_contract=api_contract)

    base_prompt = (
        "Plan the source files for this frontend project.\n"
        "JavaScript only (.js/.jsx). Ignore TypeScript mentions.\n"
        "Return valid JSON with a files array. Each file must contain "
        "path, purpose, default_export, and named_exports.\n"
        f"Plan no more than {MAX_FILES} source files. "
        "Include src/App.jsx and all essential files. "
        "Keep each purpose to one short sentence and export lists concise.\n\n"
        "FRONTEND PLAN:\n"
        + json.dumps(plan_data, ensure_ascii=False, separators=(",", ":"))
    )

    last_error = "unknown error"

    for attempt in range(1, MANIFEST_ATTEMPTS + 1):
        if attempt == 1:
            prompt = base_prompt
            token_budget = 5000
        else:
            # Keep retries compact; do not append a long previous response.
            prompt = (
                base_prompt
                + "\n\nRETRY INSTRUCTIONS:\n"
                + "Return the COMPLETE valid JSON object, not a partial answer. "
                + "Use short purpose descriptions and concise export lists. "
                + "Do not exceed "
                + str(MAX_FILES)
                + " files. Previous attempt failed: "
                + last_error[:300]
            )
            token_budget = 6000

        try:
            response = await call_groq_with_retry(
                prompt=prompt,
                system=FRONTEND_FILE_PLANNER_PROMPT,
                max_tokens=token_budget,
                temperature=0.2,
                response_format={"type": "json_object"},
            )

            manifest = _validate_manifest(
                _parse_json_object(response, "Frontend file planner")
            )
            manifest = _ensure_auth_manifest(plan, manifest, api_contract)
            print(
                f"Frontend manifest created: {len(manifest)} files "
                f"(limit: {MAX_FILES})."
            )
            return manifest

        except Exception as exc:
            last_error = str(exc)
            details = last_error.lower()

            is_truncated = (
                "output was truncated" in details
                or "reached its token limit" in details
                or "incomplete json" in details
                or "unterminated string" in details
            )

            # Only retry errors that a shorter prompt or larger output
            # budget could reasonably fix.
            is_json_validation_error = "json_validate_failed" in details

            retryable = (
                is_truncated
                or isinstance(exc, ValueError)
                or is_json_validation_error
            )

            if not retryable:
                raise

            print(
                f"Manifest attempt {attempt}/{MANIFEST_ATTEMPTS} failed: "
                f"{last_error[:250]}"
            )

            if attempt < MANIFEST_ATTEMPTS:
                print("Retrying manifest generation without reducing MAX_FILES...")

    raise ValueError(
        f"Frontend manifest failed after {MANIFEST_ATTEMPTS} attempts: "
        f"{last_error}"
    )

# --------------------------------------------------------------------------
# Context sent to the code generator
# --------------------------------------------------------------------------
def _plan_for_llm(
    plan: FrontendPlan,
    api_contract: list[dict] | None = None,
) -> dict:
    data = plan.model_dump()
    data["framework"] = "React 18 with JavaScript (ES modules), Vite"
    data["styling"] = "Tailwind CSS 3"
    data.pop("tasks", None)  # task bookkeeping adds tokens, not information
    if FORCE_CONTEXT_STATE:
        stores = (data.get("state_management") or {}).get("stores", [])
        data["state_management"] = {
            "library": "React Context + hooks",
            "stores": stores,
        }
    if api_contract is not None:
        data["backend_api_contract"] = api_contract
        data["api_integrations"] = [
            item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for item in data.get("api_integrations", [])
        ]
    else:
        data["api_integrations"] = [
            {"name": a.get("name", ""), "purpose": a.get("purpose", "")}
            for a in data.get("api_integrations", [])
        ]
    return data


def _contract_text(manifest: list[dict]) -> str:
    lines = []
    for f in manifest:
        parts = []
        if f["default_export"]:
            parts.append(f"default {f['default_export']}")
        if f["named_exports"]:
            parts.append("named {" + ", ".join(f["named_exports"]) + "}")
        lines.append(f"{f['path']}  ->  " + "; ".join(parts) + f"   // {f['purpose']}")
    return "\n".join(lines)


def _file_context(
    plan: FrontendPlan,
    manifest: list[dict],
    target: dict,
    api_contract: list[dict] | None = None,
) -> str:
    ds = plan.design_system
    project = {
        "business_name": plan.business_name,
        "design": {
            "description": ds.description,
            "theme_color_classes": sorted(_theme_colors(plan)),
            "heading_font": ds.typography.get("heading", ""),
            "body_font": ds.typography.get("body", ""),
        },
        "pages": [p.model_dump() for p in plan.pages],
        "shared_components": plan.shared_components,
    }
    exports = []
    if target["default_export"]:
        exports.append(f"default export named {target['default_export']}")
    if target["named_exports"]:
        exports.append("named exports: " + ", ".join(target["named_exports"]))

    return (
        "PROJECT:\n"
        + json.dumps(project, ensure_ascii=False, separators=(",", ":"))
        + "\n\nFILE CONTRACT (path -> exports):\n"
        + _contract_text(manifest)
        + "\n\nREQUESTED FILE: "
        + target["path"]
        + "\nPURPOSE: "
        + target["purpose"]
        + "\nTHIS FILE MUST EXPORT: "
        + " and ".join(exports)
        + (
            "\n\nBACKEND API CONTRACT (use these exact paths/methods):\n"
            + json.dumps(api_contract, ensure_ascii=False, separators=(",", ":"))
            + "\nUse fetch with relative /api paths. Vite proxies /api to the backend."
            if api_contract is not None else ""
        )
    )


async def _generate_file(
    plan: FrontendPlan,
    manifest: list[dict],
    target: dict,
    *,
    previous_code: str | None = None,
    issues: list[str] | None = None,
    api_contract: list[dict] | None = None,
) -> str:
    base_prompt = (
        "Generate ONLY the complete source code for the requested file.\n"
        "JavaScript only. No JSON, no Markdown fences.\n\n"
        + _file_context(plan, manifest, target, api_contract=api_contract)
    )
    prompt = base_prompt
    if previous_code is not None and issues:
        prompt += (
            "\n\nYOUR PREVIOUS VERSION OF THIS FILE HAS PROBLEMS. Fix every one "
            "of them and return the full corrected file.\nPROBLEMS:\n- "
            + "\n- ".join(issues)
            + "\n\nPREVIOUS VERSION:\n"
            + previous_code
        )

    
    shrink = False   # retry after a truncated answer
    slim = False     # retry without the previous code when the prompt is too big
    for attempt in range(3):
        current_prompt = prompt

        if shrink:
            current_prompt += (
                "\n\nRETRY: Your previous output was truncated. "
                "Generate a much smaller but complete implementation. "
                "Remove decorative details and unnecessary helpers. "
                "Preserve all required exports and working core behavior."
            )

        try:
            response = await call_groq_with_retry(
                prompt=current_prompt,
                system=FRONTEND_FILE_CODE_PROMPT,
                max_tokens=1800 if shrink else FILE_MAX_TOKENS,
                temperature=0.2,
            )
        except Exception as exc:
            text = str(exc).lower()
            if "output was truncated" in text and not shrink:
                print(
                    f"Output truncated for {target['path']}; "
                    "retrying with a shorter implementation..."
                )
                shrink = True
                continue
            if (
                "configured tpm budget" in text
                and previous_code is not None
                and issues
                and not slim
            ):
                # previous code + context is bigger than the per-minute token budget:
                # rewrite the file from scratch, using only the list of problems.
                print(
                    f"Repair prompt for {target['path']} is too large; "
                    "rewriting the file from scratch instead..."
                )
                slim = True
                prompt = (
                    base_prompt
                    + "\n\nA PREVIOUS VERSION OF THIS FILE WAS REJECTED. Write the whole "
                    "file again and make sure none of these problems remain:\n- "
                    + "\n- ".join(issues)
                )
                continue
            raise

        content = _clean_code(response)

        if not content:
            raise ValueError(
                f"Model returned empty code for {target['path']}."
            )

        return content

    raise ValueError(
        f"Could not generate a complete file: {target['path']}"
    )



# --------------------------------------------------------------------------
# Static validation (imports, exports, routers, packages, truncation)
# --------------------------------------------------------------------------
_IMPORT_RE = re.compile(
    r"""^\s*import\s+(?P<clause>[^;'"]*?)\s*from\s*['"](?P<spec>[^'"]+)['"]""",
    re.M,
)
_SIDE_IMPORT_RE = re.compile(r"""^\s*import\s*['"](?P<spec>[^'"]+)['"]""", re.M)
_ROUTER_RE = re.compile(r"<\s*(BrowserRouter|HashRouter|MemoryRouter|Router)\b")
_TS_RE = re.compile(
    r"^\s*(?:export\s+)?(?:interface|type)\s+\w+\s*(?:<[^>]*>)?\s*(?:=|\{)", re.M
)


def _exports_of(text: str) -> tuple[bool, set[str]]:
    default = bool(re.search(r"\bexport\s+default\b", text))
    named = set(
        re.findall(
            r"\bexport\s+(?:async\s+)?(?:const|let|var|function\*?|class)\s+([A-Za-z_$][\w$]*)",
            text,
        )
    )
    for block in re.findall(r"\bexport\s*\{([^}]*)\}", text):
        for part in block.split(","):
            m = re.match(r"\s*([\w$]+)(?:\s+as\s+([\w$]+))?\s*$", part)
            if not m:
                continue
            exported = m.group(2) or m.group(1)
            if exported == "default":
                default = True
            else:
                named.add(exported)
    return default, named


def _parse_clause(clause: str) -> tuple[str | None, list[str], bool]:
    clause = clause.strip()
    default_name = None
    named: list[str] = []
    namespace = False

    block = re.search(r"\{([^}]*)\}", clause)
    if block:
        for part in block.group(1).split(","):
            name = re.split(r"\s+as\s+", part.strip())[0].strip()
            if name.startswith("type "):
                name = name[5:].strip()
            if name == "default":
                default_name = default_name or "default"
            elif name:
                named.append(name)
        clause = clause[: block.start()] + clause[block.end():]

    if "*" in clause:
        namespace = True
        clause = re.sub(r"\*\s*as\s+[\w$]+", "", clause)

    leftover = clause.replace(",", "").strip()
    if leftover:
        default_name = leftover
    return default_name, named, namespace


def _package_root(spec: str) -> str:
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") else parts[0]


def _resolve(project_dir: Path, importer: str, spec: str) -> str | None:
    base = os.path.normpath(
        os.path.join(os.path.dirname(importer), spec)
    ).replace("\\", "/")
    for suffix in RESOLVE_SUFFIXES:
        candidate = base + suffix
        if (project_dir / candidate).is_file():
            return candidate
    return None




def _validate_context_contracts(
    project_dir: Path,
    manifest: list[dict],
    texts: dict[str, str],
) -> dict[str, list[str]]:
    """Detect React Context values referenced but not provided."""
    problems: dict[str, list[str]] = {}

    context_files = {
        path: text
        for path, text in texts.items()
        if "/context/" in path.replace("\\", "/")
    }

    for consumer_path, consumer_text in texts.items():
        normalized_consumer = consumer_path.replace("\\", "/")

        if "/context/" in normalized_consumer:
            continue

        for context_path, context_text in context_files.items():
            context_name = Path(context_path).stem

            # Only inspect consumers that reference this context.
            if context_name not in consumer_text:
                continue

            # Support both:
            # const value = { session, loading };
            # <Context.Provider value={{ session, loading }}>
            value_match = re.search(
                r"\bconst\s+value\s*=\s*\{([^{}]*)\}",
                context_text,
                re.S,
            )

            if value_match:
                value_body = value_match.group(1)
            else:
                inline_match = re.search(
                    r"\bvalue\s*=\s*\{\s*\{([^{}]*)\}\s*\}",
                    context_text,
                    re.S,
                )
                if not inline_match:
                    continue
                value_body = inline_match.group(1)

            # Parse ordinary properties and aliases:
            # { items, totalAmount, reload: loadMenu }
            provided = set()

            for entry in value_body.split(","):
                entry = entry.strip()

                if not entry or entry.startswith("..."):
                    continue

                match = re.match(
                    r"^([A-Za-z_$][\w$]*)"
                    r"(?:\s*:\s*[^,]+)?$",
                    entry,
                )

                if match:
                    provided.add(match.group(1))

            hook_name = "use" + context_name.removesuffix("Context")

            hook_pattern = (
                r"\bconst\s*\{([^}]*)\}\s*=\s*"
                + re.escape(hook_name)
                + r"\s*\(\s*\)"
            )

            for hook_match in re.finditer(
                hook_pattern, consumer_text, re.S
            ):
                destructured = hook_match.group(1)

                for entry in destructured.split(","):
                    entry = entry.strip()

                    if not entry or entry.startswith("..."):
                        continue

                    # The property is the left side of an alias:
                    # { items: cartItems } -> items
                    name_match = re.match(
                        r"^([A-Za-z_$][\w$]*)"
                        r"(?:\s*:\s*[^,]+)?$",
                        entry,
                    )

                    if not name_match:
                        continue

                    property_name = name_match.group(1)

                    if property_name not in provided:
                        problems.setdefault(
                            consumer_path, []
                        ).append(
                            f"{hook_name}() reads '{property_name}', "
                            f"but {context_name} does not expose it. "
                            f"Available properties: "
                            f"{', '.join(sorted(provided)) or 'none'}. "
                            "Use an existing property or update the "
                            "context provider and consumer consistently."
                        )

            # Preserve existing CartContext-specific diagnostics.
            if context_name == "CartContext":
                if "items" in provided and re.search(
                    r"\bcartItems\b", consumer_text
                ):
                    problems.setdefault(
                        consumer_path, []
                    ).append(
                        "CartContext exposes 'items', not 'cartItems'. "
                        "Use `const { items: cartItems } = useCart()`."
                    )

                if "addToCart" in provided and re.search(
                    r"\baddItem\b", consumer_text
                ):
                    problems.setdefault(
                        consumer_path, []
                    ).append(
                        "CartContext exposes 'addToCart', not 'addItem'."
                    )

                if "totalAmount" in provided and re.search(
                    r"\btotal\b", consumer_text
                ):
                    problems.setdefault(
                        consumer_path, []
                    ).append(
                        "CartContext exposes 'totalAmount', not 'total'."
                    )

                if "removeFromCart" in provided and re.search(
                    r"\bremoveItem\b", consumer_text
                ):
                    problems.setdefault(
                        consumer_path, []
                    ).append(
                        "CartContext exposes 'removeFromCart', "
                        "not 'removeItem'."
                    )

    # Per-provider check (handles files that hold several providers and
    # hook names that do not match the file name).
    for path, issues in validate_context_properties(project_dir).items():
        for issue in issues:
            if issue not in problems.setdefault(path, []):
                problems[path].append(issue)

    return problems



def _validate_project(
    project_dir: Path,
    manifest: list[dict],
    plan: FrontendPlan | None = None,
    api_contract: list[dict] | None = None,
) -> dict[str, list[str]]:
    """Return {file_path: [problem, ...]} for every file that needs repair."""
    problems: dict[str, list[str]] = {}
    by_path = {f["path"]: f for f in manifest}

    def add(path: str, message: str) -> None:
        problems.setdefault(path, []).append(message)

    texts: dict[str, str] = {}
    actual: dict[str, tuple[bool, set[str]]] = {}

    for f in manifest:
        path = f["path"]
        file_path = project_dir / path
        if not file_path.is_file():
            add(path, "File was not generated.")
            continue
        text = file_path.read_text(encoding="utf-8")
        texts[path] = text
        actual[path] = _exports_of(text)

        stripped = text.rstrip()
        if not stripped:
            add(path, "File is empty.")
            continue
        if not re.search(r"\bexport\b", text):
            add(path, "File has no export statement (probably cut off).")
        if stripped[-1] not in "};)>`'\"" and not stripped.endswith("*/"):
            add(
                path,
                "File ends in the middle of a statement (output was cut off). "
                "Return a shorter, complete file.",
            )
        if _TS_RE.search(text) or re.search(r":\s*React\.(FC|ReactNode)\b", text):
            add(path, "Contains TypeScript syntax; use plain JavaScript.")
        if _ROUTER_RE.search(text):
            add(
                path,
                "Renders a Router. main.jsx already provides <BrowserRouter>; "
                "remove it and use <Routes>/<Route> only.",
            )

    # Each file must honor its own export contract.
    for f in manifest:
        path = f["path"]
        if path not in actual:
            continue
        has_default, named = actual[path]
        if f["default_export"] and not has_default:
            add(path, f"Must have `export default {f['default_export']}` but has no default export.")
        for n in f["named_exports"]:
            if n not in named:
                add(path, f"Must export `{n}` as a named export but does not.")

    # Imports must resolve and match what the target really exports.
    for path, text in texts.items():
        for m in _SIDE_IMPORT_RE.finditer(text):
            spec = m.group("spec")
            if spec.startswith("."):
                if _resolve(project_dir, path, spec) is None:
                    add(path, f"Imports '{spec}' but that file does not exist.")

        for m in _IMPORT_RE.finditer(text):
            spec = m.group("spec")
            default_name, named, namespace = _parse_clause(m.group("clause"))

            if not spec.startswith("."):
                root = _package_root(spec)
                if root not in PINNED_PACKAGES:
                    add(
                        path,
                        f"Imports package '{root}', which is not allowed. "
                        f"Allowed: {', '.join(sorted(PINNED_PACKAGES))}.",
                    )
                continue

            target = _resolve(project_dir, path, spec)
            if target is None:
                add(path, f"Imports '{spec}' but that file does not exist in the project.")
                continue
            if not target.endswith(SOURCE_EXTS) or target not in actual:
                continue
            if namespace:
                continue

            t_default, t_named = actual[target]
            contract = by_path.get(target)

            if default_name and not t_default:
                # Blame the target if its contract promised a default export
                # (it is already flagged). Otherwise the importer is wrong.
                if not (contract and contract["default_export"]):
                    add(
                        path,
                        f"Default-imports '{spec}', which has no default export. "
                        f"Use a named import: its exports are "
                        f"{sorted(t_named) or 'none'}.",
                    )
            for n in named:
                if n not in t_named:
                    promised = contract and n in contract["named_exports"]
                    if not promised:
                        add(
                            path,
                            f"Imports {{ {n} }} from '{spec}', which does not export "
                            f"it. Its exports: default="
                            f"{contract['default_export'] if contract else None}, "
                            f"named={sorted(t_named) or 'none'}.",
                        )
    # Semantic authentication checks: a successful JSX build is not enough.
    login_required, signup_required = (
        _authentication_requirements(plan, api_contract)
        if plan is not None else (False, False)
    )
    app_path = "src/App.jsx"
    app_text = texts.get(app_path, "")
    if login_required or signup_required:
        required_auth_pages = []
        if login_required:
            required_auth_pages.append(("Login", "src/pages/Login.jsx", "/login"))
        if signup_required:
            required_auth_pages.append(("Signup", "src/pages/Signup.jsx", "/signup"))

        for component, page_path, route in required_auth_pages:
            if page_path not in texts:
                add(app_path, f"Authentication page file is missing: {page_path}.")
                continue
            import_pattern = (
                r"import\s+" + re.escape(component)
                + r"""\s+from\s+['"]\.?/[^'"]*"""
                + re.escape(component) + r"""\.jsx['"]"""
            )
            if not re.search(import_pattern, app_text):
                add(app_path, f"App.jsx must import {component} from {page_path}.")
            route_tags = re.findall(r"<Route\b[^>]*>", app_text, flags=re.S)
            mapped = False
            for tag in route_tags:
                path_match = re.search(r"""\bpath\s*=\s*['"]([^'"]+)['"]""", tag)
                element_match = re.search(r"element\s*=\s*\{\s*<([A-Za-z_$][\w$]*)", tag)
                if path_match and element_match and path_match.group(1).rstrip("/") == route:
                    mapped = element_match.group(1) == component
                    break
            if not mapped:
                add(app_path, f"Route {route} must render <{component} />.")

        login_text = texts.get("src/pages/Login.jsx", "")
        signup_text = texts.get("src/pages/Signup.jsx", "")
        if login_required and login_text:
            if not re.search(r"""type\s*=\s*['"]email['"]|name\s*=\s*['"]email['"]""", login_text, re.I):
                add("src/pages/Login.jsx", "Login page must contain an email input.")
            if not re.search(r"""type\s*=\s*['"]password['"]|name\s*=\s*['"]password['"]""", login_text, re.I):
                add("src/pages/Login.jsx", "Login page must contain a password input.")
        if signup_required and signup_text:
            if not re.search(r"""type\s*=\s*['"]text['"]|name\s*=\s*['"]name['"]""", signup_text, re.I):
                add("src/pages/Signup.jsx", "Signup page must contain a name input.")
            if not re.search(r"""type\s*=\s*['"]email['"]|name\s*=\s*['"]email['"]""", signup_text, re.I):
                add("src/pages/Signup.jsx", "Signup page must contain an email input.")
            if not re.search(r"""type\s*=\s*['"]password['"]|name\s*=\s*['"]password['"]""", signup_text, re.I):
                add("src/pages/Signup.jsx", "Signup page must contain a password input.")

    context_problems = _validate_context_contracts(
        project_dir, manifest, texts
    )
    for path, issues in context_problems.items():
        problems.setdefault(path, []).extend(issues)
    for path, issues in validate_component_props(project_dir).items():
        for issue in issues:
            if issue not in problems.setdefault(path, []):
                problems[path].append(issue)
    return problems


# --------------------------------------------------------------------------
# Scaffold templates (deterministic)
# --------------------------------------------------------------------------
def _collect_dependencies(project_dir: Path, manifest: list[dict]) -> dict[str, str]:
    used = set(CORE_DEPENDENCIES)
    for f in manifest:
        text = (project_dir / f["path"]).read_text(encoding="utf-8")
        for m in _IMPORT_RE.finditer(text):
            spec = m.group("spec")
            if not spec.startswith("."):
                root = _package_root(spec)
                if root in PINNED_PACKAGES:
                    used.add(root)
    return {name: PINNED_PACKAGES[name] for name in sorted(used)}


def _package_json(plan: FrontendPlan, deps: dict[str, str]) -> str:
    data = {
        "name": _slugify(plan.project_name),
        "private": True,
        "version": "1.0.0",
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "vite build",
            "preview": "vite preview",
        },
        "dependencies": deps,
        "devDependencies": dict(DEV_DEPENDENCIES),
    }
    return json.dumps(data, indent=2) + "\n"


def _index_html(plan: FrontendPlan) -> str:
    ds = plan.design_system
    heading = _clean_font(ds.typography.get("heading"), "Poppins")
    body = _clean_font(ds.typography.get("body"), "Inter")
    families = "&".join(
        f"family={name.replace(' ', '+')}:wght@400;500;600;700"
        for name in dict.fromkeys([heading, body])
    )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="UTF-8" />\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        f"    <title>{html.escape(plan.business_name)}</title>\n"
        '    <link rel="preconnect" href="https://fonts.googleapis.com" />\n'
        '    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />\n'
        f'    <link href="https://fonts.googleapis.com/css2?{families}&display=swap" rel="stylesheet" />\n'
        "  </head>\n"
        "  <body>\n"
        '    <div id="root"></div>\n'
        '    <script type="module" src="/src/main.jsx"></script>\n'
        "  </body>\n"
        "</html>\n"
    )


VITE_CONFIG = """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
      '/uploads': { target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});
"""

POSTCSS_CONFIG = """export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
"""

MAIN_JSX = """import React from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App.jsx';
import './styles.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
"""

STYLES_CSS = """@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  body {
    font-family: theme('fontFamily.body');
    @apply bg-white text-gray-900 antialiased;
  }
  h1, h2, h3, h4 {
    font-family: theme('fontFamily.heading');
  }
}
"""


def _mix(hex_color: str, other: tuple[int, int, int], weight: float) -> str:
    """Blend a hex color toward `other`; weight = share of the original."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    rgb = [int(h[i : i + 2], 16) for i in (0, 2, 4)]
    mixed = [round(c * weight + o * (1 - weight)) for c, o in zip(rgb, other)]
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _tailwind_config(plan: FrontendPlan) -> str:
    colors = _theme_colors(plan)
    ds = plan.design_system
    heading = _clean_font(ds.typography.get("heading"), "Poppins")
    body = _clean_font(ds.typography.get("body"), "Inter")

    color_theme: dict[str, object] = {}
    for name, value in colors.items():
        color_theme[name] = {
            "DEFAULT": value,
            "light": _mix(value, (255, 255, 255), 0.15),
            "dark": _mix(value, (0, 0, 0), 0.8),
        }

    theme = {
        "colors": color_theme,
        "fontFamily": {
            "heading": [heading, "ui-sans-serif", "system-ui", "sans-serif"],
            "body": [body, "ui-sans-serif", "system-ui", "sans-serif"],
        },
    }
    return (
        "/** @type {import('tailwindcss').Config} */\n"
        "export default {\n"
        "  content: ['./index.html', './src/**/*.{js,jsx}'],\n"
        "  theme: {\n"
        "    extend: "
        + json.dumps(theme, indent=2).replace("\n", "\n    ")
        + ",\n  },\n"
        "  plugins: [],\n"
        "};\n"
    )


def _write_scaffold(
    project_dir: Path, plan: FrontendPlan, manifest: list[dict]
) -> list[str]:
    deps = _collect_dependencies(project_dir, manifest)
    files = {
        "package.json": _package_json(plan, deps),
        "index.html": _index_html(plan),
        "vite.config.js": VITE_CONFIG,
        "postcss.config.js": POSTCSS_CONFIG,
        "tailwind.config.js": _tailwind_config(plan),
        "src/main.jsx": build_main_jsx(project_dir),
        "src/styles.css": STYLES_CSS,
    }
    for rel, content in files.items():
        dest = project_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    return list(files)


# --------------------------------------------------------------------------
# Repair loops for build and browser failures
# --------------------------------------------------------------------------
def _trim_error(text: str, limit: int = 2500) -> str:
    """Keep the tail of a long log; the real error is usually at the end."""
    text = text.strip()
    return text if len(text) <= limit else "...\n" + text[-limit:]


def _plan_routes(plan: FrontendPlan, limit: int = 8) -> list[str]:
    """Static page routes (no :params or wildcards) for the browser test."""
    routes = ["/"]
    for page in getattr(plan, "pages", []) or []:
        route = str(getattr(page, "route", "") or "")
        if (
            route.startswith("/")
            and ":" not in route
            and "*" not in route
            and route not in routes
        ):
            routes.append(route)
    return routes[:limit]


async def _repair_files_from_error(
    project_dir: Path,
    plan: FrontendPlan,
    manifest: list[dict],
    api_contract: list[dict] | None,
    error_text: str,
    stage: str,
) -> list[str]:
    """Ask the code agent to fix the files named by an error. Returns them."""
    by_path = {f["path"]: f for f in manifest}
    targets = _find_error_files(error_text, manifest, project_dir)
    targets = targets[:MAX_REPAIR_TARGETS]
    if not targets:
        return []

    detail = _trim_error(error_text)
    for path in targets:
        file = project_dir / path
        current = file.read_text(encoding="utf-8") if file.is_file() else ""
        issues = [
            f"The {stage} failed. Fix the cause in THIS file and keep its "
            f"exports unchanged. Error output:\n{detail}"
        ]
        print(f"  repairing {path}")
        fixed = await _generate_file(
            plan,
            manifest,
            by_path[path],
            previous_code=current or "(file was missing)",
            issues=issues,
            api_contract=api_contract,
        )
        file.write_text(fixed.rstrip() + "\n", encoding="utf-8")

    # Providers may have changed; main.jsx is derived from them.
    (project_dir / "src" / "main.jsx").write_text(
        build_main_jsx(project_dir), encoding="utf-8"
    )
    return targets


async def _build_with_repair(
    project_dir: Path,
    plan: FrontendPlan,
    manifest: list[dict],
    api_contract: list[dict] | None,
) -> None:
    """Run the production build; send syntax/build errors to the fix agent."""
    for attempt in range(1, MAX_BUILD_REPAIR_ROUNDS + 2):
        try:
            _validate_frontend_build(project_dir)
            return
        except ValueError as exc:
            message = str(exc)
            if (
                attempt > MAX_BUILD_REPAIR_ROUNDS
                or "dependency installation failed" in message
                or "package.json" in message
            ):
                raise
            print(
                f"\nBuild failed (attempt {attempt}); "
                "sending the error to the fix agent..."
            )
            fixed = await _repair_files_from_error(
                project_dir, plan, manifest, api_contract, message,
                "production build",
            )
            if not fixed:
                raise


async def _runtime_with_repair(
    project_dir: Path,
    plan: FrontendPlan,
    manifest: list[dict],
    api_contract: list[dict] | None,
    routes: list[str],
) -> None:
    """Open every route in a real browser; repair files that crash."""
    for attempt in range(1, MAX_RUNTIME_REPAIR_ROUNDS + 2):
        try:
            _validate_frontend_runtime(project_dir, routes=routes)
            return
        except ValueError as exc:
            message = str(exc)
            if (
                attempt > MAX_RUNTIME_REPAIR_ROUNDS
                or "browser runtime validation failed" not in message
            ):
                raise
            print(
                f"\nBrowser check failed (attempt {attempt}); "
                "sending the error to the fix agent..."
            )
            fixed = await _repair_files_from_error(
                project_dir, plan, manifest, api_contract, message,
                "browser runtime check",
            )
            if not fixed:
                raise
            await _build_with_repair(project_dir, plan, manifest, api_contract)


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
async def generate_frontend(
    frontend_plan: FrontendPlan,
    output_dir: str | Path = "generated_projects",
    overwrite: bool = False,
    strict: bool = True,
    api_contract: list[dict] | None = None,
    validate_runtime: bool = True,
    resume: bool = False,
) -> Path:
    """
    Generate a project-specific frontend using the configured LLM.

    Args:
        frontend_plan: Validated output of create_frontend_plan().
        output_dir: Parent directory for generated applications.
        overwrite: Allow replacing existing generated files.
        strict: Raise if validation problems remain after the repair passes.
        api_contract: API endpoints the frontend must call (connected mode).
        validate_runtime: Run the browser smoke test. The frontend-first
            pipeline passes False because no backend exists yet and runs the
            same test later against the generated backend.
        resume: Continue an interrupted run: reuse the saved file manifest and
            skip every file that is already on disk, then validate/repair.

    Returns:
        Absolute path to the generated frontend project.
    """
    if not isinstance(frontend_plan, FrontendPlan):
        frontend_plan = FrontendPlan.model_validate(frontend_plan)
    if not frontend_plan.pages:
        raise ValueError("Frontend plan must contain at least one page.")

    root = Path(output_dir).resolve()
    project_dir = (root / _slugify(frontend_plan.project_name)).resolve()
    if project_dir == root or root not in project_dir.parents:
        raise ValueError("Invalid generated project directory.")
    if project_dir.exists() and any(project_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"Project directory already contains files: {project_dir}. "
            "Choose another output directory or pass overwrite=True."
        )
    project_dir.mkdir(parents=True, exist_ok=True)

    # The manifest (which files exist + their exports) is LLM-generated and differs on
    # every call, so it is saved. A resumed run MUST reuse it, otherwise the files
    # already on disk would not match the new contract.
    resume_cache = root / ".resume_manifest.json"
    manifest = None
    reuse = False
    if resume and resume_cache.is_file():
        try:
            manifest = json.loads(resume_cache.read_text(encoding="utf-8"))
            reuse = bool(manifest)
        except (OSError, json.JSONDecodeError):
            manifest = None
    if manifest is None:
        manifest = await _generate_manifest(frontend_plan, api_contract=api_contract)
        resume_cache.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    print(f"Planned {len(manifest)} source files." + (" (resuming)" if reuse else ""))

    # ---- 1. generate every source file --------------------------------
    for index, item in enumerate(manifest, start=1):
        destination = (project_dir / item["path"]).resolve()
        if project_dir not in destination.parents:
            raise ValueError(f"Generated path escapes project: {item['path']}")
        if reuse and destination.is_file() and destination.stat().st_size > 0:
            print(f"[{index}/{len(manifest)}] Reusing {item['path']}")
            continue
        print(f"[{index}/{len(manifest)}] Generating {item['path']}")
        content = await _generate_file(frontend_plan, manifest, item, api_contract=api_contract)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content.rstrip() + "\n", encoding="utf-8")

    # ---- 2. validate, then repair only the broken files ----------------
    by_path = {f["path"]: f for f in manifest}
    problems = _validate_project(project_dir, manifest, frontend_plan, api_contract)

    for repair_pass in range(1, MAX_REPAIR_PASSES + 1):
        if not problems:
            break
        print(
            f"\nValidation found problems in {len(problems)} file(s); "
            f"repair pass {repair_pass}/{MAX_REPAIR_PASSES}"
        )
        for path, issues in problems.items():
            print(f"  - {path}")
            for issue in issues:
                print(f"      {issue}")
            target = by_path[path]
            current = (project_dir / path).read_text(encoding="utf-8") if (
                project_dir / path
            ).is_file() else ""
            fixed = await _generate_file(
                frontend_plan,
                manifest,
                target,
                previous_code=current or "(file was missing)",
                issues=issues,
                api_contract=api_contract,
            )
            (project_dir / path).write_text(fixed.rstrip() + "\n", encoding="utf-8")
        problems = _validate_project(project_dir, manifest, frontend_plan, api_contract)

    
    if problems:
        summary = "; ".join(
            f"{path}: {len(issues)} issue(s)"
            for path, issues in problems.items()
        )
        if strict:
            raise ValueError(
                f"Frontend validation failed after {MAX_REPAIR_PASSES} "
                f"repair passes: {summary}"
            )
        # Not strict: keep going. The production build, the browser test and the
        # pipeline's validate -> debug loop get the chance to fix what is left.
        print(f"WARNING: continuing with unresolved problems: {summary}")

    # ---- 3. Generate deterministic scaffold --------------------------
    scaffold = _write_scaffold(project_dir, frontend_plan, manifest)

    # ---- 3b. Remove Navbar/Footer duplicated inside pages --------------
    from generation.layout_cleanup import remove_duplicate_layout

    for cleaned in remove_duplicate_layout(project_dir):
        print(f"Removed duplicate layout components from {cleaned}")

    # ---- 3c. Every context hook must have its provider mounted ---------
    wiring = validate_provider_wiring(project_dir)
    for wiring_attempt in range(1, 3):
        if not wiring:
            break
        diagnostic = "Context provider wiring is broken:\\n- " + "\\n- ".join(wiring)
        print(
            f"\\nProvider wiring validation failed "
            f"(repair {wiring_attempt}/2); sending diagnostics to the fix agent..."
        )
        repaired = await _repair_files_from_error(
            project_dir,
            frontend_plan,
            manifest,
            api_contract,
            diagnostic,
            "React context/provider wiring",
        )
        if not repaired:
            break
        (project_dir / "src" / "main.jsx").write_text(
            build_main_jsx(project_dir), encoding="utf-8"
        )
        wiring = validate_provider_wiring(project_dir)

    if wiring:
        if strict:
            raise ValueError(
                "Context provider wiring remains broken after 2 repair attempts:\\n- "
                + "\\n- ".join(wiring)
            )
        print("WARNING: context provider wiring problems:\\n- " + "\\n- ".join(wiring))
        problems = {**problems, "(provider wiring)": wiring}

    # ---- 4. Production build, with automatic repair of syntax errors ----
    await _build_with_repair(project_dir, frontend_plan, manifest, api_contract)

    # ---- 5. Open every page in a real browser, with automatic repair ----
    if validate_runtime:
        await _runtime_with_repair(
            project_dir,
            frontend_plan,
            manifest,
            api_contract,
            routes=_plan_routes(frontend_plan),
        )
    metadata = {
        "business_name": frontend_plan.business_name,
        "project_name": frontend_plan.project_name,
        "frontend_plan": frontend_plan.model_dump(),
        "generated_files": manifest,
        "scaffold_files": scaffold,
        "unresolved_problems": problems,
        "build_validation": "passed",
    }

    (project_dir / "agentforge-manifest.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    resume_cache.unlink(missing_ok=True)
    print(f"\nFrontend generated and build validated at: {project_dir}")
    return project_dir 