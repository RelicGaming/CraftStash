"""Token-based JSON API, for Home Assistant and other tools."""
import re, sqlite3
from conftest import seed

def db(env): return sqlite3.connect(env.data / "craft.db")

def make_token(c, access="read", name="Home Assistant"):
    html = c.post("/account/tokens", {"name": name, "access": access}).get_data(as_text=True)
    return re.search(r'value="(cs_[\w-]+)"', html)[1]

def api(env, tok):
    """A client with no browser session at all, like Home Assistant."""
    c = env.client().c
    h = {"Authorization": f"Bearer {tok}"}
    return (lambda url, **kw: c.get(url, headers=h, **kw)), (lambda url, json=None, **kw: c.post(url, headers=h, json=json, **kw))

def test_token_is_shown_once_and_stored_only_as_a_hash(env, amy):
    tok = make_token(amy)
    assert tok.startswith("cs_") and len(tok) > 40
    stored = db(env).execute("SELECT hash, can_write FROM api_tokens").fetchone()
    assert tok not in stored[0] and len(stored[0]) == 64 and stored[1] == 0
    page = amy.html("/account")
    assert "Home Assistant" in page and tok not in page and "never used" in page

def test_requests_without_a_valid_token_are_refused(env, amy):
    c = env.client().c
    r = c.get("/api/v1/summary")
    assert r.status_code == 401 and r.is_json and "Bearer" in r.headers["WWW-Authenticate"]
    assert c.get("/api/v1/summary", headers={"Authorization": "Bearer cs_wrong"}).status_code == 401
    assert c.get("/api/v1/nope", headers={"Authorization": "Bearer cs_wrong"}).status_code == 401
    amy_session = amy.get("/api/v1/summary")                 # a logged-in browser session is not enough either
    assert amy_session.status_code == 401

def test_bad_tokens_are_throttled_per_address(env, amy):
    c = env.client().c
    for _ in range(8): c.get("/api/v1", headers={"Authorization": "Bearer cs_guess"})
    assert c.get("/api/v1", headers={"Authorization": "Bearer cs_guess"}).status_code == 429
    tok = make_token(amy)                                     # someone at another address isn't locked out
    r = c.get("/api/v1", headers={"Authorization": f"Bearer {tok}"}, environ_base={"REMOTE_ADDR": "10.0.0.2"})
    assert r.status_code == 200

def test_summary_and_shopping_list(env, amy):
    seed(amy); amy.post("/items/2/edit", {"name": "Cotton", "colour": "Blue", "qty": "0"})   # Cotton is out of stock
    get, _ = api(env, make_token(amy))
    s = get("/api/v1/summary").get_json()
    assert s["shopping_list"] == 1 and s["out_of_stock"] == 1 and s["items"] == 2 and s["stash_value"] == 47.5
    assert get("/api/v1/summary").headers["Cache-Control"] == "no-store"
    shop = get("/api/v1/shopping").get_json()
    assert shop["count"] == 1 and shop["items"][0]["name"] == "Cotton" and shop["items"][0]["status"] == "out"
    assert shop["items"][0]["suggested"] == 1 and shop["items"][0]["can_edit"] is True

def test_read_only_token_cannot_change_stock(env, amy):
    seed(amy); _, post = api(env, make_token(amy))
    r = post("/api/v1/items/1/adjust", {"action": "use", "amount": 1})
    assert r.status_code == 403 and "read-only" in r.get_json()["error"]
    assert db(env).execute("SELECT qty FROM items WHERE id=1").fetchone()[0] == 5

def test_write_token_restocks_and_adjusts_with_activity_log(env, amy):
    seed(amy); amy.post("/items/2/edit", {"name": "Cotton", "colour": "Blue", "qty": "0"})
    get, post = api(env, make_token(amy, "write"))
    r = post("/api/v1/shopping/restock", {"items": [{"id": 2}]}).get_json()          # no amount: the suggested amount
    assert r["restocked"] == [{"id": 2, "name": "Cotton", "added": 1, "qty": 1}] and r["shopping_list"] == 0
    item = post("/api/v1/items/1/adjust", {"action": "use", "amount": 2}).get_json()
    assert item["qty"] == 3 and item["name"] == "Merino"
    assert post("/api/v1/items/1/adjust", {"action": "eat", "amount": 2}).status_code == 400
    assert post("/api/v1/items/1/adjust", {"action": "use", "amount": -2}).status_code == 400
    assert post("/api/v1/items/1/adjust", {"action": "use", "amount": 1, "place": "Moon"}).status_code == 400
    act = amy.html("/collections/1/activity")
    assert "Used 2 skein of Merino" in act and "Added 1 skein of Cotton" in act

def test_restock_form_body_and_all(env, amy):
    seed(amy); amy.post("/items/1/edit", {"name": "Merino", "qty": "1", "low": "2", "location": "Drawer 3"})
    c = env.client().c; h = {"Authorization": "Bearer " + make_token(amy, "write")}
    r = c.post("/api/v1/shopping/restock", data={"id": "1", "amount": "4"}, headers=h).get_json()   # form-encoded works too
    assert r["restocked"][0]["qty"] == 5
    amy.post("/items/2/edit", {"name": "Cotton", "qty": "0"})
    r = c.post("/api/v1/shopping/restock", json={"all": True}, headers=h).get_json()
    assert [x["id"] for x in r["restocked"]] == [2] and r["shopping_list"] == 0
    assert c.post("/api/v1/shopping/restock", json={}, headers=h).status_code == 400

def test_items_collections_and_projects(env, amy):
    seed(amy); amy.post("/projects/1/assign", {"item_id": "1", "qty": "2"})
    get, _ = api(env, make_token(amy))
    cols = get("/api/v1/collections").get_json()["collections"]
    assert cols[0]["name"] == "Knitting" and cols[0]["items"] == 2 and cols[0]["is_owner"] is True
    col = get("/api/v1/collections/1").get_json()
    assert col["types"][0]["name"] == "Yarn" and {i["name"] for i in col["items"]} == {"Merino", "Cotton"}
    merino = get("/api/v1/items/1").get_json()
    assert merino["free"] == 3 and merino["hex"] == "#d98ca3" and merino["location"] == "Drawer 3" and merino["url"].endswith("/items/1/edit")
    assert [i["name"] for i in get("/api/v1/items?q=rose").get_json()["items"]] == ["Merino"]
    assert get("/api/v1/items?status=low").get_json()["items"] == []
    assert [i["name"] for i in get("/api/v1/items?status=ok").get_json()["items"]] == ["Cotton", "Merino"]
    prj = get("/api/v1/projects").get_json()["projects"]
    assert prj[0]["name"] == "Hat" and prj[0]["materials"] == 1
    one = get("/api/v1/projects/1").get_json()
    assert one["materials"][0]["reserved"] == 2 and one["materials"][0]["name"] == "Merino"
    assert get("/api/v1/items/999").status_code == 404 and get("/api/v1/items/999").is_json

def test_token_only_sees_its_users_data(env, amy, bob):
    seed(amy)
    get, post = api(env, make_token(bob, "write"))
    assert get("/api/v1/collections").get_json()["collections"] == []
    assert get("/api/v1/items/1").status_code == 404 and get("/api/v1/projects/1").status_code == 404
    assert post("/api/v1/items/1/adjust", {"action": "use", "amount": 1}).status_code == 404
    r = post("/api/v1/shopping/restock", {"items": [{"id": 1, "amount": 3}]}).get_json()
    assert r["restocked"] == [] and r["skipped"][0]["reason"] == "not found or read-only"

def test_view_only_share_cannot_write_even_with_a_write_token(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    get, post = api(env, make_token(bob, "write"))
    assert get("/api/v1/items/1").get_json()["name"] == "Merino"
    assert post("/api/v1/items/1/adjust", {"action": "add", "amount": 1}).status_code == 404

def test_revoked_token_and_disabled_user_stop_working(env, amy, bob):
    tok = make_token(bob); get, _ = api(env, tok)
    assert get("/api/v1").get_json()["user"] == "bob"
    uid = db(env).execute("SELECT id FROM users WHERE username='bob'").fetchone()[0]
    amy.post(f"/admin/users/{uid}/active")                    # admin disables bob
    assert get("/api/v1").status_code == 401
    amy.post(f"/admin/users/{uid}/active")                    # and enables again
    assert get("/api/v1").status_code == 200
    tid = db(env).execute("SELECT id FROM api_tokens").fetchone()[0]
    bob.post(f"/account/tokens/{tid}/delete")
    assert get("/api/v1").status_code == 401

def test_cannot_revoke_someone_elses_token(env, amy, bob):
    make_token(amy); tid = db(env).execute("SELECT id FROM api_tokens").fetchone()[0]
    bob.post(f"/account/tokens/{tid}/delete")
    assert db(env).execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 1
