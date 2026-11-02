
import re
from pathlib import Path
from typing import Any


HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

FETCH_RE = re.compile(
    r"""fetch\(\s*(['"`])(.*?)\1\s*(?:,\s*\{(.*?)\})?\s*\)""",
    re.DOTALL,
)

AXIOS_RE = re.compile(
    r"""axios\.(get|post|put|patch|delete)\(\s*(['"`])(.*?)\2""",
    re.IGNORECASE,
)

# `${id}` inside a template literal becomes the path parameter {id}.
TEMPLATE_PARAM_RE = re.compile(r"\$\{[^}]*\}")
# A leading `${API_BASE}` / `${BASE_URL}` prefix is not part of the route.
BASE_PREFIX_RE = re.compile(r"^\$\{([^}]*)\}(/.*)$")
BASE_NAME_RE = re.compile(r"api|base|url|host|origin|server", re.IGNORECASE)

METHOD_RE = re.compile(r"""method\s*:\s*['"]([A-Za-z]+)['"]""")
JSON_BODY_RE = re.compile(
    r"""body\s*:\s*JSON\.stringify\(\s*(\{.*\})\s*\)""",
    re.DOTALL,
)
OBJECT_KEY_RE = re.compile(r"""(?:^|,)\s*['"]?([A-Za-z_$][\w$]*)['"]?\s*:""")
IDENTIFIER_BODY_RE = re.compile(
    r"""body\s*:\s*JSON\.stringify\(\s*([A-Za-z_$][\w$]*)\s*\)"""
)
FIELD_RE = re.compile(r"""(?:name|register)\(\s*['"]([^'"]+)['"]""")
RESPONSE_FIELD_RE = re.compile(
    r"""\.\s*(?:map|filter|find)\s*\(\s*\w+\s*=>\s*\w+\.([A-Za-z_$][\w$]*)"""
)


def analyze_frontend_api_requirements(
    frontend_dir: str | Path,
) -> list[dict[str, Any]]:
    """Extract statically identifiable API calls from frontend source files."""
    root = Path(frontend_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Frontend directory not found: {root}")

    ignored = {"node_modules", "dist", ".git"}
    source_files = [
        path
        for pattern in ("*.js", "*.jsx", "*.ts", "*.tsx")
        for path in root.rglob(pattern)
        if not any(part in ignored for part in path.parts)
    ]

    requirements: dict[tuple[str, str], dict[str, Any]] = {}

    for file_path in source_files:
        try:
            source = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue

        relative_path = file_path.relative_to(root).as_posix()

        for url, options in _iter_fetch_calls(source):
            method_match = METHOD_RE.search(options)
            method = method_match.group(1).upper() if method_match else "GET"

            if method not in HTTP_METHODS:
                continue

            request_fields = _extract_request_fields(options)
            _add_requirement(
                requirements,
                relative_path,
                method,
                url,
                request_fields=request_fields,
                response_fields=_extract_response_fields(source),
            )

        for match in AXIOS_RE.finditer(source):
            method, url = match.group(1).upper(), match.group(3)
            _add_requirement(
                requirements,
                relative_path,
                method,
                url,
                request_fields=[],
                response_fields=_extract_response_fields(source),
            )

    return list(requirements.values())



def _extract_request_fields(options: str) -> list[str]:
    """Extract keys from a statically defined JSON.stringify object."""
    marker = re.search(
        r"""body\s*:\s*JSON\.stringify\(\s*\{""",
        options,
    )
    if not marker:
        return []

    # Locate the opening brace of the object.
    open_brace = options.find("{", marker.start())
    close_brace = _find_matching_brace(options, open_brace)

    if close_brace == -1:
        return []

    object_source = options[open_brace + 1:close_brace]

    # Extract top-level object keys; ignore nested object keys.
    fields = []
    depth = 0
    quote = None
    escaped = False
    segment_start = 0

    for index, char in enumerate(object_source):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in ("'", '"', "`"):
            quote = char
        elif char in "{[(":
            depth += 1
        elif char in "}])":
            depth -= 1
        elif char == "," and depth == 0:
            field = _segment_field(object_source[segment_start:index])
            if field:
                fields.append(field)
            segment_start = index + 1

    field = _segment_field(object_source[segment_start:])
    if field:
        fields.append(field)

    return list(dict.fromkeys(fields))


def _segment_field(segment: str) -> str | None:
    """Field name of one object-literal segment: `a: 1`, `'a': 1` or `a`."""
    text = segment.strip()
    if not text or text.startswith("..."):
        return None

    keyed = re.match(r"""['"]?([A-Za-z_$][\w$]*)['"]?\s*:""", text)
    if keyed:
        return keyed.group(1)

    shorthand = re.fullmatch(r"([A-Za-z_$][\w$]*)", text)
    return shorthand.group(1) if shorthand else None


def _find_matching_brace(source: str, opening: int) -> int:
    """Return the matching closing brace, respecting quoted strings."""
    depth = 0
    quote = None
    escaped = False

    for index in range(opening, len(source)):
        char = source[index]

        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index

    return -1



def _iter_fetch_calls(source: str):
    """Yield (url, options) for fetch calls with nested option objects."""
    start_pattern = re.compile(
        r"""fetch\(\s*(['"`])(.*?)\1""",
        re.DOTALL,
    )

    for match in start_pattern.finditer(source):
        url = match.group(2)
        cursor = match.end()

        while cursor < len(source) and source[cursor].isspace():
            cursor += 1

        if cursor >= len(source) or source[cursor] != ",":
            yield url, ""
            continue

        cursor += 1
        while cursor < len(source) and source[cursor].isspace():
            cursor += 1

        if cursor >= len(source) or source[cursor] != "{":
            yield url, ""
            continue

        closing = _find_matching_brace(source, cursor)
        if closing == -1:
            yield url, source[cursor + 1:]
        else:
            yield url, source[cursor + 1:closing]


def _extract_response_fields(source: str) -> list[str]:
    """
    Extract fields explicitly accessed in common array callbacks.
    This is a heuristic, not a complete response-schema inference.
    """
    return list(dict.fromkeys(RESPONSE_FIELD_RE.findall(source)))


def _add_requirement(
    requirements: dict,
    source_file: str,
    method: str,
    url: str,
    request_fields: list[str] | None = None,
    response_fields: list[str] | None = None,
) -> None:
    base_match = BASE_PREFIX_RE.match(url)
    if base_match and BASE_NAME_RE.search(base_match.group(1)):
        url = base_match.group(2)
    url = TEMPLATE_PARAM_RE.sub("{id}", url)

    # Only local absolute paths are analyzed.
    if not url.startswith("/") or url.startswith("//"):
        return

    path = url.split("?", 1)[0].rstrip("/") or "/"
    key = (method, path)

    if key not in requirements:
        requirements[key] = {
            "source_file": source_file,
            "method": method,
            "path": path,
            "purpose": f"{method} request used by frontend",
            "request_fields": request_fields or [],
            "response_fields": response_fields or [],
            "authentication_required": False,
        }
    else:
        item = requirements[key]
        item["request_fields"] = list(
            dict.fromkeys(item["request_fields"] + (request_fields or []))
        )
        item["response_fields"] = list(
            dict.fromkeys(item["response_fields"] + (response_fields or []))
        )
