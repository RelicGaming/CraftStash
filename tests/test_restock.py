"""Restock several shopping-list items at once."""
import sqlite3
from conftest import seed

def qtys(env): return dict(env.db().execute("SELECT id, qty FROM items"))

def test_shopping_list_suggests_amounts(env, amy):
    seed(amy)                                                 # Merino 5 (low at 2), Cotton 1
    amy.post("/items/1/adjust", {"action": "use", "amount": "3"})   # Merino 2: at its level, still low
    amy.post("/projects/1/assign", {"item_id": "2", "qty": "4"})   # Cotton short by 3
    html = amy.html("/shopping")
    assert 'name="bought_1"' in html and 'data-suggest="1"' in html and 'data-suggest="3"' in html and 'id="fill"' in html

def test_bulk_restock_adds_each_amount_and_logs_it(env, amy):
    seed(amy); amy.post("/items/1/adjust", {"action": "use", "amount": "4"})
    page = amy.post("/shopping/restock", {"bought_1": "6", "bought_2": "", "bought_x": "3", "other": "1"}, follow_redirects=True).get_data(as_text=True)
    assert qtys(env) == {1: 7, 2: 1} and "Restocked 1 item." in page
    assert env.db().execute("SELECT delta FROM activity WHERE item_id=1 ORDER BY id DESC").fetchone()[0] == 6
    amy.post("/shopping/restock", {"bought_1": "1", "bought_2": "2.5"}); assert qtys(env) == {1: 8, 2: 3.5}

def test_bulk_restock_ignores_bad_amounts(env, amy):
    seed(amy)
    page = amy.post("/shopping/restock", {"bought_1": "-4", "bought_2": "nan"}, follow_redirects=True).get_data(as_text=True)
    assert qtys(env) == {1: 5, 2: 1} and "at least one item" in page

def test_bulk_restock_needs_edit_rights(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob"})          # view only
    page = bob.post("/shopping/restock", {"bought_2": "5"}, follow_redirects=True).get_data(as_text=True)
    assert qtys(env)[2] == 1 and "skipped" in page
    assert 'name="bought_2"' not in bob.html("/shopping")
    amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/shopping/restock", {"bought_2": "5"}); assert qtys(env)[2] == 6
