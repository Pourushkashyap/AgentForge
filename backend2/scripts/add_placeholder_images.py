"""Give an already-generated project working images.

Usage (from backend2):
  python scripts/add_placeholder_images.py <project_dir>
where <project_dir> contains backend/ and frontend/<slug>/.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generation.seed_data import (  # noqa: E402
    IMAGE_FIELD_PATTERN, _record_label, detect_image_category,
    write_placeholder_image,
)


def main(project_dir: str) -> None:
    root = Path(project_dir).resolve()
    frontends = [p for p in (root / "frontend").iterdir() if (p / "package.json").is_file()]
    if not frontends or not (root / "backend" / "data").is_dir():
        raise SystemExit("Expected <project>/backend/data and <project>/frontend/<slug>/")
    frontend = frontends[0]
    category = detect_image_category(root.name.replace("-", " "), "")
    updated = 0
    for data_file in sorted((root / "backend" / "data").glob("*.json")):
        records = json.loads(data_file.read_text(encoding="utf-8") or "[]")
        if not isinstance(records, list):
            continue
        changed = False
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            for field in [k for k in record if IMAGE_FIELD_PATTERN.search(k)]:
                if isinstance(record[field], str) and not record[field].startswith("/images/"):
                    label = _record_label(record, f"Item {index + 1}")
                    record[field] = write_placeholder_image(
                        frontend, label, category, f"{data_file.stem}-{index + 1}-{label}")
                    changed = True
                    updated += 1
        if changed:
            data_file.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    print(f"Updated {updated} image field(s). Restart the backend and refresh the page.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
