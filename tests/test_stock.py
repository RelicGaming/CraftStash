import re
from conftest import seed, png_bytes
import io

def test_low_stock_and_shopping_list(env, amy):
    seed(amy); amy.post("/items/1/adjust", {"amount": "3", "action": "use"})        # 5 -> 2, at the low level
    page = amy.html("/collections/1"); shop = amy.html("/shopping")
    assert "Low stock" in page and "Merino" in shop and "Cotton" not in shop        # Cotton has no low level and stock 1

def test_adjust_undo_and_never_negative(env, amy):
    seed(amy); amy.post("/items/1/adjust", {"amount": "99", "action": "use"})
    assert "(5 to 0)" in amy.html("/collections/1/activity")
    amy.post("/activity/%s/undo" % re.search(r"/activity/(\d+)/undo", amy.html("/collections/1/activity")).group(1))
    assert "Undone" in amy.html("/collections/1/activity") and "<strong>5</strong>" in amy.html("/collections/1")

def test_adjust_redirect_is_same_site_only(env, amy):
    seed(amy)
    assert amy.post("/items/1/adjust", {"amount": "1", "action": "add", "next": "//evil.example"}).headers["Location"] == "/collections/1"

def test_shortfall_reaches_the_shopping_list_and_completion_is_reversible(env, amy):
    seed(amy)
    r = amy.post("/projects/1/assign", {"item_id": "1", "qty": "8"}, follow_redirects=True).get_data(as_text=True)
    assert "3 skein more than you have free" in r
    assert "Short by 3 skein" in amy.html("/shopping") and "Short by 3" in amy.html("/collections/1")
    amy.post("/projects/1/complete")
    page = amy.html("/collections/1"); assert "<strong>0</strong>" in page                   # only the 5 on hand were used, stock can't go negative
    amy.post("/projects/1/reopen")
    assert "<strong>5</strong>" in amy.html("/collections/1")                                # exactly what was taken comes back, not 8

def test_duplicate_item_prefills_the_form(env, amy):
    seed(amy); html = amy.html("/types/1/items/new?copy=1")
    assert 'value="Merino copy"' in html and 'value="Rose"' in html and "Copied from Merino" in html

def test_photo_is_reencoded_served_and_removed_with_the_item(env, amy):
    seed(amy)
    amy.post("/items/1/edit", {"name": "Merino", "qty": "5", "photo": (io.BytesIO(png_bytes()), "yarn.png")})
    r = amy.get("/photos/1"); assert r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"
    from PIL import Image; assert max(Image.open(io.BytesIO(r.data)).size) <= 900               # shrunk, so a big upload can't bloat the page
    stored = list((env.data / "uploads" / "photos").glob("*.jpg")); assert len(stored) == 1
    amy.post("/items/1/delete"); assert not list((env.data / "uploads" / "photos").glob("*.jpg"))

def test_fake_image_is_rejected(env, amy):
    seed(amy)
    r = amy.post("/items/1/edit", {"name": "Merino", "qty": "5", "photo": (io.BytesIO(b"<script>alert(1)</script>"), "x.jpg")}, follow_redirects=True)
    assert "be read" in r.get_data(as_text=True) and amy.get("/photos/1").status_code == 404

def test_delete_warnings_say_what_will_be_lost(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "1"})
    assert "takes them out of 1 project" in amy.html("/collections/1") and "used in 1 project" in amy.html("/items/1/edit")

def test_currency_symbol_is_configurable(env, amy):
    seed(amy); amy.post("/admin/currency", {"currency": "£"})
    assert "£9.50" in amy.html("/collections/1")
