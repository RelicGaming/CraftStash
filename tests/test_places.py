"""An item's stock can be split across several storage places."""
import sqlite3
from conftest import seed

MERINO = {"name": "Merino", "colour": "Rose", "qty": "5", "location": "Drawer 3"}

def places(env): return dict(sqlite3.connect(env.data / "craft.db").execute("SELECT place, qty FROM item_places WHERE item_id=1 ORDER BY place"))
def qty(env): return sqlite3.connect(env.data / "craft.db").execute("SELECT qty FROM items WHERE id=1").fetchone()[0]
def edit(c, names, amounts, **kw): return c.post("/items/1/edit", {**MERINO, **kw, "place_name": names, "place_qty": amounts}, follow_redirects=True)

def test_edit_saves_places_and_shows_the_split(env, amy):
    seed(amy); edit(amy, ["Shelf B", "", "shelf b", "Drawer 3"], ["2", "4", "1", "1"])
    assert places(env) == {"Shelf B": 3} and qty(env) == 5                      # merged case-insensitively; main location skipped
    html = amy.html("/collections/1"); assert "Drawer 3: 2" in html and "Shelf B: 3" in html
    assert 'value="Shelf B"' in amy.html("/items/1/edit")
    edit(amy, [""], [""]); assert places(env) == {}

def test_places_larger_than_total_raise_the_total(env, amy):
    seed(amy); page = edit(amy, ["Shelf B", "Box"], ["4", "3"]).get_data(as_text=True)
    assert qty(env) == 7 and "total in stock is now 7" in page

def test_using_from_a_place_takes_it_from_there(env, amy):
    seed(amy); edit(amy, ["Shelf B"], ["3"])
    amy.post("/items/1/adjust", {"action": "use", "amount": "5", "place": "Shelf B"})   # capped at what the shelf holds
    assert qty(env) == 2 and places(env) == {}
    edit(amy, ["Shelf B"], ["1"], qty="2"); amy.post("/items/1/adjust", {"action": "add", "amount": "2", "place": "Shelf B"})
    assert qty(env) == 4 and places(env) == {"Shelf B": 3}

def test_using_from_the_main_location_shrinks_places_only_when_needed(env, amy):
    seed(amy); edit(amy, ["Shelf B", "Box"], ["2", "2"])
    amy.post("/items/1/adjust", {"action": "use", "amount": "1"}); assert places(env) == {"Box": 2, "Shelf B": 2}
    amy.post("/items/1/adjust", {"action": "use", "amount": "2"}); assert qty(env) == 2 and places(env) == {"Box": 1, "Shelf B": 1}

def test_move_between_places_keeps_the_total(env, amy):
    seed(amy)
    amy.post("/items/1/move", {"amount": "2", "from": "", "to": "Shelf B"}); assert places(env) == {"Shelf B": 2}
    amy.post("/items/1/move", {"amount": "1", "from": "Shelf B", "to": "Box"}); assert places(env) == {"Box": 1, "Shelf B": 1}
    amy.post("/items/1/move", {"amount": "1", "from": "Box", "to": "drawer 3"}); assert places(env) == {"Shelf B": 1}
    page = amy.post("/items/1/move", {"amount": "9", "from": "", "to": "Box"}, follow_redirects=True).get_data(as_text=True)
    assert "only has 4" in page and qty(env) == 5 and places(env) == {"Shelf B": 1}
    assert "Moved 2 skein of Merino from Drawer 3 to Shelf B" in amy.html("/collections/1/activity")

def test_completing_a_project_and_search_see_places(env, amy):
    seed(amy); edit(amy, ["Shelf B"], ["4"])
    amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"}); amy.post("/projects/1/complete")
    assert qty(env) == 2 and places(env) == {"Shelf B": 2}            # main had 1, so the shelf gives up the rest in proportion
    assert "Merino" in amy.html("/collections/1?q=shelf")

def test_places_need_edit_rights_and_go_with_the_item(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob"})
    assert bob.post("/items/1/move", {"amount": "1", "to": "Box"}).status_code == 404
    edit(amy, ["Shelf B"], ["1"]); amy.post("/items/1/delete")
    assert sqlite3.connect(env.data / "craft.db").execute("SELECT COUNT(*) FROM item_places").fetchone()[0] == 0
