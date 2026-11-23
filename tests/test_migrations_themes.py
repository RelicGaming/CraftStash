import json, sqlite3
from pathlib import Path
import themes

OLD_SCHEMA = (Path(__file__).resolve().parent.parent / "app" / "schema.sql").read_text()
for frag in (", theme TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1", ", low_at REAL", ", photo TEXT NOT NULL DEFAULT ''", ", used REAL NOT NULL DEFAULT 0"):
    OLD_SCHEMA = OLD_SCHEMA.replace(frag, "")

def test_old_database_is_upgraded_in_place_with_a_safety_copy(make_env, tmp_path):
    c = sqlite3.connect(tmp_path / "craft.db"); c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO users(id,username,pw,is_admin) VALUES(1,'amy','x',1)"); c.execute("INSERT INTO pools(id,user_id,name) VALUES(1,1,'Sew')")
    c.execute("""INSERT INTO types(id,pool_id,name,unit,fields) VALUES(1,1,'Fabric','m','["Width"]')""")
    c.execute("""INSERT INTO items(type_id,name,qty,extra) VALUES(1,'Poplin',3,'{"Width":"150"}')""")
    c.execute("INSERT INTO projects(id,user_id,name,status) VALUES(1,1,'Dress','done')"); c.execute("INSERT INTO assignments(project_id,item_id,qty,consumed) VALUES(1,1,2,1)")
    c.commit(); c.close()
    env = make_env()
    d = sqlite3.connect(tmp_path / "craft.db"); d.row_factory = sqlite3.Row
    assert {"low_at", "photo"} <= {r[1] for r in d.execute("PRAGMA table_info(items)")} and {"theme", "active"} <= {r[1] for r in d.execute("PRAGMA table_info(users)")}
    assert json.loads(d.execute("SELECT fields FROM types").fetchone()[0])[0]["label"] == "Width"      # legacy field list converted
    assert d.execute("SELECT used FROM assignments").fetchone()[0] == 2                              # completed projects can be reopened
    assert d.execute("SELECT name FROM stock").fetchone()[0] == "Poplin"                              # the view was rebuilt with the new columns
    assert d.execute("PRAGMA user_version").fetchone()[0] == len(__import__("migrations").STEPS)
    assert list((tmp_path / "backups").glob("pre-migrate-*.db"))

def test_migrations_are_idempotent(make_env, tmp_path):
    make_env(); first = sqlite3.connect(tmp_path / "craft.db").execute("PRAGMA user_version").fetchone()[0]
    make_env(); assert sqlite3.connect(tmp_path / "craft.db").execute("PRAGMA user_version").fetchone()[0] == first
    assert not list((tmp_path / "backups").glob("pre-migrate-*.db")) if (tmp_path / "backups").exists() else True   # up to date: no needless copy

def test_theme_input_is_sanitised():
    t = themes.from_form({"theme": "custom", "bg": "#fff;}</style><script>", "surface": "red", "ink": "#12345", "accent": "#GGGGGG", "radius": "x;y", "mode": "evil"})
    css = themes.css_for(json.dumps(t)); assert "<" not in css and ";}" not in css.replace(";color-scheme", "") and "--bg:#f4f5f7" in css

def test_every_preset_builds_and_dark_ones_flip_status_colours():
    for key in themes.PRESETS:
        css = themes.css_for(json.dumps({"name": key})); assert css.startswith(":root{--bg:") and "--accent-ink:" in css
    assert "color-scheme:dark" in themes.css_for(json.dumps({"name": "ember"}))

def test_appearance_is_saved_per_user(env, amy, bob):
    amy.post("/appearance", {"theme": "forest", "radius": "round"})
    assert "--r-lg:22px" in amy.html("/") and "<style>:root" not in bob.html("/")
