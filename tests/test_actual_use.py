"""Completing a project can record how much of each material was really used."""
import sqlite3
from conftest import seed

def qty(env, iid): return env.db().execute("SELECT qty FROM items WHERE id=?", (iid,)).fetchone()[0]
def used(env): return dict(env.db().execute("SELECT item_id, used FROM assignments WHERE project_id=1"))

def setup(amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"}); amy.post("/projects/1/assign", {"item_id": "2", "qty": "1"})

def test_finish_page_prefills_reserved_amounts(env, amy):
    setup(amy); html = amy.html("/projects/1/finish")
    assert 'name="used_1" type="number" step="any" min="0" value="3"' in html and 'name="used_2"' in html
    assert 'href="/projects/1/finish"' in amy.html("/projects/1")

def test_entered_amounts_are_deducted(env, amy):
    setup(amy)
    amy.post("/projects/1/complete", {"stock": "use", "used_1": "1.5", "used_2": "0"})
    assert qty(env, 1) == 3.5 and qty(env, 2) == 1 and used(env) == {1: 1.5, 2: 0}
    page = amy.html("/projects/1"); assert "Used 1.5" in page and "Not deducted" in page
    amy.post("/projects/1/reopen"); assert qty(env, 1) == 5 and qty(env, 2) == 1

def test_using_more_than_reserved_is_capped_at_stock(env, amy):
    setup(amy)
    page = amy.post("/projects/1/complete", {"stock": "use", "used_1": "4", "used_2": "9"}, follow_redirects=True).get_data(as_text=True)
    assert qty(env, 1) == 1 and qty(env, 2) == 0 and used(env) == {1: 4, 2: 1} and "less in stock" in page

def test_bad_amounts_fall_back_to_reserved(env, amy):
    setup(amy)
    amy.post("/projects/1/complete", {"stock": "use", "used_1": "-2", "used_2": "abc"})
    assert used(env) == {1: 3, 2: 1}

def test_keep_stock_ignores_amounts(env, amy):
    setup(amy); amy.post("/projects/1/complete", {"stock": "keep", "used_1": "2"})
    assert qty(env, 1) == 5 and used(env) == {1: 0, 2: 0}

def test_finish_needs_edit_rights(env, amy, bob):
    setup(amy); amy.post("/projects/1/share", {"username": "bob"})
    assert bob.get("/projects/1/finish").status_code == 404
    amy.post("/projects/1/complete"); assert amy.get("/projects/1/finish").status_code == 302
