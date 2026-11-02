"""Find React context values that components use but providers never provide.

The older check assumed one context per file named after the file. Generated
projects often put several providers in one file (e.g. StoreContext.jsx with
ProductsProvider, CartProvider, ...). This check reads every
`<XContext.Provider value=...>` and every `useX()` hook, then verifies each
`const { a, b } = useX()` in the other files.
"""
from __future__ import annotations

import re
from typing import Iterable

_PROVIDER = re.compile(r"<\s*(\w+)\.Provider\s+value\s*=\s*\{")
_HOOK = re.compile(r"(?:function\s+(use\w+)\s*\(|const\s+(use\w+)\s*=)")
_USE_CONTEXT = re.compile(r"useContext\(\s*(\w+)\s*\)")


def _balanced(text: str, start: int) -> tuple[str, int] | None:
    """text[start] is an opening bracket; return (inner text, index after close)."""
    pairs = {"{": "}", "(": ")", "[": "]"}
    if start >= len(text) or text[start] not in pairs:
        return None
    depth, i, quote = 0, start, None
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch in "{([":
            depth += 1
        elif ch in "})]":
            depth -= 1
            if depth == 0:
                return text[start + 1:i], i + 1
        i += 1
    return None


def _split_top_level(body: str) -> list[str]:
    parts, depth, quote, current = [], 0, None, []
    i = 0
    while i < len(body):
        ch = body[i]
        if quote:
            current.append(ch)
            if ch == "\\" and i + 1 < len(body):
                current.append(body[i + 1])
                i += 1
            elif ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
            current.append(ch)
        elif ch in "{([":
            depth += 1
            current.append(ch)
        elif ch in "})]":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        i += 1
    if "".join(current).strip():
        parts.append("".join(current))
    return parts


def _object_keys(body: str) -> set[str] | None:
    """Property names of an object literal body; None if it uses a spread."""
    keys: set[str] = set()
    for entry in _split_top_level(body):
        entry = entry.strip()
        if not entry:
            continue
        if entry.startswith("..."):
            return None  # cannot know what is spread in
        entry = re.sub(r"^(?:async\s+|get\s+|set\s+)", "", entry)
        match = re.match(r"^(?:\[[^\]]*\]|['\"]?([A-Za-z_$][\w$]*)['\"]?)", entry)
        if match and match.group(1):
            keys.add(match.group(1))
    return keys


def _value_keys(text: str, after_brace: int) -> set[str] | None:
    """Keys of the object given to `value={...}`; after_brace is the `{` index."""
    outer = _balanced(text, after_brace)
    if outer is None:
        return None
    expr = outer[0].strip()

    if expr.startswith("{"):                      # value={{ a, b }}
        inner = _balanced(expr, 0)
        return _object_keys(inner[0]) if inner else None

    if expr.startswith("useMemo"):                # value={useMemo(() => ({...}), [])}
        return _memo_keys(expr)

    if re.fullmatch(r"[A-Za-z_$][\w$]*", expr):   # value={value}
        declarations = list(re.finditer(rf"\bconst\s+{re.escape(expr)}\s*=\s*", text[:after_brace]))
        if not declarations:
            return None
        rest = text[declarations[-1].end():]
        if rest.startswith("{"):
            inner = _balanced(rest, 0)
            return _object_keys(inner[0]) if inner else None
        if rest.startswith("useMemo"):
            return _memo_keys(rest)
    return None


def _memo_keys(expr: str) -> set[str] | None:
    arrow = re.search(r"=>\s*\(\s*\{", expr)
    if not arrow:
        return None
    brace = expr.index("{", arrow.start())
    inner = _balanced(expr, brace)
    return _object_keys(inner[0]) if inner else None


def _context_maps(context_texts: Iterable[str]) -> tuple[dict[str, set[str]], dict[str, str]]:
    """({context name: provided keys}, {hook name: context name})."""
    provided: dict[str, set[str]] = {}
    hooks: dict[str, str] = {}
    for text in context_texts:
        for match in _PROVIDER.finditer(text):
            keys = _value_keys(text, match.end() - 1)
            if keys is not None:
                provided[match.group(1)] = keys
        for match in _HOOK.finditer(text):
            name = match.group(1) or match.group(2)
            use = _USE_CONTEXT.search(text, match.end(), match.end() + 500)
            if use:
                hooks[name] = use.group(1)
    return provided, hooks


def check_context_usage(texts: dict[str, str]) -> dict[str, list[str]]:
    """{file path: [problem, ...]} for properties read from a hook but not provided."""
    context_paths = [p for p in texts if "/context/" in p.replace("\\", "/")]
    provided, hooks = _context_maps(texts[p] for p in context_paths)
    if not provided or not hooks:
        return {}

    problems: dict[str, list[str]] = {}
    for path, text in texts.items():
        if path in context_paths:
            continue
        for hook, context in hooks.items():
            keys = provided.get(context)
            if keys is None:
                continue
            pattern = r"\bconst\s*\{([^}]*)\}\s*=\s*" + re.escape(hook) + r"\s*\(\s*\)"
            for match in re.finditer(pattern, text, re.S):
                for entry in _split_top_level(match.group(1)):
                    name = re.match(r"^\s*([A-Za-z_$][\w$]*)", entry)
                    if not name or entry.strip().startswith("..."):
                        continue
                    if name.group(1) not in keys:
                        problems.setdefault(path, []).append(
                            f"{hook}() reads '{name.group(1)}', but {context} does not "
                            f"expose it. Available properties: "
                            f"{', '.join(sorted(keys)) or 'none'}. Use an existing "
                            "property or update the context provider and consumer "
                            "consistently.")
    return problems
