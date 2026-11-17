"""Offline placeholder pictures for generated projects.

Every card used to get the same picture: the emoji was chosen from the item's name only, so
"Cozy double room with modern decor" matched nothing and fell back to the CATEGORY emoji, and
a hotel was filed under "travel", so each room, each gallery slide and each offer showed the
same aeroplane in the same colour. Categories also disagreed with each other ("electronics" had
no palette, "tech" had no keywords), so half of them rendered a plain star.

Now: a hotel is hospitality, every category has eight emoji and five colour themes, the emoji
comes from the item's name when it names something (pool, suite, spa...) and otherwise rotates
by a stable hash of the name, so neighbouring cards differ but a card never changes between runs.
"""
from __future__ import annotations

import html
import json
import re
import zlib
from pathlib import Path
from typing import Any

# ---- themes ------------------------------------------------------------------------
THEMES: dict[str, dict[str, Any]] = {
    "hospitality": {"emoji": "🏨🛏️🛎️🌴🏊🍽️🧖🌅", "bases": ["#4a90b8", "#c98a4b", "#5fa97f", "#8e6bb3", "#d0705a"]},
    "travel": {"emoji": "✈️🧳🗺️🏖️⛰️🚆🌍📸", "bases": ["#4a90b8", "#3f9aa6", "#d98a3d", "#6a7fc4", "#58a06b"]},
    "food": {"emoji": "🍰🥐🍕🍔🥗☕🍝🍜", "bases": ["#e08e45", "#c8553d", "#7aa05a", "#d9a441", "#a6623a"]},
    "electronics": {"emoji": "💻📱🎧📷⌚🖥️🔌🎮", "bases": ["#4a63b8", "#3d8fa3", "#7a5cc0", "#2f7f9a", "#5c6bc0"]},
    "fashion": {"emoji": "👗👟👜🧥👓💍👔🧣", "bases": ["#c0587a", "#a05cb0", "#d0805a", "#7a6ab8", "#b84a6a"]},
    "fitness": {"emoji": "🏋️🏃🧘🚴🥊🏊💪🥗", "bases": ["#4f9d55", "#e07a3d", "#3f8fb0", "#7a5cc0", "#c85a5a"]},
    "beauty": {"emoji": "💄💅💆💇🧴🪞🌸✨", "bases": ["#c0587a", "#d08aa0", "#a06ab8", "#d0805a", "#6aa89a"]},
    "furniture": {"emoji": "🛋️🪑🛏️🪴💡🚪🖼️🧺", "bases": ["#a6784a", "#6a8f7a", "#8a7ab0", "#c08a5a", "#5a7fa0"]},
    "education": {"emoji": "📚🎓✏️🧪🔬🧮🌍💡", "bases": ["#4a7fc4", "#5aa07a", "#d09a3d", "#8a6ab8", "#c8603d"]},
    "health": {"emoji": "🩺💊🏥🦷🧬❤️🩹🧘", "bases": ["#3f9aa6", "#4a90b8", "#5fa97f", "#6a7fc4", "#c86a7a"]},
    "realestate": {"emoji": "🏠🏡🏢🔑🛋️🌳🏙️📐", "bases": ["#5a8f6a", "#4a7fa8", "#c08a4a", "#7a6ab0", "#b86a5a"]},
    "automotive": {"emoji": "🚗🚙🏎️🔧⛽🛞🚘🧰", "bases": ["#3d5a99", "#c0483d", "#4f7f8a", "#7a7a85", "#d0883d"]},
    "events": {"emoji": "🎟️🎤🎶🎪🎉🎭🏟️🥂", "bases": ["#8a4ab8", "#d0485a", "#d0883d", "#3f7fc0", "#4aa08a"]},
    "pets": {"emoji": "🐶🐱🐰🐾🦜🐹🐠🦴", "bases": ["#d0883d", "#7a9a5a", "#5a8ab8", "#b86a8a", "#a6784a"]},
    "jobs": {"emoji": "💼👔📄🧑‍💻🏢📈🤝🎯", "bases": ["#3d5a99", "#4a8f8a", "#7a5cc0", "#c0803d", "#5a7fa0"]},
    "general": {"emoji": "⭐🎨📦✨🌟🧩🎁🔖", "bases": ["#7a7aa8", "#5a8fb0", "#8aa05a", "#c0805a", "#a06a9a"]},
}
ALIASES = {"tech": "electronics", "hotel": "hospitality", "stay": "hospitality"}

# category detection: ORDER MATTERS (a tie goes to the first listed)
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "hospitality": ("hotel", "resort", "suite", "lodge", "hostel", "accommodation", "guest house",
                    "guesthouse", "motel", "check-in", "check in", "room", "rooms", "stay"),
    "education": ("tutor", "tutoring", "course", "school", "student", "academy", "lesson", "learn", "teacher"),
    "health": ("clinic", "doctor", "patient", "hospital", "medical", "dental", "pharmacy", "wellness"),
    "realestate": ("real estate", "property", "properties", "apartment", "rent", "listing", "realtor"),
    "automotive": ("car rental", "car ", "cars", "vehicle", "automotive", "driving", "dealership"),
    "events": ("event", "ticket", "concert", "festival", "conference", "venue"),
    "pets": ("pet ", "pets", "adoption", "adopt", "puppy", "kitten", "veterinar"),
    "jobs": ("job board", "jobs", "career", "hiring", "recruit", "resume", "vacancy"),
    "food": ("food", "bakery", "cake", "bread", "cookie", "pizza", "restaurant", "cafe", "menu",
             "grocery", "sweet", "oven", "dessert", "meal"),
    "travel": ("travel", "tour", "destination", "holiday", "trip", "tourism", "flight", "vacation", "itinerary"),
    "electronics": ("electronics", "camera", "laptop", "computer", "phone", "headphone", "gadget",
                    "device", "technology", "tech store"),
    "fashion": ("fashion", "clothing", "apparel", "shoe", "dress", "shirt", "jewelry"),
    "fitness": ("fitness", "gym", "workout", "exercise", "sports", "training", "yoga"),
    "beauty": ("beauty", "skincare", "cosmetic", "makeup", "salon", "spa"),
    "furniture": ("furniture", "sofa", "chair", "interior", "decor"),
}

# an item that names something gets a picture of that thing (most specific first)
NAME_EMOJI: tuple[tuple[str, str], ...] = (
    (r"\b(?:pool|swim)", "🏊"), (r"\b(?:spa|massage|sauna|wellness)\b", "🧖"),
    (r"\b(?:restaurant|dining|breakfast|buffet|dinner|brunch|cuisine)", "🍽️"),
    (r"\b(?:bar|lounge|cocktail)\b", "🍸"), (r"\b(?:suite|bedroom|room|bed)\b", "🛏️"),
    (r"\b(?:lobby|reception|concierge|front desk)", "🛎️"), (r"\b(?:sunset|sunrise|view|skyline)", "🌅"),
    (r"\b(?:beach|ocean|sea|coast)", "🏖️"), (r"\b(?:garden|terrace|balcony|patio|park)", "🌴"),
    (r"\b(?:gym|fitness|workout)", "🏋️"), (r"\b(?:wedding|banquet|reception hall)", "💒"),
    (r"\b(?:conference|meeting|seminar)", "🎤"), (r"\b(?:hotel|resort|exterior|facade|building)", "🏨"),
    (r"\b(?:cookie|biscuit)", "🍪"), (r"\b(?:muffin|cupcake)", "🧁"), (r"\bcroissant", "🥐"),
    (r"\b(?:bread|loaf|baguette|sourdough)", "🍞"), (r"\b(?:cake|brownie)", "🍰"),
    (r"\b(?:tart|pie)\b", "🥧"), (r"\b(?:donut|doughnut)", "🍩"), (r"\bpizza", "🍕"),
    (r"\bburger", "🍔"), (r"\b(?:coffee|latte|espresso)", "☕"), (r"\bsalad", "🥗"),
    (r"\b(?:shoe|sneaker)", "👟"), (r"\b(?:phone|mobile)", "📱"), (r"\blaptop", "💻"),
    (r"\b(?:headphone|earbud)", "🎧"), (r"\bcamera", "📷"), (r"\bwatch\b", "⌚"),
    (r"\b(?:dress|gown)", "👗"), (r"\bbag\b", "👜"), (r"\b(?:jacket|coat)", "🧥"),
    (r"\b(?:yoga|meditat|mindful|zen)", "🧘"), (r"\bcandle", "🕯️"),
    (r"\b(?:manicure|nail|polish)", "💅"), (r"\b(?:facial|skin)", "💆"), (r"\b(?:hair|salon)", "💇"),
    (r"\b(?:makeup|cosmetic|lipstick)", "💄"), (r"\b(?:sofa|couch)", "🛋️"), (r"\bchair", "🪑"),
    (r"\b(?:lamp|light)", "💡"), (r"\b(?:plant|garden)", "🪴"),
    (r"\b(?:math|algebra|calculus)", "🧮"), (r"\b(?:science|chemistry|physics|biology)", "🔬"),
    (r"\b(?:language|english|spanish|french)", "🌍"), (r"\b(?:music|piano|guitar|violin)", "🎵"),
    (r"\b(?:tutor|course|lesson|class|teacher)", "📚"), (r"\b(?:doctor|clinic|physician)", "🩺"),
    (r"\b(?:dental|dentist|tooth)", "🦷"), (r"\b(?:dog|puppy)", "🐶"), (r"\b(?:cat|kitten)", "🐱"),
    (r"\b(?:rabbit|bunny)", "🐰"), (r"\b(?:bird|parrot)", "🦜"),
    (r"\b(?:suv|sedan|hatchback|car)\b", "🚗"), (r"\b(?:truck|van)\b", "🚚"),
    (r"\b(?:apartment|flat|condo)", "🏢"), (r"\b(?:house|villa|home|cottage)", "🏠"),
    (r"\b(?:concert|band|live music)", "🎶"), (r"\b(?:festival|carnival)", "🎪"),
    (r"\b(?:ticket|pass)\b", "🎟️"), (r"\b(?:developer|engineer|software)", "🧑‍💻"),
)


def _stable(label: str) -> int:
    return zlib.crc32(label.strip().lower().encode("utf-8")) & 0xFFFFFFFF


def _split_emoji(sequence: str) -> list[str]:
    """Split a string of emoji into whole emoji (keeps VS16 and ZWJ sequences together)."""
    parts: list[str] = []
    for ch in sequence:
        if parts and (ch in "\ufe0f\u200d" or parts[-1].endswith("\u200d")):
            parts[-1] += ch
        else:
            parts.append(ch)
    return parts


def normalize_category(category: str | None) -> str:
    category = ALIASES.get((category or "general").lower(), (category or "general").lower())
    return category if category in THEMES else "general"


def detect_category(text: str) -> str:
    """Best matching category for a business description, 'general' if nothing fits."""
    lowered = f" {text.lower()} "
    scores = {c: sum(1 for k in words if k in lowered) for c, words in CATEGORY_KEYWORDS.items()}
    best = max(scores.values(), default=0)
    if best == 0:
        return "general"
    return next(c for c in CATEGORY_KEYWORDS if scores[c] == best)      # ties: first listed


def _mix(hex_color: str, ratio: float) -> str:
    """Blend a colour with white (ratio 0 = unchanged, 1 = white)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#{:02x}{:02x}{:02x}".format(*(round(c + (255 - c) * ratio) for c in (r, g, b)))


def pick_emoji(label: str, category: str, variant: int | None = None) -> str:
    lowered = label.lower()
    for pattern, emoji in NAME_EMOJI:
        if re.search(pattern, lowered):
            return emoji
    options = _split_emoji(THEMES[normalize_category(category)]["emoji"])
    return options[(variant if variant is not None else _stable(label)) % len(options)]


def pick_palette(category: str, variant: int) -> tuple[str, str]:
    bases = THEMES[normalize_category(category)]["bases"]
    dark = bases[variant % len(bases)]
    return _mix(dark, 0.55), dark


def wrap_label(label: str, width: int = 26, max_lines: int = 2) -> list[str]:
    lines: list[str] = []
    current, words = "", label.split()
    for index, word in enumerate(words):
        candidate = f"{current} {word}".strip()
        if len(candidate) <= width or not current:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            current = " ".join(words[index:])
            break
    if current:
        lines.append(current)
    if not lines:
        return [""]
    if len(lines[-1]) > width:
        lines[-1] = lines[-1][: width - 1].rstrip() + "…"
    return lines[:max_lines]


def build_svg(label: str, category: str, variant: int | None = None) -> str:
    """A 600x400 picture: themed gradient, soft shapes, a big emoji and a readable caption bar."""
    category = normalize_category(category)
    seed = _stable(label) if variant is None else variant
    light, dark = pick_palette(category, seed)
    emoji = pick_emoji(label, category, seed)
    c1x, c2x = 60 + seed % 220, 380 + (seed >> 8) % 180
    caption = wrap_label(label)
    bar_top = 400 - (28 + 34 * len(caption))
    texts = "".join(
        f'<text x="300" y="{bar_top + 36 + n * 34}" font-size="27" font-family="Georgia, serif" '
        f'fill="#ffffff" text-anchor="middle">{html.escape(line)}</text>'
        for n, line in enumerate(caption))
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400" width="600" height="400" '
        f'role="img" aria-label="{html.escape(label, quote=True)}">'
        '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{light}"/><stop offset="1" stop-color="{dark}"/>'
        '</linearGradient></defs>'
        '<rect width="600" height="400" fill="url(#g)"/>'
        f'<circle cx="{c1x}" cy="70" r="95" fill="#ffffff" opacity="0.16"/>'
        f'<circle cx="{c2x}" cy="330" r="120" fill="#ffffff" opacity="0.12"/>'
        '<circle cx="300" cy="170" r="96" fill="#ffffff" opacity="0.22"/>'
        '<text x="300" y="215" font-size="128" text-anchor="middle" '
        'font-family="Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif">'
        f'{emoji}</text>'
        f'<rect x="0" y="{bar_top}" width="600" height="{400 - bar_top}" fill="#000000" opacity="0.34"/>'
        f'{texts}</svg>')


# ---- existing projects -------------------------------------------------------------
IMAGE_KEY = re.compile(r"(image|photo|thumbnail|picture|cover|banner|avatar)", re.IGNORECASE)
GALLERY_COLLECTION = re.compile(r"(gallery|image|photo|picture|slide|banner|carousel|portfolio|media)", re.IGNORECASE)
GENERIC_KEYS = {"url", "src", "source", "img", "link"}
LABEL_KEYS = ("name", "title", "product_name", "productName", "label", "altText", "alt", "caption")


def _frontend_dir(project_root: Path) -> Path | None:
    base = Path(project_root) / "frontend"
    if (base / "package.json").is_file():
        return base
    return next((p for p in sorted(base.glob("*")) if (p / "package.json").is_file()), None) \
        if base.is_dir() else None


def _project_text(project_root: Path, collections: dict[str, list[Any]]) -> str:
    parts = [Path(project_root).name.replace("-", " ")]
    frontend = _frontend_dir(project_root)
    if frontend is not None:
        index = frontend / "index.html"
        if index.is_file():
            match = re.search(r"<title>(.*?)</title>", index.read_text(encoding="utf-8", errors="ignore"), re.S)
            parts.append(match.group(1) if match else "")
    for name, records in collections.items():
        parts.append(name.replace("_", " "))
        for record in records[:8]:
            if isinstance(record, dict):
                parts += [str(record[k]) for k in LABEL_KEYS if isinstance(record.get(k), str)]
    return " ".join(parts)


def refresh_placeholder_images(project_root: Path, category: str | None = None) -> dict[str, Any]:
    """Give every record its own, fitting picture (also fixes pictures that all look the same).

    * a placeholder drawn earlier is redrawn with the better art;
    * an empty image field gets a new picture;
    * records that share one picture file get their own;
    * real photos and remote URLs are never touched."""
    project_root = Path(project_root)
    frontend = _frontend_dir(project_root)
    data_dir = project_root / "backend" / "data"
    if frontend is None or not data_dir.is_dir():
        return {"changed": 0, "category": None, "files": []}

    collections: dict[str, list[Any]] = {}
    for file in sorted(data_dir.glob("*.json")):
        try:
            records = json.loads(file.read_text(encoding="utf-8") or "[]")
        except (OSError, ValueError):
            continue
        if isinstance(records, list):
            collections[file.stem] = records
    category = normalize_category(category or detect_category(_project_text(project_root, collections)))

    folder = frontend / "public" / "images" / "catalog"
    changed, written = 0, []
    used: set[str] = set()
    for name, records in collections.items():
        gallery_like = bool(GALLERY_COLLECTION.search(name))
        touched = False
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            label = next((str(record[k]).strip() for k in LABEL_KEYS
                          if isinstance(record.get(k), str) and record[k].strip()), f"Item {index + 1}")
            keys = [k for k in record if IMAGE_KEY.search(k) or (gallery_like and k.lower() in GENERIC_KEYS)]
            for key in keys:
                value = str(record.get(key) or "").strip()
                placeholder = value.startswith("/images/catalog/") and value.endswith(".svg")
                if value and not placeholder:
                    continue                                    # a real photo or a remote URL
                slug = re.sub(r"[^a-z0-9]+", "-", f"{name}-{index + 1}-{label}".lower()).strip("-")[:80]
                target = f"/images/catalog/{slug}.svg"
                if value and value in used and value != target:  # shared by several records
                    value = ""
                used.add(target)
                folder.mkdir(parents=True, exist_ok=True)
                svg = build_svg(label, category, _stable(f"{name}{index}{label}"))
                file = folder / f"{slug}.svg"
                did = False
                if not file.exists() or file.read_text(encoding="utf-8", errors="ignore") != svg:
                    file.write_text(svg, encoding="utf-8")
                    written.append(f"public{target}")
                    did = True
                if record.get(key) != target:
                    record[key] = target
                    touched = True
                    did = True
                changed += 1 if did else 0
        if touched:
            (data_dir / f"{name}.json").write_text(
                json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"changed": changed, "category": category, "files": written}


# ---- pictures the code points at but nobody created ------------------------------------
_IMG_REF = re.compile(r"""(["'(])(/(?!/)[\w\-./]+\.(?:png|jpe?g|webp|gif|svg))(?=["')?#])""", re.IGNORECASE)


def fix_missing_images(frontend_dir: Path) -> list[dict[str, Any]]:
    """`<img src="/images/hero.jpg">` with no such file: draw a picture and point the code at it."""
    frontend_dir = Path(frontend_dir)
    src, public = frontend_dir / "src", frontend_dir / "public"
    if not src.is_dir():
        return []
    category = detect_category(_project_text(frontend_dir.parent, {}) if frontend_dir.parent.name != "frontend"
                               else _project_text(frontend_dir.parent.parent, {}))
    fixes: list[dict[str, Any]] = []
    for path in sorted(src.rglob("*")):
        if path.suffix not in {".js", ".jsx", ".ts", ".tsx", ".css"} or "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        replaced: dict[str, str] = {}
        for m in _IMG_REF.finditer(text):
            ref = m.group(2)
            if ref in replaced or (public / ref.lstrip("/")).is_file():
                continue
            stem = re.sub(r"[^a-z0-9]+", " ", Path(ref).stem.lower()).strip() or "image"
            slug = re.sub(r"\s+", "-", stem)
            target = f"/images/generated/{slug}.svg"
            out = public / target.lstrip("/")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(build_svg(stem.title(), category, _stable(ref)), encoding="utf-8")
            replaced[ref] = target
        if replaced:
            for old, new in replaced.items():
                text = text.replace(old, new)
            path.write_text(text, encoding="utf-8")
            fixes.append({"file": f"src/{path.relative_to(src).as_posix()}",
                          "action": f"{len(replaced)} missing image(s) replaced by generated pictures"})
    return fixes
