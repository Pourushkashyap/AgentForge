
import json

import pytest

from storage.json_store import JSONStore


def test_create_and_read_record(tmp_path):
    store = JSONStore(tmp_path)

    store.create_record(
        "products",
        {"id": "p1", "name": "Chocolate Cake", "price": 450},
    )

    assert store.get_record("products", "p1") == {
        "id": "p1",
        "name": "Chocolate Cake",
        "price": 450,
    }

    assert (tmp_path / "products.json").exists()


def test_missing_collection_returns_empty_list(tmp_path):
    store = JSONStore(tmp_path)

    assert store.list_records("products") == []


def test_duplicate_id_is_rejected(tmp_path):
    store = JSONStore(tmp_path)
    store.create_record("products", {"id": "p1", "name": "Cake"})

    with pytest.raises(ValueError, match="already exists"):
        store.create_record("products", {"id": "p1", "name": "Bread"})


def test_update_record(tmp_path):
    store = JSONStore(tmp_path)
    store.create_record("products", {"id": "p1", "name": "Cake"})

    updated = store.update_record(
        "products", "p1", {"name": "Chocolate Cake"}
    )

    assert updated["name"] == "Chocolate Cake"


def test_delete_record(tmp_path):
    store = JSONStore(tmp_path)
    store.create_record("products", {"id": "p1", "name": "Cake"})

    assert store.delete_record("products", "p1") is True
    assert store.get_record("products", "p1") is None
    assert store.delete_record("products", "p1") is False


def test_invalid_json_is_not_silently_overwritten(tmp_path):
    store = JSONStore(tmp_path)
    path = tmp_path / "products.json"
    path.write_text("{invalid json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON"):
        store.create_record("products", {"id": "p1", "name": "Cake"})

    assert path.read_text(encoding="utf-8") == "{invalid json"


def test_invalid_collection_name_is_rejected(tmp_path):
    store = JSONStore(tmp_path)

    with pytest.raises(ValueError, match="Invalid collection"):
        store.list_records("../secrets")


def test_file_contains_valid_json(tmp_path):
    store = JSONStore(tmp_path)
    store.create_record("products", {"id": "p1", "name": "Bread"})

    with (tmp_path / "products.json").open(encoding="utf-8") as file:
        assert json.load(file) == [{"id": "p1", "name": "Bread"}]
