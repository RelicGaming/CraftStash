"""The wishlist: quick add while shopping, shops and prices, "Bought it", links from projects and the restock list,
who can see what, the API, and the tables in backups, exports and upgrades."""
import io, os, re, sqlite3, subprocess, sys, time
from pathlib import Path
import pytest
from conftest import png_bytes, seed, TEST_DB
from test_api import api, make_token

APP = Path(__file__).resolve().parent.parent / "app"

def rows(env, sql, *args): return env.db().execute(sql, args).fetchall()
def one(env, sql, *args): return env.db().execute(sql, args).fetchone()

def wish(c, **kw):
    """Quick add. Returns the response; the new wish's page is where it redirects."""
    data = {"name": "Chunky wool", "shop": "", "price": "", **kw}
    return c.post("/wishlist/new", data, content_type="multipart/form-data")

def test_quick_add_with_photo_shop_and_price(env, amy):
    r = wish(amy, name="Chunky wool", colour="Mustard", has_hex="1", hex="#d4a017", shop="Spotlight", price="12.5",
             url="https://www.spotlight.com.au/wool", shop_notes="On sale", photo=(io.BytesIO(png_bytes()), "w.png"))
    assert r.status_code == 302 and r.headers["Location"].endswith("/wishlist/1")
    w = one(env, "SELECT name, colour, hex, qty, photo, got, created FROM wishes")
    assert w[:4] == ("Chunky wool", "Mustard", "#d4a017", 1) and w[5] == 0 and w[6]
    assert (env.core.PHOTOS / w[4]).is_file()                                   # in the mappable photos folder
    s = one(env, "SELECT shop, url, price, seen, notes FROM wish_sightings")
    assert s[:3] == ("Spotlight", "https://www.spotlight.com.au/wool", 12.5) and re.fullmatch(r"\d{4}-\d\d-\d\d", s[3]) and s[4] == "On sale"
    page = amy.html("/wishlist/1")
    assert "Chunky wool" in page and "$12.50" in page and 'rel="noopener noreferrer nofollow"' in page
    assert amy.get("/wishlist/1/photo").status_code == 200

def test_list_shows_the_cheapest_shop_and_how_many_others(env, amy):
    wish(amy, shop="Spotlight", price="12.5")
    for shop, price in (("Lincraft", "9.95"), ("Online", ""), ("Market", "11")):
        amy.post("/wishlist/1/sightings", {"shop": shop, "price": price})
    page = amy.html("/wishlist")
    assert "$9.95" in page and "at Lincraft" in page and "+3 other shops" in page
    assert re.search(r'<tr class="cheapest"><td>Lincraft', amy.html("/wishlist/1"))
    assert "Wishlist <span class=\"pill\">1</span>" in amy.html("/shopping")            # the tab count

def test_sightings_are_checked(env, amy):
    wish(amy)
    amy.post("/wishlist/1/sightings", {"url": "javascript:alert(1)", "price": "3"})
    amy.post("/wishlist/1/sightings", {"url": "https://www.etsy.com/listing/123?x=1"})
    amy.post("/wishlist/1/sightings", {"price": "-4", "seen": "not a date", "shop": "Bad"})
    got = rows(env, "SELECT shop, url, price, seen FROM wish_sightings ORDER BY id")
    assert got[0][:3] == ("", "", 3) and got[1][:2] == ("etsy.com", "https://www.etsy.com/listing/123?x=1")
    assert got[2][2] is None and re.fullmatch(r"\d{4}-\d\d-\d\d", got[2][3])     # a bad price is left out, a bad date is today
    r = amy.post("/wishlist/1/sightings", {"notes": "only notes"})
    assert "Enter the shop" in amy.html("/wishlist/1") and len(rows(env, "SELECT id FROM wish_sightings")) == 3 and r.status_code == 302
    amy.post("/wishlist/sightings/1/delete")
    assert len(rows(env, "SELECT id FROM wish_sightings")) == 2

def test_bought_it_makes_a_new_item_and_keeps_the_wish(env, amy):
    seed(amy)
    wish(amy, name="Alpaca", colour="Grey", has_hex="1", hex="#888888", qty="3", notes="For the hat", type_id="1",
         shop="Spotlight", price="7.25", photo=(io.BytesIO(png_bytes()), "w.png"))
    amy.post("/wishlist/1/sightings", {"shop": "Lincraft", "price": "8"})
    r = amy.post("/wishlist/1/bought", {"qty": "3"})                             # defaults to the cheapest shop
    iid = one(env, "SELECT id FROM items WHERE name='Alpaca'")[0]
    assert r.headers["Location"].endswith(f"/items/{iid}/edit")
    i = one(env, "SELECT colour, hex, qty, price, notes, photo, type_id FROM items WHERE id=?", iid)
    assert i[:4] == ("Grey", "#888888", 3, 7.25) and i[6] == 1
    assert i[4].startswith("For the hat\nBought ") and "at Spotlight for $7.25 each" in i[4]
    w = one(env, "SELECT got, got_at, item_id, photo FROM wishes")
    assert w[0] == 1 and w[1] and w[2] == iid and i[5] and i[5] != w[3]               # its own copy of the photo
    assert (env.core.PHOTOS / i[5]).is_file() and (env.core.PHOTOS / w[3]).is_file()
    amy.html("/wishlist")                                                       # shows the flash message once
    assert "Alpaca" not in amy.html("/wishlist") and "Alpaca" in amy.html("/wishlist?show=got")
    assert "from the wishlist" in one(env, "SELECT text FROM activity ORDER BY id DESC LIMIT 1")[0]
    assert "already marked as bought" in amy.post("/wishlist/1/bought", {}, follow_redirects=True).get_data(as_text=True)
    amy.post("/wishlist/1/reopen"); assert one(env, "SELECT got FROM wishes")[0] == 0

def test_bought_it_needs_somewhere_to_go_and_a_shop_on_this_wish(env, amy):
    seed(amy); wish(amy, shop="Spotlight", price="5")
    assert "Choose which collection" in amy.post("/wishlist/1/bought", {"qty": "1"}, follow_redirects=True).get_data(as_text=True)
    r = amy.post("/wishlist/1/bought", {"qty": "1", "type_id": "1", "sighting": "99"}, follow_redirects=True)
    assert "isn&#39;t on this wish" in r.get_data(as_text=True)
    amy.post("/wishlist/1/bought", {"qty": "2", "type_id": "1", "sighting": "none"})
    assert one(env, "SELECT qty, price FROM items WHERE name='Chunky wool'") == (2, None)

def test_watching_an_item_from_the_restock_list(env, amy):
    seed(amy)                                                                   # Cotton: 1 left, no level; Merino: 5, low at 2
    amy.post("/items/2/adjust", {"action": "use", "amount": "1"})               # Cotton is out
    page = amy.html("/shopping")
    assert "/wishlist/new?item=2" in page and "Watch the price" in page
    form = amy.html("/wishlist/new?item=2&qty=4")
    assert 'value="Cotton"' in form and 'value="4"' in form and "Watching the price of Cotton" in form and 'name="item_id" value="2"' in form
    wish(amy, name="Cotton", item_id="2", qty="4", shop="Spotlight", price="3.5")
    assert one(env, "SELECT item_id, type_id, unit, colour FROM wishes") == (2, 1, "skein", "Blue")
    assert "On the wishlist" in amy.html("/shopping") and "Watch the price" not in amy.html("/shopping")
    amy.post("/wishlist/1/bought", {"qty": "4"})
    assert one(env, "SELECT qty, price FROM items WHERE id=2") == (4, 3.5) and one(env, "SELECT COUNT(*) FROM items")[0] == 2
    assert "Added 4 skein of Cotton" in one(env, "SELECT text FROM activity WHERE kind='adjust' ORDER BY id DESC LIMIT 1")[0]

def test_project_materials_and_wishes_for_a_project(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "8"})       # Merino: 5 in stock, short by 3
    page = amy.html("/projects/1")
    assert "/wishlist/new?item=1&amp;project=1&amp;qty=3" in page and "Add a wish for this project" in page
    wish(amy, name="Merino", item_id="1", project_id="1", qty="3", shop="Spotlight", price="9")
    wish(amy, name="Buttons", project_id="1")
    page = amy.html("/projects/1")
    assert "On the wishlist" in page and "Buttons" in page and "from $9.00" in page
    assert "for Hat" in amy.html("/wishlist")

def test_wishes_are_private_unless_filed_under_a_shared_collection(env, amy, bob):
    seed(amy); wish(amy, name="Secret", project_id="1"); wish(amy, name="Shared", type_id="1", project_id="1")
    assert "Secret" not in bob.html("/wishlist") and bob.get("/wishlist/1").status_code == 404
    assert bob.get("/wishlist/1/photo").status_code == 404 and bob.get("/wishlist/2").status_code == 404
    amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    page = bob.html("/wishlist")
    assert "Shared" in page and "Secret" not in page and "amy's" in page and "for Hat" not in page   # not her project's name
    assert 'action="/wishlist/2/bought"' not in bob.html("/wishlist/2")
    for url in ("/wishlist/2/edit", "/wishlist/2/bought", "/wishlist/2/sightings", "/wishlist/2/delete", "/wishlist/sightings/1/delete"):
        assert bob.post(url, {"name": "x", "shop": "x", "qty": "1"}).status_code == 404, url
    amy.post("/projects/1/share", {"username": "bob"}); assert "for Hat" in bob.html("/wishlist")
    amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/wishlist/2/edit", {"name": "Shared, renamed", "type_id": "1", "project_id": "", "qty": "2"})
    assert one(env, "SELECT name, qty, project_id, user_id FROM wishes WHERE id=2") == ("Shared, renamed", 2, 1, 1)   # her project stays
    bob.post("/wishlist/2/bought", {"qty": "2"}); assert one(env, "SELECT got FROM wishes WHERE id=2")[0] == 1

def test_links_must_be_to_things_you_can_use(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    for bad in ({"type_id": "1"}, {"item_id": "1"}, {"project_id": "1"}, {"type_id": "999"}):
        assert "Chunky wool" not in bob.html("/wishlist")
        r = wish(bob, **bad); assert r.status_code == 302 and "/wishlist/new" in r.headers["Location"], bad
    assert one(env, "SELECT COUNT(*) FROM wishes")[0] == 0
    amy.post("/projects", {"name": "Template"}); env.db().execute("UPDATE projects SET status='template' WHERE id=2")
    wish(amy, project_id="2"); assert one(env, "SELECT COUNT(*) FROM wishes")[0] == 0

def test_deleting_things_a_wish_links_to_keeps_the_wish(env, amy):
    seed(amy); amy.post("/projects", {"name": "Bag"})
    wish(amy, name="Watch", item_id="2", project_id="2")
    amy.post("/items/2/delete"); amy.post("/projects/2/delete")
    assert one(env, "SELECT item_id, type_id, project_id FROM wishes") == (None, 1, None)
    amy.post("/types/1/delete"); assert one(env, "SELECT type_id FROM wishes")[0] is None
    assert "Watch" in amy.html("/wishlist")

def test_deleting_a_wish_or_an_account_removes_its_photos(env, amy, bob):
    wish(amy, photo=(io.BytesIO(png_bytes()), "w.png")); wish(bob, photo=(io.BytesIO(png_bytes()), "w.png"), shop="Spotlight")
    a, b = (env.core.PHOTOS / r[0] for r in rows(env, "SELECT photo FROM wishes ORDER BY id"))
    amy.post("/wishlist/1/delete"); assert not a.exists()
    amy.post("/admin/users/2/delete")
    assert not b.exists() and one(env, "SELECT COUNT(*) FROM wishes")[0] == 0 and one(env, "SELECT COUNT(*) FROM wish_sightings")[0] == 0

def test_edit_photo_replace_and_remove(env, amy):
    wish(amy, photo=(io.BytesIO(png_bytes()), "w.png")); old = env.core.PHOTOS / one(env, "SELECT photo FROM wishes")[0]
    amy.post("/wishlist/1/edit", {"name": "Wool", "qty": "2", "photo": (io.BytesIO(png_bytes((50, 50))), "n.png")}, content_type="multipart/form-data")
    new = one(env, "SELECT photo, name, qty FROM wishes")
    assert not old.exists() and new[1:] == ("Wool", 2) and (env.core.PHOTOS / new[0]).is_file()
    amy.post("/wishlist/1/edit", {"name": "Wool", "remove_photo": "on"})
    assert one(env, "SELECT photo FROM wishes")[0] == "" and not (env.core.PHOTOS / new[0]).exists()

@pytest.mark.sqlite_only
def test_cleanup_keeps_wish_photos(env, amy):
    wish(amy, photo=(io.BytesIO(png_bytes()), "w.png"))
    old = time.time() - 7200
    for p in env.core.PHOTOS.iterdir(): os.utime(p, (old, old))
    r = subprocess.run([sys.executable, "manage.py", "cleanup"], cwd=APP, env=os.environ, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "0 orphaned" in r.stdout and len(list(env.core.PHOTOS.iterdir())) == 1

def test_copy_as_text_lines(env, amy):
    wish(amy, name="Wool", colour="Mustard", qty="2", unit="balls", shop="Spotlight", price="4")
    assert 'data-line="Wool (Mustard), 2 balls, $4.00 at Spotlight"' in amy.html("/wishlist")

def test_api_wishlist(env, amy):
    seed(amy); get, post = api(env, make_token(amy, "write"))
    r = post("/api/v1/wishlist", json={"name": "Wool", "qty": 2, "unit": "balls", "shop": "Spotlight", "price": 4.5, "url": "https://x.example/w"})
    assert r.status_code == 201 and r.json["cheapest"]["price"] == 4.5 and r.json["sightings"][0]["shop"] == "Spotlight"
    wid = r.json["id"]
    r = post(f"/api/v1/wishlist/{wid}/sightings", json={"shop": "Lincraft", "price": 3.99, "seen": "2026-11-01", "notes": "sale"})
    assert r.status_code == 201
    assert r.json["cheapest"] == {"id": 2, "shop": "Lincraft", "url": None, "price": 3.99, "seen": "2026-11-01", "notes": "sale"}
    assert post(f"/api/v1/wishlist/{wid}/sightings", json={"notes": "x"}).status_code == 400
    assert post("/api/v1/wishlist", json={"qty": 1}).status_code == 400 and post("/api/v1/wishlist", json=[1]).status_code == 400
    lst = get("/api/v1/wishlist").json
    assert lst["count"] == 1 and lst["currency"] == "$" and lst["wishes"][0]["name"] == "Wool"
    assert get(f"/api/v1/wishlist/{wid}").json["url"].endswith(f"/wishlist/{wid}")
    assert post(f"/api/v1/wishlist/{wid}/bought", json={}).status_code == 400            # nowhere to put it yet
    r = post(f"/api/v1/wishlist/{wid}/bought", json={"type_id": 1})
    assert r.status_code == 200 and r.json["item"]["name"] == "Wool" and r.json["item"]["qty"] == 2 and r.json["item"]["price"] == 3.99
    assert r.json["wish"]["got"] is True and get("/api/v1/wishlist").json["count"] == 0 and get("/api/v1/wishlist?got=1").json["count"] == 1
    assert post(f"/api/v1/wishlist/{wid}/bought", json={"type_id": 1}).status_code == 409
    assert "POST /api/v1/wishlist" in get("/api/v1").json["endpoints"]

def test_api_wishlist_respects_tokens_and_access(env, amy, bob):
    wish(amy, name="Mine")
    get, post = api(env, make_token(amy, "read"))
    assert get("/api/v1/wishlist").json["count"] == 1 and post("/api/v1/wishlist", json={"name": "x"}).status_code == 403
    bget, bpost = api(env, make_token(bob, "write"))
    assert bget("/api/v1/wishlist").json["count"] == 0 and bget("/api/v1/wishlist/1").status_code == 404
    assert bpost("/api/v1/wishlist/1/sightings", json={"shop": "x"}).status_code == 404 and bpost("/api/v1/wishlist/1/bought", json={}).status_code == 404

def test_tables_are_in_exports_and_backups(env, amy):
    import dbx, dump
    assert dbx.TABLES.index("wishes") > max(dbx.TABLES.index(t) for t in ("users", "types", "items", "projects"))
    assert dbx.TABLES.index("wish_sightings") > dbx.TABLES.index("wishes") and {"wishes", "wish_sightings"} <= dbx.ID_TABLES
    wish(amy, shop="Spotlight", price="2")
    data = dump.dump(env.db())
    assert len(data["tables"]["wishes"]["rows"]) == 1 and len(data["tables"]["wish_sightings"]["rows"]) == 1

def test_limits(env, amy, monkeypatch):
    import wishlist
    monkeypatch.setattr(wishlist, "SIGHTINGS_PER_WISH", 2); monkeypatch.setattr(wishlist, "WISHES_PER_USER", 1)
    wish(amy, shop="A"); amy.post("/wishlist/1/sightings", {"shop": "B"}); amy.post("/wishlist/1/sightings", {"shop": "C"})
    assert one(env, "SELECT COUNT(*) FROM wish_sightings")[0] == 2 and "at most 2 shops" in amy.html("/wishlist/1")
    wish(amy, name="Two"); assert one(env, "SELECT COUNT(*) FROM wishes")[0] == 1

@pytest.mark.sqlite_only
def test_an_older_sqlite_database_gets_the_tables(make_env, tmp_path):
    make_env(); d = sqlite3.connect(tmp_path / "craft.db")
    d.execute("DROP TABLE wish_sightings"); d.execute("DROP TABLE wishes"); d.execute("PRAGMA user_version=8"); d.commit(); d.close()
    make_env(); d = sqlite3.connect(tmp_path / "craft.db")
    assert {"wishes", "wish_sightings"} <= {r[0] for r in d.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert d.execute("PRAGMA user_version").fetchone()[0] == 9 and list((tmp_path / "backups").glob("*"))   # backed up first

@pytest.mark.skipif(not TEST_DB, reason="needs a server database")
def test_a_server_database_without_the_tables_gets_them(make_env):
    env = make_env(); import dbx, migrations
    c = dbx.connect(dbx.CFG); cur = c.raw.cursor()
    cur.execute("DROP TABLE wish_sightings"); cur.execute("DROP TABLE wishes"); c.commit(); c.close()
    c = dbx.connect(dbx.CFG); migrations.run_server(c); c.close()
    c = dbx.connect(dbx.CFG); assert c.execute("SELECT COUNT(*) FROM wishes").fetchone()[0] == 0; c.close()
