"""Fixes from the 1.0 security review of everything since #36."""
import io, socket, sys, threading, time
import pytest
from conftest import seed

# ---------- pictures: a small file can hold a huge picture ----------
def png(w, h, mode="RGB"):
    from PIL import Image
    b = io.BytesIO(); Image.new(mode, (w, h), (200, 30, 30)).save(b, "PNG"); return b.getvalue()

def upload(data): import types; return types.SimpleNamespace(stream=io.BytesIO(data), filename="x.png")

def test_a_huge_picture_in_a_small_file_is_refused_before_decoding(env):
    from PIL import Image; Image.MAX_IMAGE_PIXELS, keep = None, Image.MAX_IMAGE_PIXELS
    try: bomb = png(8900, 8900)        # about 250 KB, 79 million pixels: decoding it took ~1 GB
    finally: Image.MAX_IMAGE_PIXELS = keep
    assert len(bomb) < 400_000
    import core, patterns
    t = time.monotonic()
    assert core.save_photo(upload(bomb)) is None and patterns.save_source(upload(bomb)) is None
    assert time.monotonic() - t < 1                                          # refused from the header, not decoded

def test_png_webp_gif_limit_is_lower_than_jpeg(env):
    import core
    assert core.open_small(io.BytesIO(png(4100, 3000)), 900) is None
    im = core.open_small(io.BytesIO(png(4000, 3000)), 900); assert im and max(im.size) == 900
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (6000, 6000), (1, 2, 3)).save(b, "JPEG", quality=40)
    im = core.open_small(io.BytesIO(b.getvalue()), 900); assert im and max(im.size) == 900   # 36 MP JPEG: scaled while decoding

def test_photos_still_turn_upright(env):
    from PIL import Image
    im = Image.new("RGB", (400, 200), (10, 200, 10)); e = im.getexif(); e[0x0112] = 6
    b = io.BytesIO(); im.save(b, "JPEG", exif=e)
    import core; assert core.open_small(io.BytesIO(b.getvalue()), 900).size == (200, 400)

def test_an_oversized_photo_upload_is_refused_with_a_message(env, amy):
    seed(amy); from PIL import Image; Image.MAX_IMAGE_PIXELS, keep = None, Image.MAX_IMAGE_PIXELS
    try: bomb = png(8900, 8900)
    finally: Image.MAX_IMAGE_PIXELS = keep
    r = amy.post("/items/1/edit", {"name": "Merino", "qty": "5", "photo": (io.BytesIO(bomb), "big.png")}, content_type="multipart/form-data")
    assert r.status_code in (200, 302) and not env.db().execute("SELECT photo FROM items WHERE id=1").fetchone()[0]

# ---------- the link fetcher ----------
@pytest.mark.parametrize("ip", ["64:ff9b::a00:1", "64:ff9b::7f00:1", "64:ff9b::c0a8:101", "::127.0.0.1", "::10.0.0.1"])
def test_ipv6_forms_of_private_addresses_are_refused(env, ip):
    lf = sys.modules["linkfetch"]; assert not lf.addr_ok(ip)

def test_ipv6_forms_of_public_addresses_still_pass(env):
    lf = sys.modules["linkfetch"]; assert lf.addr_ok("64:ff9b::808:808") and lf.addr_ok("2606:4700::1111")

@pytest.fixture
def slow_site(env, monkeypatch):
    """A site that sends its answer one byte every 0.2 s: headers (/h) or body (/b)."""
    lf = sys.modules["linkfetch"]
    srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(8); port = srv.getsockname()[1]; stop = threading.Event()
    def serve(c):
        try:
            path = c.recv(4096).split(b" ")[1]
            head = b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 100000\r\n\r\n"
            if path == b"/b": c.sendall(head)
            for ch in (head if path == b"/h" else b"x" * 100000):
                if stop.is_set(): break
                c.send(bytes([ch]) if isinstance(ch, int) else ch); time.sleep(0.2)
        except OSError: pass
        finally: c.close()
    def loop():
        while not stop.is_set():
            try: c, _ = srv.accept()
            except OSError: return
            threading.Thread(target=serve, args=(c,), daemon=True).start()
    threading.Thread(target=loop, daemon=True).start()
    monkeypatch.setattr(lf, "addr_ok", lambda ip: ip == "127.0.0.1"); monkeypatch.setattr(lf, "PORTS", {"http": port, "https": 443})
    monkeypatch.setattr(lf, "TOTAL", 1.5); monkeypatch.setattr(lf, "TIMEOUT", 1.0)
    yield f"http://127.0.0.1:{port}"
    stop.set(); srv.close()

@pytest.mark.parametrize("path", ["/h", "/b"])
def test_a_site_that_trickles_is_cut_off_at_the_deadline(slow_site, path):
    lf = sys.modules["linkfetch"]; t = time.monotonic()
    with pytest.raises(lf.FetchError, match="too long"): lf.fetch(slow_site + path)
    assert time.monotonic() - t < 3          # TOTAL is 1.5 s here; it used to run for as long as the site kept sending

# ---------- where a form sends you back to ----------
@pytest.mark.parametrize("nxt", ["/\t/evil.example", "/\n/evil.example", "/\r\n/evil.example", "//evil.example", "/\\evil.example",
                                 "https://evil.example", " /collections/1", "/ /evil.example"])
def test_return_paths_cannot_leave_the_site(env, amy, nxt):
    seed(amy)
    r = amy.post("/items/1/adjust", {"amount": "1", "action": "add", "next": nxt})
    assert r.status_code == 302 and r.headers["Location"] == "/collections/1"

def test_return_paths_inside_the_app_still_work(env, amy):
    seed(amy)
    r = amy.post("/items/1/adjust", {"amount": "1", "action": "add", "next": "/collections/1?q=Merino&sort=low"})
    assert r.headers["Location"] == "/collections/1?q=Merino&sort=low"
