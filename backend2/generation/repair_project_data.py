"""Repair an already generated project's seed data.

    python -m generation.repair_project_data <project_root> [--force-images]

<project_root> is the folder that contains both ``backend`` and ``frontend``,
for example .../generated_projects_api/4c6d70279bcb/glow-studio-website-development

What it does
  * main.py MODEL_CONFIG: image/url/phone/zip-like fields typed int/float/bool
    are changed to str (the planner sometimes types ``imageUrl`` as int).
  * data/*.json: image fields that hold numbers or other non-paths get a local
    image (curated library image or a generated placeholder), exactly like
    fresh seeding does.
--force-images regenerates every image field, even ones that already
have a path. Restart uvicorn afterwards.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from generation.backend_generator import _STRING_FIELD_RE
from generation.backend_reconciler import _guess_type
from generation.seed_data import (
    image_field_names,
    load_model_config,
    resolve_record_images,
)


def _find_frontend(root: Path) -> Path:
    base = root / "frontend"
    if (base / "package.json").is_file():
        return base
    for pkg in base.glob("*/package.json"):
        return pkg.parent
    raise SystemExit(f"No frontend with package.json under {base}")


def repair(root: Path, force_images: bool = False) -> None:
    backend = root / "backend"
    frontend = _find_frontend(root)
    main_py = backend / "main.py"
    source = main_py.read_text(encoding="utf-8")

    changed_fields: list[str] = []

    def retype(match: re.Match) -> str:
        name = match.group(2)
        # imageUrl, message, page, language ... are text, never numbers.
        if _STRING_FIELD_RE.search(name) or _guess_type(name) == "string":
            changed_fields.append(name)
            return f"{match.group(1)}'str'"
        return match.group(0)

    new_source = re.sub(
        r"('name':\s*'([^']+)',\s*'type':\s*)'(?:int|float|bool)'",
        retype,
        source,
    )
    if new_source != source:
        main_py.write_text(new_source, encoding="utf-8")
    print(f"MODEL_CONFIG fields retyped to str: {sorted(set(changed_fields)) or 'none'}")

    business = root.name.replace("-", " ")
    for spec in load_model_config(backend):
        data_file = backend / "data" / f"{spec['collection']}.json"
        if not data_file.is_file():
            continue
        records = json.loads(data_file.read_text(encoding="utf-8"))
        image_names = image_field_names({
            **spec,
            "fields": [
                {
                    "name": f["name"],
                    "type": "str" if _STRING_FIELD_RE.search(f["name"]) else f["type"],
                }
                for f in spec["fields"]
            ],
        })
        if not image_names:
            continue
        for record in records:
            for name in image_names:
                value = record.get(name)
                keep = isinstance(value, str) and value.startswith(("/images/", "/uploads/"))
                if force_images or not keep:
                    record[name] = ""
        spec_for_seed = {
            "collection": spec["collection"],
            "fields": [
                {"name": f["name"], "type": "str" if f["name"] in image_names else f["type"]}
                for f in spec["fields"]
            ]
        }
        records = resolve_record_images(
            records, spec_for_seed,
            business_name=business, project_goal="", frontend_dir=frontend,
        )
        data_file.write_text(
            json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Repaired images in {data_file.name}: {len(records)} records")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(
            "usage: python -m generation.repair_project_data <project_root> "
            "[--force-images]"
        )
        raise SystemExit(2)
    repair(Path(args[0]).resolve(), force_images="--force-images" in sys.argv)