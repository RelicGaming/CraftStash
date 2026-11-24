"""Completing a project can leave stock untouched."""
import sqlite3
from conftest import seed

def state(env):
    d = env.db()
    return (d.execute("SELECT qty FROM items WHERE id=1").fetchone()[0],
            d.execute("SELECT consumed, used FROM assignments WHERE project_id=1").fetchone(),
            d.execute("SELECT status FROM projects WHERE id=1").fetchone()[0])

def test_complete_keeping_stock_leaves_quantities_alone(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"})
    page = amy.post("/projects/1/complete", {"stock": "keep"}, follow_redirects=True).get_data(as_text=True)
    assert state(env) == (5, (1, 0), "done") and "left as it was" in page and "Not deducted" in page
    assert not env.db().execute("SELECT 1 FROM activity WHERE kind='project'").fetchone()

def test_reopen_after_keeping_stock_returns_nothing(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"}); amy.post("/projects/1/complete", {"stock": "keep"})
    assert "go back into stock" not in amy.html("/projects/1")
    amy.post("/projects/1/reopen"); assert state(env) == (5, (0, 0), "active")

def test_default_complete_still_deducts(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"})
    amy.post("/projects/1/complete", {"stock": "use"}); assert state(env) == (2, (1, 3), "done")
    assert "go back into stock" in amy.html("/projects/1")

def test_complete_without_a_choice_deducts_like_before(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"})
    amy.post("/projects/1/complete"); assert state(env) == (2, (1, 3), "done")

def test_project_page_offers_both_choices(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "3"})
    html = amy.html("/projects/1")
    assert 'name="stock" value="keep"' in html and 'href="/projects/1/finish"' in html
