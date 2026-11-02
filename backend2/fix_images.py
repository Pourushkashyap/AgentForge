import json, re, sys, html
from pathlib import Path

root = Path(sys.argv[1]).resolve()
frontend = next(p for p in (root / "frontend").iterdir() if (p / "package.json").is_file())
folder = frontend / "public" / "images" / "catalog"
folder.mkdir(parents=True, exist_ok=True)

EMOJI = [(("cookie",), "\U0001F36A"), (("muffin", "cupcake"), "\U0001F9C1"),
         (("croissant",), "\U0001F950"), (("bread", "loaf", "baguette"), "\U0001F35E"),
         (("cake", "brownie"), "\U0001F370"), (("tart", "pie"), "\U0001F967"),
         (("donut",), "\U0001F369")]

def svg(name):
    emoji = next((e for keys, e in EMOJI if any(k in name.lower() for k in keys)), "\U0001F370")
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 400">'
            '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
            '<stop offset="0" stop-color="#f6d7a7"/><stop offset="1" stop-color="#e08e45"/>'
            '</linearGradient></defs><rect width="600" height="400" fill="url(#g)"/>'
            f'<text x="300" y="215" font-size="130" text-anchor="middle">{emoji}</text>'
            '<text x="300" y="320" font-size="34" font-family="Georgia,serif" fill="#fff" '
            f'text-anchor="middle">{html.escape(name[:28])}</text></svg>')

count = 0
for data_file in (root / "backend" / "data").glob("*.json"):
    records = json.loads(data_file.read_text(encoding="utf-8") or "[]")
    if not isinstance(records, list):
        continue
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            continue
        for key in [k for k in rec if re.search("image|photo|thumbnail|picture", k, re.I)]:
            name = str(rec.get("name") or rec.get("title") or f"Item {i+1}")
            slug = re.sub(r"[^a-z0-9]+", "-", f"{data_file.stem}-{i+1}-{name}".lower()).strip("-")
            (folder / f"{slug}.svg").write_text(svg(name), encoding="utf-8")
            rec[key] = f"/images/catalog/{slug}.svg"
            count += 1
    data_file.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print(f"Done: {count} images added.")
