"""Where Craft Stash keeps its files.

Every folder can be mapped to its own volume or host folder, so large ones (photos, uploaded files, backups) can live
wherever the person wants: an array share, a NAS, a bigger disk. By default everything sits under DATA_DIR, as before.

    DATA_DIR      /data                 database, secret key, app lock
    UPLOADS_DIR   $DATA_DIR/uploads     pattern and guide files
    PHOTOS_DIR    $UPLOADS_DIR/photos   item and step photos
    BACKUP_DIR    $DATA_DIR/backups     daily snapshots, pre-migrate and pre-restore copies
    TMP_DIR       $DATA_DIR/tmp         uploads while they arrive, backup zips, restore staging, self-test sandbox
    SELFTEST_DIR  $DATA_DIR/selftest    self-test reports

Run `python paths.py` to print them (the entrypoint uses this to fix ownership).
"""
import os, shutil, sys, tempfile
from pathlib import Path

VARS = {"data": "DATA_DIR", "uploads": "UPLOADS_DIR", "photos": "PHOTOS_DIR", "backups": "BACKUP_DIR", "tmp": "TMP_DIR", "selftest": "SELFTEST_DIR"}
LABELS = {"data": "Database and settings", "uploads": "Uploaded files", "photos": "Photos", "backups": "Backups", "tmp": "Temporary files", "selftest": "Self-test reports"}

def _env(name):
    v = os.environ.get(name, "").strip()
    return Path(v) if v else None

class Paths:
    def __init__(self, data=None):
        self.explicit = {k for k, v in VARS.items() if _env(v)}
        if data is not None: self.explicit.add("data")
        self.data = Path(data) if data is not None else (_env("DATA_DIR") or Path("/data"))
        self.uploads = _env("UPLOADS_DIR") or self.data / "uploads"
        self.photos = _env("PHOTOS_DIR") or self.uploads / "photos"
        self.backups = _env("BACKUP_DIR") or self.data / "backups"
        self.tmp = _env("TMP_DIR") or self.data / "tmp"
        self.selftest = _env("SELFTEST_DIR") or self.data / "selftest"
        self.db = self.data / "craft.db"

    def folders(self): return {k: getattr(self, k) for k in VARS}

    def check(self):
        """Create the default folders, refuse to start if a mapped folder is missing or read-only, and send temp files to TMP_DIR."""
        for k, p in self.folders().items():
            if not p.is_dir():
                if k in self.explicit and k != "data":
                    # A folder someone set on purpose but that isn't there is almost always a missing volume mapping (or an
                    # unmounted share). Creating it would quietly fill the container's own disk instead.
                    raise SystemExit(f"{VARS[k]}={p} does not exist. Map a volume or host folder there, or remove {VARS[k]} to use the default.")
                p.mkdir(parents=True, exist_ok=True)
            try:
                fd, probe = tempfile.mkstemp(prefix=".write-test-", dir=p); os.close(fd); os.unlink(probe)
            except OSError as e:
                raise SystemExit(f"Can't write to {VARS[k]}={p} ({e.strerror}). Check the folder's owner matches PUID/PGID, or set CHOWN=always.")
        for k, p in self.folders().items():
            if k != "tmp" and (paths_same(p, self.tmp) or under(p, self.tmp)):
                # Old temp files are swept from TMP_DIR, so it must not hold anything that has to be kept.
                raise SystemExit(f"TMP_DIR={self.tmp} contains {VARS[k]}={p}. Give TMP_DIR a folder of its own.")
        tempfile.tempdir = str(self.tmp); os.environ["TMPDIR"] = str(self.tmp)

    def in_container_layer(self, p):
        """True when a folder is on the container's own filesystem, i.e. not mapped to a volume or host folder."""
        if not os.environ.get("CRAFT_IN_CONTAINER"): return False
        try: return os.stat(p).st_dev == os.stat("/").st_dev
        except OSError: return False

    def where(self, k, p, unmapped):
        """A short description of how a folder is stored, for the admin page."""
        if unmapped: return "Not mapped (inside the container)"
        try:
            if os.path.ismount(p): return "Own volume or folder"
            if k != "data" and os.stat(p).st_dev != os.stat(self.data).st_dev: return "Own volume or folder"
        except OSError: return "Missing"
        if k != "data" and under(p, self.data): return "In the data folder"
        return "Own volume or folder" if os.environ.get("CRAFT_IN_CONTAINER") else "Local folder"

    def report(self):
        """One row per folder for the admin page and the startup log."""
        rows = []
        for k, p in self.folders().items():
            try: u = shutil.disk_usage(p); free, total = u.free, u.total
            except OSError: free = total = None
            unmapped = self.in_container_layer(p)
            rows.append({"key": k, "var": VARS[k], "label": LABELS[k], "path": str(p), "free": free, "total": total,
                         "unmapped": unmapped, "where": self.where(k, p, unmapped)})
        return rows

    def log_summary(self, out=None):
        out = out or sys.stderr
        def gb(n): return "?" if n is None else f"{n / 1e9:.1f} GB free"
        rows = self.report()
        def how(r): return {"Own volume or folder": "mapped", "In the data folder": "in data", "Local folder": "local"}.get(r["where"], "NOT MAPPED" if r["unmapped"] else r["where"].lower())
        print("[storage] " + "  ".join(f"{r['key']}={r['path']} ({how(r)}, {gb(r['free'])})" for r in rows), file=out, flush=True)
        for r in rows:
            if r["unmapped"] and r["key"] != "selftest":
                print(f"[storage] WARNING: {r['var']}={r['path']} is inside the container, not on a mapped volume. Files written "
                      f"there fill Docker's own disk (the vdisk on Unraid) and are lost when the container is recreated.", file=out, flush=True)

def resolve(x=None):
    """A Paths for a Paths, a data folder, or (None) the environment."""
    if isinstance(x, Paths): return x
    return Paths(x)

def paths_same(a, b): return Path(a).resolve() == Path(b).resolve()

def under(child, parent):
    try: Path(child).resolve().relative_to(Path(parent).resolve()); return True
    except ValueError: return False

def clear_dir(folder, keep=()):
    """Empty a folder without removing it (it may be a mount point). Leaves `keep` folders and anything mounted inside."""
    folder = Path(folder); keep = {Path(k).resolve() for k in keep}
    if not folder.is_dir(): return
    for child in folder.iterdir():
        r = child.resolve()
        if r in keep or any(under(k, r) for k in keep) or os.path.ismount(child): continue
        shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()

if __name__ == "__main__":
    if sys.argv[1:] == ["--tmp"]: print(Paths().tmp); sys.exit()
    # Folders the entrypoint should create (if missing) and own. A folder set on purpose but missing is left out: the
    # app refuses to start with a clear message rather than writing into the container.
    p = Paths(); folders = p.folders()
    missing = [f for k, f in folders.items() if k in p.explicit and k != "data" and not f.is_dir()]
    for k, f in folders.items():
        if f.is_dir() or (k not in p.explicit or k == "data") and not any(under(f, m) for m in missing): print(f)
