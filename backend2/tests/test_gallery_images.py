from pathlib import Path

from generation.seed_data import image_field_names, resolve_record_images


def _spec(collection, fields):
    return {"collection": collection, "fields": [{"name": n, "type": t} for n, t in fields]}


def test_url_field_in_gallery_collection_is_an_image_field():
    spec = _spec("gallery", [("id", "str"), ("url", "str"), ("altText", "str")])
    assert image_field_names(spec) == ["url"]


def test_url_field_in_other_collections_is_not_an_image_field():
    spec = _spec("resource_links", [("id", "str"), ("url", "str")])
    assert image_field_names(spec) == []


def test_empty_gallery_url_gets_a_local_image(tmp_path: Path):
    spec = _spec("gallery", [("id", "str"), ("url", "str"), ("altText", "str")])
    records = [{"id": "g1", "url": "", "altText": "Studio"}, {"id": "g2", "url": "", "altText": "Mats"}]
    out = resolve_record_images(
        records, spec, business_name="Zen Flow", project_goal="yoga studio", frontend_dir=tmp_path
    )
    assert all(r["url"].startswith("/images/catalog/") for r in out)
    for r in out:
        assert (tmp_path / "public" / r["url"].lstrip("/")).is_file()
