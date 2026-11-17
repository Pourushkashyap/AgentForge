"""Pictures: every card its own, fitting placeholder (a hotel used to show one aeroplane everywhere)."""
import asyncio
import json
import xml.etree.ElementTree as ET

import pytest

from generation import placeholder_art as pa
from workflow import flow_checks as fc
from workflow import revise

HOTEL = ["Cozy double room with modern decor", "Infinity pool at sunset", "Rooftop restaurant",
         "Spa and wellness", "Lobby", "Deluxe suite", "Garden terrace", "Welcome drink"]


def emoji_of(svg):
    return svg.split('font-family="Segoe UI Emoji, Apple Color Emoji, Noto Color Emoji, sans-serif">')[1].split("</text>")[0]


def test_a_hotel_is_hospitality_not_travel():
    assert pa.detect_category("StayWell hotel booking website with rooms and a gallery") == "hospitality"
    assert pa.detect_category("LearnLoop tutoring platform for students") == "education"
    assert pa.detect_category("DriveEasy car rental website") == "automotive"
    assert pa.detect_category("PawHome pet adoption") == "pets"
    assert pa.detect_category("something unrelated xyz") == "general"


def test_the_cards_of_one_hotel_do_not_all_look_the_same():
    svgs = [pa.build_svg(name, "hospitality") for name in HOTEL]
    assert len({emoji_of(s) for s in svgs}) >= 5
    assert len({s.split('stop-color="')[2].split('"')[0] for s in svgs}) >= 3
    assert not any("✈" in s for s in svgs)                   # the old aeroplane
    assert emoji_of(pa.build_svg("Infinity pool", "hospitality")) == "🏊"


def test_a_picture_never_changes_between_runs():
    assert pa.build_svg("Lobby", "hospitality") == pa.build_svg("Lobby", "hospitality")
    assert pa.build_svg("Lobby", "hospitality", 1) != pa.build_svg("Lobby", "hospitality", 2)


def test_svg_is_valid_xml_for_any_caption():
    for label in ['Tom & Jerry <script>"x"</script>', "x" * 200, "", "Café Crème 🍰"]:
        ET.fromstring(pa.build_svg(label, "food"))


def test_every_category_has_a_theme_so_nothing_falls_back_to_a_plain_star():
    for category in pa.CATEGORY_KEYWORDS:
        theme = pa.THEMES[category]
        assert len(pa._split_emoji(theme["emoji"])) == 8 and len(theme["bases"]) == 5, category


def _project(tmp_path):
    root = tmp_path / "staywell"
    app = root / "frontend" / "app"
    (app / "src").mkdir(parents=True)
    (app / "public" / "images" / "catalog").mkdir(parents=True)
    (app / "package.json").write_text("{}", encoding="utf-8")
    (app / "index.html").write_text("<html><title>StayWell Hotel</title></html>", encoding="utf-8")
    old = '<svg xmlns="http://www.w3.org/2000/svg"><text>✈️</text></svg>'
    (app / "public" / "images" / "catalog" / "hotel.svg").write_text(old, encoding="utf-8")
    (app / "public" / "images" / "real.jpg").write_bytes(b"\xff\xd8photo")
    data = root / "backend" / "data"
    data.mkdir(parents=True)
    (data / "gallery.json").write_text(json.dumps([
        {"id": f"g{i}", "url": "/images/catalog/hotel.svg", "altText": name} for i, name in enumerate(HOTEL[:4])
    ]), encoding="utf-8")
    (data / "rooms.json").write_text(json.dumps([
        {"id": "r1", "name": "Deluxe suite", "image": ""},
        {"id": "r2", "name": "Sea view room", "image": "/images/real.jpg"},
        {"id": "r3", "name": "Garden room", "image": "https://example.com/a.jpg"},
    ]), encoding="utf-8")
    return root, app


def test_records_that_shared_one_picture_each_get_their_own(tmp_path):
    root, app = _project(tmp_path)
    result = pa.refresh_placeholder_images(root)
    assert result["category"] == "hospitality" and result["changed"] >= 5
    gallery = json.loads((root / "backend/data/gallery.json").read_text())
    assert len({r["url"] for r in gallery}) == 4
    assert all((app / "public" / r["url"].lstrip("/")).is_file() for r in gallery)
    rooms = json.loads((root / "backend/data/rooms.json").read_text())
    assert rooms[0]["image"].startswith("/images/catalog/")      # empty -> new picture
    assert rooms[1]["image"] == "/images/real.jpg"               # real photo untouched
    assert rooms[2]["image"] == "https://example.com/a.jpg"      # remote URL untouched
    assert "✈" not in (app / "public" / gallery[0]["url"].lstrip("/")).read_text()


def test_refreshing_twice_changes_nothing_the_second_time(tmp_path):
    root, _ = _project(tmp_path)
    pa.refresh_placeholder_images(root)
    assert pa.refresh_placeholder_images(root)["changed"] == 0


def test_images_the_code_points_at_but_nobody_created_are_drawn(tmp_path):
    app = tmp_path / "proj" / "frontend" / "app"
    (app / "src").mkdir(parents=True)
    (app / "public" / "images").mkdir(parents=True)
    (app / "public" / "images" / "exists.png").write_bytes(b"png")
    page = app / "src" / "Home.jsx"
    page.write_text('<img src="/images/hero-banner.jpg" alt="x" />\n<img src="/images/exists.png" />\n'
                    '<img src="https://cdn.example.com/y.jpg" />\n', encoding="utf-8")
    css = app / "src" / "app.css"
    css.write_text('.hero { background: url("/images/hero-banner.jpg"); }\n', encoding="utf-8")
    fixes = pa.fix_missing_images(app)
    assert len(fixes) == 2
    text = page.read_text()
    assert "/images/generated/hero-banner.svg" in text
    assert "/images/exists.png" in text and "https://cdn.example.com/y.jpg" in text
    assert (app / "public/images/generated/hero-banner.svg").is_file()
    assert "generated/hero-banner.svg" in css.read_text()
    assert pa.fix_missing_images(app) == []                       # idempotent


def test_harden_frontend_also_draws_missing_pictures(tmp_path):
    app = tmp_path / "frontend"
    (app / "src").mkdir(parents=True)
    (app / "src" / "Home.jsx").write_text('<img src="/images/team.jpg" />\n', encoding="utf-8")
    fixes = fc.harden_frontend(app)
    assert any("missing image" in f["action"] for f in fixes)


# ---- chat --------------------------------------------------------------------------------
def test_chat_redraws_pictures_without_the_model(tmp_path, monkeypatch):
    root, app = _project(tmp_path)
    be = root / "backend"
    (be / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (app / "src" / "App.jsx").write_text("export default function App(){return null}\n", encoding="utf-8")

    async def boom(**_):
        raise AssertionError("the model must not be needed")

    monkeypatch.setattr(revise, "_default_llm", boom)
    result = asyncio.run(revise.revise_project(
        app, be, "all the gallery images look the same, add dummy images", build_check=lambda d: None))
    assert result["ok"] and "own picture" in result["summary"]
    gallery = json.loads((be / "data/gallery.json").read_text())
    assert len({r["url"] for r in gallery}) == 4


def test_a_request_for_a_new_gallery_page_still_goes_to_the_model(tmp_path, monkeypatch):
    root, app = _project(tmp_path)
    be = root / "backend"
    (be / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n", encoding="utf-8")
    (app / "src" / "App.jsx").write_text("export default function App(){return null}\n", encoding="utf-8")
    asked = []

    async def llm(**kw):
        asked.append(1)
        return json.dumps({"files": []})

    monkeypatch.setattr(revise, "_default_llm", llm)
    asyncio.run(revise.revise_project(app, be, "add a new gallery page with photos", build_check=lambda d: None))
    assert asked


def test_new_projects_get_hospitality_pictures_from_the_seed_pipeline(tmp_path):
    pytest.importorskip("groq")
    from generation.seed_data import detect_image_category, resolve_record_images

    spec = {"collection": "gallery", "fields": [{"name": "id", "type": "str"}, {"name": "url", "type": "str"},
                                                 {"name": "altText", "type": "str"}]}
    records = [{"id": f"g{i}", "url": "", "altText": name} for i, name in enumerate(HOTEL)]
    assert detect_image_category("StayWell", "hotel booking") == "hospitality"
    out = resolve_record_images(records, spec, business_name="StayWell", project_goal="hotel booking",
                                frontend_dir=tmp_path)
    emojis = {emoji_of((tmp_path / "public" / r["url"].lstrip("/")).read_text(encoding="utf-8")) for r in out}
    assert len(emojis) >= 5 and "✈️" not in emojis
