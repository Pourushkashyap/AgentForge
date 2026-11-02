
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


class JSONStore:
    """Simple file-based storage using one JSON file per collection."""

    def __init__(self, data_dir: str | Path = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _file_path(self, collection: str) -> Path:
        # Prevent path traversal and unexpected file access.
        if (
            not collection
            or collection in {".", ".."}
            or not collection.replace("_", "").isalnum()
        ):
            raise ValueError("Invalid collection name.")

        return self.data_dir / f"{collection}.json"

    def _read(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []

        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid JSON in storage file: {path.name}"
            ) from exc

        if not isinstance(data, list):
            raise ValueError(
                f"Storage file must contain a JSON list: {path.name}"
            )

        if not all(isinstance(item, dict) for item in data):
            raise ValueError(
                f"Every record must be a JSON object: {path.name}"
            )

        return data

    def _write(self, path: Path, records: list[dict[str, Any]]) -> None:
        # Write to a temporary file, then replace the original.
        # This helps avoid partially written JSON files.
        temp_path = None

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                suffix=".tmp",
                delete=False,
            ) as file:
                temp_path = Path(file.name)
                json.dump(records, file, indent=2, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())

            os.replace(temp_path, path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()

    def list_records(self, collection: str) -> list[dict[str, Any]]:
        path = self._file_path(collection)

        with self._lock:
            return self._read(path)

    def get_record(
        self, collection: str, record_id: str
    ) -> dict[str, Any] | None:
        return next(
            (
                record
                for record in self.list_records(collection)
                if str(record.get("id")) == str(record_id)
            ),
            None,
        )

    def create_record(
        self, collection: str, record: dict[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise ValueError("Record must be a dictionary.")

        if not record.get("id"):
            raise ValueError("Record must contain a non-empty id.")

        path = self._file_path(collection)

        with self._lock:
            records = self._read(path)

            if any(str(item.get("id")) == str(record["id"]) for item in records):
                raise ValueError(
                    f"Record with id {record['id']} already exists."
                )

            records.append(record)
            self._write(path, records)

        return record

    def update_record(
        self,
        collection: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not isinstance(updates, dict):
            raise ValueError("Updates must be a dictionary.")

        if "id" in updates and str(updates["id"]) != str(record_id):
            raise ValueError("Record ID cannot be changed.")

        path = self._file_path(collection)

        with self._lock:
            records = self._read(path)

            for record in records:
                if str(record.get("id")) == str(record_id):
                    record.update(updates)
                    self._write(path, records)
                    return record

        return None

    def delete_record(self, collection: str, record_id: str) -> bool:
        path = self._file_path(collection)

        with self._lock:
            records = self._read(path)
            updated = [
                record
                for record in records
                if str(record.get("id")) != str(record_id)
            ]

            if len(updated) == len(records):
                return False

            self._write(path, updated)

        return True
