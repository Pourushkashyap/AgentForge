"""Seed generated JSON collections with realistic starter records and local images.

Image resolution is API-independent: it selects curated images from
assets/image_library/<category>/ and copies them into the generated frontend.
If no matching library images exist, image fields are left empty rather than
being populated with random or unrelated URLs.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
from pathlib import Path
from typing import Any

from core.groq_client import call_groq

SEED_COUNT = 8
MAX_SEED_TOKENS = 3500

_TRANSACTIONAL = re.compile(
    r"(order|message|contact|cart|payment|booking|reservation|submission|"
    r"enquir|inquir|subscri|newsletter|feedback|checkout|upload)",
    re.IGNORECASE,
)

IMAGE_FIELD_PATTERN = re.compile(
    r"(image|photo|thumbnail|picture|cover|banner)", re.IGNORECASE
)
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

# A gallery item's image is often just called "url" or "src". Those generic
# names only count as image fields inside gallery-like collections, so a
# "links" collection with a "url" field is not given pictures.
IMAGE_COLLECTION_PATTERN = re.compile(
    r"(gallery|image|photo|picture|slide|banner|carousel|portfolio|media)",
    re.IGNORECASE,
)
GENERIC_IMAGE_FIELDS = {"url", "src", "source", "img", "link"}


def image_field_names(spec: dict[str, Any]) -> list[str]:
    """Names of string fields that hold an image path or URL."""
    collection = str(spec.get("collection") or spec.get("name") or "")
    gallery_like = bool(IMAGE_COLLECTION_PATTERN.search(collection))
    names: list[str] = []
    for field in spec["fields"]:
        name = field["name"]
        if field.get("type") != "str":
            continue
        if IMAGE_FIELD_PATTERN.search(name) or (
            gallery_like and name.lower() in GENERIC_IMAGE_FIELDS
        ):
            names.append(name)
    return names
IMAGE_LIBRARY = Path(__file__).resolve().parent.parent / "assets" / "image_library"

CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "food": (
        "food", "bakery", "cake", "bread", "cookie", "pizza", "restaurant",
        "cafe", "menu", "grocery", "sweet", "oven", "dessert", "meal",
    ),
    "travel": (
        "travel", "tour", "hotel", "destination", "holiday", "trip",
        "tourism", "resort", "flight", "vacation",
    ),
    "electronics": (
        "electronics", "camera", "laptop", "computer", "phone", "headphone",
        "gadget", "device", "technology", "tech store",
    ),
    "fashion": (
        "fashion", "clothing", "apparel", "shoe", "dress", "shirt", "jewelry",
    ),
    "fitness": (
        "fitness", "gym", "workout", "exercise", "sports", "training",
    ),
    "beauty": ("beauty", "skincare", "cosmetic", "makeup", "salon"),
    "furniture": ("furniture", "sofa", "chair", "table", "interior", "decor"),
}

SEED_SYSTEM_PROMPT = """
You create realistic starter data for a generated website's JSON database.

Return ONLY one JSON object. Each key is a collection name from the request
and each value is an array of record objects.

Rules:
1. Use ONLY the field names listed for that collection, spelled exactly.
   Every record must include every field in "must_include".
2. Respect field types: str, int, float, bool, list, dict. Numbers are plain
   numbers without currency symbols. Lists are JSON arrays.
3. "id" is a short unique string such as "p1", "p2".
4. For image/photo/thumbnail fields (and url/src fields of gallery-style
   collections), return an empty string. Do not invent
   URLs, use Picsum, or assign unrelated images. The application resolves
   images from its curated local image library after records are validated.
5. Content must fit the business: realistic names, descriptions and prices.
   Never use lorem ipsum or "Sample item" style placeholders.
6. Return exactly the requested number of records per collection.
"""


def load_model_config(backend_dir: str | Path) -> list[dict[str, Any]]:
    """Read MODEL_CONFIG from a generated backend's main.py."""
    source = (Path(backend_dir) / "main.py").read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MODEL_CONFIG"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("MODEL_CONFIG not found in generated main.py")


def collections_to_seed(
    endpoints: list[dict[str, Any]],
    resource_collections: dict[str, str],
) -> dict[str, list[str]]:
    """Return {collection: response field names} for catalog-like collections."""
    selected: dict[str, list[str]] = {}
    for item in endpoints:
        path = str(item.get("path", ""))
        if item.get("method") != "GET" or path.endswith("/{id}"):
            continue
        parts = path.strip("/").split("/")
        if len(parts) < 2:
            continue
        resource = parts[1]
        collection = resource_collections.get(resource)
        if not collection or _TRANSACTIONAL.search(resource):
            continue
        selected[collection] = list(item.get("response_fields") or [])
    return selected


def _coerce(value: Any, kind: str) -> Any:
    """Return a value of the requested type, or raise ValueError."""
    if kind == "str":
        if isinstance(value, (dict, list)):
            raise ValueError("Expected a scalar string")
        return str(value)
    if kind == "int":
        if isinstance(value, bool):
            raise ValueError("Boolean is not an integer seed value")
        return int(float(value))
    if kind == "float":
        if isinstance(value, bool):
            raise ValueError("Boolean is not a float seed value")
        return float(value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if str(value).lower() in {"true", "false"}:
            return str(value).lower() == "true"
        raise ValueError("Expected true or false")
    if kind == "list":
        if isinstance(value, list):
            return value
        raise ValueError("Expected a list")
    if kind == "dict":
        if isinstance(value, dict):
            return value
        raise ValueError("Expected a dictionary")
    return value


def clean_records(
    collection: str,
    spec: dict[str, Any],
    records: Any,
    count: int = SEED_COUNT,
) -> list[dict[str, Any]]:
    """Keep valid records with known fields and unique IDs."""
    if not isinstance(records, list):
        return []
    kinds = {field["name"]: field["type"] for field in spec["fields"]}
    cleaned: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    for index, record in enumerate(records[:count], start=1):
        if not isinstance(record, dict):
            continue
        row: dict[str, Any] = {}
        for name, value in record.items():
            kind = kinds.get(name)
            if kind is None or value is None:
                continue
            try:
                row[name] = _coerce(value, kind)
            except (ValueError, TypeError, OverflowError):
                continue

        record_id = str(row.get("id") or f"{collection[:3]}{index}")
        if record_id in used_ids:
            record_id = f"{record_id}-{index}"
        used_ids.add(record_id)
        row["id"] = record_id
        if len(row) > 1:
            cleaned.append(row)
    return cleaned


def detect_image_category(business_name: str, project_goal: str) -> str:
    """Infer a broad image category from business and project description."""
    text = f"{business_name} {project_goal}".lower()
    scores = {
        category: sum(1 for keyword in keywords if keyword in text)
        for category, keywords in CATEGORY_KEYWORDS.items()
    }
    if not scores or max(scores.values(), default=0) == 0:
        return "general"
    return max(scores, key=scores.get)


# --- generated placeholder images (used when the image library is empty) ----
_PALETTES = {
    "food": ("#f6d7a7", "#e08e45"), "travel": ("#bfe3f2", "#4a90b8"),
    "fashion": ("#f3c9d5", "#c0587a"), "tech": ("#c9d6f2", "#4a63b8"),
    "fitness": ("#cdeccd", "#4f9d55"), "general": ("#d9d9e8", "#7a7aa8"),
}
_NAME_EMOJI = (
    (("cookie", "biscuit"), "\U0001F36A"), (("muffin", "cupcake"), "\U0001F9C1"),
    (("croissant",), "\U0001F950"), (("bread", "loaf", "baguette", "sourdough"), "\U0001F35E"),
    (("cake", "brownie"), "\U0001F370"), (("tart", "pie"), "\U0001F967"),
    (("donut", "doughnut"), "\U0001F369"), (("pizza",), "\U0001F355"),
    (("burger",), "\U0001F354"), (("coffee", "latte", "espresso"), "\u2615"),
    (("salad",), "\U0001F957"), (("shoe", "sneaker"), "\U0001F45F"),
    (("phone",), "\U0001F4F1"), (("laptop",), "\U0001F4BB"),
    (("hotel", "resort"), "\U0001F3E8"), (("beach",), "\U0001F3D6"),
    (("yoga", "meditat", "mindful", "zen"), "\U0001F9D8"),
    (("candle",), "\U0001F56F\uFE0F"),
    (("manicure", "nail", "polish"), "\U0001F485"),
    (("facial", "spa", "massage", "skin"), "\U0001F486"),
    (("hair", "salon"), "\U0001F487"), (("makeup", "cosmetic"), "\U0001F484"),
)
_CATEGORY_EMOJI = {
    "food": "\U0001F370", "travel": "\u2708\uFE0F", "fashion": "\U0001F457",
    "tech": "\U0001F4BB", "fitness": "\U0001F3CB", "general": "\u2B50",
}


def _wrap_label(label: str, width: int = 26, max_lines: int = 2) -> list[str]:
    """Split a caption into at most ``max_lines`` lines, only at spaces."""
    lines: list[str] = []
    current = ""
    words = label.split()
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
    if len(lines[-1]) > width:
        lines[-1] = lines[-1][: width - 1].rstrip() + "\u2026"
    return lines[:max_lines]


def build_placeholder_svg(label: str, category: str) -> str:
    """A clean, offline product image: gradient, emoji and the item's name."""
    import html

    light, dark = _PALETTES.get(category, _PALETTES["general"])
    lowered = label.lower()
    emoji = next((e for keys, e in _NAME_EMOJI if any(k in lowered for k in keys)),
                 _CATEGORY_EMOJI.get(category, _CATEGORY_EMOJI["general"]))
    caption = _wrap_label(label)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400" '
        'width="600" height="400">'
        f'<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
        f'<stop offset="0" stop-color="{light}"/><stop offset="1" stop-color="{dark}"/>'
        '</linearGradient></defs>'
        '<rect width="600" height="400" fill="url(#g)"/>'
        '<circle cx="500" cy="60" r="90" fill="#ffffff" opacity="0.15"/>'
        '<circle cx="80" cy="360" r="110" fill="#ffffff" opacity="0.12"/>'
        f'<text x="300" y="215" font-size="130" text-anchor="middle">{emoji}</text>'
        + "".join(
            f'<text x="300" y="{318 + n * 36}" font-size="30" '
            'font-family="Georgia, serif" fill="#ffffff" text-anchor="middle">'
            f"{html.escape(line)}</text>"
            for n, line in enumerate(caption)
        )
        + "</svg>"
    )


def write_placeholder_image(
    frontend_dir: Path, label: str, category: str, slug: str,
) -> str:
    """Write the SVG into the frontend's public folder; return its URL path."""
    folder = Path(frontend_dir) / "public" / "images" / "catalog"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-") or "item"
    target = folder / f"{safe}.svg"
    target.write_text(build_placeholder_svg(label, category), encoding="utf-8")
    return f"/images/catalog/{target.name}"


def _record_label(record: dict[str, Any], fallback: str) -> str:
    for key in ("name", "title", "product_name", "productName", "label",
                "altText", "alt", "caption"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def resolve_record_images(
    records: list[dict[str, Any]],
    spec: dict[str, Any],
    *,
    business_name: str,
    project_goal: str,
    frontend_dir: Path,
) -> list[dict[str, Any]]:
    """Copy curated category images to the frontend and assign local paths.

    Product-level matching can be added later; this first version ensures that
    food projects never fall back to arbitrary travel/electronics URLs.
    """
    image_fields = image_field_names(spec)
    if not image_fields:
        return records

    category = detect_image_category(business_name, project_goal)
    source_dir = IMAGE_LIBRARY / category
    if not source_dir.is_dir():
        source_dir = IMAGE_LIBRARY / "general"

    images = sorted(
        path for path in source_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    ) if source_dir.is_dir() else []

    destination_dir = frontend_dir / "public" / "images" / "catalog"
    if images:
        destination_dir.mkdir(parents=True, exist_ok=True)

    for index, record in enumerate(records):
        if not images:
            for field in image_fields:
                value = str(record.get(field, "") or "").strip()

                # Preserve existing local image paths.
                if (
                    value.startswith("/images/")
                    and "://" not in value
                    and ".." not in value.split("/")
                ):
                    continue

                # No curated library: draw a local placeholder named after
                # the record so the UI never shows a broken image.
                label = _record_label(record, f"Item {index + 1}")
                record[field] = write_placeholder_image(
                    frontend_dir, label, category,
                    f"{category}-{index + 1}-{label}")
            continue

        source = images[index % len(images)]
        destination = destination_dir / f"{category}-{index + 1}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        local_path = f"/images/catalog/{destination.name}"
        for field in image_fields:
            record[field] = local_path

    return records



async def seed_backend_data(
    backend_dir: str | Path,
    *,
    frontend_dir: str | Path,
    business_name: str,
    project_goal: str,
    features: list[str],
    endpoints: list[dict[str, Any]],
    resource_collections: dict[str, str],
    count: int = SEED_COUNT,
) -> dict[str, int]:
    """Seed each collection independently; preserve successful collections."""
    backend_dir = Path(backend_dir)
    frontend_dir = Path(frontend_dir)

    try:
        specs = {
            spec["collection"]: spec
            for spec in load_model_config(backend_dir)
        }
        wanted = collections_to_seed(endpoints, resource_collections)
    except Exception as exc:
        print(f"WARNING: could not prepare backend seeding: {exc}")
        return {}

    written: dict[str, int] = {}

    for collection, response_fields in wanted.items():
        spec = specs.get(collection)
        data_file = backend_dir / "data" / f"{collection}.json"

        if spec is None or not data_file.is_file():
            continue

        try:
            existing = json.loads(
                data_file.read_text(encoding="utf-8") or "[]"
            )
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: cannot read {collection}.json: {exc}")
            continue

        if existing:
            print(f"Skipping {collection}: existing data will not be overwritten.")
            continue

        known = {field["name"] for field in spec["fields"]}
        target = {
            "fields": [
                {"name": field["name"], "type": field["type"]}
                for field in spec["fields"]
            ],
            "must_include": [
                name
                for name in dict.fromkeys(["id"] + response_fields)
                if name in known
            ],
            "count": count,
        }

        prompt = (
            f"BUSINESS: {business_name}\n"
            f"GOAL: {project_goal}\n"
            f"FEATURES: {', '.join(features[:10])}\n\n"
            f"GENERATE DATA FOR THIS COLLECTION ONLY: {collection}\n"
            "Return one JSON object with this collection name as its only key. "
            "Return concise, realistic records. Use exactly the supplied fields "
            "and types. Return the requested record count.\n\n"
            "COLLECTION SPECIFICATION:\n"
            + json.dumps(
                {collection: target},
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )

        try:
            response = await call_groq(
                prompt=prompt,
                system=SEED_SYSTEM_PROMPT,
                max_tokens=MAX_SEED_TOKENS,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            payload = json.loads(response)

            if not isinstance(payload, dict):
                raise ValueError("Seed response must be a JSON object")

            records = clean_records(
                collection,
                spec,
                payload.get(collection),
                count,
            )

            if not records:
                print(
                    f"WARNING: no valid records generated for {collection}; "
                    "continuing with the next collection."
                )
                continue

            records = resolve_record_images(
                records,
                spec,
                business_name=business_name,
                project_goal=project_goal,
                frontend_dir=frontend_dir,
            )

            data_file.parent.mkdir(parents=True, exist_ok=True)
            data_file.write_text(
                json.dumps(records, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            written[collection] = len(records)
            print(f"Seeded {collection}: {len(records)} records.")

        except Exception as exc:
            print(
                f"WARNING: seeding {collection} failed: {exc}. "
                "Continuing with the next collection."
            )

    return written