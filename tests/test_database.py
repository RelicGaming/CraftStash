"""The database layer (dbx), portable dumps, and moving between engines."""
import json, os, sqlite3, subprocess, sys
from pathlib import Path
import pytest
from conftest import seed, TEST_DB, reset_test_db

APP = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP))
import dbx

# ---------------------------------------------------------------- SQL translation
@pytest.mark.parametrize("sql, out", [
    ("SELECT * FROM t WHERE a=? AND b=?", "SELECT * FROM t WHERE a=%s AND b=%s"),
    ("SELECT '?' , \"a?\" FROM t WHERE x=?", "SELECT '?' , \"a?\" FROM t WHERE x=%s"),       # quoted ? stays
    ("SELECT 'it''s ?' FROM t WHERE x LIKE ?", "SELECT 'it''s ?' FROM t WHERE x LIKE %s"),
    ("SELECT 5 % 2 FROM t WHERE '%'=?", "SELECT 5 %% 2 FROM t WHERE '%%'=%s"),             # % doubled everywhere
    ("SELECT x::jsonb ->> ? FROM t WHERE p=:p AND q=:q_1", "SELECT x::jsonb ->> %s FROM t WHERE p=%(p)s AND q=%(q_1)s"),
])
def test_placeholders(sql, out):
    assert dbx.placeholders(sql, True) == out

def test_no_params_leaves_sql_alone():
    assert dbx.placeholders("SELECT '%' WHERE 1", False) == "SELECT '%' WHERE 1"

def test_postgres_insert_translation():
    sql, ret = dbx.translate("INSERT OR IGNORE INTO project_shares(project_id,user_id) VALUES(?,?)", "postgres")
    assert sql == "INSERT INTO project_shares(project_id,user_id) VALUES(%s,%s) ON CONFLICT DO NOTHING" and not ret
    sql, ret = dbx.translate("INSERT INTO items(name) VALUES(?)", "postgres")
    assert sql.endswith("RETURNING id") and ret
    sql, ret = dbx.translate("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", "postgres")
    assert "RETURNING" not in sql and not ret                                              # settings has no id

def test_row_works_like_sqlite_row():
    r = dbx.Row((1, "amy"), {"id": 0, "username": 1})
    assert r["username"] == "amy" and r[0] == 1 and tuple(r) == (1, "amy") and dict(zip(r.keys(), r)) == {"id": 1, "username": "amy"}
    assert r == (1, "amy") and len(r) == 2

# ---------------------------------------------------------------- configuration
def cfg(monkeypatch, **env):
    for k in ("DATABASE_URL", "DATABASE_URL_FILE", "DB_TYPE", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PASSWORD_FILE", "DB_SSLMODE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items(): monkeypatch.setenv(k, str(v))
    return dbx.config_from_env("/data/craft.db")

def test_sqlite_is_the_default(monkeypatch):
    c = cfg(monkeypatch); assert c.engine == "sqlite" and str(c.sqlite_path) == "/data/craft.db" and not c.external

def test_database_url(monkeypatch):
    c = cfg(monkeypatch, DATABASE_URL="postgresql://craft:s3cret@db:5432/stash", DB_SSLMODE="require")
    assert c.engine == "postgres" and c.url.endswith("?sslmode=require")
    assert "s3cret" not in c.describe() and "db:5432/stash" in c.describe()
    assert cfg(monkeypatch, DATABASE_URL="postgres://a@b/c").engine == "postgres"

def test_separate_fields_and_password_file(monkeypatch, tmp_path):
    (tmp_path / "pw").write_text("p@ss:w/rd\n")
    c = cfg(monkeypatch, DB_TYPE="postgres", DB_HOST="pg", DB_NAME="cs", DB_USER="me", DB_PASSWORD_FILE=tmp_path / "pw")
    assert c.engine == "postgres" and c.url == "postgresql://me:p%40ss%3Aw%2Frd@pg:5432/cs"     # special characters escaped

def test_unknown_database_type_is_explained(monkeypatch):
    with pytest.raises(SystemExit, match="unknown database type"): cfg(monkeypatch, DATABASE_URL="oracle://x/y")

def test_unreadable_secret_file_is_explained(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="DB_PASSWORD_FILE"): cfg(monkeypatch, DB_TYPE="postgres", DB_PASSWORD_FILE=tmp_path / "missing")

def test_server_that_never_answers_gives_a_clear_error(monkeypatch):
    c = cfg(monkeypatch, DATABASE_URL="postgresql://u:p@127.0.0.1:1/x", DB_CONNECT_TIMEOUT="0")
    with pytest.raises(SystemExit, match="Can't connect to the database postgres"): dbx.connect(c, wait=True)

# ---------------------------------------------------------------- dumps, export, import, move
def manage(env_vars, *args):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("DB_", "DATABASE_URL"))}
    e.update(env_vars)
    return subprocess.run([sys.executable, "manage.py", *args], cwd=APP, env=e, capture_output=True, text=True)

def test_export_and_import_round_trip(env, amy, tmp_path):
    seed(amy); import dump
    c = env.db(); before = dump.dump(c); c.close()
    assert dump.count(before) > 5 and before["format"] == "craft-stash-dump"
    target = tmp_path / "other" ; target.mkdir()
    out = tmp_path / "x.json"; out.write_text(json.dumps(before))
    r = manage({"DATA_DIR": str(target), "BACKUP_KEEP": "0"}, "import-db", str(out)); assert r.returncode == 0, r.stderr
    t = sqlite3.connect(target / "craft.db"); t.row_factory = sqlite3.Row
    after = dump.dump(t)
    assert after["tables"]["items"]["rows"] == before["tables"]["items"]["rows"]
    assert t.execute("SELECT username FROM users").fetchone()[0] == "amy"
    r = manage({"DATA_DIR": str(target)}, "import-db", str(out)); assert r.returncode != 0 and "--replace" in r.stderr
    t.execute("INSERT INTO items(type_id,name) VALUES(1,'new')"); t.commit()
    assert t.execute("SELECT MAX(id) FROM items").fetchone()[0] == 3                     # ids carry on after the import

def test_import_rejects_tampered_dumps(env, tmp_path):
    import dump
    for bad in ({"format": "x"}, {"format": dump.FORMAT, "tables": {"users; DROP TABLE users": {"columns": [], "rows": []}}},
                {"format": dump.FORMAT, "tables": {"users": {"columns": ["id) VALUES(1); --"], "rows": [[1]]}}}):
        with pytest.raises(SystemExit): dump.check(bad)

def test_move_between_sqlite_databases(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"; src.mkdir(); dst.mkdir()
    assert manage({"DATA_DIR": str(src)}, "create-user", "amy", "--admin", "--password", "password1").returncode == 0
    r = manage({"DATA_DIR": str(src)}, "move-db", "--to", f"sqlite://{dst}/craft.db"); assert r.returncode == 0, r.stderr
    assert sqlite3.connect(dst / "craft.db").execute("SELECT username, is_admin FROM users").fetchall() == [("amy", 1)]

@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_move_from_sqlite_to_postgres_and_the_startup_guard(tmp_path, make_env):
    reset_test_db()
    data = tmp_path / "d"; data.mkdir()
    assert manage({"DATA_DIR": str(data)}, "create-user", "amy", "--password", "password1").returncode == 0
    # An empty external database while craft.db has accounts: refuse to start, and say how to move them.
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=APP, capture_output=True, text=True,
                       env={**os.environ, "DATA_DIR": str(data), "DATABASE_URL": TEST_DB, "BACKUP_KEEP": "0"})
    assert r.returncode != 0 and "move-db" in r.stderr and "1 account" in r.stderr
    reset_test_db()
    r = manage({"DATA_DIR": str(data)}, "move-db", "--to", TEST_DB); assert r.returncode == 0, r.stderr
    import psycopg
    with psycopg.connect(TEST_DB) as c: assert c.execute("SELECT username FROM users").fetchall() == [("amy",)]
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=APP, capture_output=True, text=True,
                       env={**os.environ, "DATA_DIR": str(data), "DATABASE_URL": TEST_DB, "BACKUP_KEEP": "0"})
    assert r.returncode == 0, r.stderr                                                     # now it starts

@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_sqlite_backup_restores_into_postgres(env, amy, tmp_path):
    seed(amy)
    old = tmp_path / "old"; old.mkdir()
    assert manage({"DATA_DIR": str(old)}, "create-user", "zoe", "--password", "password1").returncode == 0
    import backup; backup.restore(env.mod.P, old / "craft.db", force=True)
    assert env.db().execute("SELECT username FROM users").fetchall() == [("zoe",)]
    assert list((env.data / "backups").glob("pre-restore-*.json"))                        # what was there is kept

@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_usernames_and_places_ignore_case_on_postgres(env, amy):
    assert env.client().register("AMY").html("/").count("Your collections") == 0             # taken, whatever the case
    seed(amy); amy.post("/items/1/edit", {"name": "Merino", "qty": "5", "place_name": ["Shelf"], "place_qty": ["2"]})
    amy.post("/items/1/move", {"amount": "1", "from": "", "to": "SHELF"})
    assert env.db().execute("SELECT place, qty FROM item_places").fetchall() == [("Shelf", 3.0)]

def test_empty_settings_count_as_unset(monkeypatch):
    """docker-compose passes every variable, so DB_USER= and DB_NAME= arrive as empty strings."""
    c = cfg(monkeypatch, DB_TYPE="postgres", DB_PASSWORD="pw", DB_USER="", DB_NAME="", DB_HOST="", DB_PORT="", DATABASE_URL="")
    assert c.url == "postgresql://craftstash:pw@postgres:5432/craftstash"
