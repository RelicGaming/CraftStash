"""Setting up a collection: new collection, its first material types, then items."""
import json, sqlite3
from conftest import seed

def db(env): return env.db()
def fields(env, tid): return json.loads(db(env).execute("SELECT fields FROM types WHERE id=?", (tid,)).fetchone()[0])

def test_new_collection_opens_it_ready_for_a_first_type(amy):
    r = amy.post("/collections", {"name": "Knitting"})
    assert r.headers["Location"].endswith("/collections/1")
    page = amy.html("/collections/1")
    assert "Add your first material type" in page and 'value="yarn"' in page and "Import a CSV" in page
    assert 'name="q"' not in page                       # no search box while there is nothing to search
    assert page.index('value="yarn"') < page.index('value="fabric"')   # presets that suit the craft come first

def test_blank_collection_name_says_why(amy):
    page = amy.post("/collections", {"name": "  "}, follow_redirects=True).get_data(as_text=True)
    assert "Give the collection a name" in page

def test_preset_creates_typed_fields_and_goes_to_first_item(env, amy):
    amy.post("/collections", {"name": "Knitting"})
    r = amy.post("/collections/1/types", {"preset": "yarn"})
    assert r.headers["Location"].endswith("/types/1/items/new")
    assert db(env).execute("SELECT name, unit FROM types").fetchone() == ("Yarn", "skein")
    f = {x["label"]: x for x in fields(env, 1)}
    assert f["Weight"]["type"] == "choice" and "DK" in f["Weight"]["options"]
    assert f["Length per skein (m)"]["type"] == "number" and f["Fibre"]["type"] == "text"
    page = amy.html("/types/1/items/new")
    assert "Yarn is ready" in page and "Save and add another" in page and ">Worsted</option>" in page
    assert 'value="yarn"' not in amy.html("/collections/1")    # a preset already used isn't offered again

def test_starter_fields_one_per_line(env, amy):
    amy.post("/collections", {"name": "Sewing"})
    amy.post("/collections/1/types", {"name": "Fabric", "unit": "m", "fields": "Width (cm)\nFibre: Cotton, Linen\nPattern\nwidth (cm)"})
    assert [(f["label"], f["type"], f["options"]) for f in fields(env, 1)] == [
        ("Width (cm)", "number", []), ("Fibre", "choice", ["Cotton", "Linen"]), ("Pattern", "text", [])]

def test_starter_fields_still_take_a_comma_list(env, amy):
    amy.post("/collections", {"name": "Sewing"})
    amy.post("/collections/1/types", {"name": "Fabric", "fields": "Width, Fibre"})
    assert [f["label"] for f in fields(env, 1)] == ["Width", "Fibre"]
    assert db(env).execute("SELECT unit FROM types").fetchone()[0] == "pcs"

def test_blank_type_name_says_why(env, amy):
    amy.post("/collections", {"name": "Sewing"})
    page = amy.post("/collections/1/types", {"name": ""}, follow_redirects=True).get_data(as_text=True)
    assert "Give the material type a name" in page and db(env).execute("SELECT COUNT(*) FROM types").fetchone()[0] == 0

def test_save_and_add_another_stays_in_the_form(env, amy):
    seed(amy)
    r = amy.post("/types/1/items/new", {"name": "Alpaca", "qty": "2", "then": "another"})
    assert r.headers["Location"].endswith("/types/1/items/new")
    assert "Added Alpaca" in amy.html("/types/1/items/new")
    r = amy.post("/types/1/items/new", {"name": "Silk", "qty": "1"})
    iid = db(env).execute("SELECT id FROM items WHERE name='Silk'").fetchone()[0]
    assert r.headers["Location"].endswith(f"/collections/1#item-{iid}")
    assert f'id="item-{iid}"' in amy.html("/collections/1")

def test_editing_never_offers_add_another(amy):
    seed(amy)
    assert "Save and add another" not in amy.html("/items/1/edit")
    r = amy.post("/items/1/edit", {"name": "Merino", "qty": "5", "location": "Drawer 3", "then": "another"})
    assert "/collections/1#item-1" in r.headers["Location"]

def test_existing_collection_offers_another_type_and_empty_types_link_to_add(amy):
    seed(amy); amy.post("/collections/1/types", {"name": "Buttons"})
    page = amy.html("/collections/1")
    assert "Add another material type" in page and "Add your first material type" not in page and 'name="q"' in page
    assert "Add the first one" in page                  # Buttons has no items yet

def test_viewer_of_empty_collection_gets_no_setup_form(amy, bob):
    amy.post("/collections", {"name": "Knitting"}); amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    page = bob.html("/collections/1")
    assert "Nothing here yet" in page and "Add your first material type" not in page
    assert bob.post("/collections/1/types", {"preset": "yarn"}).status_code == 404

def test_fields_page_names_dropdowns_and_links_to_adding(amy):
    seed(amy)
    page = amy.html("/types/1/fields")
    assert ">Dropdown</option>" in page and "/types/1/items/new" in page
    amy.post("/types/1/fields", {"label": "Weight", "type": "choice", "options": "DK, Aran"})
    assert '<option value="choice" selected>Dropdown</option>' in amy.html("/types/1/fields")
