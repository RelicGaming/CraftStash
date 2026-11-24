"""Backups: full zip (database + uploads), daily database snapshots, and restore."""
import os, shutil, sqlite3, sys, threading, time, traceback, zipfile
from datetime import datetime, timezone
from pathlib import Path
import paths
try: import fcntl
except ImportError: fcntl = None      # Windows dev box: no "is the app running?" check

PACKED = {"jpg", "jpeg", "png", "gif", "webp", "pdf", "zip", "docx", "xlsx", "odt", "gz"}   # already compressed: store as-is
KEEP_SAFETY = 5                         # pre-restore-*.db copies to keep (pre-migrate-* keeps the same number)
_locks = {}
# Names of the temp files Craft Stash (and Python's tempfile, used by uploads and the self-test) leaves behind.
OURS = (".snapshot-", ".download-", ".restore-", ".write-test-", "tmp", "craftstash-selftest-")

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

def clean_leftovers(where=None, age=86400):
    """Temp files from a backup, restore, upload or self-test that was killed half-way. Older versions left them in the
    data folder itself, so that is swept too."""
    P = paths.resolve(where); now = time.time()
    for p in list(P.data.glob(".*")) + (list(P.tmp.iterdir()) if P.tmp.is_dir() else []):
        if not p.name.startswith(OURS if p.parent == P.tmp else OURS[:3]): continue
        try:
            if now - p.lstat().st_mtime > age: shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
        except OSError: pass

def _upload_files(P):
    """(path on disk, name in the zip) for every uploaded file and photo, wherever their folders are mapped. The zip
    layout stays uploads/... and uploads/photos/..., so backups move freely between differently mapped installs."""
    skip = [P.photos, P.tmp, P.backups, P.selftest, P.data]
    def walk(root, prefix, skip):
        if not root.is_dir(): return
        for p in sorted(root.rglob("*")):
            if p.is_file() and not any(paths.under(p, s) and not paths.under(root, s) for s in skip):
                yield p, prefix + p.relative_to(root).as_posix()
    yield from walk(P.uploads, "uploads/", skip)
    yield from walk(P.photos, "uploads/photos/", [P.tmp, P.backups, P.selftest])

def make_zip(where, zip_path):
    P = paths.resolve(where); P.tmp.mkdir(parents=True, exist_ok=True); tmp = P.tmp / f".snapshot-{time.time_ns()}.db"
    snapshot_db(P.db, tmp)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(tmp, "craft.db")
            for p, name in _upload_files(P):
                packed = p.suffix.lstrip(".").lower() in PACKED
                z.write(p, name, compress_type=zipfile.ZIP_STORED if packed else zipfile.ZIP_DEFLATED)
    finally: tmp.unlink(missing_ok=True)

def start_snapshots(where, keep):
    """Daily database-only snapshots in BACKUP_DIR. Uploads are not copied (they are large and rarely change)."""
    if keep <= 0: return
    P = paths.resolve(where)
    def loop():
        while True:
            try:
                folder = P.backups; folder.mkdir(parents=True, exist_ok=True)
                newest = max((f.stat().st_mtime for f in folder.glob("auto-*.db")), default=0)
                if time.time() - newest > 86400:
                    snapshot_db(P.db, folder / f"auto-{stamp()}.db")
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

def _restore_uploads(P, staged):
    """Put a backup's files back wherever UPLOADS_DIR and PHOTOS_DIR point now. The folders are emptied, not removed:
    either may be a mount point, and other mapped folders nested inside them are left alone."""
    others = [P.data, P.backups, P.tmp, P.selftest]
    nested = [o for o in others if paths.under(o, P.uploads) and not paths.under(P.uploads, o)]
    P.uploads.mkdir(parents=True, exist_ok=True); P.photos.mkdir(parents=True, exist_ok=True)
    paths.clear_dir(P.uploads, keep=nested + [P.photos]); paths.clear_dir(P.photos, keep=[o for o in others if paths.under(o, P.photos)])
    sp = staged / "photos"
    if sp.is_dir(): shutil.copytree(sp, P.photos, dirs_exist_ok=True); shutil.rmtree(sp)
    shutil.copytree(staged, P.uploads, dirs_exist_ok=True)

def restore(where, src, force=False):
    """Replace the database (and uploads, for a full zip) from a backup. Stop the app first."""
    P, src = paths.resolve(where), Path(src)
    if not src.exists(): raise SystemExit(f"No such file: {src}")
    lock = _exclusive(P.data, force)
    try:
        P.backups.mkdir(parents=True, exist_ok=True); P.tmp.mkdir(parents=True, exist_ok=True)
        if P.db.exists():
            snapshot_db(P.db, P.backups / f"pre-restore-{stamp()}.db")
            prune(P.backups, "pre-restore-*.db", KEEP_SAFETY)
        stage = P.tmp / f".restore-{stamp()}"; stage.mkdir()
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
            for ext in ("-wal", "-shm"): (P.data / f"craft.db{ext}").unlink(missing_ok=True)
            shutil.copyfile(newdb, P.db)
            if uploads is not None: _restore_uploads(P, uploads)
        finally: shutil.rmtree(stage, ignore_errors=True)
    finally:
        if lock: lock.close()
