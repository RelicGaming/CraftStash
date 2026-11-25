"""Importing ideas from links: a pin or any page becomes an Idea project with its picture, a product can go to the
wishlist, a board's feed imports many pins (skipping ones already imported), the phone share target, the cover picture,
the API, upgrades, and the fetcher's guards against private addresses, odd links and big files.

The pages come from a small local web server. The fetcher refuses local addresses and unusual ports, so the tests that
use it allow 127.0.0.1 and the server's port; the guards themselves are tested without that."""
import http.server, io, json, re, sqlite3, sys, threading
import pytest
from conftest import png_bytes, seed, TEST_DB
from test_api import api, make_token

def one(env, sql, *args): return env.db().execute(sql, args).fetchone()
def rows(env, sql, *args): return env.db().execute(sql, args).fetchall()

PNG = png_bytes((300, 200))

def pin_page(base, n="123", title="Rainbow ripple blanket"):
    return f"""<!doctype html><html><head><title>Pin on Blankets</title>
<meta property="og:title" content="{title}"><meta property="og:description" content="Easy &amp; cosy, in 12 colours.">
<meta property="og:image" content="/img/blanket.png"><meta property="og:url" content="{base}/pin/{n}/">
</head><body>...</body></html>"""

def feed(base, n=3, doctype=""):
    items = "".join(f"""<item><title>Idea {i}</title><link>{base}/pin/{i}/</link>
<description>&lt;a href="/pin/{i}/"&gt;&lt;img src="{base}/img/blanket.png"&gt;&lt;/a&gt;Pattern number {i}</description></item>""" for i in range(1, n + 1))
    return f"""<?xml version="1.0"?>{doctype}<rss version="2.0"><channel><title>Blankets</title><link>{base}/amy/blankets/</link>{items}</channel></rss>"""

class Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, body, ctype="text/html; charset=utf-8", status=200, headers=()):
        body = body.encode() if isinstance(body, str) else body
        self.send_response(status); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(body)))
        for k, v in headers: self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        base = f"http://127.0.0.1:{self.server.server_port}"; p = self.path
        self.server.hits.append(p)
        if p == "/short": return self.send("", status=302, headers=[("Location", "/pin/123/?utm=share")])
        if re.fullmatch(r"/pin/(\d+)/(\?.*)?", p): return self.send(pin_page(base, re.match(r"/pin/(\d+)", p)[1]))
        if p == "/img/blanket.png": return self.send(PNG, "image/png")
        if p == "/photo.png": return self.send(PNG, "image/png")
        if p == "/product": return self.send("""<html><head><meta property="og:title" content="Merino yarn, rose">
            <meta property="og:site_name" content="Wool Shop"><meta property="product:price:amount" content="12.50">
            <meta property="og:image" content="/img/blanket.png"></head></html>""")
        if p == "/plain": return self.send("<html><head><title>  Just a   blog post </title></head><body>no tags</body></html>")
        if p == "/board.rss": return self.send(feed(base), "application/rss+xml")
        if p == "/bomb.rss": return self.send(feed(base, 1, '<!DOCTYPE r [<!ENTITY a "aaaa">]>'), "application/rss+xml")
        if p == "/to-private": return self.send("", status=302, headers=[("Location", "http://10.0.0.1/secret")])
        if p == "/loop": return self.send("", status=302, headers=[("Location", "/loop")])
        if p == "/zip": return self.send(b"PK\x03\x04", "application/zip")
        return self.send("nope", status=404)

@pytest.fixture(scope="module")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site); srv.hits = []
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()

@pytest.fixture
def web(env, site, monkeypatch):
    """The base address of the local site, with the fetcher allowed to reach it (and nothing else local)."""
    lf = sys.modules["linkfetch"]
    monkeypatch.setattr(lf, "addr_ok", lambda ip: ip == "127.0.0.1")
    monkeypatch.setattr(lf, "PORTS", {"http": site.server_port, "https": 443})
    site.hits.clear()
    return f"http://127.0.0.1:{site.server_port}"

# ---------- the guards, as they really are ----------
@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/x", "javascript:alert(1)", "http://", "http://user:pw@example.com/",
                                 "http://example.com:8080/", "https://example.com:22/", "not a link"])
def test_odd_links_are_refused(env, url):
    lf = sys.modules["linkfetch"]
    with pytest.raises(lf.FetchError): lf.check_url(url)

@pytest.mark.parametrize("host", ["127.0.0.1", "10.1.2.3", "172.17.0.1", "192.168.1.10", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                                  "[::1]", "[::ffff:127.0.0.1]", "[fd00::1]", "[fe80::1]", "224.0.0.1"])
def test_private_and_local_addresses_are_refused(env, host):
    lf = sys.modules["linkfetch"]
    with pytest.raises(lf.FetchError, match="private or local"): lf.fetch(f"http://{host}/")

def test_public_addresses_pass_the_check(env):
    lf = sys.modules["linkfetch"]
    assert lf.addr_ok("93.184.216.34") and lf.addr_ok("2606:4700::1111") and not lf.addr_ok("::ffff:10.0.0.1")
    assert lf.check_url("https://www.pinterest.com/pin/1/?x=1") == ("https", "www.pinterest.com", 443, "/pin/1/?x=1")

def test_a_redirect_to_a_private_address_is_refused(env, web):
    lf = sys.modules["linkfetch"]
    with pytest.raises(lf.FetchError, match="private or local"): lf.fetch(web + "/to-private")
    with pytest.raises(lf.FetchError, match="redirects too many"): lf.fetch(web + "/loop")

def test_pictures_over_the_limit_are_refused(env, web, monkeypatch):
    lf = sys.modules["linkfetch"]; monkeypatch.setattr(lf, "MAX_IMAGE", 100)
    with pytest.raises(lf.FetchError, match="too big"): lf.fetch_image(web + "/img/blanket.png")

def test_feeds_with_entities_are_refused(env, web):
    with pytest.raises(sys.modules["linkfetch"].FetchError, match="can't be read"): sys.modules["linkimport"].board_pins(web + "/bomb.rss")

def test_pinterest_links(env):
    lf = sys.modules["linkfetch"]
    assert lf.is_pinterest("https://pin.it/abc") and lf.is_pinterest("https://www.pinterest.co.uk/pin/1/") and not lf.is_pinterest("https://notpinterest.com/")
    assert lf.board_feed("https://www.pinterest.com/amy/blankets/") == "https://www.pinterest.com/amy/blankets.rss"
    assert lf.board_feed("https://au.pinterest.com/amy/blankets") == "https://au.pinterest.com/amy/blankets.rss"
    assert lf.board_feed("https://www.pinterest.com/pin/123/") == "https://www.pinterest.com/pin/123/"       # a pin isn't a board
    assert lf.source_key("https://www.pinterest.com/pin/99/?utm=x") == lf.source_key("https://au.pinterest.com/pin/99") == "pinterest/pin/99"
    assert lf.source_key("https://www.example.com/a/?q=1#top") == "example.com/a"

# ---------- one link ----------
def test_a_pin_becomes_an_idea_with_its_picture(env, amy, web):
    r = amy.post("/import/link", {"url": web + "/short", "to": "project", "name": ""}, content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/projects/1")
    p = one(env, "SELECT name, status, notes, source, photo FROM projects WHERE id=1")
    assert p[:2] == ("Rainbow ripple blanket", "idea")
    assert p[2] == f"Easy & cosy, in 12 colours.\n\nFrom {web}/pin/123/" and p[3] == f"{web}/pin/123/"   # the page's own link, not the redirect
    assert (env.core.PHOTOS / p[4]).is_file() and p[4].endswith(".jpg")                                 # re-encoded, in the photos folder
    page = amy.html("/projects/1")
    assert "This is an idea" in page and 'class="cover"' in page and f'href="{web}/pin/123/"' in page and "noopener noreferrer nofollow" in page
    assert amy.get("/projects/1/photo").status_code == 200
    lst = amy.html("/projects")
    assert '<h2 class="section-title">Ideas</h2>' in lst and 'class="tile-photo"' in lst and "pill idea" in lst

def test_an_idea_holds_no_stock_until_it_is_planned(env, amy, web):
    seed(amy)    # Merino, 5 skeins
    amy.post("/import/link", {"url": web + "/pin/7/"}, content_type="multipart/form-data")
    amy.post("/projects/2/assign", {"item_id": "1", "qty": "4"})
    assert one(env, "SELECT free FROM stock WHERE id=1")[0] == 5
    assert "Short by" not in amy.html("/projects/2")
    amy.post("/projects/2/assign", {"item_id": "1", "qty": "9"})
    assert "Short by" not in amy.html("/projects/2") and "Merino" not in amy.html("/shopping")
    amy.post("/projects/2/update", {"name": "Blanket", "status": "planned", "notes": ""})
    assert one(env, "SELECT status FROM projects WHERE id=2")[0] == "planned" and one(env, "SELECT free FROM stock WHERE id=1")[0] == -8   # 4 + 9 reserved
    amy.post("/projects/2/update", {"name": "Blanket", "status": "idea", "notes": ""})       # and back again
    assert one(env, "SELECT status FROM projects WHERE id=2")[0] == "idea"

def test_a_product_goes_to_the_wishlist(env, amy, web):
    r = amy.post("/import/link", {"url": web + "/product", "to": "wishlist"}, content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/wishlist/1")
    w = one(env, "SELECT name, photo FROM wishes"); s = one(env, "SELECT shop, url, price FROM wish_sightings")
    assert w[0] == "Merino yarn, rose" and (env.core.PHOTOS / w[1]).is_file()
    assert s == ("Wool Shop", web + "/product", 12.5) and one(env, "SELECT COUNT(*) FROM projects")[0] == 0

def test_a_link_straight_to_a_picture(env, amy, web):
    amy.post("/import/link", {"url": web + "/photo.png"}, content_type="multipart/form-data")
    assert one(env, "SELECT name, photo<>'' FROM projects") == ("photo", 1)

def test_a_page_without_tags_uses_its_title(env, amy, web):
    amy.post("/import/link", {"url": web + "/plain"}, content_type="multipart/form-data")
    assert one(env, "SELECT name, photo FROM projects") == ("Just a blog post", "")
    assert "has no picture that could be downloaded" in amy.html("/projects/1")

def test_a_page_that_cant_be_read_needs_a_name_or_a_picture(env, amy, web):
    r = amy.post("/import/link", {"url": web + "/missing"}, content_type="multipart/form-data")
    assert r.status_code == 400 and "answered with an error (404)" in r.get_data(as_text=True) and one(env, "SELECT COUNT(*) FROM projects")[0] == 0
    assert f'value="{web}/missing"' in r.get_data(as_text=True)                                    # the form keeps what was typed
    amy.post("/import/link", {"url": web + "/missing", "name": "Granny squares", "photo": (io.BytesIO(PNG), "g.png")}, content_type="multipart/form-data")
    p = one(env, "SELECT name, status, source, photo, notes FROM projects")
    assert p[:3] == ("Granny squares", "idea", web + "/missing") and (env.core.PHOTOS / p[3]).is_file() and p[4] == f"From {web}/missing"
    assert "Saved it with what you entered" in amy.html("/projects/1")

def test_bad_links_are_refused_before_anything_is_fetched(env, amy, web, site):
    for url in ("http://10.0.0.1/", "http://localhost/", "file:///etc/passwd", "http://127.0.0.1:1/x"):
        r = amy.post("/import/link", {"url": url}, content_type="multipart/form-data")
        assert r.status_code == 400, url
    assert one(env, "SELECT COUNT(*) FROM projects")[0] == 0 and not site.hits
    amy.post("/import/link", {"url": "http://10.0.0.1/x", "name": "NAS notes"}, content_type="multipart/form-data")   # kept as a link only
    assert one(env, "SELECT name, source, photo FROM projects") == ("NAS notes", "http://10.0.0.1/x", "") and not site.hits

def test_something_that_isnt_a_page_or_a_picture(env, amy, web):
    r = amy.post("/import/link", {"url": web + "/zip"}, content_type="multipart/form-data")
    assert r.status_code == 400 and "isn&#39;t a web page or a picture" in r.get_data(as_text=True)

def test_a_picture_that_is_too_big_is_left_out(env, amy, web, monkeypatch):
    monkeypatch.setattr(sys.modules["linkfetch"], "MAX_IMAGE", 100)
    amy.post("/import/link", {"url": web + "/pin/5/"}, content_type="multipart/form-data")
    assert one(env, "SELECT name, photo FROM projects") == ("Rainbow ripple blanket", "")

def test_pinterest_titles_lose_their_keywords(env):
    lf = sys.modules["linkfetch"]
    page = lf.Page("https://www.pinterest.com/pin/9/", "text/html", b'<meta property="og:title" content="Sock yarn advent | Knitting, Gifts">')
    assert lf.page_info(page)["title"] == "Sock yarn advent"
    assert lf.page_info(lf.Page("https://blog.example/a", "text/html", b'<title>Socks | My blog</title>'))["title"] == "Socks | My blog"

def test_pinterest_links_are_tagged(env, amy, web, monkeypatch):
    monkeypatch.setattr(sys.modules["linkfetch"], "is_pinterest", lambda u: True)
    amy.post("/import/link", {"url": web + "/pin/5/"}, content_type="multipart/form-data")
    assert rows(env, "SELECT tag FROM project_tags") == [("pinterest",)]

# ---------- a board ----------
def test_a_board_imports_the_picked_pins_and_skips_them_next_time(env, amy, web):
    r = amy.get("/import/board?url=" + web + "/board.rss"); page = r.get_data(as_text=True)
    assert r.status_code == 200 and "Blankets" in page and page.count('name="pin"') == 3
    assert "https://i.pinimg.com" in r.headers["Content-Security-Policy"]                       # Pinterest's thumbnails, on this page only
    assert "i.pinimg.com" not in amy.get("/projects").headers["Content-Security-Policy"]
    r = amy.post("/import/board", {"url": web + "/board.rss", "pin": [web + "/pin/1/", web + "/pin/3/"]})
    assert r.headers["Location"].endswith("/projects?status=idea")
    got = rows(env, "SELECT name, status, source, photo<>'', notes FROM projects ORDER BY id")
    assert [g[:4] for g in got] == [("Idea 1", "idea", web + "/pin/1/", 1), ("Idea 3", "idea", web + "/pin/3/", 1)]
    assert got[0][4] == f"Pattern number 1\n\nFrom {web}/pin/1/"
    assert "Imported 2 ideas." in amy.html("/projects")
    page = amy.html("/import/board?url=" + web + "/board.rss")
    assert page.count('name="pin"') == 1 and "2 imported already" in page and 'href="/projects/1">Imported' in page
    amy.post("/import/board", {"url": web + "/board.rss", "pin": [web + "/pin/1/", web + "/pin/2/", web + "/pin/3/"]})
    assert one(env, "SELECT COUNT(*) FROM projects")[0] == 3
    assert "Imported 1 idea. 2 were imported before." in amy.html("/projects")

def test_a_big_board_is_imported_in_parts(env, amy, web, monkeypatch):
    monkeypatch.setattr(sys.modules["linkimport"], "BOARD_PICK", 2)
    amy.post("/import/board", {"url": web + "/board.rss", "pin": [web + f"/pin/{i}/" for i in (1, 2, 3)]})
    assert one(env, "SELECT COUNT(*) FROM projects")[0] == 2 and "1 more to go" in amy.html("/projects")

def test_imports_are_per_person(env, amy, web):
    amy.post("/import/board", {"url": web + "/board.rss", "pin": [web + "/pin/1/"]})
    bob = env.client().register("bob")
    assert bob.html("/import/board?url=" + web + "/board.rss").count('name="pin"') == 3            # bob hasn't imported any

def test_a_page_that_isnt_a_feed(env, amy, web):
    r = amy.get("/import/board?url=" + web + "/plain")
    assert r.status_code == 400 and "isn&#39;t a board or a feed" in r.get_data(as_text=True)

# ---------- the phone's Share menu ----------
def test_a_shared_link_fills_the_form(env, amy):
    page = amy.html("/import/link?title=Look&text=Look+at+this+https%3A%2F%2Fpin.it%2FabcD+on+Pinterest")
    assert 'value="https://pin.it/abcD"' in page and "Shared from your phone" in page

def test_sharing_while_signed_out_comes_back_after_signing_in(env, amy):
    amy.post("/logout")
    r = amy.get("/import/link?url=https%3A%2F%2Fpin.it%2Fxyz")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    r = amy.login("amy")
    assert r.status_code == 302 and "/import/link?url=https" in r.headers["Location"]
    assert 'value="https://pin.it/xyz"' in amy.html(r.headers["Location"])
    amy.post("/logout"); assert amy.login("amy").headers["Location"].endswith("/")                # only once

def test_the_manifest_is_public_and_offers_the_share_target(env):
    c = env.client(); r = c.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.mimetype == "application/manifest+json"
    m = json.loads(r.get_data())
    assert m["share_target"] == {"action": "/import/link", "method": "GET", "params": {"title": "title", "text": "text", "url": "url"}}
    assert {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"} and c.get(m["icons"][0]["src"]).status_code == 200
    assert 'rel="manifest"' in c.html("/login")

def test_link_import_can_be_turned_off(make_env):
    env = make_env(LINK_IMPORT="off"); amy = env.client().register("amy")
    assert amy.get("/import/link").status_code == 404 and amy.get("/import/board").status_code == 404
    assert "share_target" not in json.loads(amy.get("/manifest.webmanifest").get_data())
    assert "Import from a link" not in amy.html("/projects")
    _, post = api(env, make_token(amy, "write"))
    r = post("/api/v1/import/link", json={"url": "https://example.com/"})
    assert r.status_code == 404 and "turned off" in r.get_json()["error"]

# ---------- the cover picture ----------
def test_cover_picture_upload_replace_remove(env, amy):
    amy.post("/projects", {"name": "Hat"})
    amy.post("/projects/1/photo", {"photo": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
    first = one(env, "SELECT photo FROM projects")[0]; assert (env.core.PHOTOS / first).is_file()
    amy.post("/projects/1/photo", {"photo": (io.BytesIO(PNG), "b.png")}, content_type="multipart/form-data")
    second = one(env, "SELECT photo FROM projects")[0]
    assert second != first and not (env.core.PHOTOS / first).exists() and (env.core.PHOTOS / second).is_file()
    r = amy.post("/projects/1/photo", {"photo": (io.BytesIO(b"not a picture"), "c.png")}, content_type="multipart/form-data")
    assert one(env, "SELECT photo FROM projects")[0] == second and "couldn" in amy.html("/projects/1")
    amy.post("/projects/1/photo", {"remove": "1"})
    assert one(env, "SELECT photo FROM projects")[0] == "" and not (env.core.PHOTOS / second).exists()
    assert amy.get("/projects/1/photo").status_code == 404

def test_cover_picture_sharing_copying_and_deleting(env, amy):
    amy.post("/projects", {"name": "Hat"})
    amy.post("/projects/1/photo", {"photo": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
    bob = env.client().register("bob")
    assert bob.get("/projects/1/photo").status_code == 404                                       # not shared yet
    amy.post("/projects/1/share", {"username": "bob"})
    assert bob.get("/projects/1/photo").status_code == 200
    bob.post("/projects/1/photo", {"remove": "1"}); assert one(env, "SELECT photo FROM projects WHERE id=1")[0] != ""
    bob.post("/projects/1/copy")                                                                 # bob's own copy has its own file
    a, b = (one(env, "SELECT photo FROM projects WHERE id=?", i)[0] for i in (1, 2))
    assert a != b and (env.core.PHOTOS / b).is_file()
    amy.post("/projects/1/delete"); assert not (env.core.PHOTOS / a).exists() and (env.core.PHOTOS / b).is_file()

def test_copying_an_idea_keeps_it_an_idea(env, amy, web):
    amy.post("/import/link", {"url": web + "/pin/5/"}, content_type="multipart/form-data")
    amy.post("/projects/1/copy")
    assert one(env, "SELECT status, source FROM projects WHERE id=2") == ("idea", web + "/pin/5/")

def test_deleting_an_account_deletes_its_cover_pictures(env, amy):
    bob = env.client().register("bob"); bob.post("/projects", {"name": "Scarf"})
    bob.post("/projects/1/photo", {"photo": (io.BytesIO(PNG), "a.png")}, content_type="multipart/form-data")
    f = env.core.PHOTOS / one(env, "SELECT photo FROM projects")[0]; assert f.is_file()
    bob_id = one(env, "SELECT id FROM users WHERE username='bob'")[0]
    amy.post(f"/admin/users/{bob_id}/delete", {"confirm": "bob"})
    assert one(env, "SELECT COUNT(*) FROM users WHERE username='bob'")[0] == 0 and not f.exists()

# ---------- the API ----------
def test_api_imports_a_link(env, amy, web):
    _, post = api(env, make_token(amy, "write"))
    r = post("/api/v1/import/link", json={"url": web + "/short"})
    assert r.status_code == 201
    p = r.get_json()["project"]
    assert p["status"] == "idea" and p["name"] == "Rainbow ripple blanket" and p["source"] == web + "/pin/123/" and p["photo"].endswith("/projects/1/photo")
    r = post("/api/v1/import/link", json={"url": web + "/product", "to": "wishlist"})
    assert r.status_code == 201 and r.get_json()["wish"]["name"] == "Merino yarn, rose"
    r = post("/api/v1/import/link", json={"url": web + "/missing"})
    assert r.status_code == 400 and "404" in r.get_json()["error"]
    assert post("/api/v1/import/link", json={}).status_code == 400
    _, ro = api(env, make_token(amy, "read", "ro"))
    assert ro("/api/v1/import/link", json={"url": web + "/short"}).status_code == 403

def test_api_board(env, amy, web):
    get, post = api(env, make_token(amy, "write"))
    b = get("/api/v1/import/board?url=" + web + "/board.rss").get_json()
    assert b["title"] == "Blankets" and [p["link"] for p in b["pins"]] == [web + f"/pin/{i}/" for i in (1, 2, 3)] and not any(p["imported"] for p in b["pins"])
    r = post("/api/v1/import/board", json={"url": web + "/board.rss", "pins": [web + "/pin/2/"]})
    assert r.status_code == 201 and [p["name"] for p in r.get_json()["imported"]] == ["Idea 2"]
    r = post("/api/v1/import/board", json={"url": web + "/board.rss"})                          # no list: every new pin
    assert [p["name"] for p in r.get_json()["imported"]] == ["Idea 1", "Idea 3"] and r.get_json()["already_imported"] == 1
    r = post("/api/v1/import/board", json={"url": web + "/board.rss"})
    assert r.status_code == 200 and r.get_json()["imported"] == [] and r.get_json()["already_imported"] == 3
    assert post("/api/v1/import/board", json={"url": web + "/board.rss", "pins": "all"}).status_code == 400
    assert get("/api/v1/projects/1").get_json()["source"] == web + "/pin/2/"
    assert get("/api/v1/projects?status=idea").get_json()["projects"][0]["status"] == "idea"

# ---------- upgrades ----------
@pytest.mark.sqlite_only
def test_an_older_sqlite_database_gets_the_columns(make_env, tmp_path):
    env = make_env(); env.client().register("amy").post("/projects", {"name": "Hat"})
    d = sqlite3.connect(tmp_path / "craft.db")
    d.execute("ALTER TABLE projects DROP COLUMN photo"); d.execute("ALTER TABLE projects DROP COLUMN source"); d.execute("PRAGMA user_version=9"); d.commit(); d.close()
    make_env(); d = sqlite3.connect(tmp_path / "craft.db")
    assert d.execute("SELECT name, photo, source FROM projects").fetchall() == [("Hat", "", "")]
    assert d.execute("PRAGMA user_version").fetchone()[0] == 10

@pytest.mark.skipif(not TEST_DB, reason="needs a server database")
def test_a_server_database_without_the_columns_gets_them(make_env):
    env = make_env(); import dbx, migrations
    c = dbx.connect(dbx.CFG); cur = c.raw.cursor()
    cur.execute("ALTER TABLE projects DROP COLUMN photo"); cur.execute("ALTER TABLE projects DROP COLUMN source")
    cur.execute("UPDATE schema_version SET version=10"); c.commit(); c.close()
    c = dbx.connect(dbx.CFG); migrations.run_server(c); c.close()
    c = dbx.connect(dbx.CFG); assert c.execute("SELECT COUNT(*) FROM projects WHERE photo='' AND source=''").fetchone()[0] == 0; c.close()
