"""Container smoke test: the real image with split, mapped folders writes nothing inside the container.

Slow (builds the image), so it only runs with CRAFT_DOCKER_TESTS=1 and a working `docker`:
    CRAFT_DOCKER_TESTS=1 python -m pytest tests/test_container.py
"""
import http.cookiejar, os, re, shutil, subprocess, time, urllib.parse, urllib.request, uuid
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(not os.environ.get("CRAFT_DOCKER_TESTS") or not shutil.which("docker"),
                                reason="set CRAFT_DOCKER_TESTS=1 (needs docker) to run the container smoke test")
IMAGE = "craft-stash:storage-test"

def sh(*a, **kw): return subprocess.run(a, check=True, capture_output=True, text=True, **kw).stdout

@pytest.fixture(scope="module")
def image():
    sh("docker", "build", "-q", "-t", IMAGE, str(ROOT)); return IMAGE

def start(image, vols, extra=()):
    name = f"cs-smoke-{uuid.uuid4().hex[:8]}"
    args = ["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::8000", "-e", f"PUID={os.getuid()}", "-e", f"PGID={os.getgid()}",
            "-e", "PHOTOS_DIR=/photos", "-e", "UPLOADS_DIR=/files", "-e", "BACKUP_DIR=/backups", *extra]
    for host, cont in vols.items(): args += ["-v", f"{host}:{cont}"]
    sh(*args, image)
    port = sh("docker", "port", name, "8000").split(":")[-1].strip()
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            if urllib.request.urlopen(base + "/healthz", timeout=2).status == 200: return name, base
        except OSError: time.sleep(0.5)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True); sh("docker", "rm", "-f", name)
    raise AssertionError("container did not become healthy:\n" + logs.stdout + logs.stderr)

def volumes(tmp_path):
    v = {tmp_path / k: "/" + k for k in ("data", "photos", "files", "backups")}
    for d in v: d.mkdir()
    return {str(k): c for k, c in v.items()}

def use_the_app(base):
    """Register, add a collection, type and item with a photo, and upload a project file."""
    jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    def tok(): return re.search(r'name="_csrf" value="(\w+)"', op.open(base + "/login").read().decode()).group(1)
    def post(path, fields, files=None):
        if not files:
            return op.open(base + path, urllib.parse.urlencode({**fields, "_csrf": tok()}).encode())
        b = uuid.uuid4().hex; body = b""
        for k, v in {**fields, "_csrf": tok()}.items(): body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, (fn, data) in files.items():
            body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n"
        req = urllib.request.Request(base + path, body + f"--{b}--\r\n".encode(), {"Content-Type": f"multipart/form-data; boundary={b}"})
        return op.open(req)
    import io; from PIL import Image
    png = io.BytesIO(); Image.new("RGB", (1600, 1200), (200, 40, 40)).save(png, "PNG")   # >500 KB? no matter: spooled either way
    post("/register", {"username": "amy", "password": "password1"})
    post("/collections", {"name": "Knitting"}); post("/collections/1/types", {"name": "Yarn", "unit": "skein"})
    post("/types/1/items/new", {"name": "Merino", "qty": "3"}, {"photo": ("p.png", png.getvalue())})
    post("/projects", {"name": "Hat"}); post("/projects/1/upload", {}, {"file": ("pattern.pdf", b"%PDF-1.4 " + os.urandom(800_000))})
    assert post("/admin/backup", {}).read(2) == b"PK"

def test_split_folders_and_nothing_written_inside_the_container(image, tmp_path):
    vols = volumes(tmp_path); name, base = start(image, vols)
    try:
        use_the_app(base)
        assert len(list((tmp_path / "photos").iterdir())) == 1 and list((tmp_path / "files").glob("*.pdf"))
        assert (tmp_path / "data" / "craft.db").exists() and not (tmp_path / "data" / "uploads").exists()
        changed = [l for l in sh("docker", "diff", name).splitlines() if l.split()[-1] not in ("/photos", "/files", "/backups", "/data")]
        assert not changed, f"written inside the container: {changed}"
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True); out = logs.stdout + logs.stderr
        assert "[storage]" in out and "WARNING" not in out, out
    finally: subprocess.run(["docker", "rm", "-f", name], capture_output=True)

def test_read_only_container_starts_and_works(image, tmp_path):
    vols = volumes(tmp_path); name, base = start(image, vols, ["--read-only", "--tmpfs", "/run"])
    try: use_the_app(base); assert len(list((tmp_path / "photos").iterdir())) == 1
    finally: subprocess.run(["docker", "rm", "-f", name], capture_output=True)

def test_unmapped_folder_is_reported(image, tmp_path):
    v = tmp_path / "data"; v.mkdir()
    name, base = start(image, {str(v): "/data"}, ["-e", "PHOTOS_DIR=", "-e", "UPLOADS_DIR=", "-e", "BACKUP_DIR="])
    try: assert "WARNING" not in sh("docker", "logs", name)
    finally: subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    name, base = start(image, {}, ["-e", "PHOTOS_DIR=", "-e", "UPLOADS_DIR=", "-e", "BACKUP_DIR="])   # nothing mapped (the image's VOLUME is anonymous)
    try:
        logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
        assert "[storage]" in logs.stdout + logs.stderr
    finally: subprocess.run(["docker", "rm", "-f", name], capture_output=True)
