"""Static data-flow checks for generated React projects.

The generator keeps producing three kinds of bugs that no syntax/build check catches:

1. *Call-signature mismatches* in the auth flow: ``register(form.name, form.email, ...)``
   against ``register({ name, email, password })``. The request body becomes ``{}`` and
   the backend answers 422, so users are created (or not) but can never log in.
2. *Links to pages that do not exist* (``<Link to="/job/5">`` with a route ``/jobs/:id``).
3. *Route params that are never read* (``useParams()`` returns ``id`` but the page reads
   ``jobId``), which leaves every detail / sub page empty.

Everything here is regex + a small bracket scanner (no JS parser is available), written
to be conservative: when it cannot resolve something it stays silent instead of guessing.
`fix_*` functions repair the mechanical cases without any LLM tokens.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from workflow.validators import make_issue

JS_SUFFIXES = {".jsx", ".js", ".tsx", ".ts"}
IGNORED_PARTS = {"node_modules", "dist", "build", ".git"}


# ---- tiny JS helpers -----------------------------------------------------------
def src_files(frontend_dir: Path) -> dict[str, Path]:
    src = Path(frontend_dir) / "src"
    if not src.is_dir():
        return {}
    return {
        p.relative_to(src).as_posix(): p
        for p in sorted(src.rglob("*"))
        if p.is_file() and p.suffix in JS_SUFFIXES
        and not any(part in IGNORED_PARTS for part in p.parts)
    }


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


_PAIRS = {"(": ")", "[": "]", "{": "}"}


def _skip_string(text: str, i: int) -> int:
    """text[i] is a quote; return the index just after the closing quote."""
    quote = text[i]
    i += 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if quote == "`" and ch == "$" and text[i + 1:i + 2] == "{":
            i = _match_close(text, i + 1) + 1
            continue
        if ch == quote:
            return i + 1
        i += 1
    return i


def _match_close(text: str, i: int) -> int:
    """text[i] is an opening bracket; return the index of its matching closer (or len-1)."""
    stack = [_PAIRS[text[i]]]
    i += 1
    while i < len(text) and stack:
        ch = text[i]
        if ch in "\"'`":
            i = _skip_string(text, i)
            continue
        if ch == "/" and text[i + 1:i + 2] == "/":
            i = text.find("\n", i)
            i = len(text) if i < 0 else i
            continue
        if ch == "/" and text[i + 1:i + 2] == "*":
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        if ch in _PAIRS:
            stack.append(_PAIRS[ch])
        elif ch == stack[-1]:
            stack.pop()
            if not stack:
                return i
        i += 1
    return len(text) - 1


def split_args(argtext: str) -> list[str]:
    """Split 'a, {b: c, d}, f(x, y)' on its top-level commas."""
    args, depth_start, i = [], 0, 0
    while i < len(argtext):
        ch = argtext[i]
        if ch in "\"'`":
            i = _skip_string(argtext, i)
            continue
        if ch in _PAIRS:
            i = _match_close(argtext, i) + 1
            continue
        if ch == ",":
            args.append(argtext[depth_start:i].strip())
            depth_start = i + 1
        i += 1
    last = argtext[depth_start:].strip()
    if last:
        args.append(last)
    return args


# ---- routes ------------------------------------------------------------------
ROUTE_OPEN = re.compile(r"<Route\b")


def _tag_end(text: str, i: int) -> int:
    """Index of the '>' that closes the tag starting at text[i] == '<'."""
    i += 1
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if ch == "{":
            i = _match_close(text, i) + 1
            continue
        if ch == ">":
            return i
        i += 1
    return len(text) - 1


def _join(parent: str, child: str) -> str:
    if child.startswith("/"):
        return child.rstrip("/") or "/"
    return ((parent.rstrip("/") + "/" + child.strip("/")) if child.strip("/") else parent) or "/"


def collect_routes(frontend_dir: Path) -> list[dict[str, Any]]:
    """Every <Route> with its full path, rendered component and params."""
    routes: list[dict[str, Any]] = []
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        if "<Route" not in text:
            continue
        stack: list[str] = []
        pos = 0
        events = sorted(
            [(m.start(), "open") for m in ROUTE_OPEN.finditer(text)]
            + [(m.start(), "close") for m in re.finditer(r"</Route>", text)])
        for start, kind in events:
            if start < pos:
                continue
            if kind == "close":
                if stack:
                    stack.pop()
                continue
            end = _tag_end(text, start)
            tag = text[start:end + 1]
            self_closing = tag.rstrip(">").rstrip().endswith("/")
            path_m = re.search(r"""\bpath\s*=\s*(?:["']([^"']*)["']|\{\s*["'`]([^"'`]*)["'`]\s*\})""", tag)
            is_index = bool(re.search(r"\bindex\b", tag))
            rel = (path_m.group(1) if path_m and path_m.group(1) is not None
                   else path_m.group(2) if path_m else ("" if is_index else None))
            parent = stack[-1] if stack else ""
            full = _join(parent, rel) if rel is not None else parent
            element = re.search(r"element\s*=\s*\{\s*<\s*([A-Za-z_$][\w$.]*)", tag)
            component = re.search(r"\bComponent\s*=\s*\{\s*([A-Za-z_$][\w$.]*)", tag)
            if rel is not None and (element or component or is_index):
                routes.append({
                    "path": full or "/", "file": label,
                    "component": (element or component).group(1) if (element or component) else None,
                    "params": re.findall(r":(\w+)", full),
                })
            if not self_closing:
                stack.append(full)
    return routes


def _route_regex(pattern: str) -> re.Pattern[str]:
    parts = []
    for seg in pattern.strip("/").split("/"):
        if seg == "*":
            parts.append(".*")
        elif seg.startswith(":"):
            parts.append(r"[^/]+")
        else:
            parts.append(re.escape(seg))
    return re.compile(r"^/" + "/".join(parts) + r"/?$", re.IGNORECASE)


def _target_to_path(raw: str) -> str | None:
    """A literal / template link target as a concrete-looking path, or None if dynamic."""
    raw = raw.strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    raw = re.sub(r"\$\{[^}]*\}", "x", raw)
    raw = raw.split("#")[0].split("?")[0]
    return raw or "/"


LINK_PATTERNS = (
    re.compile(r"""<(?:Link|NavLink|Navigate)\b[^>]*?\bto\s*=\s*"([^"]*)\""""),
    re.compile(r"""<(?:Link|NavLink|Navigate)\b[^>]*?\bto\s*=\s*'([^']*)'"""),
    re.compile(r"""<(?:Link|NavLink|Navigate)\b[^>]*?\bto\s*=\s*\{\s*[`"']([^`"']*)[`"']\s*\}"""),
    re.compile(r"""\bnavigate\s*\(\s*[`"']([^`"']*)[`"']"""),
)


def check_routes(frontend_dir: Path) -> list[dict[str, Any]]:
    """Links to missing pages and route params that are never read."""
    frontend_dir = Path(frontend_dir)
    routes = collect_routes(frontend_dir)
    if not routes:
        return []
    issues: list[dict[str, Any]] = []
    matchers = [(_route_regex(r["path"]), r) for r in routes]
    known = ", ".join(sorted({r["path"] for r in routes}))

    seen: set[tuple[str, str]] = set()
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        for pattern in LINK_PATTERNS:
            for m in pattern.finditer(text):
                target = _target_to_path(m.group(1))
                if target is None or (label, target) in seen:
                    continue
                seen.add((label, target))
                if any(rx.match(target) for rx, _ in matchers):
                    continue
                issues.append(make_issue(
                    "routes",
                    f"{label} links to '{target}' but no route matches it. Defined routes: {known}. "
                    "Point the link at an existing route.",
                    code="link_to_missing_route", file=f"src/{label}", auto_fixable=True))

    imports = _import_map(frontend_dir)
    for route in routes:
        comp = route["component"]
        if not comp or not route["params"]:
            continue
        target = imports.get((route["file"], comp.split(".")[0]))
        if not target:
            continue
        used = _useparams_names(read(src_files(frontend_dir)[target]))
        missing = [n for n in used if n not in route["params"]]
        if missing and route["params"]:
            issues.append(make_issue(
                "routes",
                f"src/{target} reads route param(s) {missing} but route '{route['path']}' only "
                f"defines {route['params']}, so the page never receives them.",
                code="route_param_mismatch", file=f"src/{target}", auto_fixable=True))
    return issues


_USEPARAMS = re.compile(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*useParams\s*\(\s*\)")


def _useparams_names(text: str) -> list[str]:
    names: list[str] = []
    for m in _USEPARAMS.finditer(text):
        for part in split_args(m.group(1)):
            key = part.split(":")[0].split("=")[0].strip()
            if key:
                names.append(key)
    return names


def _resolve_module(frontend_dir: Path, from_label: str, spec: str) -> str | None:
    if not spec.startswith("."):
        return None
    files = src_files(frontend_dir)
    base = (Path(from_label).parent / spec).as_posix()
    parts: list[str] = []
    for seg in base.split("/"):
        if seg == "..":
            if parts:
                parts.pop()
        elif seg not in ("", "."):
            parts.append(seg)
    base = "/".join(parts)
    for cand in (base, *(base + s for s in JS_SUFFIXES), *(base + "/index" + s for s in JS_SUFFIXES)):
        if cand in files:
            return cand
    return None


_IMPORT_STMT = re.compile(r"""import\s+([^;]*?)\s+from\s+["']([^"']+)["']""", re.S)


def _import_map(frontend_dir: Path) -> dict[tuple[str, str], str]:
    """(file, local name) -> resolved project file, for default and named imports."""
    result: dict[tuple[str, str], str] = {}
    for label, path in src_files(frontend_dir).items():
        for m in _IMPORT_STMT.finditer(read(path)):
            target = _resolve_module(frontend_dir, label, m.group(2))
            if not target:
                continue
            clause = m.group(1).strip()
            default = re.match(r"^([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
            if default:
                result[(label, default.group(1))] = target
            named = re.search(r"\{([^}]*)\}", clause)
            if named:
                for part in split_args(named.group(1)):
                    bits = [b.strip() for b in re.split(r"\s+as\s+", part)]
                    if bits and bits[0]:
                        result[(label, bits[-1])] = target
            ns = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
            if ns:
                result[(label, ns.group(1))] = target
    return result


def fix_route_params(frontend_dir: Path) -> list[dict[str, Any]]:
    """useParams() names that the route does not define -> alias the real param.

    ``const { jobId } = useParams()`` with route ``/jobs/:id`` becomes
    ``const { id: jobId } = useParams()`` (only when exactly one param is unmatched)."""
    frontend_dir = Path(frontend_dir)
    fixes: list[dict[str, Any]] = []
    imports = _import_map(frontend_dir)
    files = src_files(frontend_dir)
    for route in collect_routes(frontend_dir):
        comp = route["component"]
        if not comp or not route["params"]:
            continue
        target = imports.get((route["file"], comp.split(".")[0]))
        if not target:
            continue
        path = files[target]
        text = read(path)
        missing = [n for n in _useparams_names(text) if n not in route["params"]]
        unused = [p for p in route["params"] if p not in _useparams_names(text)]
        if len(missing) != 1 or len(unused) != 1:
            continue
        bad, good = missing[0], unused[0]

        def repl(m: re.Match[str]) -> str:
            parts = [p.strip() for p in split_args(m.group(1))]
            parts = [f"{good}: {bad}" if p.split(":")[0].split("=")[0].strip() == bad and ":" not in p
                     and "=" not in p else p for p in parts]
            return m.group(0).replace(m.group(1), " " + ", ".join(parts) + " ")

        new_text = _USEPARAMS.sub(repl, text)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            fixes.append({"file": f"src/{target}",
                          "action": f"Read route param '{good}' as '{bad}' (route {route['path']})"})
    return fixes


# ============================================================================
# auth call signatures
# ============================================================================
LOGIN_NAME = re.compile(r"^(?:handle)?(?:log_?in|sign_?in|authenticate)(?:user|account)?$", re.I)
REGISTER_NAME = re.compile(r"^(?:handle)?(?:register|sign_?up|create_?account|create_?user)(?:user|account)?$", re.I)


def auth_role(name: str) -> str | None:
    if LOGIN_NAME.match(name):
        return "login"
    if REGISTER_NAME.match(name):
        return "register"
    return None


def _param_shape(params: str, body: str) -> dict[str, Any]:
    """Describe a function's parameter list."""
    parts = split_args(params)
    if re.search(r"typeof\s+\w+\s*===?\s*['\"]object['\"]|\bargs?\.length\b|arguments", body[:600]):
        return {"kind": "flex", "names": []}
    if len(parts) == 1 and parts[0].startswith("{"):
        inner = parts[0][1:parts[0].rfind("}")]
        names = [p.split(":")[0].split("=")[0].strip() for p in split_args(inner)]
        return {"kind": "object", "names": [n for n in names if n]}
    names = [re.split(r"[=:]", p)[0].strip().lstrip(".") for p in parts]
    required = sum(1 for p in parts if "=" not in p and not p.startswith("..."))
    if len(parts) == 1:
        return {"kind": "single", "names": names, "required": required}
    return {"kind": "positional", "names": names, "required": required}


_DEF_PATTERNS = (
    re.compile(r"(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s+(\w+)\s*\("),
    re.compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?function\s*\w*\s*\("),
    re.compile(r"(?:export\s+)?(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?\("),
)


def find_definitions(text: str) -> dict[str, dict[str, Any]]:
    """name -> parameter shape, for functions declared in `text` (auth-related names only)."""
    result: dict[str, dict[str, Any]] = {}
    for rx in _DEF_PATTERNS:
        for m in rx.finditer(text):
            name = m.group(1)
            if auth_role(name) is None:
                continue
            open_idx = m.end() - 1
            close_idx = _match_close(text, open_idx)
            params = text[open_idx + 1:close_idx]
            body = text[close_idx + 1:close_idx + 700]
            result.setdefault(name, {**_param_shape(params, body), "file_text_pos": m.start()})
    return result


def _call_shape(args: list[str]) -> dict[str, Any]:
    if len(args) == 1 and args[0].startswith("{"):
        inner = args[0][1:args[0].rfind("}")]
        pairs: dict[str, str] = {}
        for part in split_args(inner):
            if part.startswith("..."):
                return {"kind": "spread", "args": args}
            if ":" in part:
                k, v = part.split(":", 1)
                pairs[k.strip().strip("\"'")] = v.strip()
            else:
                pairs[part.strip()] = part.strip()
        return {"kind": "object", "pairs": pairs, "args": args}
    if len(args) == 1:
        return {"kind": "single", "args": args}
    return {"kind": "positional", "args": args}


def _compatible(definition: dict[str, Any], call: dict[str, Any]) -> bool:
    d, c = definition["kind"], call["kind"]
    if d == "flex" or c == "spread" or not call["args"]:
        return d == "flex" or c == "spread" and True
    if d == "object":
        return c in ("object", "single")
    if d == "single":
        return c in ("object", "single")
    # positional definition
    if c == "positional":
        return definition["required"] <= len(call["args"]) <= max(len(definition["names"]), 1)
    return False


_USE_DESTRUCT = re.compile(r"(?:const|let|var)\s*\{([^}]*)\}\s*=\s*(use\w+)\s*\(\s*(\w*)\s*\)")
_USE_ASSIGN = re.compile(r"(?:const|let|var)\s+(\w+)\s*=\s*(use\w+)\s*\(\s*(\w*)\s*\)")


def _hook_args(hook: str, arg: str) -> tuple[str, str | None]:
    """useContext(AuthContext) -> ('useContext', 'AuthContext'); useAuth() -> ('useAuth', None)."""
    return (hook, arg) if hook == "useContext" and arg else (hook, None)


def _hook_value_map(frontend_dir: Path, hook: str, context: str | None = None) -> dict[str, tuple[str, str]]:
    """key exposed by a context hook (or by useContext(Ctx)) -> (file label, handler name).

    The hook and its provider may live in different files (context/useAuth.js and
    context/AuthContext.jsx), so the provider is searched in the whole project."""
    texts = {label: read(path) for label, path in src_files(frontend_dir).items()}
    context_name = context
    if context_name is None:
        for text in texts.values():
            if re.search(rf"(?:function\s+{hook}\b|(?:const|let|var)\s+{hook}\s*=)", text):
                ctx = re.search(r"useContext\s*\(\s*(\w+)\s*\)", text)
                if ctx:
                    context_name = ctx.group(1)
                    break
    if not context_name:
        return {}
    for label, text in texts.items():
        m = re.search(rf"<{context_name}\.Provider\s+value\s*=\s*\{{", text)
        if not m:
            continue
        span = _value_object_span(text, m)
        if span is None:
            return {}
        value = text[span[0]:span[1]]
        mapping: dict[str, tuple[str, str]] = {}
        for part in split_args(value):
            if part.startswith("..."):
                continue
            key, _, expr = part.partition(":")
            key = key.strip()
            expr = expr.strip() or key
            if re.fullmatch(r"[\w$]+", expr):
                mapping[key] = (label, expr)
        return mapping
    return {}


def _value_object_span(text: str, provider_match: re.Match[str]) -> tuple[int, int] | None:
    """(start, end) of the object body passed as value={...}; follows `value={value}`."""
    brace = text.index("{", provider_match.end() - 1)
    inner = text[brace + 1:_match_close(text, brace)]
    stripped = inner.strip()
    if re.fullmatch(r"[\w$]+", stripped):
        decl = re.search(rf"(?:const|let|var)\s+{re.escape(stripped)}\s*=\s*\{{", text)
        if not decl:
            return None
        obrace = text.index("{", decl.end() - 1)
        return obrace + 1, _match_close(text, obrace)
    if stripped.startswith("{"):
        obrace = brace + 1 + inner.index("{")
        return obrace + 1, _match_close(text, obrace)
    return None


def _import_origins(frontend_dir: Path, label: str, text: str) -> dict[str, tuple[str, str]]:
    """local name -> (file label, exported name) for named imports; namespace -> (file, '*')."""
    out: dict[str, tuple[str, str]] = {}
    for m in _IMPORT_STMT.finditer(text):
        target = _resolve_module(frontend_dir, label, m.group(2))
        if not target:
            continue
        clause = m.group(1)
        named = re.search(r"\{([^}]*)\}", clause)
        if named:
            for part in split_args(named.group(1)):
                bits = [b.strip() for b in re.split(r"\s+as\s+", part)]
                if bits and bits[0]:
                    out[bits[-1]] = (target, bits[0])
        ns = re.search(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
        if ns:
            out[ns.group(1)] = (target, "*")
        default = re.match(r"^([A-Za-z_$][\w$]*)\s*(?:,|$)", clause.strip())
        if default:
            out[default.group(1)] = (target, "default")
    return out


def _resolve_definition(
    frontend_dir: Path, label: str, text: str, callee: str, member_of: str | None,
    defs_cache: dict[str, dict[str, dict[str, Any]]],
) -> tuple[str, str, dict[str, Any]] | None:
    """(file, name, shape) of the function a call resolves to, or None when unsure."""
    files = src_files(frontend_dir)

    def defs(file: str) -> dict[str, dict[str, Any]]:
        if file not in defs_cache:
            defs_cache[file] = find_definitions(read(files[file]))
        return defs_cache[file]

    origins = _import_origins(frontend_dir, label, text)
    hook_destruct: dict[str, tuple[str, str]] = {}
    for m in _USE_DESTRUCT.finditer(text):
        for part in split_args(m.group(1)):
            key, _, local = part.partition(":")
            hook_destruct[(local.strip() or key.strip())] = (_hook_args(m.group(2), m.group(3)), key.strip())
    hook_objects = {m.group(1): _hook_args(m.group(2), m.group(3)) for m in _USE_ASSIGN.finditer(text)}

    if member_of is not None:
        if member_of in origins:
            file, exported = origins[member_of]
            if exported in ("*", "default") and callee in defs(file):
                return file, callee, defs(file)[callee]
        if member_of in hook_objects:
            hmap = _hook_value_map(frontend_dir, *hook_objects[member_of])
            if callee in hmap and hmap[callee][1] in defs(hmap[callee][0]):
                file, handler = hmap[callee]
                return file, handler, defs(file)[handler]
        return None

    if callee in hook_destruct:
        hook, key = hook_destruct[callee]
        hmap = _hook_value_map(frontend_dir, *hook)
        if key in hmap and hmap[key][1] in defs(hmap[key][0]):
            file, handler = hmap[key]
            return file, handler, defs(file)[handler]
        return None
    if callee in origins:
        file, exported = origins[callee]
        if exported in defs(file):
            return file, exported, defs(file)[exported]
        return None
    local = find_definitions(text)
    if callee in local:
        return label, callee, local[callee]
    return None


_CALL = re.compile(r"(?<![\w$.])(?:(\w+)\.)?([A-Za-z_$][\w$]*)\s*\(")


def _original_name(frontend_dir: Path, label: str, text: str, member: str | None, callee: str) -> str:
    """The name a call had where it was declared: `login as apiLogin` -> login."""
    if member is not None:
        return callee
    origins = _import_origins(frontend_dir, label, text)
    if callee in origins and origins[callee][1] not in ("*", "default"):
        return origins[callee][1]
    for m in _USE_DESTRUCT.finditer(text):
        for part in split_args(m.group(1)):
            key, _, local = part.partition(":")
            if (local.strip() or key.strip()) == callee:
                return key.strip()
    return callee


CREDENTIAL_PARAMS = {"email", "password", "name", "username", "pass", "pwd"}
RESULT_ARGS = {"user", "token", "response", "res", "data", "result", "payload"}


def _semantic_mismatch(shape: dict[str, Any], call: dict[str, Any]) -> bool:
    """login(user, token) against login(email, password): same arity, wrong things."""
    if shape["kind"] not in ("positional", "object") or call["kind"] != "positional":
        return False
    for param, arg in zip(shape["names"], call["args"]):
        if param.lower() in CREDENTIAL_PARAMS and arg.strip() in RESULT_ARGS:
            return True
    return False


def find_auth_calls(frontend_dir: Path) -> list[dict[str, Any]]:
    """Every resolvable login/register call, with its definition and whether they agree."""
    frontend_dir = Path(frontend_dir)
    found: list[dict[str, Any]] = []
    defs_cache: dict[str, dict[str, dict[str, Any]]] = {}
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        if not re.search(r"login|signin|register|signup|authenticate|createaccount|createuser", text, re.I):
            continue
        for m in _CALL.finditer(text):
            member, callee = m.group(1), m.group(2)
            original = _original_name(frontend_dir, label, text, member, callee)
            role = auth_role(original)
            if role is None:
                continue
            before = text[max(0, m.start() - 25):m.start()]
            if re.search(r"(?:function|const|let|var)\s*$", before):  # a definition, not a call
                continue
            open_idx = m.end() - 1
            close_idx = _match_close(text, open_idx)
            args = split_args(text[open_idx + 1:close_idx])
            resolved = _resolve_definition(frontend_dir, label, text, callee, member, defs_cache)
            if resolved is None:
                continue
            def_file, def_name, shape = resolved
            call = _call_shape(args)
            semantic = _semantic_mismatch(shape, call)
            found.append({
                "file": label, "role": role, "callee": (member + "." if member else "") + callee,
                "args": args, "call": call, "def_file": def_file, "def_name": def_name,
                "shape": shape, "span": (open_idx + 1, close_idx),
                "semantic": semantic, "ok": _compatible(shape, call) and not semantic,
            })
    return found


def _describe_shape(name: str, shape: dict[str, Any]) -> str:
    if shape["kind"] == "object":
        return f"{name}({{ {', '.join(shape['names'])} }}) - one object"
    return f"{name}({', '.join(shape['names'])}) - {len(shape['names'])} positional argument(s)"


def check_auth_calls(frontend_dir: Path) -> list[dict[str, Any]]:
    issues = []
    for call in find_auth_calls(frontend_dir):
        if call["ok"]:
            continue
        where = f"src/{call['file']} calls {call['callee']}({', '.join(call['args'])})"
        defined = f"src/{call['def_file']} defines {_describe_shape(call['def_name'], call['shape'])}"
        if call["semantic"]:
            message = (
                f"{where} but {defined}. The page passes the RESULT of an earlier API call "
                f"into a function that performs the login/registration itself. Call "
                f"{call['callee']} exactly once with what the user typed "
                f"({_example_call(call)}), let it call the API, and only navigate afterwards. "
                f"Do not call the API separately in the page. Also check src/{call['def_file']}.")
        else:
            message = (
                f"{where} but {defined}. The values the user typed are lost (the request body "
                "becomes {} and the backend answers 422). Call it with the arguments it "
                "actually expects.")
        issues.append(make_issue(
            "authentication", message, code="auth_call_mismatch",
            file=f"src/{call['file']}", auto_fixable=True))
    return issues


def _example_call(call: dict[str, Any]) -> str:
    names = call["shape"]["names"]
    if call["shape"]["kind"] == "object":
        return f"{call['callee']}({{ {', '.join(names)} }})"
    return f"{call['callee']}({', '.join(names)})"


# ---- deterministic repair of call sites ------------------------------------
_KEY = re.compile(r"(?:^|\.)([A-Za-z_$][\w$]*)(?:\.trim\(\))?$")


def _arg_key(arg: str) -> str | None:
    """'form.email' / 'email.trim()' / 'email' -> 'email'."""
    m = _KEY.search(arg.strip())
    return m.group(1) if m else None


def _rewrite_args(call: dict[str, Any]) -> str | None:
    """The argument text that matches the definition, or None if it is not mechanical."""
    shape, kind, args = call["shape"], call["call"]["kind"], call["args"]
    names = shape["names"]
    if call["semantic"] or not names:
        return None
    if shape["kind"] in ("object", "single"):
        if kind == "positional" and len(args) >= len(names) and shape["kind"] == "object":
            pairs = [(n, a) for n, a in zip(names, args)]
        else:
            return None
        return "{ " + ", ".join(n if n == a else f"{n}: {a}" for n, a in pairs) + " }"
    if shape["kind"] == "positional":
        if kind == "object":
            pairs = call["call"]["pairs"]
            if not all(n in pairs for n in names[:shape["required"]]):
                return None
            return ", ".join(pairs[n] for n in names if n in pairs)
        if kind == "single" and len(names) >= 2 and re.fullmatch(r"[A-Za-z_$][\w$.]*", args[0]):
            return ", ".join(f"{args[0]}.{n}" for n in names)
    return None


def fix_auth_calls(frontend_dir: Path) -> list[dict[str, Any]]:
    """Rewrite mismatching login/register calls so they match their definition."""
    frontend_dir = Path(frontend_dir)
    files = src_files(frontend_dir)
    fixes: list[dict[str, Any]] = []
    for _ in range(25):
        target = None
        for call in find_auth_calls(frontend_dir):
            if not call["ok"]:
                new_args = _rewrite_args(call)
                if new_args is not None:
                    target = (call, new_args)
                    break
        if target is None:
            break
        call, new_args = target
        path = files[call["file"]]
        text = read(path)
        start, end = call["span"]
        path.write_text(text[:start] + new_args + text[end:], encoding="utf-8")
        fixes.append({"file": f"src/{call['file']}",
                      "action": f"{call['callee']}({', '.join(call['args'])}) -> "
                                f"{call['callee']}({new_args})"})
    return fixes


# ============================================================================
# auth request / response fields vs the API contract
# ============================================================================
AUTH_RESPONSE_KEYS = {"user", "token", "access_token", "accessToken", "jwt", "id", "name", "email", "role"}
_AUTH_FETCH = re.compile(r"""["'`]([^"'`]*/auth/(login|register))["'`]""")


def _stringify_keys(window: str) -> list[str] | None:
    m = re.search(r"JSON\.stringify\s*\(\s*\{", window)
    if not m:
        return None
    brace = window.index("{", m.end() - 1)
    inner = window[brace + 1:_match_close(window, brace)]
    keys = []
    for part in split_args(inner):
        if part.startswith("..."):
            return None  # spread: cannot know
        keys.append(part.split(":")[0].strip().strip("\"'"))
    return [k for k in keys if k]


def _response_reads(window: str) -> set[str]:
    names = set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*await\s+(?:response|res|\w*[Rr]esponse\w*)\s*\.json\s*\(", window))
    names |= set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*await\s+(?:handleResponse|parseResponse|handle\w*|parse\w*)\s*\(", window))
    reads: set[str] = set()
    for name in names:
        reads |= set(re.findall(rf"\b{re.escape(name)}\.(\w+)", window))
        for m in re.finditer(rf"(?:const|let|var)\s*\{{([^}}]*)\}}\s*=\s*{re.escape(name)}\b", window):
            reads |= {p.split(":")[0].strip() for p in split_args(m.group(1))}
    return reads


def backend_auth_shapes(backend_dir: Path) -> dict[str, dict[str, Any]]:
    """What the backend REALLY accepts/returns for /auth/login and /auth/register."""
    shapes: dict[str, dict[str, Any]] = {}
    for path in sorted(Path(backend_dir).rglob("*.py")):
        if any(part in {"venv", ".venv", "__pycache__", "site-packages"} for part in path.parts):
            continue
        text = read(path)
        for m in re.finditer(r"""@\w+\.post\(\s*["'][^"']*/auth/(login|register)["'][^)]*\)\s*\n\s*(?:async\s+)?def\s+\w+\s*\(([^)]*)\)[^:]*:""", text):
            kind = m.group(1)
            nxt = re.search(r"\n@\w+\.", text[m.end():])
            body = text[m.end(): m.end() + (nxt.start() if nxt else len(text))]
            returned: set[str] = set()
            for r in re.finditer(r"return\s*\{", body):
                brace = body.index("{", r.end() - 1)
                inner = body[brace + 1:_match_close(body, brace)]
                for part in split_args(inner):
                    if part.startswith("**"):
                        returned = set()
                        break
                    key = part.split(":")[0].strip().strip("\"'")
                    if key:
                        returned.add(key)
            request = None
            model = re.search(r":\s*(\w+)\s*(?:,|$)", m.group(2).strip())
            if model:
                cls = re.search(rf"class\s+{model.group(1)}\b[^:]*:\n((?:[ \t]+.*\n?)+)", text)
                if cls:
                    request = []
                    for line in cls.group(1).splitlines():
                        f = re.match(r"\s+(\w+)\s*:\s*[^=\n]+?(?:=\s*(.*))?$", line)
                        if f and not f.group(1).startswith("model_"):
                            default = (f.group(2) or "").strip()
                            if not default or (default.startswith("Field(") and "default" not in default):
                                request.append(f.group(1))
            shapes[kind] = {"response": returned or None, "request": request}
    return shapes


def check_auth_payloads(frontend_dir: Path, backend_dir: Path | None,
                        endpoints: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The auth fetch must send every field the backend requires and read only fields it returns.

    The truth is the generated backend code; the planned contract is only a fallback because it
    can be stale (the backend may return access_token while the plan says token)."""
    issues: list[dict[str, Any]] = []
    shapes = backend_auth_shapes(Path(backend_dir)) if backend_dir else {}
    contract = {
        str(e.get("path", "")).rstrip("/").lower().rsplit("/auth/", 1)[-1]: e
        for e in (endpoints or []) if isinstance(e, dict) and "/auth/" in str(e.get("path", "")).lower()
    }
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        for m in _AUTH_FETCH.finditer(text):
            kind = m.group(2)
            shape = shapes.get(kind, {})
            required = shape.get("request") or [
                f for f in (contract.get(kind) or {}).get("request_fields") or [] if isinstance(f, str)]
            response = sorted(shape["response"]) if shape.get("response") else (
                [] if shapes.get(kind) else
                [f for f in (contract.get(kind) or {}).get("response_fields") or [] if isinstance(f, str)])
            if not required and not response:
                continue
            nxt = re.search(r"\n(?:export\s+)?(?:async\s+)?(?:function|const)\s+\w+", text[m.end():])
            window = text[max(0, m.start() - 200): m.end() + (nxt.start() if nxt else 1500)]
            sent = _stringify_keys(window)
            if sent is not None and required:
                missing = [f for f in required if f not in sent]
                if missing:
                    issues.append(make_issue(
                        "authentication",
                        f"src/{label}: the {kind} request sends {sent} but the backend requires "
                        f"{required} (missing {missing}). Send exactly the fields the backend "
                        "defines, with the values the user typed.",
                        code="auth_payload_mismatch", file=f"src/{label}", auto_fixable=True))
            if response:
                wrong = sorted(k for k in _response_reads(window)
                               if k in AUTH_RESPONSE_KEYS and k not in response)
                if wrong:
                    issues.append(make_issue(
                        "authentication",
                        f"src/{label}: the {kind} response is read as {wrong} but the backend "
                        f"returns {response}. Read only the fields the backend returns.",
                        code="auth_response_mismatch", file=f"src/{label}", auto_fixable=True))
    return issues


_FLAT_USER_KEYS = ("id", "name", "email", "role")
_TOKEN_ALIASES = ("token", "jwt", "accessToken")


def fix_auth_response_reads(frontend_dir: Path, backend_dir: Path) -> list[dict[str, Any]]:
    """The login response is `{access_token, token_type, expires_in, user}`.

    Frontends written against the planner's invented shape read ``data.token`` (always
    undefined) or flat ``data.id / data.email``. Rewrite those reads inside the login
    function only, to the fields the backend really returns."""
    fixes: list[dict[str, Any]] = []
    shape = backend_auth_shapes(Path(backend_dir)).get("login") or {}
    response = shape.get("response") or set()
    if not response:
        return fixes
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        new_text, offset = text, 0
        for m in _AUTH_FETCH.finditer(text):
            if m.group(2) != "login":
                continue
            nxt = re.search(r"\n(?:export\s+)?(?:async\s+)?(?:function|const)\s+\w+", text[m.end():])
            start, end = m.end(), m.end() + (nxt.start() if nxt else 1500)
            window = text[start:end]
            names = set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*await\s+[\w.]*(?:json|handleResponse|parse\w*|handle\w*)\s*\(", window))
            names |= set(re.findall(r"(?:const|let|var)\s+(\w+)\s*=\s*await\s+(?:response|res)\.json\s*\(", window))
            if not names:
                continue
            fixed = window
            for var in names:
                for key in _TOKEN_ALIASES:
                    if key not in response and "access_token" in response:
                        fixed = re.sub(rf"\b{re.escape(var)}\.{key}\b", f"{var}.access_token", fixed)
                if "user" in response:
                    for key in _FLAT_USER_KEYS:
                        if key not in response:
                            fixed = re.sub(rf"\b{re.escape(var)}\.{key}\b(?!\s*=[^=])", f"{var}.user?.{key}", fixed)
            if fixed != window:
                new_text = new_text[:start + offset] + fixed + new_text[end + offset:]
                offset += len(fixed) - len(window)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            fixes.append({"file": f"src/{label}",
                          "action": "Read the login response as { access_token, user } like the backend returns it"})
    return fixes


# ---- second hop: code that consumes the login function's result -------------------------
def _def_return_keys(text: str, pos: int, backend_response: set[str]) -> set[str] | None:
    """Keys a login wrapper returns: its literal's keys, or the raw backend response."""
    nxt = re.search(r"\n(?:export\s+)?(?:async\s+)?(?:function|const)\s+\w+", text[pos + 10:])
    body = text[pos: pos + 10 + (nxt.start() if nxt else len(text))]
    keys: set[str] | None = None
    for r in re.finditer(r"\breturn\s+", body):
        rest = body[r.end():]
        if rest.startswith("{"):
            ks: set[str] = set()
            for part in split_args(rest[1:_match_close(rest, 0)]):
                if part.startswith("..."):
                    return None
                ks.add(part.split(":")[0].strip().strip("\"'"))
            keys = ks
        elif re.match(r"(?:await\s+)?(?:[\w.]*json\s*\(|handleResponse\s*\(|parse\w*\s*\(|data\b|result\b|json\b|payload\b)", rest):
            keys = set(backend_response)
    return keys


def login_consumers(frontend_dir: Path, backend_dir: Path) -> list[dict[str, Any]]:
    """Places that call the login API wrapper and read fields off its result."""
    shape = backend_auth_shapes(Path(backend_dir)).get("login") or {}
    backend_response = set(shape.get("response") or [])
    if not backend_response:
        return []
    files = src_files(frontend_dir)
    found: list[dict[str, Any]] = []
    for call in find_auth_calls(frontend_dir):
        if call["role"] != "login":
            continue
        def_text = read(files[call["def_file"]])
        if "/auth/login" not in def_text and "/users/login" not in def_text:
            continue  # not the HTTP wrapper itself
        pos = call["shape"].get("file_text_pos")
        if pos is None:
            continue
        returned = _def_return_keys(def_text, pos, backend_response)
        if returned is None:
            continue
        text = read(files[call["file"]])
        start = call["span"][0]
        line_start = text.rfind("\n", 0, start) + 1
        decl = re.match(r"\s*(?:const|let|var)\s+(\w+)\s*=\s*await\s+", text[line_start:start])
        if not decl:
            continue
        var = decl.group(1)
        end = call["span"][1] + 900
        window = text[call["span"][1]:end]
        stop = re.search(r"\n\s*(?:const|let|var)\s+handle\w+\s*=|\n\s*(?:export\s+)?(?:async\s+)?function\s+\w+", window)
        window = window[:stop.start()] if stop else window
        reads = set(re.findall(rf"\b{re.escape(var)}\.(\w+)", window))
        reads |= {p.split(":")[0].strip() for m in re.finditer(
            rf"(?:const|let|var)\s*\{{([^}}]*)\}}\s*=\s*{re.escape(var)}\b", window) for p in split_args(m.group(1))}
        wrong = sorted(k for k in reads if k in AUTH_RESPONSE_KEYS and k not in returned)
        if wrong:
            found.append({"file": call["file"], "var": var, "wrong": wrong, "returned": sorted(returned),
                          "def_file": call["def_file"], "raw": returned == backend_response,
                          "span": (call["span"][1], call["span"][1] + len(window))})
    return found


def check_login_consumers(frontend_dir: Path, backend_dir: Path) -> list[dict[str, Any]]:
    return [make_issue(
        "authentication",
        f"src/{c['file']} reads {c['wrong']} from the result of the login call, but "
        f"src/{c['def_file']} returns only {c['returned']}, so these values are undefined "
        "(the token is never stored and the user is never set). Read the fields that are "
        "really returned.",
        code="auth_response_mismatch", file=f"src/{c['file']}", auto_fixable=True)
        for c in login_consumers(frontend_dir, backend_dir)]


def fix_login_consumers(frontend_dir: Path, backend_dir: Path) -> list[dict[str, Any]]:
    """Rewrite reads on a RAW login response: token -> access_token, flat id/email -> user.*"""
    fixes: list[dict[str, Any]] = []
    files = src_files(frontend_dir)
    for c in login_consumers(frontend_dir, backend_dir):
        if not c["raw"]:
            continue  # the wrapper builds its own object; its own reads are fixed elsewhere
        path = files[c["file"]]
        text = read(path)
        start, end = c["span"]
        window, var = text[start:end], c["var"]
        returned = set(c["returned"])
        for key in _TOKEN_ALIASES:
            if key not in returned and "access_token" in returned:
                window = re.sub(rf"\b{re.escape(var)}\.{key}\b", f"{var}.access_token", window)
        if "user" in returned:
            for key in _FLAT_USER_KEYS:
                if key not in returned:
                    window = re.sub(rf"\b{re.escape(var)}\.{key}\b", f"{var}.user?.{key}", window)
        if window != text[start:end]:
            path.write_text(text[:start] + window + text[end:], encoding="utf-8")
            fixes.append({"file": f"src/{c['file']}",
                          "action": "Read the login result as { access_token, user } like the backend returns it"})
    return fixes


# ============================================================================
# aggregators used by the pipeline (validate / debug) and by the chat (revise)
# ============================================================================
FLOW_CODES = {
    "auth_call_mismatch", "auth_response_mismatch", "auth_payload_mismatch",
    "route_param_mismatch", "link_to_missing_route", "event_passed_to_state",
    "context_property_missing", "import_member_missing",
}
# heuristic findings that must never stop a build that is otherwise fine
SOFT_CODES = FLOW_CODES | {"unmounted_provider"}


def all_flow_issues(frontend_dir: Path, backend_dir: Path | None,
                    endpoints: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Every data-flow problem we can detect. Never raises: a bug in a check must not break a build."""
    issues: list[dict[str, Any]] = []
    checks = [
        lambda: check_auth_calls(frontend_dir),
        lambda: check_routes(frontend_dir),
        lambda: check_event_setters(frontend_dir),
        lambda: check_context_properties(frontend_dir),
        lambda: check_module_members(frontend_dir),
        lambda: check_auth_payloads(frontend_dir, backend_dir, endpoints) if backend_dir else [],
        lambda: check_login_consumers(frontend_dir, backend_dir) if backend_dir else [],
    ]
    for check in checks:
        try:
            issues += check()
        except Exception:
            continue
    seen: set[tuple[str, str, str]] = set()
    unique = []
    for issue in issues:
        key = (issue["code"], issue.get("file", ""), issue["message"])
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    return unique


def fix_flow(frontend_dir: Path, backend_dir: Path | None) -> list[dict[str, Any]]:
    """Apply every mechanical repair (no LLM tokens). Returns [{file, action}]."""
    fixes: list[dict[str, Any]] = []
    steps = [lambda: fix_auth_calls(frontend_dir), lambda: fix_route_params(frontend_dir),
             lambda: fix_context_properties(frontend_dir),
             lambda: fix_module_members(frontend_dir),
             lambda: harden_frontend(frontend_dir)]
    if backend_dir:
        steps += [lambda: fix_login_consumers(frontend_dir, backend_dir),
                  lambda: fix_auth_response_reads(frontend_dir, backend_dir)]
    for step in steps:
        try:
            fixes += step()
        except Exception:
            continue
    return fixes


# ============================================================================
# "x.toLowerCase is not a function": events and objects stored in string state
# ============================================================================
# `<input onChange={setQuery}>` (or <SearchBar onChange={setQuery}>) stores the whole event in a
# string state; the next render calls query.toLowerCase() and the page crashes. Components also
# disagree on whether a callback gets the event or the value. The adapter below accepts both.
_STRING_STATE = re.compile(r"const\s*\[\s*(\w+)\s*,\s*(set\w+)\s*\]\s*=\s*useState\(\s*(?:\"\"|''|``)\s*\)")
_EVENT_PROPS = ("onChange", "onInput", "onSearch", "onSelect", "onQueryChange", "onValueChange")
_STRING_NAMES = r"(?:query|search|searchTerm|searchQuery|term|keyword|filter|q|text)"
_STRING_CALL = re.compile(
    r"(?<![\w$.])((?:[A-Za-z_$][\w$]*\.)*" + _STRING_NAMES + r")\.(toLowerCase|toUpperCase|trim)\(\)")


def _setter_sites(text: str) -> list[tuple[int, int, str]]:
    sites = []
    for state in _STRING_STATE.finditer(text):
        setter = state.group(2)
        for prop in _EVENT_PROPS:
            for m in re.finditer(rf"\b{prop}=\{{\s*{setter}\s*\}}", text):
                sites.append((m.start(), m.end(), f"{prop}|{setter}"))
    return sites


def check_event_setters(frontend_dir: Path) -> list[dict[str, Any]]:
    issues = []
    for label, path in src_files(frontend_dir).items():
        for _, _, what in _setter_sites(read(path)):
            prop, setter = what.split("|")
            issues.append(make_issue(
                "runtime",
                f"src/{label}: {prop}={{{setter}}} hands the browser event (or whatever the "
                f"component passes) straight to a string state, so a later call such as "
                f"{setter[3:].lower()}.toLowerCase() crashes the page. Use "
                f"{prop}={{(e) => {setter}(e.target.value)}} or accept a string explicitly.",
                code="event_passed_to_state", file=f"src/{label}", auto_fixable=True))
    return issues


def fix_event_setters(frontend_dir: Path) -> list[dict[str, Any]]:
    fixes = []
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        sites = _setter_sites(text)
        if not sites:
            continue
        for start, end, what in sorted(sites, reverse=True):
            prop, setter = what.split("|")
            adapter = (f"{prop}={{(value) => {setter}(value && value.target ? "
                       f"value.target.value : value)}}")
            text = text[:start] + adapter + text[end:]
        path.write_text(text, encoding="utf-8")
        fixes.append({"file": f"src/{label}",
                      "action": f"{len(sites)} state setter(s) now receive the typed value, not the event"})
    return fixes


def fix_string_calls(frontend_dir: Path) -> list[dict[str, Any]]:
    """query.toLowerCase() -> String(query ?? "").toLowerCase(): never crashes on an object."""
    fixes = []
    for label, path in src_files(frontend_dir).items():
        text = read(path)
        new, count = _STRING_CALL.subn(lambda m: f'String({m.group(1)} ?? "").{m.group(2)}()', text)
        if count:
            path.write_text(new, encoding="utf-8")
            fixes.append({"file": f"src/{label}",
                          "action": f"{count} string call(s) made safe against non-string values"})
    return fixes


def harden_frontend(frontend_dir: Path) -> list[dict[str, Any]]:
    """Always-safe repairs run on every generated project before it is validated."""
    fixes: list[dict[str, Any]] = []
    from generation.placeholder_art import fix_missing_images

    for step in (fix_event_setters, fix_string_calls, fix_missing_images):
        try:
            fixes += step(Path(frontend_dir))
        except Exception:
            continue
    return fixes



# ============================================================================
# "loginUser is not a function": a name that is read but never provided
# ============================================================================
_ROLE_NAMES = {
    "login": re.compile(r"^(?:handle|do|perform)?(?:log_?in|sign_?in|authenticate)(?:user|account)?$", re.I),
    "register": re.compile(r"^(?:handle|do|perform)?(?:register|sign_?up|create_?account|create_?user)(?:user|account)?$", re.I),
    "logout": re.compile(r"^(?:handle|do|perform)?(?:log_?out|sign_?out)(?:user|account)?$", re.I),
}


def _alias_role(name: str) -> str | None:
    for role, rx in _ROLE_NAMES.items():
        if rx.match(name):
            return role
    return None


_CTX_PROBLEM = re.compile(r"(\w+)\(\) reads '(\w+)', but (\w+) \(src/([^)]+)\) does not provide it")


def check_context_properties(frontend_dir: Path) -> list[dict[str, Any]]:
    """`const { loginUser } = useAuth()` while the provider only offers `login`."""
    from generation.provider_wiring import validate_context_properties

    issues = []
    for file, problems in validate_context_properties(Path(frontend_dir)).items():
        for problem in problems:
            m = _CTX_PROBLEM.search(problem)
            if not m:
                continue
            issues.append(make_issue(
                "runtime",
                f"{file}: {m.group(2)} is read from {m.group(1)}() but {m.group(3)} never provides it, "
                f"so it is always undefined (calling it throws '{m.group(2)} is not a function'). "
                f"{problem}",
                code="context_property_missing", file=file, auto_fixable=True))
    return issues


def _add_object_key(text: str, span: tuple[int, int], name: str, sibling: str) -> str:
    body = text[span[0]:span[1]]
    trimmed = body.rstrip().rstrip(",")
    return text[:span[0]] + trimmed + f", {name}: {sibling} " + text[span[1]:]


def fix_context_properties(frontend_dir: Path) -> list[dict[str, Any]]:
    """Provide the missing name as an alias of the same-role function the provider already has."""
    from generation.provider_wiring import validate_context_properties

    frontend_dir = Path(frontend_dir)
    fixes: list[dict[str, Any]] = []
    for file, problems in validate_context_properties(frontend_dir).items():
        for problem in problems:
            m = _CTX_PROBLEM.search(problem)
            if not m:
                continue
            hook, name, _provider, provider_file = m.groups()
            role = _alias_role(name)
            if role is None:
                continue
            path = frontend_dir / "src" / provider_file
            text = read(path)
            match = re.search(r"<(\w+)\.Provider\s+value\s*=\s*\{", text)
            span = _value_object_span(text, match) if match else None
            if span is None:
                continue
            keys = [part.partition(":")[0].strip() for part in split_args(text[span[0]:span[1]])
                    if not part.startswith("...")]
            sibling = next((k for k in keys if k != name and _alias_role(k) == role), None)
            if sibling is None:
                continue
            path.write_text(_add_object_key(text, span, name, sibling), encoding="utf-8")
            fixes.append({"file": f"src/{provider_file}",
                          "action": f"{hook}() now also provides {name} (same as {sibling})"})
    return fixes


# ---- named exports of a module -----------------------------------------------------------
_NAMED_EXPORT = re.compile(r"export\s+(?:async\s+)?(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)")


def module_exports(text: str) -> dict[str, str] | None:
    """name -> 'named' | 'member' (a key of `export default { ... }`); None when unknown (export *)."""
    if re.search(r"export\s*\*\s*from", text):
        return None
    names: dict[str, str] = {n: "named" for n in _NAMED_EXPORT.findall(text)}
    for block in re.findall(r"export\s*\{([^}]*)\}", text):
        for part in block.split(","):
            bits = [b.strip() for b in re.split(r"\s+as\s+", part.strip())]
            if bits and bits[-1]:
                names[bits[-1]] = "named"
    d = re.search(r"export\s+default\s*\{", text)
    if d:
        brace = text.index("{", d.end() - 1)
        for part in split_args(text[brace + 1:_match_close(text, brace)]):
            key = part.partition(":")[0].strip().strip("\"'")
            if key and not part.startswith("..."):
                names.setdefault(key, "member")
    return names


def _member_problems(frontend_dir: Path) -> list[dict[str, Any]]:
    """Auth-named things that are imported / called on a module that does not have them."""
    files = src_files(frontend_dir)
    texts = {label: read(path) for label, path in files.items()}
    exports: dict[str, dict[str, str] | None] = {}
    found: list[dict[str, Any]] = []
    for label, text in texts.items():
        for m in _IMPORT_STMT.finditer(text):
            target = _resolve_module(frontend_dir, label, m.group(2))
            if not target:
                continue
            if target not in exports:
                exports[target] = module_exports(texts[target])
            have = exports[target]
            if have is None:
                continue
            clause = m.group(1).strip()
            wanted: list[str] = []
            named = re.search(r"\{([^}]*)\}", clause)
            if named:
                for part in split_args(named.group(1)):
                    orig = re.split(r"\s+as\s+", part.strip())[0].strip()
                    if orig and orig != "default":
                        wanted.append(orig)
            for ns in re.findall(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause) + (
                    [d.group(1)] if (d := re.match(r"^([A-Za-z_$][\w$]*)\s*(?:,|$)", clause))
                    and "member" in have.values() else []):
                wanted += re.findall(rf"\b{re.escape(ns)}\.([A-Za-z_$][\w$]*)\s*\(", text)
            for name in dict.fromkeys(wanted):
                role = _alias_role(name)
                if role and name not in have:
                    found.append({"file": label, "target": target, "name": name, "role": role,
                                  "have": have})
    return found


def check_module_members(frontend_dir: Path) -> list[dict[str, Any]]:
    return [make_issue(
        "runtime",
        f"src/{p['file']} uses {p['name']} from src/{p['target']}, which does not export it "
        f"(it has: {', '.join(sorted(p['have'])) or 'nothing'}), so the call throws "
        f"'{p['name']} is not a function'. Use a name the module really exports.",
        code="import_member_missing", file=f"src/{p['file']}", auto_fixable=True)
        for p in _member_problems(frontend_dir)]


def fix_module_members(frontend_dir: Path) -> list[dict[str, Any]]:
    """Add the missing name to the module as an alias of its same-role function."""
    frontend_dir = Path(frontend_dir)
    files = src_files(frontend_dir)
    fixes: list[dict[str, Any]] = []
    for p in _member_problems(frontend_dir):
        sibling = next((n for n in p["have"] if n != p["name"] and _alias_role(n) == p["role"]), None)
        if sibling is None:
            continue
        path = files[p["target"]]
        text = read(path)
        if p["have"][sibling] == "named":
            text = text.rstrip() + f"\nexport const {p['name']} = {sibling};\n"
        else:
            d = re.search(r"export\s+default\s*\{", text)
            brace = text.index("{", d.end() - 1)
            text = _add_object_key(text, (brace + 1, _match_close(text, brace)), p["name"], sibling)
        path.write_text(text, encoding="utf-8")
        fixes.append({"file": f"src/{p['target']}",
                      "action": f"exports {p['name']} (same as {sibling}) for src/{p['file']}"})
    return fixes
