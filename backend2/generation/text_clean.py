"""Normalize odd Unicode that LLMs put into generated text.

Seen in generated projects: non-breaking hyphens (U+2011) that show up as
"â€‘" in some editors/consoles, narrow no-break spaces (U+202F), zero-width
characters, and words glued together ("andcalming", "manicurewith").
"""
from __future__ import annotations

import re
import unicodedata

_HYPHENS = {"\u2010": "-", "\u2011": "-", "\u2043": "-", "\u00ad": ""}
_ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}


def normalize_text(text: str) -> str:
    """Replace look-alike hyphens/spaces and drop invisible characters."""
    if text.isascii():
        return text
    # A zero-width space between two letters/digits stands in for a space.
    text = re.sub(r"(?<=\w)\u200b(?=\w)", " ", text)
    out: list[str] = []
    for ch in text:
        if ch in _HYPHENS:
            out.append(_HYPHENS[ch])
        elif ch in _ZERO_WIDTH:
            continue
        elif unicodedata.category(ch) == "Zs" and ch != " ":
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)