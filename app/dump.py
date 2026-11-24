"""Portable database dumps: every table as JSON, readable by any engine.

Used by `manage.py export-db / import-db / move-db`, by the daily snapshots and full-backup zips when the database is
external, and to restore a backup made on one engine into another.
"""
import json, os, shutil, sqlite3, tempfile, zipfile
from pathlib import Path
import dbx

FORMAT = "craft-stash-dump"

def _columns(c, table):
    cur = c.execute(f"SELECT * FROM {table} LIMIT 0")
    return [d[0] for d in cur.description]

def dump(c):
    """Every table of an open connection (sqlite3 or dbx), rows in id order."""
    import migrations
    out = {"format": FORMAT, "version": len(migrations.STEPS) + len(migrations.SERVER_STEPS), "tables": {}}
    for t in dbx.TABLES:
        cols = _columns(c, t)
        order = " ORDER BY id" if "id" in cols else ""
        out["tables"][t] = {"columns": cols, "rows": [list(r) for r in c.execute(f"SELECT {','.join(cols)} FROM {t}{order}").fetchall()]}
    return out

def count(data): return sum(len(t["rows"]) for t in data["tables"].values())

def check(data):
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("tables"), dict):
        raise SystemExit("That isn't a Craft Stash export.")
    for name, t in data["tables"].items():
        if name not in dbx.TABLES: raise SystemExit(f"That export has a table Craft Stash doesn't use ({name}).")
        if not isinstance(t.get("columns"), list) or not isinstance(t.get("rows"), list): raise SystemExit("That export is damaged.")
        for col in t["columns"]:
            if not isinstance(col, str) or not dbx.IDENT.fullmatch(col) or "." in col: raise SystemExit("That export is damaged.")

def load(c, data, replace=False):
    """Load a dump into an open connection, in one transaction. Without replace, the database must be empty."""
    check(data)
    have = {t: c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in dbx.TABLES}
    if any(have.values()) and not replace:
        raise SystemExit("The target database already has data. Pass --replace to delete it first.")
    n = 0
    try:
        for t in reversed(dbx.TABLES): c.execute(f"DELETE FROM {t}")
        for t in dbx.TABLES:
            src = data["tables"].get(t)
            if not src or not src["rows"]: continue
            target = set(_columns(c, t)); keep = [i for i, col in enumerate(src["columns"]) if col in target]   # only real columns reach the SQL
            cols = [src["columns"][i] for i in keep]
            rows = [[r[i] for i in keep] for r in src["rows"]]
            c.executemany(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})", rows)
            n += len(rows)
        if getattr(c, "engine", "sqlite") == "postgres":       # identity counters carry on after the highest id loaded
            for t in sorted(dbx.ID_TABLES):
                c.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")
        c.commit()
    except BaseException:
        c.rollback(); raise
    return n

def from_sqlite(path):
    """Dump a SQLite craft.db (any version): checked, and upgraded on a scratch copy first."""
    import backup, migrations
    backup._check_db(path)
    fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
    try:
        shutil.copyfile(path, tmp)
        migrations.run(tmp, migrations.schema_file("sqlite"), Path(tmp).parent / "craft-dump-scratch")
        c = sqlite3.connect(tmp); c.row_factory = sqlite3.Row
        try: return dump(c)
        finally: c.close()
    finally:
        Path(tmp).unlink(missing_ok=True); shutil.rmtree(Path(tmp).parent / "craft-dump-scratch", ignore_errors=True)

def read_any(path):
    """A dump from a JSON export, a SQLite database file, or a backup zip holding either."""
    path = Path(path)
    if not path.exists(): raise SystemExit(f"No such file: {path}")
    with open(path, "rb") as f: head = f.read(16)
    if head.startswith(b"SQLite format 3"): return from_sqlite(path)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "craft-dump.json" in names:
                with z.open("craft-dump.json") as f: data = json.load(f)
                check(data); return data
            if "craft.db" in names:
                fd, tmp = tempfile.mkstemp(suffix=".db"); os.close(fd)
                try:
                    with z.open("craft.db") as i, open(tmp, "wb") as o: shutil.copyfileobj(i, o, 1 << 20)
                    return from_sqlite(tmp)
                finally: Path(tmp).unlink(missing_ok=True)
        raise SystemExit("Not a Craft Stash backup: it has neither craft.db nor craft-dump.json.")
    try:
        with open(path) as f: data = json.load(f)
    except (ValueError, UnicodeDecodeError): raise SystemExit("Not a Craft Stash export, database or backup.")
    check(data); return data

def write(c, path):
    data = dump(c)
    with open(path, "w") as f: json.dump(data, f)
    return data
