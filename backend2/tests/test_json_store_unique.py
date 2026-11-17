
import pytest

from storage.json_store import JSONStore


def test_create_record_unique_success(tmp_path):
    store = JSONStore(tmp_path)

    result = store.create_record_unique(
        "users",
        {"id": "1", "email": "test@example.com"},
        ["email"],
    )

    assert result["id"] == "1"


def test_duplicate_email_is_rejected(tmp_path):
    store = JSONStore(tmp_path)

    store.create_record_unique(
        "users",
        {"id": "1", "email": "test@example.com"},
        ["email"],
    )

    with pytest.raises(ValueError, match="email"):
        store.create_record_unique(
            "users",
            {"id": "2", "email": "TEST@example.com"},
            ["email"],
        )


def test_different_emails_are_allowed(tmp_path):
    store = JSONStore(tmp_path)

    store.create_record_unique(
        "users",
        {"id": "1", "email": "one@example.com"},
        ["email"],
    )

    store.create_record_unique(
        "users",
        {"id": "2", "email": "two@example.com"},
        ["email"],
    )

    assert len(store.list_records("users")) == 2


def test_duplicate_id_is_rejected(tmp_path):
    store = JSONStore(tmp_path)

    store.create_record_unique(
        "users",
        {"id": "1", "email": "one@example.com"},
        ["email"],
    )

    with pytest.raises(ValueError, match="id"):
        store.create_record_unique(
            "users",
            {"id": "1", "email": "two@example.com"},
            ["email"],
        )
