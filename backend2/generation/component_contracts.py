"""Check that components receive the props they actually accept.

Catches bugs like
    <ServiceCard id={s.id} name={s.name} price={s.price} />      (page)
    export default function ServiceCard({ service }) { ... }     (component)
where the page passes flat props but the component expects one ``service``
object, so ``service`` is undefined and rendering throws
"Cannot read properties of undefined (reading 'name')".

Only destructured signatures are checked. Components that take ``props``
whole, use ``...rest`` or are defined more than once are skipped, so a hit is
almost always a real bug.
"""
from __future__ import annotations

import re
from pathlib import Path

_IGNORED_PROPS = {"key", "ref"}
# Passing these to a component that ignores them is cosmetic, not a bug.
# Props that are optional by convention; never report them as "missing".
_OPTIONAL_BY_CONVENTION = {"className", "style", "children", "id", "key", "ref"}
_HARMLESS_UNKNOWN = {
    "className", "style", "id", "type", "role", "title", "href", "target",
    "rel", "htmlFor", "tabIndex", "disabled", "onClick",
}

_DEF_RES = (
    re.compile(r"function\s+([A-Z][\w$]*)\s*\(\s*\{"),
    re.compile(
        r"const\s+([A-Z][\w$]*)\s*=\s*(?:React\.)?"
        r"(?:(?:memo|forwardRef)\s*\(\s*)?\(?\s*\(\s*\{"
    ),
)


def _balanced(text: str, open_index: int) -> tuple[str, int] | None:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index + 1:i], i
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


def _src_files(project_dir: Path) -> dict[str, str]:
    src = Path(project_dir) / "src"
    out: dict[str, str] = {}
    if src.is_dir():
        for p in src.rglob("*"):
            if p.is_file() and p.suffix in (".js", ".jsx"):
                out[p.relative_to(src).as_posix()] = p.read_text(
                    encoding="utf-8", errors="ignore"
                )
    return out


def _definitions(files: dict[str, str]) -> dict[str, list[tuple[str, set[str], set[str], bool]]]:
    """{ComponentName: [(file, accepted, required, has_rest), ...]}"""
    found: dict[str, list[tuple[str, set[str], bool]]] = {}
    for rel, text in files.items():
        for pattern in _DEF_RES:
            for m in pattern.finditer(text):
                parsed = _balanced(text, m.end() - 1)
                if parsed is None:
                    continue
                body, _ = parsed
                accepted: set[str] = set()
                required: set[str] = set()
                has_rest = False
                for part in _split_top_level(body):
                    if part.startswith("..."):
                        has_rest = True
                        continue
                    nm = re.match(r"^([A-Za-z_$][\w$]*)", part)
                    if nm:
                        accepted.add(nm.group(1))
                        if "=" not in part:
                            required.add(nm.group(1))
                found.setdefault(m.group(1), []).append(
                    (rel, accepted, required, has_rest)
                )
    return found


def _tag_props(text: str, start: int) -> tuple[set[str], bool, bool] | None:
    """Prop names written on the JSX tag that begins at ``start`` ('<Name')."""
    i = start + 1
    n = len(text)
    while i < n and (text[i].isalnum() or text[i] in "_$."):
        i += 1
    depth = 0
    quote = ""
    masked: list[str] = []
    has_spread = False
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif depth == 0 and ch in "\"'":
            quote = ch
            masked.append("§")          # value placeholder, never a prop name
        elif ch == "{":
            if depth == 0:
                masked.append("§")
                if text[i + 1:i + 4] == "...":
                    has_spread = True
            depth += 1
        elif ch == "}":
            depth -= 1
        elif depth > 0:
            pass
        elif ch == ">":
            break
        else:
            masked.append(ch)
        i += 1
    else:
        return None
    self_closing = text[i - 1] == "/"
    attrs = "".join(masked)
    names = set(re.findall(r"(?<![\w$.:§-])([A-Za-z_][\w-]*)(?=\s*=|\s|/|$)", attrs))
    return names, has_spread, self_closing


def validate_component_props(project_dir: Path) -> dict[str, list[str]]:
    """{ "src/<file to fix>": [problem, ...] }

    When several files pass the same props that a component does not accept,
    the component is the thing to fix (once). A lone mismatch is reported on
    the file that renders the component.
    """
    files = _src_files(project_dir)
    defs = _definitions(files)
    # (component, unknown props) -> {caller file: missing props}
    hits: dict[tuple[str, tuple[str, ...]], dict[str, list[str]]] = {}

    for name, entries in defs.items():
        if len(entries) != 1:
            continue                      # ambiguous name
        def_file, accepted, required, has_rest = entries[0]
        if has_rest:
            continue
        required = required - _OPTIONAL_BY_CONVENTION
        for rel, text in files.items():
            for m in re.finditer(r"<%s(?![\w$.])" % re.escape(name), text):
                parsed = _tag_props(text, m.start())
                if parsed is None:
                    continue
                passed, has_spread, self_closing = parsed
                if has_spread:
                    continue
                if not self_closing:
                    passed = passed | {"children"}
                unknown = sorted(
                    p for p in passed
                    if p not in accepted
                    and p not in _IGNORED_PROPS
                    and p not in _HARMLESS_UNKNOWN
                    and not p.startswith(("data-", "aria-"))
                )
                missing = sorted(required - passed)
                # Wrong prop NAMES: something meaningful is passed that the
                # component ignores AND something it needs is not passed.
                if not unknown or not missing:
                    continue
                hits.setdefault((name, tuple(unknown)), {})[rel] = missing

    problems: dict[str, list[str]] = {}

    def add(path: str, message: str) -> None:
        bucket = problems.setdefault(path, [])
        if message not in bucket:
            bucket.append(message)

    for (name, unknown), callers in hits.items():
        def_file, accepted, _required, _rest = defs[name][0]
        accepted_text = ", ".join(sorted(accepted)) or "no props"
        if len(callers) >= 2:
            where = ", ".join(f"src/{c}" for c in sorted(callers))
            add(
                f"src/{def_file}",
                f"{len(callers)} files render <{name}> with prop(s) "
                f"{', '.join(unknown)} ({where}), but {name} only accepts: "
                f"{accepted_text}. Update {name} to ALSO accept "
                f"{', '.join(unknown)} (use them as the displayed content / "
                f"handlers) while keeping its existing props working. Do not "
                "rename or remove its export.",
            )
        else:
            (caller, missing), = callers.items()
            add(
                f"src/{caller}",
                f"<{name}> is given prop(s) {', '.join(unknown)}, but {name} "
                f"(src/{def_file}) only accepts: {accepted_text} and does not "
                f"receive: {', '.join(missing)}. Pass exactly the props "
                f"{name} accepts.",
            )
    return problems