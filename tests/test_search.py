"""One search across every collection the user can see."""
import json, sqlite3
from conftest import seed

def test_search_finds_items_in_every_collection(env, amy):
    seed(amy)
    amy.post("/collections", {"name": "Sewing"}); amy.post("/collections/2/types", {"name": "Thread", "unit": "spool"})
    amy.post("/types/2/items/new", {"name": "Gutermann", "colour": "Rose", "qty": "2"})
    html = amy.html("/search?q=rose")
    assert "Merino" in html and "Gutermann" in html and "Knitting" in html and "Sewing" in html and "Cotton" not in html

def test_search_covers_location_fields_and_keyed_terms(env, amy):
    seed(amy)
    fid = json.loads(env.db().execute("SELECT fields FROM types WHERE id=1").fetchone()[0])[0]["id"]
    amy.post("/items/1/edit", {"name": "Merino", "colour": "Rose", "qty": "5", "location": "Drawer 3", "x_" + fid: "Drops"})
    assert "Merino" in amy.html("/search?q=drawer 3")
    html = amy.html("/search?q=Brand:drops"); assert "Merino" in html and "Cotton" not in html
    assert "Nothing found" in amy.html("/search?q=Brand:rose")   # a keyed term only looks at that field

def test_search_includes_shared_but_not_private_collections(env, amy, bob):
    seed(amy); bob.post("/collections", {"name": "Bob's"}); bob.post("/collections/2/types", {"name": "Yarn"})
    bob.post("/types/2/items/new", {"name": "Secret rose", "qty": "1"})
    assert "Secret rose" not in amy.html("/search?q=rose")
    bob.post("/collections/2/share", {"username": "amy"})
    html = amy.html("/search?q=rose"); assert "Secret rose" in html and "from bob" in html

def test_search_finds_projects_and_escapes_wildcards(env, amy):
    seed(amy)
    assert "/projects/1" in amy.html("/search?q=hat")
    html = amy.html("/search?q=%25"); assert "Nothing found" in html

def test_search_page_without_term_and_login_required(env, amy):
    assert "Search everything" in amy.html("/search")
    assert env.client().get("/search?q=x").status_code == 302

def test_collection_search_still_works(env, amy):
    seed(amy)
    html = amy.html("/collections/1?q=blue"); assert "Cotton" in html and "Merino" not in html
