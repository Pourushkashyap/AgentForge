import pprint
import json
import keyword
import re
import shutil
from pathlib import Path

from planning.backend_schemas import BackendPlan
from generation.api_endpoints import normalize_action_endpoint

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



def build_auth_config(endpoints):
    """Detect whether the backend plan requires authentication."""

    auth_routes = {}

    for endpoint in endpoints:
        data = (
            endpoint
            if isinstance(endpoint, dict)
            else endpoint.model_dump()
        )

        method = str(data.get("method", "")).upper()
        path = str(data.get("path", "")).strip()
        endpoint_type = data.get("endpoint_type")

        action = normalize_action_endpoint(
            method,
            path,
            endpoint_type,
        )

        if action:
            _, canonical_path, inferred_type = action
            auth_routes[(method, canonical_path)] = inferred_type

    has_registration = any(
        endpoint_type == "registration"
        for endpoint_type in auth_routes.values()
    )

    has_login = any(
        endpoint_type == "authentication"
        and method == "POST"
        and path in {
            "/api/auth/login",
            "/api/users/login",
        }
        for (method, path), endpoint_type in auth_routes.items()
    )

    if has_registration != has_login:
        raise ValueError(
            "Authentication requires both registration and login "
            "endpoints. Add POST /api/auth/register and "
            "POST /api/auth/login to the backend plan."
        )

    return {
        "enabled": has_registration and has_login,
        "routes": auth_routes,
    }



def build_auth_routes(auth_config):
    """Generate secure registration, login and current-user routes."""

    if not auth_config["enabled"]:
        return ""

    routes = auth_config["routes"]

    register_path = next(
        path for (method, path), kind in routes.items()
        if method == "POST" and kind == "registration"
    )

    login_path = next(
        path for (method, path), kind in routes.items()
        if method == "POST"
        and kind == "authentication"
        and path in {"/api/auth/login", "/api/users/login"}
    )

    me_path = "/api/auth/me"

    return f'''
class RegisterInput(BaseModel):
    name: str = Field(default="User", min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


password_hasher = PasswordHasher()
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")

if not JWT_SECRET_KEY:
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable is required."
    )

JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = int(
    os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "60")
)


def public_user(user):
    return {{
        "id": user["id"],
        "name": user.get("name", "User"),
        "email": user["email"],
        "role": user.get("role", "user"),
    }}


def create_access_token(user):
    now = datetime.now(timezone.utc)
    payload = {{
        "sub": str(user["id"]),
        "iat": now,
        "exp": now + timedelta(
            minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        ),
    }}
    return jwt.encode(
        payload,
        JWT_SECRET_KEY,
        algorithm=JWT_ALGORITHM,
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="Authentication required",
            headers={{"WWW-Authenticate": "Bearer"}},
        )

    try:
        payload = jwt.decode(
            credentials.credentials,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM],
            options={{"require": ["sub", "exp"]}},
        )
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("Missing subject")
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired access token",
            headers={{"WWW-Authenticate": "Bearer"}},
        )

    user = store.get_record("users", str(user_id))
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="User no longer exists",
        )

    return user


@app.post({json.dumps(register_path)}, status_code=201)
def register_user(payload: RegisterInput):
    email = payload.email.strip().lower()

    if not re.fullmatch(r"[^@\\s]+@[^@\\s]+\\.[^@\\s]+", email):
        raise HTTPException(status_code=422, detail="Invalid email address")

    user = {{
        "id": str(uuid.uuid4()),
        "name": payload.name.strip() or "User",
        "email": email,
        "password_hash": password_hasher.hash(payload.password),
        "role": "user",
    }}

    try:
        created = store.create_record_unique(
            "users",
            user,
            unique_fields=["email"],
        )
    except ValueError:
        raise HTTPException(
            status_code=409,
            detail="An account with this email already exists",
        )

    return public_user(created)


@app.post({json.dumps(login_path)})
def login_user(payload: LoginInput):
    email = payload.email.strip().lower()

    user = next(
        (
            item for item in store.list_records("users")
            if item.get("email", "").strip().lower() == email
        ),
        None,
    )

    if user is None:
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    try:
        password_hasher.verify(
            user["password_hash"],
            payload.password,
        )
    except (VerifyMismatchError, InvalidHash):
        raise HTTPException(
            status_code=401,
            detail="Invalid email or password",
        )

    if password_hasher.check_needs_rehash(user["password_hash"]):
        user["password_hash"] = password_hasher.hash(payload.password)
        store.update_record("users", user["id"], {{
            "password_hash": user["password_hash"]
        }})

    return {{
        "access_token": create_access_token(user),
        "token_type": "bearer",
        "expires_in": JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": public_user(user),
    }}


@app.get({json.dumps(me_path)})
def get_me(current_user: dict = Depends(get_current_user)):
    return public_user(current_user)
@app.post("/api/auth/logout")
def logout_user():
    return {{"message": "Logged out. Clear the access token on the client."}}    
'''



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
        
        action = normalize_action_endpoint(
            method,
            path,
            data.get("endpoint_type"),
        )

        if action:
            # Authentication handlers are generated separately.
            continue

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

    auth_config = build_auth_config(plan.endpoints)
    auth_enabled = auth_config["enabled"]

    if auth_enabled:
        # Authentication always uses the dedicated users collection.
        if "users" not in used_collections:
            used_collections.add("users")

            models.append({
                "name": "User",
                "collection": "users",
                "fields": [
                    {"name": "id", "type": "str", "required": False},
                    {"name": "name", "type": "str", "required": False},
                    {"name": "email", "type": "str", "required": True},
                    {"name": "password_hash", "type": "str", "required": True},
                    {"name": "role", "type": "str", "required": False},
                ],
            })

        users_file = data_dir / "users.json"
        if not users_file.exists():
            users_file.write_text("[]\n", encoding="utf-8")
    
    
    generated_main = r'''
import uuid
from pathlib import Path
__AUTH_IMPORTS__
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

__AUTH_ROUTES__

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

    auth_imports = ""
    auth_routes = ""

    if auth_enabled:
        auth_imports = """
import os
import re
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

bearer_scheme = HTTPBearer(auto_error=False)
"""

        auth_routes = build_auth_routes(auth_config)

    generated_main = generated_main.replace(
        "__AUTH_IMPORTS__",
        auth_imports,
    )

    generated_main = generated_main.replace(
        "__AUTH_ROUTES__",
        auth_routes,
    )

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

    requirements = [
        "fastapi",
        "uvicorn[standard]",
        "python-multipart",
    ]

    if auth_enabled:
        requirements.extend([
            "argon2-cffi",
            "PyJWT",
        ])

    (project_dir / "requirements.txt").write_text(
        "\n".join(requirements) + "\n",
        encoding="utf-8",
    )

    if auth_enabled:
        (project_dir / ".env.example").write_text(
            "JWT_SECRET_KEY=replace-with-a-long-random-secret\n"
            "JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60\n",
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

    if auth_enabled:
        readme += """
## Authentication

Authentication is enabled for this project.

Set these environment variables before starting the server:

```powershell
$env:JWT_SECRET_KEY = "use-a-random-secret-of-at-least-32-characters"
$env:JWT_ACCESS_TOKEN_EXPIRE_MINUTES = "60"
python -m uvicorn main:app --reload
```

- Passwords are hashed using Argon2.
- Access tokens are signed JWTs.
- Protected routes require `Authorization: Bearer <token>`.
- Password hashes are never returned by auth endpoints.
- Logout is stateless; the client must discard its token.
"""
    else:
        readme += "\nAuthentication is not enabled for this project.\n"

    readme += """
## Storage note

JSON storage is suitable for local development and single-process use.
Use a transactional database for multi-worker or multi-instance production.
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
        "authentication": {
            "enabled": auth_enabled,
            "user_collection": "users" if auth_enabled else None,
            "token_type": "bearer" if auth_enabled else None,
        },
    }


    (project_dir / "generation_manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return project_dir
