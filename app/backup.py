"""Backups: full zip (database + uploads), daily database snapshots, and restore."""
import os, shutil, sqlite3, threading, time, zipfile
from datetime import datetime, timezone
from pathlib import Path

def stamp(): return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

def snapshot_db(src, dst):
    s = sqlite3.connect(src); d = sqlite3.connect(dst); s.backup(d); d.close(); s.close()

def make_zip(data_dir, zip_path):
    data_dir = Path(data_dir); tmp = data_dir / f".snapshot-{time.time_ns()}.db"
    snapshot_db(data_dir / "craft.db", tmp)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "craft.db")
            up = data_dir / "uploads"
            for p in sorted(up.rglob("*")) if up.exists() else []:
                if p.is_file(): z.write(p, "uploads/" + p.relative_to(up).as_posix())
    finally: tmp.unlink(missing_ok=True)

def start_snapshots(data_dir, keep):
    """Daily database-only snapshots in /data/backups. Uploads are not copied (they are large and rarely change)."""
    if keep <= 0: return
    data_dir = Path(data_dir)
    def loop():
        while True:
            try:
                folder = data_dir / "backups"; folder.mkdir(exist_ok=True)
                newest = max((f.stat().st_mtime for f in folder.glob("auto-*.db")), default=0)
                if time.time() - newest > 86400:
                    snapshot_db(data_dir / "craft.db", folder / f"auto-{stamp()}.db")
                    for old in sorted(folder.glob("auto-*.db"))[:-keep]: old.unlink()
            except Exception: pass
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="snapshots").start()

def _check_db(path):
    c = sqlite3.connect(path)
    try:
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
    except sqlite3.DatabaseError: ok = False
    finally: c.close()
    if not ok: raise SystemExit("That file is not a healthy Craft Stash database.")

def restore(data_dir, src):
    """Replace the database (and uploads, for a full zip) from a backup. Stop the app first."""
    data_dir, src = Path(data_dir), Path(src)
    if not src.exists(): raise SystemExit(f"No such file: {src}")
    (data_dir / "backups").mkdir(exist_ok=True)
    if (data_dir / "craft.db").exists(): snapshot_db(data_dir / "craft.db", data_dir / "backups" / f"pre-restore-{stamp()}.db")
    stage = data_dir / f".restore-{stamp()}"; stage.mkdir()
    try:
        uploads = None
        if zipfile.is_zipfile(src):
            with zipfile.ZipFile(src) as z:
                if "craft.db" not in z.namelist(): raise SystemExit("Not a Craft Stash backup: craft.db is missing from the zip.")
                (stage / "craft.db").write_bytes(z.read("craft.db"))
                uploads = stage / "uploads"; uploads.mkdir()
                for n in z.namelist():
                    if n.startswith("uploads/") and not n.endswith("/"):
                        target = (stage / n).resolve()
                        if not str(target).startswith(str(uploads.resolve()) + os.sep): continue   # zip-slip guard
                        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(z.read(n))
            newdb = stage / "craft.db"
        else:
            newdb = src
        _check_db(newdb)
        for ext in ("-wal", "-shm"): (data_dir / f"craft.db{ext}").unlink(missing_ok=True)
        shutil.copyfile(newdb, data_dir / "craft.db")
        if uploads is not None:
            shutil.rmtree(data_dir / "uploads", ignore_errors=True); shutil.copytree(uploads, data_dir / "uploads")
    finally: shutil.rmtree(stage, ignore_errors=True)
