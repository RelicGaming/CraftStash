"""Ordered, idempotent schema upgrades.

SQLite: PRAGMA user_version records how many STEPS have run (schema_version mirrors it).
PostgreSQL, MariaDB/MySQL: a new database starts from schema/<engine>.sql at the current version, recorded in schema_version;
SERVER_STEPS holds upgrades written for server engines from then on.
"""
import json, sqlite3
import dbx
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

def m_project_dates(c):   # v8: projects get created/updated (project_tags is a new table, so the schema script makes it)
    add_col(c, "projects", "created", "TEXT NOT NULL DEFAULT ''")
    add_col(c, "projects", "updated", "TEXT NOT NULL DEFAULT ''")
    c.execute("UPDATE projects SET created=?, updated=? WHERE created=''", (dbx.now(), dbx.now()))

def m_wishlist(c):   # v9: wishes and wish_sightings are new tables, so the schema script makes them on every engine.
    """Nothing to change in existing rows. The step marks the version, so an upgrade takes its usual backup first."""

STEPS = [m_fields, m_low, m_theme, m_active, m_photo, m_used, m_stale_reservations, m_project_dates, m_wishlist]

def statements(script):
    """Split a SQL script into single statements, so it can run inside one transaction (executescript would commit)."""
    buf = ""
    for line in script.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf): yield buf.strip(); buf = ""
    if buf.strip(): yield buf.strip()

def prune_activity(c, days=365):
    c.execute("DELETE FROM activity WHERE ts < ?", (dbx.ago(int(days)),))

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
        c.execute("DELETE FROM schema_version"); c.execute("INSERT INTO schema_version(version) VALUES(?)", (len(STEPS),))
        c.execute("COMMIT")
    except BaseException:
        if c.in_transaction: c.execute("ROLLBACK")
        raise
    finally: c.close()

# Upgrades for server databases, applied after the version their schema file starts at (len(STEPS) today).
# Each is a function of the dbx connection; add new ones to the end, and the matching change to sqlite.sql's STEPS.
def s_project_dates(c):   # a fresh server database already has the columns from its schema file, so check first
    have = {r[0].lower() for r in c.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name='projects' AND table_schema=" +
        ("current_schema()" if c.engine == "postgres" else "DATABASE()")).fetchall()}
    for col in ("created", "updated"):
        if col not in have:
            c.execute(f"ALTER TABLE projects ADD COLUMN {col} " + ("TEXT" if c.engine == "postgres" else "VARCHAR(19)") + " NOT NULL DEFAULT ''")
    c.execute("UPDATE projects SET created=?, updated=? WHERE created=''", (dbx.now(), dbx.now()))

SERVER_STEPS = [s_project_dates]

def schema_file(engine):
    return (Path(__file__).parent / "schema" / f"{engine}.sql").read_text()

MYSQL_TRIGGER = """CREATE TRIGGER places_fit AFTER UPDATE ON items FOR EACH ROW
BEGIN
  DECLARE held DOUBLE;
  IF NEW.qty < OLD.qty THEN
    SELECT COALESCE(SUM(qty), 0) INTO held FROM item_places WHERE item_id = NEW.id;
    IF NEW.qty < held THEN
      UPDATE item_places SET qty = qty * NEW.qty / held WHERE item_id = NEW.id;
      DELETE FROM item_places WHERE item_id = NEW.id AND qty < 1e-9;
    END IF;
  END IF;
END"""

def _script(c, sql):
    """Run a whole schema script on a server connection."""
    if c.engine == "postgres": c.raw.cursor().execute(sql); return
    for st in _split_mysql(sql):
        c.raw.cursor().execute(st)

def _split_mysql(sql):
    """Statements of mysql.sql (no procedure bodies there, so ; at a line end ends one)."""
    buf = []
    for line in sql.splitlines():
        if line.strip().startswith("--") and not buf: continue
        buf.append(line)
        if line.rstrip().endswith(";"): yield "\n".join(buf).rstrip().rstrip(";"); buf = []
    if "".join(buf).strip(): yield "\n".join(buf)

def run_server(c, sqlite_path=None):
    """Create or upgrade a server database, one process at a time.
    Refuses to start on an empty database while a SQLite database with accounts sits in the data folder: that is
    almost always a switch to an external database that forgot to move the data."""
    cur = c.raw.cursor(); pg = c.engine == "postgres"
    if pg:
        cur.execute("SELECT pg_advisory_xact_lock(726173)")          # 'craft' - other app copies wait here
        cur.execute("SELECT to_regclass('users') IS NOT NULL")
    else:
        cur.execute("SELECT GET_LOCK('craftstash_migrate', 120)")
        cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='users'")
    exists = bool(cur.fetchone()[0])
    try:
        if not exists and sqlite_path and Path(sqlite_path).exists():
            try:
                s = sqlite3.connect(sqlite_path); n = s.execute("SELECT COUNT(*) FROM users").fetchone()[0]; s.close()
            except sqlite3.DatabaseError: n = 0
            if n:
                c.rollback()
                raise SystemExit(f"The database {dbx.CFG.describe()} is empty, but {sqlite_path} has {n} account(s). To move "
                                 f"them across, stop the app and run:  python manage.py move-db --to <your DATABASE_URL>  with "
                                 f"DATABASE_URL unset (see the README, 'Moving to another database'). To start empty "
                                 f"instead, move {sqlite_path} out of the data folder.")
        _script(c, schema_file(c.engine))                               # idempotent
        if not pg:
            # The app keeps places within totals itself; this trigger is only a backstop, and creating one needs the
            # TRIGGER privilege (and, with binary logging, log_bin_trust_function_creators), so it's optional.
            try:
                cur.execute("DROP TRIGGER IF EXISTS places_fit"); cur.execute(MYSQL_TRIGGER)
            except dbx.DatabaseError as e:
                print(f"[db] note: couldn't create the places_fit trigger ({e}); the app enforces the rule itself.", flush=True)
        cur.execute("SELECT version FROM schema_version")
        row = cur.fetchone(); base = len(STEPS)
        have = row[0] if row else base
        if not row: cur.execute("INSERT INTO schema_version(version) VALUES(%s)", (base,))
        for n, step in enumerate(SERVER_STEPS, base + 1):
            if n > have: step(c)
        if have < base + len(SERVER_STEPS): cur.execute("UPDATE schema_version SET version=%s", (base + len(SERVER_STEPS),))
        c.commit()
    finally:
        if not pg:
            try: c.raw.cursor().execute("SELECT RELEASE_LOCK('craftstash_migrate')")
            except Exception: pass
    return not exists

def prepare(P, wait=True):
    """Configure the database from the environment and bring its schema up to date. Returns the dbx config."""
    cfg = dbx.configure(dbx.config_from_env(P.db))
    if cfg.external:
        c = dbx.connect(cfg, wait=wait)
        try: run_server(c, P.db)
        finally: c.close()
    else:
        run(P.db, schema_file("sqlite"), P.backups)
    return cfg
