import importlib, io, os, re, sys, types
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

class C:
    """A browser-like client that handles the CSRF token for you."""
    def __init__(self, app): self.c = app.test_client()
    def token(self): return re.search(r'name="_csrf" value="(\w+)"', self.c.get("/login").get_data(as_text=True)).group(1)
    def get(self, url, **kw): return self.c.get(url, **kw)
    def html(self, url): return self.c.get(url).get_data(as_text=True)
    def post(self, url, data=None, **kw): return self.c.post(url, data={**(data or {}), "_csrf": self.token()}, **kw)
    def register(self, name, pw="password1"): self.post("/register", {"username": name, "password": pw}); return self
    def login(self, name, pw="password1"): return self.post("/login", {"username": name, "password": pw})

# Run the whole suite against PostgreSQL with TEST_DATABASE_URL=postgresql://user:pass@host:5432/db. That database is
# wiped before every test, so point it at a throw-away one. Tests marked sqlite_only are skipped then.
TEST_DB = os.environ.get("TEST_DATABASE_URL", "").strip()

def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: needs the SQLite engine (skipped when TEST_DATABASE_URL is set)")

def pytest_collection_modifyitems(config, items):
    if not TEST_DB: return
    skip = pytest.mark.skip(reason="SQLite-only test; TEST_DATABASE_URL is set")
    for it in items:
        if "sqlite_only" in it.keywords: it.add_marker(skip)

def reset_test_db():
    if TEST_DB.split(":", 1)[0].lower() in ("mysql", "mariadb"):
        import pymysql, dbx
        a = dbx.mysql_args(TEST_DB); name = a.pop("database"); a["autocommit"] = True
        c = pymysql.connect(**a)
        try:
            cur = c.cursor(); me = c.thread_id()
            cur.execute("SELECT id FROM information_schema.processlist WHERE db=%s AND id<>%s", (name, me))
            for (pid,) in cur.fetchall():
                try: cur.execute(f"KILL {int(pid)}")
                except pymysql.err.MySQLError: pass
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
            cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
        finally: c.close()
        return
    import psycopg
    with psycopg.connect(TEST_DB, autocommit=True) as c:
        # Connections a test left open (and the previous app's) would block the drop: end them first.
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()")
        c.execute("DROP SCHEMA IF EXISTS public CASCADE"); c.execute("CREATE SCHEMA public")

@pytest.fixture
def make_env(tmp_path, monkeypatch):
    fresh = [True]
    def make(**env):
        if TEST_DB:
            monkeypatch.setenv("DATABASE_URL", TEST_DB)
            if fresh: reset_test_db(); fresh.clear()          # once per test: a second make_env is a restart
        monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("BACKUP_KEEP", "0"); monkeypatch.setenv("TZ", "Australia/Brisbane")
        # The app points tempfile and TMPDIR at its TMP_DIR; put them back after each test.
        import tempfile; monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
        if "TMPDIR" in os.environ: monkeypatch.setenv("TMPDIR", os.environ["TMPDIR"])
        else: monkeypatch.delenv("TMPDIR", raising=False)
        for k, v in env.items(): monkeypatch.setenv(k, str(v))
        for m in ("app", "core", "auth", "admin", "items", "pools", "projects", "wishlist", "linkfetch", "linkimport", "api", "importer", "backup", "migrations", "manage", "dump"): sys.modules.pop(m, None)   # fresh app and database per test
        mod = importlib.import_module("app")
        import dbx
        def db():       # the app's database, rows as plain tuples (as tests compare them)
            c = dbx.connect(dbx.CFG)
            if hasattr(c, "row_factory"): c.row_factory = None
            elif c.engine == "mysql": c.raw.autocommit(True)  # a test's reads must not hold locks the app then waits on
            else: c.raw.autocommit = True
            return c
        return types.SimpleNamespace(mod=mod, core=sys.modules["core"], admin=sys.modules["admin"], data=tmp_path, client=lambda: C(mod.app), db=db)
    return make

@pytest.fixture
def env(make_env): return make_env()

@pytest.fixture
def amy(env): return env.client().register("amy")        # the first account is the admin

@pytest.fixture
def bob(env, amy): return env.client().register("bob")

def seed(c):
    """A knitting collection with one yarn type, two items and a project."""
    c.post("/collections", {"name": "Knitting"})
    c.post("/collections/1/types", {"name": "Yarn", "unit": "skein", "fields": "Brand"})
    c.post("/types/1/items/new", {"name": "Merino", "colour": "Rose", "has_hex": "1", "hex": "#d98ca3", "qty": "5", "price": "9.5", "low": "2", "location": "Drawer 3"})
    c.post("/types/1/items/new", {"name": "Cotton", "colour": "Blue", "qty": "1"})
    c.post("/projects", {"name": "Hat"})

def png_bytes(size=(1200, 800)):
    from PIL import Image
    b = io.BytesIO(); Image.new("RGBA", size, (200, 30, 30, 255)).save(b, "PNG"); return b.getvalue()
