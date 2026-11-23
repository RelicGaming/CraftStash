import importlib, io, re, sys, types
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

@pytest.fixture
def make_env(tmp_path, monkeypatch):
    def make(**env):
        monkeypatch.setenv("DATA_DIR", str(tmp_path)); monkeypatch.setenv("BACKUP_KEEP", "0"); monkeypatch.setenv("TZ", "Australia/Brisbane")
        for k, v in env.items(): monkeypatch.setenv(k, str(v))
        for m in ("app", "importer", "backup", "migrations", "manage"): sys.modules.pop(m, None)   # fresh app and database per test
        mod = importlib.import_module("app")
        return types.SimpleNamespace(mod=mod, data=tmp_path, client=lambda: C(mod.app))
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
