"""Add from a catalogue (issue #42): pick the DMC flosses you own, each filled in with number, name, family and swatch."""
import json, re
import pytest

def floss(amy):
    amy.post("/collections", {"name": "Embroidery"})
    amy.post("/collections/1/types", {"name": "Embroidery floss", "unit": "skein", "fields": "Brand\nColour number"})

def items(env):
    return env.db().execute("SELECT name, colour, hex, qty, location, extra FROM items ORDER BY id").fetchall()

def fields(env):
    return {f["label"]: f for f in json.loads(env.db().execute("SELECT fields FROM types WHERE id=1").fetchone()[0])}

@pytest.mark.parametrize("name,hexv,fam", [("Black", "#000000", "Black"), ("Snow White", "#FFFFFF", "White"), ("Yellow Green Medium", "#8FA359", "Green"),
    ("Beige Gray Dark", "#A2998C", "Grey"), ("Salmon Very Light", "#FFE2E2", "Pink"), ("Salmon Very Dark", "#BF2D2D", "Red"),
    ("Yellow Plum", "#D9D56D", "Yellow"), ("Avocado Grn Black", "#313919", "Green"), ("Apple Blossom", "#EDE2ED", "Pink"), ("Grape Very Dark", "#572433", "Purple"),
    ("Mystery", "#2050C0", "Blue")])
def test_colour_family(env, name, hexv, fam):
    import catalogue
    assert catalogue.family(name, hexv) == fam

def test_every_bundled_colour_gets_a_family(env):
    import catalogue, patternkit as pk
    for cid in pk.CATALOGUES:
        rows = catalogue.colours(cid)
        assert rows and {r["family"] for r in rows} <= set(catalogue.FAMILIES)
        assert len({r["code"] for r in rows}) == len(rows)
    assert len(catalogue.colours("dmc-floss")) == 489

def test_pick_and_add_dmc_floss(env, amy):
    floss(amy)
    page = amy.html("/types/1/catalogue")
    assert page.count('name="pick"') == 489 and "310" in page and "Black" in page and "--c:#000000" in page
    assert 'href="/types/1/catalogue"' in amy.html("/collections/1")
    r = amy.post("/types/1/catalogue", {"c": "dmc-floss", "pick": ["310", "3713", "Blanc"], "qty_310": "3", "loc_310": "Box B",
                                        "qty_3713": "", "location": "Box A", "qty_666": "9"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/collections/1#type-1")
    rows = items(env)
    got = {n: (c, h, q, l) for n, c, h, q, l, _ in rows}
    assert got == {"DMC 3713": ("Salmon Very Light", "#FFE2E2", 1, "Box A"), "DMC 310": ("Black", "#000000", 3, "Box B"),
                   "DMC Blanc": ("White", "#FCFBF8", 1, "Box A")}           # blank qty is 1; unticked 666 ignored
    f = fields(env); x = json.loads(next(e for n, *_, e in rows if n == "DMC 310"))
    assert f["Colour family"]["type"] == "choice" and "Purple" in f["Colour family"]["options"]
    assert x == {f["Brand"]["id"]: "DMC", f["Colour number"]["id"]: "310", f["Colour family"]["id"]: "Black"}   # existing fields reused
    assert "Added 3 DMC floss colours" in amy.html("/collections/1/activity")

def test_colours_already_there_are_marked_and_never_added_twice(env, amy):
    floss(amy)
    amy.post("/types/1/items/new", {"name": "DMC 666 Bright Red", "qty": "2"})      # added by hand before
    amy.post("/types/1/catalogue", {"c": "dmc-floss", "pick": ["310"]})
    page = amy.html("/types/1/catalogue")
    assert page.count("In stash") >= 3 and page.count('name="pick"') == 487
    r = amy.post("/types/1/catalogue", {"c": "dmc-floss", "pick": ["310", "666", "321"]}, follow_redirects=True)
    assert "Added 1 DMC floss colour." in r.get_data(as_text=True) and "2 you ticked were here already" in r.get_data(as_text=True)
    assert [n for n, *_ in items(env)] == ["DMC 666 Bright Red", "DMC 310", "DMC 321"]
    r = amy.post("/types/1/catalogue", {"c": "dmc-floss", "pick": ["310"]}, follow_redirects=True)
    assert len(items(env)) == 3 and "in this type already" in r.get_data(as_text=True)

def test_codes_dont_match_other_brands_or_other_numbers(env, amy):
    floss(amy); amy.post("/collections", {"name": "Sewing"}); amy.post("/collections/2/types", {"name": "Thread", "unit": "spool", "fields": "Length (m)"})
    amy.post("/types/2/items/new", {"name": "Gutermann 310", "qty": "1", "x_" + json.loads(env.db().execute("SELECT fields FROM types WHERE id=2").fetchone()[0])[0]["id"]: "3713"})
    import catalogue
    with env.mod.app.test_request_context():
        from flask import g; g.user = {"id": 1}
        import core; assert catalogue.owned(core.type_of(2), "dmc-floss") == set()   # no DMC anywhere, and lengths aren't codes

def test_nothing_ticked_and_bad_catalogue(env, amy):
    floss(amy)
    r = amy.post("/types/1/catalogue", {"c": "dmc-floss"})
    assert r.status_code == 302 and not items(env)
    assert amy.get("/types/1/catalogue?c=../../etc/passwd").status_code == 404
    assert amy.get("/types/1/catalogue?c=nope").status_code == 404

def test_other_catalogues_and_defaults(env, amy):
    amy.post("/collections", {"name": "Beads"}); amy.post("/collections/1/types", {"name": "Hama beads", "unit": "bead"})
    assert amy.html("/types/1/catalogue").count('name="pick"') == 92                        # picked from the type's name
    assert amy.html("/types/1/catalogue?c=artkal-s").count('name="pick"') == 199
    import patternkit as pk; code = pk.catalogue("artkal-s")["colors"][0][0]
    amy.post("/types/1/catalogue", {"c": "artkal-s", "pick": [code]})
    assert [n for n, *_ in items(env)] == [f"Artkal {code}"]

def test_only_editors_can_add(env, amy, bob):
    floss(amy); amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    assert bob.get("/types/1/catalogue").status_code == 404
    assert bob.post("/types/1/catalogue", {"c": "dmc-floss", "pick": ["310"]}).status_code == 404 and not items(env)
    assert 'href="/types/1/catalogue"' not in bob.html("/collections/1")

def test_the_pattern_maker_finds_added_floss_by_number(env, amy):
    floss(amy); amy.post("/types/1/catalogue", {"c": "dmc-floss", "pick": ["310"]})
    import patterns
    with env.mod.app.test_request_context():
        from flask import g; g.user = {"id": 1}
        pal = patterns.match_stash([{"code": "310", "brand": "DMC", "hex": "#000000"}, {"code": "321", "brand": "DMC", "hex": "#C72B3B"}],
                                   {"check": "all"})
    assert pal[0]["match"] == "code" and pal[1]["match"] == "none"
