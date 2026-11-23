"""Manual helper (see .github/workflows/screenshots.yml): seeds a demo database and screenshots each page."""
import os, sys, tempfile, threading
from pathlib import Path

os.environ.update(DATA_DIR=tempfile.mkdtemp(), BACKUP_KEEP="0")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import app as m
from playwright.sync_api import sync_playwright

PORT = 5055
threading.Thread(target=lambda: m.app.run(port=PORT, use_reloader=False), daemon=True).start()
OUT = Path("shots"); OUT.mkdir(exist_ok=True)

with sync_playwright() as p:
    b = p.chromium.launch(); page = b.new_page(viewport={"width": 1100, "height": 900})
    base = f"http://127.0.0.1:{PORT}"
    page.goto(base + "/register"); page.fill("[name=username]", "demo"); page.fill("[name=password]", "demopass123"); page.click("button.btn")
    page.fill("[name=name]", "Knitting"); page.click("text=Create collection")
    page.fill("[name=name]", "Yarn"); page.fill("[name=unit]", "skein"); page.click("text=Create type"); page.goto(base + "/collections/1")
    for name, color, qty in (("Merino", "#d98ca3", "5"), ("Cotton", "#5ba7d6", "1"), ("Alpaca", "#4fb286", "8")):
        page.goto(base + "/types/1/items/new"); page.fill("[name=name]", name); page.check("#has_hex"); page.fill("[name=hex]", color)
        page.fill("[name=qty]", qty); page.click("text=Save item")
    for theme in ("system", "dark", "forest"):
        page.goto(base + "/appearance"); page.check(f"input[name=theme][value={theme}]"); page.click("text=Save appearance")
        for path in ("/", "/collections/1", "/shopping", "/appearance"):
            page.goto(base + path); page.screenshot(path=str(OUT / f"{theme}-{path.strip('/').replace('/', '-') or 'home'}.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 800}); page.goto(base + "/collections/1"); page.screenshot(path=str(OUT / "mobile-collection.png"), full_page=True)
    b.close()
