"""Backups: full zip (database + uploads), daily database snapshots, and restore."""
import os, shutil, sqlite3, sys, threading, time, traceback, zipfile
from datetime import datetime, timezone
from pathlib import Path
try: import fcntl
except ImportError: fcntl = None      # Windows dev box: no "is the app running?" check

PACKED = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "zip", "docx", "xlsx", "odt", "gz"}   # already compressed: store as-is
KEEP_SAFETY = 5                         # pre-restore-*.db copies to keep (pre-migrate-* keeps the same number)
_locks = {}

def stamp(): return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

def snapshot_db(src, dst):
    s = sqlite3.connect(src); d = sqlite3.connect(dst); s.backup(d); d.close(); s.close()

def prune(folder, pattern, keep):
    for old in sorted(Path(folder).glob(pattern))[:-keep or None]: old.unlink(missing_ok=True)

def hold_app_lock(data_dir):
    """The running app keeps a shared lock on /data/.app.lock, so restore can tell it is still running."""
    if not fcntl: return
    f = open(Path(data_dir) / ".app.lock", "a+")
    fcntl.flock(f, fcntl.LOCK_SH); _locks[str(data_dir)] = f

def clean_leftovers(data_dir, age=86400):
    """Temp files from a backup or restore that was killed half-way."""
    now = time.time()
    for p in Path(data_dir).glob(".*"):
        if p.name.startswith((".snapshot-", ".download-", ".restore-")) and now - p.stat().st_mtime > age:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)

def make_zip(data_dir, zip_path):
    data_dir = Path(data_dir); tmp = data_dir / f".snapshot-{time.time_ns()}.db"
    snapshot_db(data_dir / "craft.db", tmp)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "craft.db")
            up = data_dir / "uploads"
            for p in sorted(up.rglob("*")) if up.exists() else []:
                if p.is_file():
                    packed = p.suffix.lstrip(".").lower() in PACKED
                    z.write(p, "uploads/" + p.relative_to(up).as_posix(), compress_type=zipfile.ZIP_STORED if packed else zipfile.ZIP_DEFLATED)
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
                    prune(folder, "auto-*.db", keep)
            except Exception:    # keep trying hourly, but say why in `docker logs`
                print(f"[snapshots] daily snapshot failed:\n{traceback.format_exc()}", file=sys.stderr, flush=True)
            time.sleep(3600)
    threading.Thread(target=loop, daemon=True, name="snapshots").start()

def _check_db(path):
    c = sqlite3.connect(path)
    try:
        # Craft Stash never creates virtual tables (FTS5 and the like). Refuse a file that has them before anything
        # else reads it: a crafted FTS5 index can overflow SQLite's buffers (CVE-2026-119373, CVE-2026-119374).
        if c.execute("SELECT 1 FROM sqlite_master WHERE sql LIKE 'CREATE VIRTUAL TABLE%' COLLATE NOCASE").fetchone():
            raise SystemExit("That file has tables Craft Stash never makes (virtual tables), so it was not restored.")
        ok = c.execute("PRAGMA integrity_check").fetchone()[0] == "ok" and c.execute("SELECT 1 FROM sqlite_master WHERE name='users'").fetchone()
    except sqlite3.DatabaseError: ok = False
    finally: c.close()
    if not ok: raise SystemExit("That file is not a healthy Craft Stash database.")

def _exclusive(data_dir, force):
    """Lock out the app for the length of the restore; refuse if it is running (unless forced)."""
    if not fcntl: return None
    f = open(Path(data_dir) / ".app.lock", "a+")
    try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        if not force:
            f.close()
            raise SystemExit("The app is still running. Stop it first (docker compose stop craft-stash), or pass --force.")
    return f

def restore(data_dir, src, force=False):
    """Replace the database (and uploads, for a full zip) from a backup. Stop the app first."""
    data_dir, src = Path(data_dir), Path(src)
    if not src.exists(): raise SystemExit(f"No such file: {src}")
    lock = _exclusive(data_dir, force)
    try:
        (data_dir / "backups").mkdir(exist_ok=True)
        if (data_dir / "craft.db").exists():
            snapshot_db(data_dir / "craft.db", data_dir / "backups" / f"pre-restore-{stamp()}.db")
            prune(data_dir / "backups", "pre-restore-*.db", KEEP_SAFETY)
        stage = data_dir / f".restore-{stamp()}"; stage.mkdir()
        try:
            uploads = None
            if zipfile.is_zipfile(src):
                with zipfile.ZipFile(src) as z:
                    if "craft.db" not in z.namelist(): raise SystemExit("Not a Craft Stash backup: craft.db is missing from the zip.")
                    def extract(name, target):      # streamed, so a big file never has to fit in memory
                        with z.open(name) as i, open(target, "wb") as o: shutil.copyfileobj(i, o, 1 << 20)
                    extract("craft.db", stage / "craft.db")
                    uploads = stage / "uploads"; uploads.mkdir()
                    for n in z.namelist():
                        if n.startswith("uploads/") and not n.endswith("/"):
                            target = (stage / n).resolve()
                            if not str(target).startswith(str(uploads.resolve()) + os.sep): continue   # zip-slip guard
                            target.parent.mkdir(parents=True, exist_ok=True); extract(n, target)
                newdb = stage / "craft.db"
            else:
                newdb = src
            _check_db(newdb)
            for ext in ("-wal", "-shm"): (data_dir / f"craft.db{ext}").unlink(missing_ok=True)
            shutil.copyfile(newdb, data_dir / "craft.db")
            if uploads is not None:
                shutil.rmtree(data_dir / "uploads", ignore_errors=True); shutil.copytree(uploads, data_dir / "uploads")
        finally: shutil.rmtree(stage, ignore_errors=True)
    finally:
        if lock: lock.close()
