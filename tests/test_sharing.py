import io
from conftest import seed

def test_strangers_see_nothing(env, amy, bob):
    seed(amy); amy.post("/projects/1/steps", {"body": "x"})
    for u in ("/collections/1", "/projects/1", "/items/1/edit", "/collections/1/activity", "/collections/1/export.csv", "/photos/1"):
        assert bob.get(u).status_code == 404, u

def test_view_only_vs_can_edit_collection(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    assert bob.get("/collections/1").status_code == 200
    assert bob.post("/types/1/items/new", {"name": "x"}).status_code == 404
    assert bob.post("/items/1/adjust", {"amount": "1", "action": "use"}).status_code == 404
    amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    assert bob.post("/items/1/adjust", {"amount": "1", "action": "use"}).status_code == 302

def test_leaving_a_collection_drops_your_reservations(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/projects", {"name": "Scarf"}); bob.post("/projects/2/assign", {"item_id": "1", "qty": "2"})
    assert "<strong>3</strong>" in amy.html("/collections/1") or "3 free" in amy.html("/collections/1")
    bob.post("/collections/1/leave")
    assert bob.get("/collections/1").status_code == 404 and "2 free" not in amy.html("/collections/1")

def test_shared_project_is_read_only_and_can_be_copied(env, amy, bob):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "2"}); amy.post("/projects/1/steps", {"title": "Cast on", "body": "40 stitches"})
    amy.post("/projects/1/upload", {"file": (io.BytesIO(b"%PDF-1.4 pattern"), "pattern.pdf")}); amy.post("/projects/1/share", {"username": "bob"})
    view = bob.html("/projects/1"); assert "Cast on" in view and "Add step" not in view and "Merino" in view
    assert bob.post("/projects/1/update", {"name": "hacked"}).status_code == 404
    bob.post("/projects/1/copy"); mine = bob.html("/projects/2")
    assert "Materials from the original" in mine and "2 skein Merino" in mine and "Cast on" in mine and "pattern.pdf" in mine
    amy.post("/projects/1/delete"); assert bob.get("/files/2").status_code == 200       # the copy has its own file

def test_owner_duplicate_copies_reservations(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "2"}); amy.post("/projects/1/copy")
    assert "Hat (copy)" in amy.html("/projects/2") and "<strong>2 skein</strong>" in amy.html("/projects/2")

def test_unsharing_removes_their_reservations(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/projects", {"name": "Scarf"}); bob.post("/projects/2/assign", {"item_id": "1", "qty": "2"})
    amy.post("/collections/1/unshare/2")
    assert "Merino" not in bob.html("/projects/2")
