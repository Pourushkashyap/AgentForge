"""Remove duplicated site layout from page files.

App.jsx normally renders <Navbar /> and <Footer /> around the routes. Models
often also render them inside each page, which shows two headers and footers.
This deterministic pass keeps the App.jsx copy and strips the page copies.
"""
from __future__ import annotations

import re
from pathlib import Path

LAYOUT_NAMES = ("Navbar", "NavBar", "Header", "Footer", "SiteHeader", "SiteFooter")
_NAMES = "|".join(LAYOUT_NAMES)

# a line that is only the self-closing component, e.g. "   <Navbar />"
_TAG_LINE = re.compile(rf"^\s*<(?P<name>{_NAMES})\s*/>\s*$")
# a default import of the component, any quote style
_IMPORT_LINE = re.compile(rf"^\s*import\s+(?P<name>{_NAMES})\s+from\s+['\"][^'\"]+['\"];?\s*$")


def _app_layout_names(app_source: str) -> set[str]:
    return set(re.findall(rf"<({_NAMES})\s*/>", app_source))


def remove_duplicate_layout(project_dir: str | Path) -> list[str]:
    """Strip layout components from page files when App.jsx already renders them.

    Returns the relative paths of files that were changed.
    """
    src = Path(project_dir) / "src"
    app = next((src / n for n in ("App.jsx", "App.js", "App.tsx") if (src / n).is_file()), None)
    pages = src / "pages"
    if app is None or not pages.is_dir():
        return []

    names = _app_layout_names(app.read_text(encoding="utf-8"))
    if not names:
        return []

    changed: list[str] = []
    for path in sorted(pages.rglob("*")):
        if path.suffix not in {".jsx", ".js", ".tsx"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        kept: list[str] = []
        for line in text.splitlines(keepends=True):
            tag = _TAG_LINE.match(line)
            imp = _IMPORT_LINE.match(line)
            if (tag and tag.group("name") in names) or (imp and imp.group("name") in names):
                continue
            kept.append(line)
        new_text = "".join(kept)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed.append(path.relative_to(src.parent).as_posix())
    return changed
