"""Projects can be saved as templates and new projects started from them."""
import sqlite3
from conftest import seed

def db(env): return sqlite3.connect(env.data / "craft.db")
def free(env, iid): return db(env).execute("SELECT free FROM stock WHERE id=?", (iid,)).fetchone()[0]

def make_template(amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "2"})
    amy.post("/projects/1/steps", {"title": "Cast on", "body": "80 stitches"})
    return amy.post("/projects/1/copy", {"as": "template"}, follow_redirects=True).get_data(as_text=True)

def test_save_as_template_keeps_name_and_holds_no_stock(env, amy):
    page = make_template(amy)
    assert "Saved as a template" in page and "Start a project" in page
    assert db(env).execute("SELECT name, status FROM projects WHERE id=2").fetchone() == ("Hat", "template")
    assert free(env, 1) == 3                             # only the real project's 2 are reserved, not the template's
    amy.post("/projects/2/assign", {"item_id": "2", "qty": "9"})
    assert free(env, 2) == 1 and "Cotton" not in amy.html("/shopping")

def test_starting_a_project_copies_materials_and_steps(env, amy):
    make_template(amy)
    page = amy.post("/projects/2/copy", follow_redirects=True).get_data(as_text=True)
    assert "Project started from the template" in page
    assert db(env).execute("SELECT name, status FROM projects WHERE id=3").fetchone() == ("Hat", "planned")
    assert db(env).execute("SELECT item_id, qty FROM assignments WHERE project_id=3").fetchall() == [(1, 2)]
    assert db(env).execute("SELECT title FROM steps WHERE project_id=3").fetchall() == [("Cast on",)]
    assert free(env, 1) == 1

def test_templates_cannot_be_completed_or_change_status(env, amy):
    make_template(amy)
    amy.post("/projects/2/complete"); amy.post("/projects/2/update", {"name": "Hat", "status": "active"})
    assert db(env).execute("SELECT status FROM projects WHERE id=2").fetchone()[0] == "template"
    assert db(env).execute("SELECT qty FROM items WHERE id=1").fetchone()[0] == 5
    html = amy.html("/projects/2")
    assert "Mark complete" not in html and "Save as template" not in html and 'name="status"' not in html

def test_projects_page_lists_templates_separately(env, amy):
    seed(amy); assert "Save as template" in amy.html("/projects/1") and "Templates" in amy.html("/projects")
    amy.post("/projects/1/copy", {"as": "template"})
    html = amy.html("/projects"); assert html.index("Templates") < html.index('href="/projects/2"')

def test_shared_template_copy_becomes_a_written_list(env, amy, bob):
    make_template(amy); amy.post("/projects/2/share", {"username": "bob"})
    assert "Save a copy" in bob.html("/projects/2") and "Save as template" not in bob.html("/projects/2")
    bob.post("/projects/2/copy")
    row = db(env).execute("SELECT status, notes FROM projects WHERE id=3").fetchone()
    assert row[0] == "planned" and "Merino" in row[1]
