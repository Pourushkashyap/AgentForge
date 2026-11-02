"""Deterministic wiring of React Context providers (no LLM involved).

Why this exists
---------------
The LLM writes ``src/context/*.jsx`` (a ``XProvider`` plus a ``useX`` hook) and
``src/App.jsx``. It often forgets to mount one of the providers, so a page
that calls ``useGallery()`` crashes with

    Uncaught Error: useGallery must be used within a GalleryProvider

React unmounts the whole tree on that error, which is why the preview shows a
blank page while the home page (which does not use that hook) looks fine.

The old ``_validate_context_contracts`` only compared the *properties* a hook
destructures with the properties the provider exposes. It never checked that
the provider is actually rendered anywhere. This module does that check and
also fixes the problem by generating ``src/main.jsx`` itself:

* every provider found in ``src/context`` that is NOT already rendered by
  some other file (usually App.jsx) is mounted around ``<App />``;
* providers are ordered so a provider that calls another context's hook is
  placed inside that context's provider;
* the app is wrapped in an error boundary, so a render crash shows a readable
  message (and a ``data-agentforge-crash`` marker the browser test can detect)
  instead of a blank page.

Use it from code:

    from generation.provider_wiring import build_main_jsx, validate_provider_wiring

or repair an already generated project from the command line:

    python -m generation.provider_wiring path/to/generated/frontend
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCE_SUFFIXES = (".js", ".jsx")

_PROVIDER_DECL_RE = re.compile(
    r"export\s+(?P<default>default\s+)?(?:async\s+)?(?:function|const|class)\s+"
    r"(?P<name>[A-Z][\w$]*Provider)\b"
)
_HOOK_DECL_RE = re.compile(
    r"export\s+(?:function|const)\s+(?P<name>use[A-Z][\w$]*)\b"
)
_EXPORT_BLOCK_RE = re.compile(r"export\s*\{([^}]*)\}")


@dataclass
class ContextInfo:
    provider: str
    file: str                      # path relative to src/, posix style
    is_default_export: bool = False
    hooks: list[str] = field(default_factory=list)
    needs: set[str] = field(default_factory=set)   # hook names its body calls

    @property
    def stem(self) -> str:
        return self.provider[: -len("Provider")]

    @property
    def own_hook(self) -> str:
        return "use" + self.stem


def _src_files(project_dir: Path) -> dict[str, str]:
    """{path relative to src/ (posix): text} for every JS/JSX file."""
    src = Path(project_dir) / "src"
    files: dict[str, str] = {}
    if not src.is_dir():
        return files
    for path in src.rglob("*"):
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            files[path.relative_to(src).as_posix()] = path.read_text(
                encoding="utf-8", errors="ignore"
            )
    return files


def discover_contexts(project_dir: Path) -> list[ContextInfo]:
    """Find every ``XProvider`` exported from src/context."""
    files = _src_files(project_dir)
    found: list[ContextInfo] = []

    for rel in sorted(files):
        if not rel.startswith("context/"):
            continue
        text = files[rel]
        decls = list(_PROVIDER_DECL_RE.finditer(text))

        # ``export { AProvider, useA }`` style exports
        for block in _EXPORT_BLOCK_RE.findall(text):
            for part in block.split(","):
                name = re.split(r"\s+as\s+", part.strip())[-1].strip()
                if name.endswith("Provider") and name[:1].isupper():
                    if not any(d.group("name") == name for d in decls):
                        found.append(
                            ContextInfo(provider=name, file=rel)
                        )

        hooks = [m.group("name") for m in _HOOK_DECL_RE.finditer(text)]
        for block in _EXPORT_BLOCK_RE.findall(text):
            for part in block.split(","):
                name = re.split(r"\s+as\s+", part.strip())[-1].strip()
                if re.fullmatch(r"use[A-Z][\w$]*", name) and name not in hooks:
                    hooks.append(name)

        for i, decl in enumerate(decls):
            end = decls[i + 1].start() if i + 1 < len(decls) else len(text)
            body = text[decl.start():end]
            called = set(re.findall(r"\b(use[A-Z][\w$]*)\s*\(", body))
            found.append(
                ContextInfo(
                    provider=decl.group("name"),
                    file=rel,
                    is_default_export=bool(decl.group("default")),
                    hooks=hooks,
                    needs=called,
                )
            )

    # Hooks declared in a file that also had only block-exported providers.
    for info in found:
        if not info.hooks:
            text = files.get(info.file, "")
            info.hooks = [m.group("name") for m in _HOOK_DECL_RE.finditer(text)]
        if not info.needs:
            text = files.get(info.file, "")
            m = re.search(
                r"(?:function|const)\s+%s\b" % re.escape(info.provider), text
            )
            if m:
                tail = text[m.start():]
                nxt = re.search(r"\n(?:export\s+)?(?:function|const)\s+[A-Z]\w*Provider\b", tail[10:])
                body = tail[: nxt.start() + 10] if nxt else tail
                info.needs = set(re.findall(r"\b(use[A-Z][\w$]*)\s*\(", body))
    return found


def _rendered_providers(project_dir: Path, names: set[str]) -> dict[str, list[str]]:
    """{provider: [files that render <Provider ...>]} (the defining file excluded)."""
    files = _src_files(project_dir)
    result: dict[str, list[str]] = {n: [] for n in names}
    for rel, text in files.items():
        for name in names:
            if re.search(r"<\s*%s[\s/>]" % re.escape(name), text):
                # ``<XContext.Provider>`` inside the provider file is not a mount.
                if rel.startswith("context/") and re.search(
                    r"(?:function|const|class)\s+%s\b" % re.escape(name), text
                ):
                    continue
                result[name].append(rel)
    return result


def unmounted_providers(project_dir: Path) -> list[ContextInfo]:
    """Providers that no file renders."""
    contexts = discover_contexts(project_dir)
    rendered = _rendered_providers(project_dir, {c.provider for c in contexts})
    return [c for c in contexts if not rendered.get(c.provider)]


def _mounted_outside_main(project_dir: Path, names: set[str]) -> set[str]:
    rendered = _rendered_providers(project_dir, names)
    return {
        name
        for name, where in rendered.items()
        if any(w != "main.jsx" for w in where)
    }


def _order(providers: list[ContextInfo]) -> list[ContextInfo]:
    """Outermost first. A provider that calls another's hook goes inside it."""
    by_hook = {p.own_hook: p for p in providers}
    deps = {
        p.provider: {
            by_hook[h].provider
            for h in p.needs
            if h in by_hook and by_hook[h].provider != p.provider
        }
        for p in providers
    }
    ordered: list[ContextInfo] = []
    placed: set[str] = set()
    remaining = list(providers)
    while remaining:
        progressed = False
        for p in list(remaining):
            if deps[p.provider] <= placed:
                ordered.append(p)
                placed.add(p.provider)
                remaining.remove(p)
                progressed = True
        if not progressed:           # dependency cycle: keep original order
            ordered.extend(remaining)
            break
    return ordered


_MAIN_TEMPLATE = """import React from 'react';
import {{ createRoot }} from 'react-dom/client';
import {{ BrowserRouter }} from 'react-router-dom';
import App from './App.jsx';
{provider_imports}import './styles.css';

class AppErrorBoundary extends React.Component {{
  constructor(props) {{
    super(props);
    this.state = {{ error: null }};
  }}

  static getDerivedStateFromError(error) {{
    return {{ error }};
  }}

  componentDidCatch(error, info) {{
    console.error('[AgentForge] render crash:', error, info && info.componentStack);
  }}

  render() {{
    if (this.state.error) {{
      const message =
        (this.state.error && this.state.error.message) || String(this.state.error);
      return (
        <div
          data-agentforge-crash="true"
          role="alert"
          style={{{{ padding: '2rem', fontFamily: 'sans-serif' }}}}
        >
          <h1 style={{{{ fontSize: '1.25rem', fontWeight: 700 }}}}>This page crashed</h1>
          <pre style={{{{ whiteSpace: 'pre-wrap', marginTop: '1rem' }}}}>{{message}}</pre>
        </div>
      );
    }}
    return this.props.children;
  }}
}}

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <BrowserRouter>
{open_tags}        <App />
{close_tags}      </BrowserRouter>
    </AppErrorBoundary>
  </React.StrictMode>
);
"""


def build_main_jsx(project_dir: Path) -> str:
    """Return the text of src/main.jsx with every missing provider mounted."""
    project_dir = Path(project_dir)
    contexts = discover_contexts(project_dir)
    already = _mounted_outside_main(project_dir, {c.provider for c in contexts})
    to_mount = _order([c for c in contexts if c.provider not in already])

    imports: dict[str, list[str]] = {}
    defaults: dict[str, str] = {}
    for c in to_mount:
        if c.is_default_export:
            defaults[c.file] = c.provider
        else:
            imports.setdefault(c.file, []).append(c.provider)

    lines = []
    for file in sorted(set(imports) | set(defaults)):
        parts = []
        if file in defaults:
            parts.append(defaults[file])
        if file in imports:
            parts.append("{ " + ", ".join(imports[file]) + " }")
        lines.append(f"import {', '.join(parts)} from './{file}';\n")
    provider_imports = "".join(lines)

    open_tags = ""
    close_tags = ""
    for depth, c in enumerate(to_mount):
        pad = "        " + "  " * depth
        open_tags += f"{pad}<{c.provider}>\n"
    for depth in range(len(to_mount) - 1, -1, -1):
        pad = "        " + "  " * depth
        close_tags += f"{pad}</{to_mount[depth].provider}>\n"
    # indent <App /> to sit inside the innermost provider
    app_pad = "        " + "  " * len(to_mount)
    text = _MAIN_TEMPLATE.format(
        provider_imports=provider_imports,
        open_tags=open_tags,
        close_tags=close_tags,
    )
    return text.replace("        <App />\n", f"{app_pad}<App />\n", 1)


def validate_provider_wiring(project_dir: Path) -> list[str]:
    """Problems that would crash at runtime with 'must be used within ...'.

    Checked after main.jsx has been written: every context hook that a
    non-context file calls must have its provider rendered somewhere.
    """
    project_dir = Path(project_dir)
    files = _src_files(project_dir)
    contexts = discover_contexts(project_dir)
    rendered = _rendered_providers(project_dir, {c.provider for c in contexts})
    problems: list[str] = []

    for c in contexts:
        if rendered.get(c.provider):
            continue
        users = [
            rel
            for rel, text in files.items()
            if not rel.startswith("context/")
            and re.search(r"\b%s\s*\(" % re.escape(c.own_hook), text)
        ]
        if users:
            problems.append(
                f"{c.provider} (src/{c.file}) is never rendered, but "
                f"{c.own_hook}() is called in: {', '.join(sorted(users))}. "
                f"That crashes with '{c.own_hook} must be used within a "
                f"{c.provider}'."
            )

    # A hook called with no matching context file at all.
    known_hooks = {h for c in contexts for h in c.hooks}
    for rel, text in files.items():
        if rel.startswith("context/"):
            continue
        for m in re.finditer(
            r"\bconst\s*\{[^}]*\}\s*=\s*(use[A-Z][\w$]*)\s*\(\s*\)", text
        ):
            hook = m.group(1)
            if hook not in known_hooks and hook not in _REACT_AND_ROUTER_HOOKS:
                if not re.search(
                    r"(?:function|const)\s+%s\b" % re.escape(hook), text
                ):
                    problems.append(
                        f"src/{rel}: calls {hook}(), which no context file "
                        "or local file exports."
                    )
    return problems


_REACT_AND_ROUTER_HOOKS = {
    "useState", "useEffect", "useMemo", "useCallback", "useRef", "useReducer",
    "useContext", "useLayoutEffect", "useId", "useTransition",
    "useDeferredValue", "useNavigate", "useParams", "useLocation",
    "useSearchParams", "useOutletContext", "useMatch", "useInView",
}


# ---------------------------------------------------------------------------
# Property-level contract: what a hook reads vs. what its provider exposes
# ---------------------------------------------------------------------------
_VALUE_PATTERNS = (
    re.compile(r"value\s*=\s*\{\s*\{"),
    re.compile(r"\bconst\s+value\s*=\s*(?:useMemo\(\s*\(\)\s*=>\s*\(\s*)?\{"),
)


def _balanced(text: str, open_index: int) -> str | None:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i]
    return None


def _split_top_level(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _provided_keys(provider_body: str) -> tuple[set[str], bool] | None:
    """Keys of the object a provider passes as ``value``; None if unknown."""
    for pattern in _VALUE_PATTERNS:
        m = pattern.search(provider_body)
        if not m:
            continue
        inner = _balanced(provider_body, m.end() - 1)
        if inner is None:
            continue
        keys: set[str] = set()
        has_spread = False
        for part in _split_top_level(inner):
            if part.startswith("..."):
                has_spread = True
                continue
            km = re.match(r"^(?:async\s+)?([A-Za-z_$][\w$]*)\s*(?::|\(|$)", part)
            if km:
                keys.add(km.group(1))
        return keys, has_spread
    return None


def validate_context_properties(project_dir: Path) -> dict[str, list[str]]:
    """{ "src/<consumer>": [problem, ...] } for hook properties not provided.

    Works per provider, so a file such as StoreContext.jsx that holds several
    providers (ServiceProvider/useService, GalleryProvider/useGallery, ...) is
    checked correctly. The hook name comes from the provider name, not from
    the file name.
    """
    project_dir = Path(project_dir)
    files = _src_files(project_dir)
    problems: dict[str, list[str]] = {}

    for info in discover_contexts(project_dir):
        text = files.get(info.file, "")
        m = re.search(
            r"(?:function|const|class)\s+%s\b" % re.escape(info.provider), text
        )
        if not m:
            continue
        nxt = re.search(
            r"\n(?:export\s+(?:default\s+)?)?(?:function|const)\s+[A-Z]\w*Provider\b",
            text[m.end():],
        )
        body = text[m.start(): m.end() + nxt.start()] if nxt else text[m.start():]

        provided = _provided_keys(body)
        if provided is None:
            continue
        keys, has_spread = provided
        if has_spread or not keys:
            continue

        hook = info.own_hook
        pattern = re.compile(
            r"\bconst\s*\{([^}]*)\}\s*=\s*%s\s*\(\s*\)" % re.escape(hook), re.S
        )
        for rel, ctext in files.items():
            if rel.startswith("context/"):
                continue
            for hm in pattern.finditer(ctext):
                for entry in _split_top_level(hm.group(1)):
                    if entry.startswith("..."):
                        continue
                    nm = re.match(r"^([A-Za-z_$][\w$]*)", entry)
                    if nm and nm.group(1) not in keys:
                        problems.setdefault(f"src/{rel}", []).append(
                            f"{hook}() reads '{nm.group(1)}', but {info.provider} "
                            f"(src/{info.file}) does not provide it. It provides: "
                            f"{', '.join(sorted(keys))}. Use one of those names "
                            f"(e.g. `const {{ ... }} = {hook}()` with an existing "
                            f"property), or add '{nm.group(1)}' to the provider's "
                            "value object."
                        )
    return problems


def repair_project_main(project_dir: Path) -> list[str]:
    """Rewrite src/main.jsx of an existing project. Returns mounted providers."""
    project_dir = Path(project_dir)
    text = build_main_jsx(project_dir)
    (project_dir / "src").mkdir(parents=True, exist_ok=True)
    (project_dir / "src" / "main.jsx").write_text(text, encoding="utf-8")
    contexts = discover_contexts(project_dir)
    already = _mounted_outside_main(project_dir, {c.provider for c in contexts})
    return [c.provider for c in contexts if c.provider not in already]


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m generation.provider_wiring <frontend_dir>")
        raise SystemExit(2)
    target = Path(sys.argv[1]).resolve()
    mounted = repair_project_main(target)
    print(f"main.jsx rewritten. Providers mounted there: {mounted or 'none'}")
    leftovers = validate_provider_wiring(target)
    for problem in leftovers:
        print("STILL BROKEN:", problem)
    raise SystemExit(1 if leftovers else 0)
