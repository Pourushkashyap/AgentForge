import pprint
import json
import keyword
import re
import shutil
from pathlib import Path

from planning.backend_schemas import BackendPlan


_STRING_FIELD_RE = re.compile(
    r"^(?:image|img|photo|picture|thumbnail|banner|cover|avatar|logo|icon)"
    r"(?:_?(?:url|src|path|link))?$"
    r"|(?:url|uri|link|phone|zip|zipcode|postal|postalcode|pincode)$",
    re.IGNORECASE,
)


def safe_name(value: str, fallback: str = "item") -> str:
    name = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip().lower())
    name = re.sub(r"_+", "_", name).strip("_")

    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"item_{name}"
    if keyword.iskeyword(name):
        name += "_item"

    return name




def safe_field_name(value: str, fallback: str = "field") -> str:
    """Like safe_name, but keeps casing so JSON keys match the frontend."""
    name = re.sub(r"[^a-zA-Z0-9_]", "_", value.strip())
    name = re.sub(r"_+", "_", name).strip("_")

    if not name:
        name = fallback
    if name[0].isdigit():
        name = f"field_{name}"
    if keyword.iskeyword(name):
        name += "_field"

    return name


def build_planned_routes(endpoints, models):
    """Build CRUD handlers and normalize API paths to /api/..."""
    lines = []
    used_routes = set()

    
    def resolve_collection(resource: str):
        resource = resource.lower().replace("-", "_")

        for model in models:
            collection = model["collection"].lower()
            name = model["name"].lower()

            candidates = {
                collection,
                name,
                collection + "s",
                collection + "es",
                name + "s",
                name + "es",
            }

            # Common singular/plural forms.
            for value in (collection, name):
                if value.endswith("y"):
                    candidates.add(value[:-1] + "ies")
                if value.endswith("message"):
                    candidates.add(value + "s")
                if value.endswith("item"):
                    candidates.add(value + "s")

            # Allow resource aliases such as /contact -> ContactMessage.
            aliases = {
                "contact": {"contactmessage", "contactmessages"},
                "contacts": {"contactmessage", "contactmessages"},
            }

            if resource in candidates:
                return model["collection"]

            if resource in aliases and name in aliases[resource]:
                return model["collection"]

        return None

    for endpoint in endpoints:
        data = endpoint if isinstance(endpoint, dict) else endpoint.model_dump()
        method = str(data.get("method", "")).upper()
        path = str(data.get("path", "")).strip().rstrip("/")

        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError(f"Unsupported endpoint method: {method} {path}")

        if not path or "?" in path or path.startswith("//"):
            raise ValueError(f"Invalid endpoint path: {path}")

        # Normalize /products -> /api/products.
        if not path.startswith("/"):
            path = "/" + path
        if not path.startswith("/api/") and path != "/api":
            path = "/api" + path

        parts = [part for part in path.strip("/").split("/") if part]

        if len(parts) not in {2, 3} or parts[0] != "api":
            raise ValueError(
                f"Unsupported CRUD path after normalization: {method} {path}"
            )

        
        # Health checks are infrastructure routes, not CRUD endpoints.
        if path == "/api/health":
            if method != "GET":
                raise ValueError(
                    f"Health endpoint only supports GET: {method} {path}"
                )

            lines.extend([
                '@app.get("/api/health")',
                'def health_check():',
                '    return {"status": "ok"}',
                "",
            ])
            continue

        collection = resolve_collection(parts[1])
        if collection is None:
            raise ValueError(
                f"No planned model matches endpoint: {method} {path}. "
                f"Available models: {[m['name'] for m in models]}"
            )


        is_item = len(parts) == 3
        parameter = None

        if is_item:
            match = re.fullmatch(
                r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", parts[2]
            )
            if not match:
                raise ValueError(
                    f"Expected a path parameter such as {{id}}: {path}"
                )
            parameter = match.group(1)

        if method == "POST" and is_item:
            raise ValueError(f"POST item endpoints need custom logic: {path}")

        if method in {"PUT", "PATCH", "DELETE"} and not is_item:
            raise ValueError(f"{method} requires an item ID: {path}")

        # Use normalized paths consistently in generated FastAPI routes.
        path = "/" + "/".join(parts)
        route_key = (method, path)

        if route_key in used_routes:
            raise ValueError(f"Duplicate endpoint: {method} {path}")
        used_routes.add(route_key)

        function_name = safe_name(
            f"{method}_{collection}_{'item' if is_item else 'collection'}"
        )
        function_name += f"_{len(lines)}"
        quoted_collection = json.dumps(collection)

        decorator = f"@app.{method.lower()}({json.dumps(path)}"
        if method == "POST":
            decorator += ", status_code=201"
        decorator += ")"
        lines.append(decorator)

        if method == "GET" and not is_item:
            lines.extend([
                f"def {function_name}():",
                f"    return store.list_records({quoted_collection})",
                "",
            ])

        elif method == "POST":
            lines.extend([
                f"def {function_name}(payload: dict):",
                f"    record = validate_payload({quoted_collection}, payload)",
                '    record["id"] = record.get("id") or str(uuid.uuid4())',
                "    try:",
                f"        return store.create_record({quoted_collection}, record)",
                "    except ValueError as exc:",
                "        raise HTTPException(status_code=409, detail=str(exc)) from exc",
                "",
            ])

        elif method == "GET" and is_item:
            lines.extend([
                f"def {function_name}({parameter}: str):",
                f"    record = store.get_record({quoted_collection}, {parameter})",
                "    if record is None:",
                '        raise HTTPException(status_code=404, detail="Record not found")',
                "    return record",
                "",
            ])

        elif method in {"PUT", "PATCH"}:
            lines.extend([
                f"def {function_name}({parameter}: str, payload: dict):",
                f"    updates = validate_payload({quoted_collection}, payload)",
                f'    if updates.get("id") not in (None, {parameter}):',
                '        raise HTTPException(status_code=400, detail="Record ID cannot be changed")',
                '    updates.pop("id", None)',
                f"    updated = store.update_record({quoted_collection}, {parameter}, updates)",
                "    if updated is None:",
                '        raise HTTPException(status_code=404, detail="Record not found")',
                "    return updated",
                "",
            ])

        elif method == "DELETE":
            lines.extend([
                f"def {function_name}({parameter}: str):",
                f"    deleted = store.delete_record({quoted_collection}, {parameter})",
                "    if not deleted:",
                '        raise HTTPException(status_code=404, detail="Record not found")',
                f'    return {{"deleted": True, "id": {parameter}}}',
                "",
            ])

    return "\n".join(lines)



def generate_backend_project(
    plan: BackendPlan,
    output_dir: str | Path,
) -> Path:
    """Generate a FastAPI backend with model-driven JSON CRUD APIs."""

    project_dir = Path(output_dir).resolve()
    data_dir = project_dir / "data"
    storage_dir = project_dir / "storage"
    upload_dir = project_dir / "uploads"

    for directory in (project_dir, data_dir, storage_dir, upload_dir):
        directory.mkdir(parents=True, exist_ok=True)

    source_store = (
        Path(__file__).resolve().parent.parent
        / "storage"
        / "json_store.py"
    )
    if not source_store.is_file():
        raise FileNotFoundError(
            f"JSON storage implementation not found: {source_store}"
        )

    shutil.copy2(source_store, storage_dir / "json_store.py")
    (storage_dir / "__init__.py").write_text("", encoding="utf-8")

    type_map = {
    "str": "str",
    "string": "str",
    "text": "str",
    "email": "str",
    "url": "str",
    "image": "str",
    "uuid": "str",
    "int": "int",
    "integer": "int",
    "float": "float",
    "number": "float",
    "decimal": "float",
    "bool": "bool",
    "boolean": "bool",
    "datetime": "str",
    "date": "str",
    "list": "list",
    "array": "list",
    "dict": "dict",
    "object": "dict",
    "json": "dict",
}
    
    
    models = []
    used_collections = set()

    for model in plan.database_models:
        collection = safe_name(model.name)

        if collection in used_collections:
            raise ValueError(
                f"Duplicate model collection name: {model.name}"
            )
        used_collections.add(collection)

        fields = []
        used_fields = set()

        for field in model.fields:
            name = safe_field_name(field.name)
            if name in used_fields:
                raise ValueError(
                    f"Duplicate field {name!r} in model {model.name!r}"
                )
            used_fields.add(name)

            python_type = type_map.get(field.data_type.strip().lower())
            if python_type is None:
                raise ValueError(
                    f"Unsupported data type {field.data_type!r} "
                    f"in model {model.name!r}"
                )

            # The planner sometimes types imageUrl/phone/zip as int, which
            # makes seeding and uploads store numbers like 101 instead of
            # paths. Fields with these names are always strings.
            if python_type in ("int", "float", "bool") and _STRING_FIELD_RE.search(name):
                python_type = "str"

            fields.append({
                "name": name,
                "type": python_type,
                "required": field.required,
            })

        # JSONStore uses the id field to identify records.
        if "id" not in used_fields:
            fields.insert(0, {
                "name": "id",
                "type": "str",
                "required": False,
            })
        else:
            # IDs can be generated automatically on creation.
            for field in fields:
                if field["name"] == "id":
                    field["required"] = False

        models.append({
            "name": model.name,
            "collection": collection,
            "fields": fields,
        })

        collection_file = data_dir / f"{collection}.json"
        if not collection_file.exists():
            collection_file.write_text("[]\n", encoding="utf-8")

    generated_main = r'''
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles
from pydantic import ConfigDict, create_model

from storage.json_store import JSONStore

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = BASE_DIR / "uploads"
store = JSONStore(DATA_DIR)

app = FastAPI(title=__PROJECT_TITLE__)
MODEL_CONFIG = __MODEL_CONFIG__

TYPE_MAP = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}

MODEL_SCHEMAS = {}
COLLECTIONS = {
    spec["collection"]: spec
    for spec in MODEL_CONFIG
}

for spec in MODEL_CONFIG:
    definitions = {}

    for field in spec["fields"]:
        annotation = TYPE_MAP[field["type"]]
        required = field["required"]

        if field["name"] == "id":
            definitions["id"] = (str | None, None)
        elif required:
            definitions[field["name"]] = (annotation, ...)
        else:
            definitions[field["name"]] = (annotation | None, None)

    MODEL_SCHEMAS[spec["collection"]] = create_model(
        spec["collection"].title() + "Input",
        __config__=ConfigDict(extra="allow"),
        **definitions,
    )


def get_schema(collection: str):
    if collection not in MODEL_SCHEMAS:
        raise HTTPException(status_code=404, detail="Unknown collection")
    return MODEL_SCHEMAS[collection]


def validate_payload(collection: str, payload: dict) -> dict:
    schema = get_schema(collection)

    try:
        validated = schema.model_validate(payload)
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return validated.model_dump(exclude_unset=True)


@app.get("/health")
def health():
    return {"status": "ok", "storage": "json"}


__PLANNED_ROUTES__


@app.post("/api/uploads", status_code=201)
async def upload_image(file: UploadFile = File(...)):
    allowed = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    suffix = Path(file.filename or "").suffix.lower()

    if suffix not in allowed:
        raise HTTPException(status_code=400, detail="Unsupported image format")

    content = await file.read(5 * 1024 * 1024 + 1)

    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image exceeds 5 MB limit")
    if not content:
        raise HTTPException(status_code=400, detail="Empty image")

    filename = f"{uuid.uuid4().hex}{suffix}"
    (UPLOAD_DIR / filename).write_bytes(content)

    return {"image_url": f"/uploads/{filename}"}


app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
'''

    generated_main = generated_main.replace(
        "__PROJECT_TITLE__",
        json.dumps(plan.project_name, ensure_ascii=False),
    )
    generated_main = generated_main.replace(
        "__MODEL_CONFIG__",
        pprint.pformat(models, sort_dicts=False),
    )
    generated_main = generated_main.replace(
        "__PLANNED_ROUTES__",
        build_planned_routes(plan.endpoints, models),
    )

    (project_dir / "main.py").write_text(
        generated_main,
        encoding="utf-8",
    )

    (project_dir / "requirements.txt").write_text(
        "fastapi\nuvicorn[standard]\npython-multipart\n",
        encoding="utf-8",
    )

    readme = f"""# {plan.project_name}

Generated FastAPI backend with JSON file storage.

## Run

```powershell
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload
```

Open http://127.0.0.1:8000/docs.

- Each planned model has a JSON collection in `data/`.
- CRUD APIs are available at `/api/{{collection}}`.
- Images can be uploaded through `POST /api/uploads`.
- Uploaded files are stored in `uploads/`.
- No external database server is required.

Image upload accepts common image extensions and files up to 5 MB.
For production, add authentication, authorization, content validation,
and multi-process-safe storage.
"""

    (project_dir / "README.md").write_text(readme, encoding="utf-8")

    
    manifest = {
        "business_name": plan.business_name,
        "project_name": plan.project_name,
        "storage_type": "json",
        "storage_path": "data/",
        "collections": sorted(used_collections),
        "models": models,
        
        
        "endpoints": [
            {
                "name": data.get("name", ""),
                "method": str(data.get("method", "GET")).upper(),
                "path": data.get("path", ""),
                "purpose": data.get("purpose", ""),
                "authentication_required": data.get("authentication_required", False),
                "request_fields": data.get("request_fields", []),
                "response_fields": data.get("response_fields", []),
            }
            for data in [
                endpoint if isinstance(endpoint, dict) else endpoint.model_dump()
                for endpoint in plan.endpoints
            ]
        ],
        "image_upload": {
            "enabled": True,
            "endpoint": "/api/uploads",
            "directory": "uploads/",
        },
    }


    (project_dir / "generation_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return project_dir
