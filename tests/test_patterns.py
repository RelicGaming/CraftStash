"""The pixel pattern maker: the engine (colour matching, palettes, merging, dithering, estimates), saved patterns and
remaking them, checking against the stash, making a project, privacy, clean-up, the API and upgrades."""
import io, json, re, sqlite3, sys
import pytest
from PIL import Image, ImageDraw
from conftest import TEST_DB
from test_api import api, make_token

def one(env, sql, *args): return env.db().execute(sql, args).fetchone()
def rows(env, sql, *args): return env.db().execute(sql, args).fetchall()

def picture(fmt="PNG"):
    """A red disc with a blue square in it, on a transparent background."""
    im = Image.new("RGBA", (200, 120), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    d.ellipse([20, 10, 180, 110], fill=(220, 40, 60, 255)); d.rectangle([80, 40, 120, 80], fill=(30, 120, 200, 255))
    b = io.BytesIO(); (im if fmt == "PNG" else im.convert("RGB")).save(b, fmt); return b.getvalue()

def make(c, **kw):
    data = {"photo": (io.BytesIO(picture()), "heart.png"), "craft": "cross-stitch", "width": "40", "colours": "6", **kw}
    return c.post("/patterns/new", data, content_type="multipart/form-data")

def floss(c, name="DMC 321", colour="Red", hex="#C7072E", qty="3", collection="DMC threads", unit="skein"):
    """A collection (made once) with a floss type, and one item in it."""
    if 'href="/collections/1"' not in c.html("/"):
        c.post("/collections", {"name": collection, "craft": "Cross stitch"}); c.post("/collections/1/types", {"name": "Floss", "unit": unit})
    c.post("/types/1/items/new", {"name": name, "colour": colour, "has_hex": "1", "hex": hex, "qty": qty})

# ---------- the engine ----------
@pytest.fixture
def pk(env): return sys.modules["patternkit"]

def test_ciede2000_matches_the_published_test_data(pk):
    assert round(pk.de2000((50, 2.6772, -79.7751), (50, 0, -82.7485)), 4) == 2.0425        # Sharma et al., pair 1
    assert round(pk.de2000((50, 2.5, 0), (73, 25, -18)), 4) == 27.1492                      # pair 18

def test_a_chart_keeps_the_shape_and_leaves_transparent_cells_empty(pk):
    im = Image.open(io.BytesIO(picture()))
    c = pk.make(im, 40, colours=3)
    assert (c["w"], c["h"]) == (40, 24) and len(c["cells"]) == 40 * 24
    assert c["cells"][0] == pk.EMPTY and c["cells"][12 * 40 + 20] != pk.EMPTY                 # corner empty, centre stitched
    assert 2 <= len(c["palette"]) <= 3 and [p["symbol"] for p in c["palette"]] == pk.SYMBOLS[:len(c["palette"])]
    counts = [p["count"] for p in c["palette"]]
    assert counts == sorted(counts, reverse=True) and sum(counts) == sum(1 for x in c["cells"] if x != pk.EMPTY)
    assert pk.unpack(pk.pack(c)) == c

def test_sizes_are_limited(pk):
    im = Image.open(io.BytesIO(picture()))
    assert pk.grid_size(im, 5000, 0)[0] == pk.MAX_SIDE and pk.grid_size(im, 1, 1) == (pk.MIN_SIDE, pk.MIN_SIDE)
    assert len(pk.make(im, 60, colours=500)["palette"]) <= pk.MAX_COLOURS

def test_catalogue_colours_only_and_rare_ones_merged(pk):
    im = Image.open(io.BytesIO(picture()))
    cat = [{"code": c, "name": n, "hex": h, "brand": "DMC"} for c, n, h in pk.catalogue("dmc-floss")["colors"]]
    codes = {c["code"] for c in cat}
    c = pk.make(im, 40, colours=10, candidates=cat)
    assert all(p["code"] in codes for p in c["palette"])
    merged = pk.make(im, 40, colours=10, candidates=cat, min_count=200)
    assert len(merged["palette"]) < len(c["palette"]) and all(p["count"] >= 200 for p in merged["palette"][:-1])
    dithered = pk.make(im, 40, colours=10, candidates=cat, dither=True)
    assert all(p["code"] in codes for p in dithered["palette"])

def test_the_catalogues_are_whole_and_credited(pk):
    sizes = {k: len(pk.catalogue(k)["colors"]) for k in pk.CATALOGUES}
    assert sizes == {"dmc-floss": 489, "dmc-diamond": 154, "perler-midi": 103, "hama-midi": 92, "artkal-s": 199}
    for k in pk.CATALOGUES:
        cols = pk.catalogue(k)["colors"]
        assert len({c[0] for c in cols}) == len(cols) and all(re.fullmatch(r"#[0-9A-F]{6}", c[2]) for c in cols)
    notice = (pk.CATALOGUE_DIR / "NOTICE.md").read_text()
    assert "CC BY 4.0" in notice and "MakeBead" in notice and "Copyright (c) 2020 maxcleme" in notice
    assert "states no licence" in notice and "floss.maxxmint.com" in notice     # the DMC values are not claimed as CC BY

def test_estimates(pk):
    cs = {"count": 14, "strands": 2, "per_unit": 0}
    assert pk.per_skein(cs) == 1800 and pk.per_skein({**cs, "count": 28}) == 3600 and pk.per_skein({**cs, "strands": 3}) == 1200
    assert pk.need("cross-stitch", cs, 900) == 0.5 and pk.need("cross-stitch", {**cs, "per_unit": 100}, 900) == 9
    assert pk.need("beads", {"board": 29}, 1234) == 1234
    assert pk.need("diamond", {"spare": 10, "drill_mm": 2.5}, 1000) == 1100
    assert pk.need("colourwork", {"sts10": 22, "rows10": 30, "cm_per_st": 3}, 1000) == 33.0
    assert "2 pegboards of 29 x 29" in pk.size_lines("beads", {"board": 29}, 40, 20)[1]
    assert pk.in_unit(2, "skein", "skeins") == 2 and pk.in_unit(5, "bead", "pcs") == 5 and pk.in_unit(1, "skein", "g") is None
    assert round(pk.in_unit(9.144, "m", "yd"), 3) == 10

def test_the_pdf_has_an_overview_and_chart_pages(pk):
    c = pk.make(Image.open(io.BytesIO(picture())), 120, colours=4)
    data = pk.pdf(c, "Heart", ["120 x 72"], [(p["symbol"], p["hex"], p["hex"], p["count"], "1 skein") for p in c["palette"]])
    assert data.startswith(b"%PDF")
    assert len(re.findall(rb"/Type\s*/Page[^s]", data)) == 1 + 3 * 2               # overview + 3 x 2 parts of 50 x 70

# ---------- making and remaking ----------
def test_make_a_pattern_and_see_what_to_buy(env, amy):
    r = make(amy); assert r.status_code == 302 and r.headers["Location"].endswith("/patterns/1")
    p = one(env, "SELECT name, photo, settings FROM patterns")
    assert p[0] == "heart" and (env.core.PHOTOS / p[1]).is_file() and p[1].endswith(".png")
    s = json.loads(p[2]); assert s["palette"] == "catalogue:dmc-floss" and s["width"] == 40 and s["count"] == 14
    page = amy.html("/patterns/1")
    assert "40 x 24 stitches on 14-count fabric" in page and "Need to buy</span> <span class=\"small\">1 skein</span>" in page and re.search(r"DMC \d+ ", page)
    for u in ("/patterns/1/chart.png", "/patterns/1/chart.png?style=symbols", "/patterns/1/chart.png?thumb=1", "/patterns/1/source"):
        r = amy.get(u); assert r.status_code == 200 and r.mimetype == "image/png", u
    r = amy.get("/patterns/1/pattern.pdf"); assert r.mimetype == "application/pdf" and r.data.startswith(b"%PDF")
    assert "heart" in amy.html("/tools")

def test_a_thread_you_own_is_found_by_its_code(env, amy):
    make(amy)
    code = re.search(r"DMC (\d+) ", amy.html("/patterns/1"))[1]                       # the chart's most used colour
    floss(amy, name=f"DMC {code}", hex="#000000", qty="0.1")                            # its swatch is wrong on purpose
    amy.post("/patterns/1", {"width": "40"})                                            # make it again to match the stash
    page = amy.html("/patterns/1")
    assert f"DMC {code} (Red)</a>" in page and "Short by" in page
    amy.post("/items/1/edit", {"name": f"DMC {code}", "colour": "Red", "has_hex": "1", "hex": "#000000", "qty": "20"})
    assert "Have it" in amy.html("/patterns/1")                                        # compared with stock now, not then

def test_a_close_swatch_counts_and_a_far_one_doesnt(env, amy):
    make(amy, palette="free", colours="2")
    hexes = re.findall(r'class="muted small">(#[0-9A-F]{6})<', amy.html("/patterns/1"))
    floss(amy, name="Red thread", hex=hexes[0])
    floss(amy, name="Green thread", hex="#00FF00")
    amy.post("/patterns/1", {"width": "40"})
    page = amy.html("/patterns/1")
    assert "Red thread" in page and "Green thread" not in page and "Need to buy" in page

def test_only_colours_from_your_stash(env, amy):
    for name, hx in (("Rose", "#D02040"), ("Sky", "#2080D0"), ("Grass", "#30A030")): floss(amy, name=name, colour="", hex=hx)
    r = make(amy, palette="pool:1", colours="3")
    chart = json.loads(one(env, "SELECT chart FROM patterns")[0])
    assert {p["name"] for p in chart["palette"]} <= {"Rose", "Sky", "Grass"} and all(p["match"] == "stash" for p in chart["palette"])
    assert "Grass" not in {p["name"] for p in chart["palette"]}                         # no green in the picture

def test_a_stash_palette_needs_two_swatches(env, amy):
    floss(amy, name="Rose", hex="#D02040")
    r = make(amy, palette="pool:1")
    assert r.status_code == 400 and "fewer than 2 items" in r.get_data(as_text=True) and one(env, "SELECT COUNT(*) FROM patterns")[0] == 0
    assert not list(env.core.PHOTOS.glob("pat-*"))

def test_settings_are_checked(env, amy):
    make(amy, width="9999", colours="1", craft="beads", palette="catalogue:hama-midi", board="5000")
    s = json.loads(one(env, "SELECT settings FROM patterns")[0])
    assert (s["width"], s["colours"], s["board"], s["craft"]) == (250, 2, 100, "beads") and "count" not in s
    assert make(amy, craft="weaving").status_code == 400
    assert make(amy, palette="catalogue:nope").status_code == 400
    bob = env.client().register("bob")
    assert make(bob, palette="pool:999").status_code == 400                             # not a collection bob can see
    r = bob.post("/patterns/new", {"photo": (io.BytesIO(b"GIF89a nope"), "x.gif")}, content_type="multipart/form-data")
    assert r.status_code == 400 and "couldn" in r.get_data(as_text=True)

def test_remaking_a_pattern(env, amy):
    make(amy); amy.get("/patterns/1/chart.png")
    old_photo = one(env, "SELECT photo FROM patterns")[0]
    cached = list(env.core.PATTERN_CACHE.glob("1-*.png")); assert cached
    amy.post("/patterns/1", {"name": "Heart, big", "width": "80", "dither": "1"})
    p = one(env, "SELECT name, settings, photo FROM patterns")
    assert p[0] == "Heart, big" and json.loads(p[1])["width"] == 80 and json.loads(p[1])["dither"] and p[2] == old_photo
    assert not any(f.exists() for f in cached) and "80 x 48 stitches" in amy.html("/patterns/1")
    amy.post("/patterns/1", {"photo": (io.BytesIO(picture("JPEG")), "b.jpg")}, content_type="multipart/form-data")
    new_photo = one(env, "SELECT photo FROM patterns")[0]
    assert new_photo != old_photo and not (env.core.PHOTOS / old_photo).exists() and (env.core.PHOTOS / new_photo).is_file()

def test_a_pattern_becomes_a_project(env, amy):
    make(amy, colours="3")
    code = re.search(r"DMC (\d+) ", amy.html("/patterns/1"))[1]
    floss(amy, name=f"DMC {code}", qty="0.01"); amy.post("/patterns/1", {})
    r = amy.post("/patterns/1/project", {"wishes": "1"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/projects/1")
    pr = one(env, "SELECT name, status, notes FROM projects")
    assert pr[:2] == ("heart", "planned") and "14-count" in pr[2] and rows(env, "SELECT tag FROM project_tags") == [("pattern",)]
    f = one(env, "SELECT name, stored, size FROM files"); assert f[0] == "heart-chart.pdf" and (env.core.UPLOADS / f[1]).read_bytes().startswith(b"%PDF")
    a = one(env, "SELECT item_id, qty FROM assignments"); assert a[0] == 1 and 0 < a[1] < 2           # skeins, not stitches
    wishes = rows(env, "SELECT name, project_id, unit FROM wishes")
    assert wishes and all(w[1] == 1 and w[2] == "skein" and w[0].startswith("DMC ") for w in wishes)
    assert (env.core.PHOTOS / one(env, "SELECT photo FROM projects")[0]).is_file()                    # the chart as its cover
    assert "Short by" in amy.html("/projects/1") and "DMC" in amy.html("/shopping")                 # short: on the shopping list
    assert one(env, "SELECT project_id, file_id FROM patterns") == (1, 1)
    amy.post("/patterns/1/project")                                                                    # again: only the chart
    assert one(env, "SELECT COUNT(*) FROM files")[0] == 1 and one(env, "SELECT COUNT(*) FROM assignments")[0] == 1
    assert not (env.core.UPLOADS / f[1]).exists() and "On <a" in amy.html("/patterns/1")
    amy.post("/projects/1/delete")
    assert one(env, "SELECT project_id, file_id FROM patterns") == (None, None) and "Make a project" in amy.html("/patterns/1")

def test_two_colours_on_one_item_are_added_together(env, amy):
    pt = sys.modules["patterns"]
    item = {"id": 7, "name": "Red", "colour": "", "unit": "skein", "free": 1.0, "pname": "", "tname": "", "can_edit": 1}
    chart = {"palette": [{"hex": "#C72B3B", "count": 1200, "symbol": "X", "item_id": 7, "match": "close"},
                         {"hex": "#B71F33", "count": 900, "symbol": "O", "item_id": 7, "match": "close"}]}
    with env.mod.app.test_request_context():
        pt.q = lambda *a: [item]; pt.uid = lambda: 1
        rows = pt.legend(None, chart, {"craft": "cross-stitch", "count": 14, "strands": 2, "per_unit": 0})
    assert [r["status"] for r in rows] == ["short", "short"] and all(r["shared"] for r in rows)   # 0.67 + 0.5 > 1 free
    assert round(rows[0]["short"], 2) == round(2100 / 1800 - 1, 2)

def test_the_wishlist_box_as_the_form_sends_it(env, amy):
    make(amy); amy.post("/patterns/1/project", {"wishes": ["0", "1"]})                   # ticked: the hidden 0, then the box
    assert one(env, "SELECT COUNT(*) FROM wishes")[0] > 0
    make(amy); amy.post("/patterns/2/project", {"wishes": ["0"]})                        # not ticked
    assert one(env, "SELECT COUNT(*) FROM wishes WHERE project_id=2")[0] == 0 and one(env, "SELECT COUNT(*) FROM projects")[0] == 2

def test_stock_in_other_units_is_not_reserved(env, amy):
    make(amy, colours="2")
    code = re.search(r"DMC (\d+) ", amy.html("/patterns/1"))[1]
    floss(amy, name=f"DMC {code}", unit="g"); amy.post("/patterns/1", {})
    assert "Check: stock is in g" in amy.html("/patterns/1")
    amy.post("/patterns/1/project")
    assert one(env, "SELECT COUNT(*) FROM assignments")[0] == 0 and "unit isn" in amy.html("/projects/1")

# ---------- privacy and clean-up ----------
def test_patterns_are_private(env, amy):
    make(amy); bob = env.client().register("bob")
    for u in ("/patterns/1", "/patterns/1/chart.png", "/patterns/1/source", "/patterns/1/pattern.pdf"): assert bob.get(u).status_code == 404, u
    assert bob.post("/patterns/1/delete").status_code == 404 and bob.post("/patterns/1/project").status_code == 404
    assert "heart" not in bob.html("/tools")

def test_deleting_a_pattern_or_its_owner_removes_its_files(env, amy):
    make(amy); amy.get("/patterns/1/chart.png")
    photo = env.core.PHOTOS / one(env, "SELECT photo FROM patterns")[0]
    amy.post("/patterns/1/delete")
    assert not photo.exists() and not list(env.core.PATTERN_CACHE.glob("1-*")) and one(env, "SELECT COUNT(*) FROM patterns")[0] == 0
    bob = env.client().register("bob"); make(bob); bob.get("/patterns/2/chart.png")
    photo = env.core.PHOTOS / one(env, "SELECT photo FROM patterns")[0]
    bob_id = one(env, "SELECT id FROM users WHERE username='bob'")[0]
    amy.post(f"/admin/users/{bob_id}/delete")
    assert not photo.exists() and not list(env.core.PATTERN_CACHE.glob("2-*")) and one(env, "SELECT COUNT(*) FROM patterns")[0] == 0

def test_patterns_per_person_are_limited(env, amy, monkeypatch):
    monkeypatch.setattr(sys.modules["patterns"], "PATTERNS_PER_USER", 1)
    make(amy); r = make(amy)
    assert r.status_code == 400 and "patterns already" in r.get_data(as_text=True)

# ---------- finding the tool ----------
def test_the_tools_tab_and_the_collection_menu(env, amy):
    assert 'href="/tools"' in amy.html("/") and "Pixel pattern maker" in amy.html("/tools")
    amy.post("/collections", {"name": "DMC threads", "craft": "Cross stitch"}); amy.post("/collections", {"name": "Fabric", "craft": "Sewing"})
    assert 'href="/patterns/new?palette=pool:1&amp;check=pool:1"' in amy.html("/collections/1")
    assert "Pixel pattern maker with these colours" not in amy.html("/collections/2")
    page = amy.html("/patterns/new?palette=pool:1&check=pool:1")
    assert 'value="pool:1" selected' in page
    assert "MakeBead" in amy.html("/tools")                                               # the catalogue credit

# ---------- the API ----------
def test_api_patterns(env, amy):
    get, post = api(env, make_token(amy, "write"))
    c = env.client().c; h = {"Authorization": f"Bearer {make_token(amy, 'write', 'w2')}"}
    r = c.post("/api/v1/patterns", headers=h, data={"photo": (io.BytesIO(picture()), "heart.png"), "craft": "beads", "width": "30", "colours": "4"},
               content_type="multipart/form-data")
    assert r.status_code == 201
    p = r.get_json(); assert p["craft"] == "beads" and p["width"] == 30 and p["settings"]["palette"] == "catalogue:perler-midi"
    assert p["legend"][0]["unit"] == "bead" and p["legend"][0]["status"] == "buy" and p["legend"][0]["label"].startswith("Perler ")
    assert [x["id"] for x in get("/api/v1/patterns").get_json()["patterns"]] == [p["id"]]
    assert get(f"/api/v1/patterns/{p['id']}").get_json()["legend"]
    r = post(f"/api/v1/patterns/{p['id']}/project", json={"wishes": False})
    assert r.status_code == 201 and r.get_json()["project"]["status"] == "planned" and one(env, "SELECT COUNT(*) FROM wishes")[0] == 0
    r = c.post("/api/v1/patterns", headers=h, data={"craft": "beads"}, content_type="multipart/form-data")
    assert r.status_code == 400 and "picture" in r.get_json()["error"]
    ro = {"Authorization": f"Bearer {make_token(amy, 'read', 'ro')}"}
    assert c.post("/api/v1/patterns", headers=ro, data={}).status_code == 403
    bob = env.client().register("bob"); bget, _ = api(env, make_token(bob, "read"))
    assert bget(f"/api/v1/patterns/{p['id']}").status_code == 404

# ---------- upgrades ----------
@pytest.mark.sqlite_only
def test_an_older_sqlite_database_gets_the_table(make_env, tmp_path):
    make_env(); d = sqlite3.connect(tmp_path / "craft.db")
    d.execute("DROP TABLE patterns"); d.execute("PRAGMA user_version=10"); d.commit(); d.close()
    make_env(); d = sqlite3.connect(tmp_path / "craft.db")
    assert d.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] == 0 and d.execute("PRAGMA user_version").fetchone()[0] == 11
    assert list((tmp_path / "backups").glob("*"))                                       # backed up first

@pytest.mark.skipif(not TEST_DB, reason="needs a server database")
def test_a_server_database_without_the_table_gets_it(make_env):
    env = make_env(); import dbx, migrations
    c = dbx.connect(dbx.CFG); cur = c.raw.cursor(); cur.execute("DROP TABLE patterns"); c.commit(); c.close()
    c = dbx.connect(dbx.CFG); migrations.run_server(c); c.close()
    c = dbx.connect(dbx.CFG); assert c.execute("SELECT COUNT(*) FROM patterns").fetchone()[0] == 0; c.close()

def test_a_big_chart_survives_the_database(env, amy):
    """A 250 x 250 dithered chart of noise is the biggest a chart column has to hold (MariaDB: MEDIUMTEXT)."""
    import random; random.seed(3)
    im = Image.new("RGB", (250, 250)); im.putdata([tuple(random.randrange(256) for _ in range(3)) for _ in range(250 * 250)])
    b = io.BytesIO(); im.save(b, "PNG")
    r = amy.post("/patterns/new", {"photo": (io.BytesIO(b.getvalue()), "noise.png"), "width": "250", "height": "250", "colours": "60",
                                   "dither": "1", "palette": "free"}, content_type="multipart/form-data")
    assert r.status_code == 302
    chart = sys.modules["patternkit"].unpack(one(env, "SELECT chart FROM patterns")[0])
    assert (chart["w"], chart["h"]) == (250, 250) and len(chart["cells"]) == 62500
