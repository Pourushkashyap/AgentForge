"""Keep generated theme colors readable.

The planner picks ``primary``/``secondary``/``accent`` hex colors, and for
"soft", "pastel", "white and green" briefs it often picks very pale ones. The
generated pages then use ``text-secondary`` / ``text-primary`` for body text on
white cards, so the text is effectively invisible (a card with only a heading
and an empty-looking body).

``ensure_contrast`` darkens a color, keeping its hue, until it reaches a
minimum WCAG contrast ratio against white.

Repair an already generated project:

    python -m generation.theme_contrast <frontend_dir>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Colors that are used as text or as a button background under white text.
READABLE_MINIMUMS = {"primary": 4.5, "secondary": 4.5, "accent": 3.5}


def _rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def mix(hex_color: str, other: tuple[int, int, int], weight: float) -> str:
    """Blend toward ``other``; weight is the share of the original color."""
    mixed = tuple(round(c * weight + o * (1 - weight)) for c, o in zip(_rgb(hex_color), other))
    return _hex(mixed)  # type: ignore[arg-type]


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_with_white(hex_color: str) -> float:
    return 1.05 / (_luminance(_rgb(hex_color)) + 0.05)


def ensure_contrast(hex_color: str, minimum: float = 4.5) -> str:
    """Darken ``hex_color`` until it has ``minimum`` contrast against white."""
    rgb = _rgb(hex_color)
    for _ in range(80):
        if 1.05 / (_luminance(rgb) + 0.05) >= minimum:
            break
        rgb = tuple(round(c * 0.94) for c in rgb)  # type: ignore[assignment]
    return _hex(rgb)


def readable_theme_colors(colors: dict[str, str]) -> dict[str, str]:
    """Return ``colors`` with primary/secondary/accent made readable."""
    fixed = dict(colors)
    for name, minimum in READABLE_MINIMUMS.items():
        if name in fixed:
            fixed[name] = ensure_contrast(fixed[name], minimum)
    return fixed


_ENTRY_RE = re.compile(
    r'("(?P<name>primary|secondary|accent)":\s*\{\s*"DEFAULT":\s*")(?P<default>#[0-9a-fA-F]{3,6})'
    r'(",\s*"light":\s*")(?P<light>#[0-9a-fA-F]{3,6})'
    r'(",\s*"dark":\s*")(?P<dark>#[0-9a-fA-F]{3,6})(")'
)


def repair_tailwind_config(config_path: Path) -> dict[str, tuple[str, str, float]]:
    """Patch tailwind.config.js in place. Returns {name: (old, new, old_ratio)}."""
    text = config_path.read_text(encoding="utf-8")
    changes: dict[str, tuple[str, str, float]] = {}

    def fix(match: re.Match) -> str:
        name = match.group("name")
        old = match.group("default")
        ratio = contrast_with_white(old)
        new = ensure_contrast(old, READABLE_MINIMUMS[name])
        if new == old:
            return match.group(0)
        changes[name] = (old, new, round(ratio, 2))
        light = mix(new, (255, 255, 255), 0.15)
        dark = mix(new, (0, 0, 0), 0.8)
        return (
            f'{match.group(1)}{new}{match.group(4)}{light}{match.group(6)}{dark}{match.group(8)}'
        )

    new_text = _ENTRY_RE.sub(fix, text)
    if changes:
        config_path.write_text(new_text, encoding="utf-8")
    return changes


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python -m generation.theme_contrast <frontend_dir>")
        raise SystemExit(2)
    config = Path(sys.argv[1]).resolve() / "tailwind.config.js"
    if not config.is_file():
        raise SystemExit(f"tailwind.config.js not found in {config.parent}")
    text = config.read_text(encoding="utf-8")
    for m in _ENTRY_RE.finditer(text):
        print(f"{m.group('name'):10} {m.group('default')}  contrast with white: "
              f"{contrast_with_white(m.group('default')):.2f}")
    result = repair_tailwind_config(config)
    if not result:
        print("All theme colors are already readable. Nothing changed.")
    for name, (old, new, ratio) in result.items():
        print(f"FIXED {name}: {old} (contrast {ratio}) -> {new}")