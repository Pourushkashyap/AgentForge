from pathlib import Path

from generation.connected_runtime import _expected_json_type
from workflow.debugger import _files_from_runtime_error
from workflow.validators import check_backend_static


def test_singleton_endpoints_expect_objects():
    assert _expected_json_type({}, "/health") == "object"
    assert _expected_json_type({}, "/auth/me") == "object"


def test_collection_endpoints_default_to_lists():
    assert _expected_json_type({}, "/api/hotels") == "list"


def test_explicit_response_type_overrides_default():
    assert _expected_json_type({"response_type": "object"}, "/api/hotels") == "object"
    assert _expected_json_type({"response_type": "any"}, "/api/hotels") == "any"


def test_runtime_error_maps_identifier_not_generic_map(tmp_path):
    src = tmp_path / "src" / "pages"
    src.mkdir(parents=True)
    (src / "Hotels.jsx").write_text(
        "const hotels = data.hotels; return hotels.map(x => x.name);",
        encoding="utf-8",
    )
    (src / "Products.jsx").write_text(
        "const products = data.products; return products.map(x => x.name);",
        encoding="utf-8",
    )
    found = _files_from_runtime_error(
        "hotels.map is not a function", tmp_path, []
    )
    assert found == [(src / "Hotels.jsx").resolve()]


def test_runtime_error_reading_map_does_not_grep_every_map(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "A.jsx").write_text("items.map(x => x)", encoding="utf-8")
    assert _files_from_runtime_error(
        "Cannot read properties of undefined (reading 'map')", tmp_path, []
    ) == []


def test_health_comment_does_not_satisfy_health_route(tmp_path):
    backend = tmp_path / "backend"
    (backend / "data").mkdir(parents=True)
    (backend / "main.py").write_text(
        'from fastapi import FastAPI\napp = FastAPI()\n# TODO add /health\n',
        encoding="utf-8",
    )
    (backend / "requirements.txt").write_text(
        "fastapi\nuvicorn\n", encoding="utf-8"
    )
    (backend / "storage").mkdir()
    (backend / "storage" / "json_store.py").write_text("", encoding="utf-8")
    issues = check_backend_static(backend)
    assert any(issue["code"] == "missing_health" for issue in issues)
