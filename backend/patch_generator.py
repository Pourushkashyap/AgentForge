from pathlib import Path

path = Path("generation/frontend_generator.py")
raw = path.read_bytes().decode("utf-8")
nl = "\r\n" if "\r\n" in raw else "\n"
text = raw.replace("\r\n", "\n")

if "layout_cleanup" in text:
    print("Already patched.")
    raise SystemExit

hook = (
    "    # ---- 3b. Remove Navbar/Footer duplicated inside pages --------------\n"
    "    from generation.layout_cleanup import remove_duplicate_layout\n\n"
    "    for cleaned in remove_duplicate_layout(project_dir):\n"
    "        print(f\"Removed duplicate layout components from {cleaned}\")\n\n"
)
anchor = "    # ---- 4. Validate production build"
rule = (
    "SHARED LAYOUT (no duplicates)\n\n"
    "- App.jsx renders <Navbar /> and <Footer /> once around all routes.\n"
    "- Page components must NOT render Navbar, Header or Footer themselves.\n"
    "- Every navigation destination appears exactly once in the navbar. If a\n"
    "  cart link shows an item-count badge, put the badge inside that single\n"
    "  Cart link; never add a second Cart link.\n\n"
)
anchor2 = "IMAGE HANDLING\n"

if text.count(anchor) != 1 or text.count(anchor2) != 1:
    raise SystemExit("Could not find the expected places in frontend_generator.py. Send me this message.")

text = text.replace(anchor, hook + anchor, 1).replace(anchor2, rule + anchor2, 1)
path.write_bytes(text.replace("\n", nl).encode("utf-8"))
print("Patched frontend_generator.py")
