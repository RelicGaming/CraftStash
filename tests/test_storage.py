"""Separately mappable storage: every folder can live on its own volume, and nothing is written outside them."""
import io, os, sqlite3, subprocess, sys, tempfile, time, zipfile
from pathlib import Path
import pytest
from conftest import seed, png_bytes

APP = Path(__file__).resolve().parent.parent / "app"

@pytest.fixture
def split(tmp_path_factory, make_env):
    """An install with photos, files, backups and temp files each on their own 'volume' (separate folders)."""
    root = tmp_path_factory.mktemp("volumes")
    dirs = {k: root / k for k in ("photos", "files", "backups", "tmp")}
    for d in dirs.values(): d.mkdir()
    env = make_env(PHOTOS_DIR=dirs["photos"], UPLOADS_DIR=dirs["files"], BACKUP_DIR=dirs["backups"], TMP_DIR=dirs["tmp"])
    env.dirs = dirs; env.root = root
    return env

def add_things(c):
    seed(c)
    c.post("/items/1/edit", {"name": "Merino", "qty": "5", "photo": (io.BytesIO(png_bytes()), "p.png")})
    c.post("/projects/1/upload", {"file": (io.BytesIO(b"%PDF-1.4 pattern"), "pattern.pdf")})
    c.post("/projects/1/steps", {"title": "Cast on", "photos": [(io.BytesIO(png_bytes()), "s.png")]}, content_type="multipart/form-data")

def listing(folder): return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*") if p.is_file())

def test_split_folders_hold_their_own_files(split):
    amy = split.client().register("amy"); add_things(amy)
    assert len(listing(split.dirs["photos"])) == 2                                     # item photo + step photo
    assert [p for p in listing(split.dirs["files"]) if p.endswith(".pdf")]
    assert not (split.data / "uploads").exists()                                       # nothing fell back into /data
    assert amy.get("/photos/1").status_code == 200 and amy.get("/step-photos/1").status_code == 200 and amy.get("/files/1").status_code == 200
    assert tempfile.gettempdir() == str(split.dirs["tmp"]) and os.environ["TMPDIR"] == str(split.dirs["tmp"])

def test_nothing_is_written_outside_the_configured_folders(split):
    amy = split.client().register("amy"); add_things(amy)
    r = amy.post("/admin/backup"); assert r.status_code == 200
    zp = split.dirs["tmp"] / "b.zip"; zp.write_bytes(r.data)
    import backup; backup.restore(split.mod.P, zp, force=True)
    allowed = {"craft.db", "craft.db-wal", "craft.db-shm", "secret_key", ".app.lock"}
    assert set(listing(split.data)) <= allowed | {p for p in listing(split.data) if p.startswith("selftest/")}
    assert not listing(split.dirs["tmp"])[1:] or all(p == "b.zip" for p in listing(split.dirs["tmp"]))   # temp zips cleaned up

def test_photos_are_stored_by_name_so_the_folder_can_move(split, make_env, tmp_path_factory):
    amy = split.client().register("amy"); add_things(amy)
    names = [r[0] for r in sqlite3.connect(split.data / "craft.db").execute("SELECT photo FROM items WHERE photo<>'' UNION ALL SELECT stored FROM step_photos")]
    assert names and all("/" not in n and "\\" not in n for n in names)               # no folder baked into the database
    moved = tmp_path_factory.mktemp("moved"); os.rename(split.dirs["photos"], moved / "photos")
    env = make_env(PHOTOS_DIR=moved / "photos"); c = env.client(); c.login("amy")
    assert c.get("/photos/1").status_code == 200 and c.get("/step-photos/1").status_code == 200

def test_backup_zip_layout_is_the_same_however_folders_are_mapped(split):
    amy = split.client().register("amy"); add_things(amy)
    names = zipfile.ZipFile(io.BytesIO(amy.post("/admin/backup").data)).namelist()
    assert "craft.db" in names
    assert sum(n.startswith("uploads/photos/") for n in names) == 2 and any(n.startswith("uploads/") and n.endswith(".pdf") for n in names)

def test_restore_into_split_folders_puts_files_back_where_they_are_mapped(split):
    amy = split.client().register("amy"); add_things(amy)
    zp = split.root / "b.zip"; zp.write_bytes(amy.post("/admin/backup").data)
    before = {k: listing(d) for k, d in split.dirs.items() if k in ("photos", "files")}
    amy.post("/projects/1/delete"); amy.post("/items/1/delete")
    (split.dirs["photos"] / "stray.jpg").write_bytes(b"x")
    inode = split.dirs["photos"].stat().st_ino
    import backup; backup.restore(split.mod.P, zp, force=True)
    assert split.dirs["photos"].stat().st_ino == inode                                # emptied, not deleted and recreated
    assert {k: listing(split.dirs[k]) for k in before} == before                       # stray file gone, originals back
    assert list(split.dirs["backups"].glob("pre-restore-*.db"))

def test_restore_empties_a_mount_point_instead_of_removing_it(env, amy, monkeypatch):
    """Default layout, but with a separately mounted folder inside uploads that restore must leave alone."""
    add_things(amy)
    zp = env.data / "b.zip"; zp.write_bytes(amy.post("/admin/backup").data)
    uploads = env.data / "uploads"; inode = uploads.stat().st_ino
    mounted = uploads / "nas"; mounted.mkdir(); (mounted / "keep.txt").write_text("someone else's")
    import paths; real = os.path.ismount
    monkeypatch.setattr(paths.os.path, "ismount", lambda p: Path(p) in (uploads, mounted) or real(p))
    import backup; backup.restore(env.data, zp, force=True)
    assert uploads.stat().st_ino == inode and (mounted / "keep.txt").exists()
    c = env.client(); c.login("amy"); assert c.get("/photos/1").status_code == 200 and c.get("/files/1").status_code == 200

def test_a_folder_that_is_set_but_missing_stops_the_app(make_env, tmp_path_factory):
    missing = tmp_path_factory.mktemp("x") / "not-mapped"
    with pytest.raises(SystemExit, match="PHOTOS_DIR=.*does not exist"): make_env(PHOTOS_DIR=missing)
    assert not missing.exists()                                                        # not quietly created

def test_tmp_dir_must_not_hold_other_folders(make_env, tmp_path):
    with pytest.raises(SystemExit, match="TMP_DIR"): make_env(TMP_DIR=tmp_path)        # the data folder itself

def test_read_only_folder_stops_the_app(make_env, tmp_path_factory):
    if os.geteuid() == 0: pytest.skip("root can write anywhere")
    ro = tmp_path_factory.mktemp("ro"); ro.chmod(0o555)
    try:
        with pytest.raises(SystemExit, match="Can't write to BACKUP_DIR"): make_env(BACKUP_DIR=ro)
    finally: ro.chmod(0o755)

def test_old_temp_files_are_swept_but_nothing_else(split):
    import backup
    t = split.dirs["tmp"]; old = time.time() - 2 * 86400
    for n in (".download-a.zip", "tmpabc123", "craftstash-selftest-x", "keep-me.txt"):
        (t / n).write_bytes(b"x"); os.utime(t / n, (old, old))
    (t / "tmpfresh").write_bytes(b"x")
    backup.clean_leftovers(split.mod.P)
    assert sorted(p.name for p in t.iterdir()) == ["keep-me.txt", "tmpfresh"]

def test_cleanup_keeps_step_photos(split):
    amy = split.client().register("amy"); add_things(amy)
    old = time.time() - 7200
    for p in split.dirs["photos"].iterdir(): os.utime(p, (old, old))
    orphan = split.dirs["photos"] / "orphan.jpg"; orphan.write_bytes(b"x"); os.utime(orphan, (old, old))
    r = subprocess.run([sys.executable, "manage.py", "cleanup"], cwd=APP, env=os.environ, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not orphan.exists() and len(listing(split.dirs["photos"])) == 2 and "1 orphaned" in r.stdout

def test_admin_page_shows_each_folder(split):
    amy = split.client().register("amy"); html = amy.html("/admin")
    for var in ("DATA_DIR", "UPLOADS_DIR", "PHOTOS_DIR", "BACKUP_DIR", "TMP_DIR", "SELFTEST_DIR"): assert var in html
    assert str(split.dirs["photos"]) in html and " free of " in html

def test_unmapped_folders_are_flagged_in_a_container(make_env, monkeypatch, capsys):
    monkeypatch.setenv("CRAFT_IN_CONTAINER", "1")
    import paths
    P = paths.Paths(Path(tempfile.gettempdir()))                                       # the real check: same device as / means unmapped
    assert P.in_container_layer(P.data) == (os.stat(P.data).st_dev == os.stat("/").st_dev)
    monkeypatch.delenv("CRAFT_IN_CONTAINER"); assert not P.in_container_layer(P.data)  # outside a container it never warns
    monkeypatch.setenv("CRAFT_IN_CONTAINER", "1")
    monkeypatch.setattr(paths.Paths, "in_container_layer", lambda self, p: True)
    env = make_env(); html = env.client().register("amy").html("/admin")
    assert "Not mapped" in html and "fill Docker" in html
    assert "WARNING: DATA_DIR" in capsys.readouterr().err

def test_selftest_sandbox_never_sees_the_real_folders(monkeypatch):
    sys.path.insert(0, str(APP)); import selftest
    for k in ("UPLOADS_DIR", "PHOTOS_DIR", "BACKUP_DIR", "TMP_DIR", "SELFTEST_DIR"): monkeypatch.setenv(k, "/real/" + k)
    env = selftest.sandbox_env("/sandbox")
    assert env["DATA_DIR"] == "/sandbox" and not any(k in env for k in selftest.SANDBOX_DROP)

def test_paths_cli_lists_folders_for_the_entrypoint(tmp_path):
    (tmp_path / "photos").mkdir()
    e = {**os.environ, "DATA_DIR": str(tmp_path / "data"), "PHOTOS_DIR": str(tmp_path / "photos"), "BACKUP_DIR": str(tmp_path / "gone")}
    for k in ("UPLOADS_DIR", "TMP_DIR", "SELFTEST_DIR"): e.pop(k, None)
    out = subprocess.run([sys.executable, "paths.py"], cwd=APP, env=e, capture_output=True, text=True, check=True).stdout.split()
    assert str(tmp_path / "photos") in out and str(tmp_path / "data" / "uploads") in out
    assert str(tmp_path / "gone") not in out                                           # set but missing: left for the app to refuse
    tmp = subprocess.run([sys.executable, "paths.py", "--tmp"], cwd=APP, env=e, capture_output=True, text=True, check=True).stdout.strip()
    assert tmp == str(tmp_path / "data" / "tmp")
