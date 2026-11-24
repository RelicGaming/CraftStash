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

def m_stale_reservations(c):   # v7: view-only (or removed) users no longer keep reservations on someone else's collection
    c.execute("""DELETE FROM assignments WHERE consumed=0 AND id IN (
                   SELECT a.id FROM assignments a JOIN projects pr ON pr.id=a.project_id JOIN items i ON i.id=a.item_id
                   JOIN types t ON t.id=i.type_id JOIN pools p ON p.id=t.pool_id
                   WHERE p.user_id<>pr.user_id AND NOT EXISTS(
                     SELECT 1 FROM pool_shares s WHERE s.pool_id=p.id AND s.user_id=pr.user_id AND s.can_edit=1))""")

STEPS = [m_fields, m_low, m_theme, m_active, m_photo, m_used, m_stale_reservations]

def statements(script):
    """Split a SQL script into single statements, so it can run inside one transaction (executescript would commit)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf): yield buf.strip(); buf = ""
    if buf.strip(): yield buf.strip()

def prune_activity(c, days=365):
    c.execute(f"DELETE FROM activity WHERE ts < datetime('now','-{int(days)} day')")

def pre_backup(c, folder):
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    dst = sqlite3.connect(folder / datetime.now(timezone.utc).strftime("pre-migrate-%Y%m%d-%H%M%S.db"))
    c.backup(dst); dst.close()
    for old in sorted(folder.glob("pre-migrate-*.db"))[:-5]: old.unlink()

def run(path, schema, backups=None):
    """Create or upgrade the database. All of it happens in one transaction: a failure leaves the old database as it was."""
    path = Path(path); fresh = not path.exists() or path.stat().st_size == 0
    c = sqlite3.connect(path, isolation_level=None, timeout=30)      # transactions handled explicitly below
    try:
        c.execute("PRAGMA journal_mode=WAL")
        if not fresh and c.execute("PRAGMA user_version").fetchone()[0] < len(STEPS):
            pre_backup(c, backups or path.parent / "backups")       # a copy of the old database, just in case (before BEGIN: backup can't read mid-write)
        c.execute("BEGIN IMMEDIATE")                               # one process migrates at a time
        have = c.execute("PRAGMA user_version").fetchone()[0]
        for st in statements(schema): c.execute(st)
        for n, step in enumerate(STEPS, 1):
            if n > have: step(c)
        c.execute("DROP VIEW IF EXISTS stock"); c.execute("DROP VIEW IF EXISTS access")   # views freeze their column lists
        for st in statements(schema): c.execute(st)
        c.execute(f"PRAGMA user_version={len(STEPS)}")
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction: c.execute("ROLLBACK")
        raise
    finally: c.close()
