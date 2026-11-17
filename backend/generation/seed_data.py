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

from generation.placeholder_art import CATEGORY_KEYWORDS  # noqa: E402  (hospitality, education, ...)

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
    from generation.placeholder_art import detect_category

    return detect_category(f"{business_name} {project_goal}")


def build_placeholder_svg(label: str, category: str, variant: int | None = None) -> str:
    """An offline picture: themed gradient, an emoji that fits the item, a readable caption."""
    from generation.placeholder_art import build_svg

    return build_svg(label, category, variant)


def write_placeholder_image(
    frontend_dir: Path, label: str, category: str, slug: str,
) -> str:
    """Write the SVG into the frontend's public folder; return its URL path."""
    from generation.placeholder_art import _stable

    folder = Path(frontend_dir) / "public" / "images" / "catalog"
    folder.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^a-z0-9]+", "-", slug.lower()).strip("-") or "item"
    target = folder / f"{safe}.svg"
    # the slug contains the record's position, so two items with the same name still differ
    target.write_text(build_placeholder_svg(label, category, _stable(slug)), encoding="utf-8")
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