"""Ordered, idempotent schema upgrades. PRAGMA user_version records how many have run."""
import json, sqlite3
from datetime import datetime, timezone
from pathlib import Path

def cols(c, table): return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]

def add_col(c, table, col, ddl):
    if col not in cols(c, table): c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def m_fields(c):   # v1: field lists of plain names -> {id,label,type,options}; item extras re-keyed by id
    for tid, fl in c.execute("SELECT id, fields FROM types").fetchall():
        fl = json.loads(fl)
        if fl and isinstance(fl[0], str):
            ids = {l: f"f{n}" for n, l in enumerate(fl)}
            for iid, ex in c.execute("SELECT id, extra FROM items WHERE type_id=?", (tid,)).fetchall():
                c.execute("UPDATE items SET extra=? WHERE id=?", (json.dumps({ids[k]: v for k, v in json.loads(ex).items() if k in ids}), iid))
            c.execute("UPDATE types SET fields=? WHERE id=?", (json.dumps([{"id": ids[l], "label": l, "type": "text", "options": []} for l in fl]), tid))

def m_low(c): add_col(c, "items", "low_at", "REAL")
def m_theme(c): add_col(c, "users", "theme", "TEXT NOT NULL DEFAULT ''")
def m_active(c): add_col(c, "users", "active", "INTEGER NOT NULL DEFAULT 1")
def m_photo(c): add_col(c, "items", "photo", "TEXT NOT NULL DEFAULT ''")
def m_used(c):
    add_col(c, "assignments", "used", "REAL NOT NULL DEFAULT 0")
    c.execute("UPDATE assignments SET used=qty WHERE consumed=1 AND used=0")

STEPS = [m_fields, m_low, m_theme, m_active, m_photo, m_used]

def pre_backup(c, path):
    folder = path.parent / "backups"; folder.mkdir(exist_ok=True)
    dst = sqlite3.connect(folder / datetime.now(timezone.utc).strftime("pre-migrate-%Y%m%d-%H%M%S.db"))
    c.backup(dst); dst.close()
    for old in sorted(folder.glob("pre-migrate-*.db"))[:-5]: old.unlink()

def run(path, schema):
    path = Path(path); fresh = not path.exists() or path.stat().st_size == 0
    c = sqlite3.connect(path)
    have = c.execute("PRAGMA user_version").fetchone()[0]
    if not fresh and have < len(STEPS): pre_backup(c, path)     # a copy of the old database, just in case
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(schema)
    for n, step in enumerate(STEPS, 1):
        if n > have: step(c)
    c.executescript("DROP VIEW IF EXISTS stock; DROP VIEW IF EXISTS access;")   # views freeze their column lists
    c.executescript(schema)
    c.execute("DELETE FROM activity WHERE ts < datetime('now','-365 day')")
    c.execute(f"PRAGMA user_version={len(STEPS)}"); c.commit(); c.close()
