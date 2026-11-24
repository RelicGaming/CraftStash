"""Regression tests for the fixes from the code review (access, login limiter, import numbers, backups, migrations, CSP)."""
import io, json, re, sqlite3, zipfile
from types import SimpleNamespace
import pytest
from conftest import seed, png_bytes

def qty(env, iid=1): return env.db().execute("SELECT qty FROM items WHERE id=?", (iid,)).fetchone()[0]

# ---------- sharing: view-only users can't move stock ----------
def test_downgrade_to_view_only_drops_reservations(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/projects", {"name": "Scarf"}); bob.post("/projects/2/assign", {"item_id": "1", "qty": "3"})
    amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    assert env.db().execute("SELECT COUNT(*) FROM assignments WHERE project_id=2").fetchone()[0] == 0
    bob.post("/projects/2/complete"); assert qty(env) == 5

def test_complete_ignores_stale_reservations(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/projects", {"name": "Scarf"}); bob.post("/projects/2/assign", {"item_id": "1", "qty": "3"})
    d = env.db(); d.execute("UPDATE pool_shares SET can_edit=0"); d.commit()   # e.g. from an older version
    bob.post("/projects/2/complete"); assert qty(env) == 5

def test_reopen_leaves_stock_alone_once_access_is_gone(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/projects", {"name": "Scarf"}); bob.post("/projects/2/assign", {"item_id": "1", "qty": "3"}); bob.post("/projects/2/complete")
    assert qty(env) == 2
    amy.post("/collections/1/share", {"username": "bob", "role": "view"})
    r = bob.post("/projects/2/reopen", follow_redirects=True)
    assert qty(env) == 2 and "stayed used" in r.get_data(as_text=True)

def test_copying_your_project_skips_reservations_you_cannot_make(env, amy, bob):
    seed(amy); amy.post("/collections/1/share", {"username": "bob", "role": "edit"})
    bob.post("/projects", {"name": "Scarf"}); bob.post("/projects/2/assign", {"item_id": "1", "qty": "3"})
    d = env.db(); d.execute("UPDATE pool_shares SET can_edit=0"); d.commit()
    bob.post("/projects/2/copy")
    assert env.db().execute("SELECT COUNT(*) FROM assignments WHERE project_id=3").fetchone()[0] == 0

def test_copy_survives_a_missing_upload(env, amy):
    seed(amy); amy.post("/projects/1/upload", {"file": (io.BytesIO(b"%PDF-1.4"), "a.pdf")})
    for p in (env.data / "uploads").glob("*.pdf"): p.unlink()
    r = amy.post("/projects/1/copy", follow_redirects=True)
    assert r.status_code == 200 and "missing from storage" in r.get_data(as_text=True)

# ---------- login limiter ----------
def test_one_address_spraying_usernames_is_throttled(env, amy):
    env.mod.IP_LIMIT = 5; c = env.client()
    for i in range(5): c.login(f"nobody{i}", "wrong")
    assert "Too many failed" in c.login("amy", "password1").get_data(as_text=True)

def test_many_addresses_guessing_one_account_are_throttled(make_env):
    e = make_env(TRUST_PROXY=1); e.client().register("amy"); e.mod.USER_LIMIT = 6; c = e.client()
    for i in range(6): c.c.post("/login", data={"username": "amy", "password": "x", "_csrf": c.token()}, headers={"X-Forwarded-For": f"10.0.0.{i}"})
    r = c.c.post("/login", data={"username": "amy", "password": "password1", "_csrf": c.token()}, headers={"X-Forwarded-For": "10.9.9.9"})
    assert "Too many failed" in r.get_data(as_text=True)

def test_failure_table_is_trimmed_not_wiped(env):
    m = env.mod; m.FAILS.clear(); m.FAIL_KEYS = 30
    m.record_fail("1.1.1.1", "amy")
    for i in range(40): m.record_fail(f"2.2.2.{i}", f"u{i}")
    assert 0 < len(m.FAILS) <= 30 and "*|u39" in m.FAILS         # the newest failures are always kept

def test_proxy_headers_are_ignored_by_default(env, amy):
    assert env.mod.app.wsgi_app.__class__.__name__ != "ProxyFix"

# ---------- first admin ----------
def test_signup_off_also_closes_the_first_admin_signup(make_env):
    e = make_env(ALLOW_SIGNUP=0); c = e.client()
    assert c.get("/register").status_code == 403 and "manage.py create-user" in c.html("/login")
    import manage
    manage.create_user(SimpleNamespace(username="amy", password="password1", admin=True))
    assert c.login("amy").status_code == 302 and c.get("/admin").status_code == 200

# ---------- CSV import numbers ----------
@pytest.mark.parametrize("text, value", [("1,234.50", 1234.5), ("1.234,50", 1234.5), ("2,5", 2.5), ("1,000,000", 1e6),
                                         ("1.234.567", 1234567), ("0,500", 0.5), ("1 234,5", 1234.5), ("$3.20", 3.2), ("12", 12)])
def test_import_numbers(text, value):
    import importer; assert importer.to_num(text) == value

@pytest.mark.parametrize("text", ["1,000", "1,500", "lots", "1.2.3", "-4", "1234,567.8"])
def test_import_rejects_unclear_numbers(text):
    import importer
    with pytest.raises(ValueError): importer.to_num(text)

def test_import_explains_ambiguous_numbers_and_cleans_number_fields(env, amy):
    seed(amy); amy.post("/types/1/fields", {"label": "Weight", "type": "number"})
    csv = "Material type,Name,In stock,Weight\nYarn,Alpaca,\"1,500\",\nYarn,Silk,2,\"1.250,5\"\nYarn,Wool,1,heavy\n"
    page = amy.post("/collections/1/import", {"file": (io.BytesIO(csv.encode()), "a.csv"), "mode": "add"}).get_data(as_text=True)
    assert "ambiguous" in page and "Weight" in page
    d = env.db(); fid = [f["id"] for f in json.loads(d.execute("SELECT fields FROM types WHERE id=1").fetchone()[0]) if f["label"] == "Weight"][0]
    assert json.loads(d.execute("SELECT extra FROM items WHERE name='Silk'").fetchone()[0])[fid] == "1250.5"
    assert not d.execute("SELECT 1 FROM items WHERE name IN ('Alpaca','Wool')").fetchone()

# ---------- search ----------
def test_search_wildcards_match_literally(env, amy):
    seed(amy); amy.post("/types/1/items/new", {"name": "Blend 50% wool", "qty": "1"})
    page = amy.html("/collections/1?q=50%25")
    assert "Blend 50% wool" in page and "Merino" not in page
    assert "Merino" not in amy.html("/collections/1?q=_")

# ---------- backups ----------
def test_restore_refuses_while_the_app_is_running(env, amy):
    import backup
    zip_path = env.data / "b.zip"; zip_path.write_bytes(amy.post("/admin/backup").data)
    with pytest.raises(SystemExit, match="still running"): backup.restore(env.data, zip_path)

def test_restore_keeps_a_limited_number_of_safety_copies(env, amy):
    import backup
    zip_path = env.data / "b.zip"; zip_path.write_bytes(amy.post("/admin/backup").data)
    for i in range(8):
        (env.data / "backups").mkdir(exist_ok=True); (env.data / "backups" / f"pre-restore-2000010{i}-000000.db").write_bytes(b"")
    backup.restore(env.data, zip_path, force=True)
    assert len(list((env.data / "backups").glob("pre-restore-*.db"))) == backup.KEEP_SAFETY

def test_backup_stores_compressed_files_as_they_are(env, amy):
    seed(amy); amy.post("/items/1/edit", {"name": "Merino", "qty": "5", "photo": (io.BytesIO(png_bytes()), "p.png")})
    amy.post("/projects/1/upload", {"file": (io.BytesIO(b"plain text " * 500), "notes.txt")})
    z = zipfile.ZipFile(io.BytesIO(amy.post("/admin/backup").data))
    kinds = {i.filename.rsplit(".", 1)[-1]: i.compress_type for i in z.infolist()}
    assert kinds["jpg"] == zipfile.ZIP_STORED and kinds["txt"] == zipfile.ZIP_DEFLATED and kinds.get("db", kinds.get("json")) == zipfile.ZIP_DEFLATED
    assert not list(env.data.glob(".download-*"))                     # temp zip is removed after sending

def test_old_temp_files_are_cleaned_up(env):
    import backup, os, time
    old = env.data / ".download-x.zip"; old.write_bytes(b"x"); os.utime(old, (time.time() - 2 * 86400,) * 2)
    backup.clean_leftovers(env.data); assert not old.exists()

# ---------- migrations ----------
@pytest.mark.sqlite_only
def test_a_failing_migration_changes_nothing(env, monkeypatch):
    import migrations
    from pathlib import Path
    schema = migrations.schema_file("sqlite")
    db = env.data / "craft.db"; before = sqlite3.connect(db).execute("PRAGMA user_version").fetchone()[0]
    def boom(c): c.execute("ALTER TABLE items ADD COLUMN half_done TEXT"); raise RuntimeError("boom")
    monkeypatch.setattr(migrations, "STEPS", migrations.STEPS + [boom])
    with pytest.raises(RuntimeError): migrations.run(db, schema)
    c = sqlite3.connect(db)
    assert c.execute("PRAGMA user_version").fetchone()[0] == before
    assert "half_done" not in [r[1] for r in c.execute("PRAGMA table_info(items)")]

@pytest.mark.sqlite_only
def test_upgrade_drops_existing_stale_reservations(make_env, tmp_path):
    e = make_env(); a = e.client().register("amy"); b = e.client().register("bob"); seed(a)
    a.post("/collections/1/share", {"username": "bob", "role": "edit"})
    b.post("/projects", {"name": "Scarf"}); b.post("/projects/2/assign", {"item_id": "1", "qty": "3"})
    d = sqlite3.connect(tmp_path / "craft.db"); d.execute("UPDATE pool_shares SET can_edit=0"); d.execute("PRAGMA user_version=6"); d.commit(); d.close()
    make_env()                                                          # restart: runs the v7 step
    assert sqlite3.connect(tmp_path / "craft.db").execute("SELECT COUNT(*) FROM assignments WHERE project_id=2").fetchone()[0] == 0

def test_old_activity_is_pruned_without_a_restart(env, amy):
    seed(amy); d = env.db()
    import dbx; d.execute("INSERT INTO activity(ts,user_id,pool_id,kind,text) VALUES(?,1,1,'edit','ancient')", (dbx.ago(400),)); d.commit()
    env.mod._pruned["at"] = 0; amy.get("/")
    assert not d.execute("SELECT 1 FROM activity WHERE text='ancient'").fetchone()

# ---------- no inline scripts ----------
def test_csp_forbids_inline_scripts_and_pages_have_none(env, amy):
    seed(amy); amy.post("/projects/1/steps", {"title": "Cast on"})
    csp = amy.get("/").headers["Content-Security-Policy"]
    assert re.search(r"script-src 'self';", csp)
    for url in ("/", "/collections/1", "/types/1/fields", "/items/1/edit", "/projects/1", "/shopping", "/appearance", "/admin", "/admin/selftest"):
        html = amy.html(url)
        assert not re.search(r"<script>|<script(?![^>]*\bsrc=)[^>]*>|\son(click|submit|change|input)=", html), url
    assert amy.get("/static/app.js").status_code == 200

def test_delete_warnings_show_counts(env, amy):
    seed(amy)
    for url in ("/collections/1", "/types/1/fields"):
        html = amy.html(url); assert "built-in method" not in html and "its 2 items" in html, url
