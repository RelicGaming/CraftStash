import pytest
import io, sqlite3, sys, zipfile
from types import SimpleNamespace
from conftest import seed

def test_full_backup_contains_database_and_uploads_and_restores(env, amy):
    seed(amy); amy.post("/projects/1/upload", {"file": (io.BytesIO(b"%PDF-1.4 pattern"), "pattern.pdf")})
    r = amy.post("/admin/backup"); z = zipfile.ZipFile(io.BytesIO(r.data)); names = z.namelist()
    assert r.mimetype == "application/zip" and ("craft.db" in names or "craft-dump.json" in names) and any(n.startswith("uploads/") and n.endswith(".pdf") for n in names)
    zip_path = env.data / "b.zip"; zip_path.write_bytes(r.data)
    amy.post("/items/1/delete"); amy.post("/projects/1/delete")                       # lose things after the backup...
    import backup; backup.restore(env.data, zip_path, force=True)                                # ...then restore
    c = env.client(); c.login("amy")
    assert "Merino" in c.html("/collections/1") and c.get("/files/1").status_code == 200
    assert list((env.data / "backups").glob("pre-restore-*"))                       # the state before restoring is kept too

def test_restore_rejects_garbage(env, amy):
    import backup; bad = env.data / "bad.zip"; bad.write_bytes(b"nope")
    try: backup.restore(env.data, bad, force=True)
    except SystemExit as e: assert "healthy" in str(e) or "Not a" in str(e)
    else: raise AssertionError("garbage was accepted")

@pytest.mark.sqlite_only
def test_restore_refuses_a_database_with_virtual_tables(env, amy):
    """A crafted FTS5 index can overflow SQLite's buffers, and Craft Stash never makes virtual tables."""
    import backup; seed(amy)
    bad = env.data / "fts.db"; backup.snapshot_db(env.data / "craft.db", bad)
    c = sqlite3.connect(bad); c.execute("CREATE VIRTUAL TABLE notes_fts USING fts5(body, detail=none)"); c.commit(); c.close()
    try: backup.restore(env.data, bad, force=True)
    except SystemExit as e: assert "virtual tables" in str(e)
    else: raise AssertionError("a database with an FTS5 table was restored")
    c = env.client(); c.login("amy"); assert "Merino" in c.html("/collections/1")          # the live database is untouched

def test_backup_is_admin_only(env, amy, bob):
    assert bob.post("/admin/backup").status_code == 403

def test_command_line_password_reset_rescues_a_locked_out_admin(env, amy):
    import manage
    manage.reset_password(SimpleNamespace(username="amy", password="brandnewpass"))
    c = env.client(); assert c.login("amy", "brandnewpass").status_code == 302 and c.get("/admin").status_code == 200
    assert amy.get("/").status_code == 302                                              # the old session was signed out

def test_storage_quota_is_enforced(make_env):
    e = make_env(USER_QUOTA_MB=1); c = e.client().register("amy"); seed(c)
    ok = c.post("/projects/1/upload", {"file": (io.BytesIO(b"x" * 600_000), "a.pdf")}); big = c.post("/projects/1/upload", {"file": (io.BytesIO(b"x" * 600_000), "b.pdf")}, follow_redirects=True)
    assert "storage limit" in big.get_data(as_text=True) and len(list((e.data / "uploads").glob("*.pdf"))) == 1

def test_cleanup_finds_orphans(env, amy):
    import manage, os, time
    orphan = env.data / "uploads" / "orphan.pdf"; orphan.write_bytes(b"x"); os.utime(orphan, (time.time() - 7200,) * 2)
    manage.cleanup(SimpleNamespace(dry_run=False)); assert not orphan.exists()

def test_user_deletion_removes_their_files_and_photos(env, amy, bob):
    seed(bob); bob.post("/projects/1/upload", {"file": (io.BytesIO(b"%PDF"), "p.pdf")})
    amy.post("/admin/users/2/delete"); assert not list((env.data / "uploads").glob("*.pdf"))
