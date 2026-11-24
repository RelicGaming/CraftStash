#!/usr/bin/env python3
"""Craft Stash self-test.

Starts a throw-away copy of the app (own temporary database, real HTTP, same code and libraries as production),
signs up users and drives every feature through the web interface, then times the main pages.
Your real data is never touched.

    python selftest.py                       # headless, prints a table, writes report.html + report.json
    python selftest.py --only Sharing        # just the checks whose name contains "Sharing"
    python selftest.py --browser --headed    # also load every page in a real browser (needs: pip install playwright)
    In the app: Users > Self-test (admin only) shows the same run live.
"""
import argparse, hashlib, html, http.client, io, json, os, platform, re, shutil, socket, statistics, struct, subprocess
import sys, tempfile, time, traceback, uuid, zipfile, zlib
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlencode, urlsplit

APP_DIR = Path(__file__).resolve().parent
PW = "selftest-pass-1"
JSON_LINES = False

def emit(**ev):
    if JSON_LINES: print(json.dumps(ev), flush=True)

# ---------------------------------------------------------------- HTTP client with timing
class Resp:
    def __init__(s, status, headers, body, path, ttfb, total):
        s.status, s.headers, s.body, s.path, s.ttfb, s.total = status, headers, body, path, ttfb, total
        s.set_cookies = headers.get_all("Set-Cookie") or []
    @property
    def text(s): return s.body.decode("utf-8", "replace")
    @property
    def loc(s): return s.headers.get("Location", "")

def multipart(fields, files):
    b = uuid.uuid4().hex; out = []
    for k, v in fields.items(): out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fn, content, ctype) in files.items():
        out.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: {ctype}\r\n\r\n'.encode() + content + b"\r\n")
    out.append(f"--{b}--\r\n".encode()); return b"".join(out), f"multipart/form-data; boundary={b}"

class Client:
    """A tiny browser: keeps cookies, follows redirects, handles the CSRF token, and times every request."""
    def __init__(self, base, log):
        u = urlsplit(base); self.host, self.port, self.log = u.hostname, u.port, log
        self.cookies, self.token, self.name = {}, None, None

    def _once(self, method, path, body, headers):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=60)
        h = dict(headers)
        if self.cookies: h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        t0 = time.perf_counter(); conn.request(method, path, body=body, headers=h)
        r = conn.getresponse(); t1 = time.perf_counter(); data = r.read(); t2 = time.perf_counter(); conn.close()
        for raw in r.headers.get_all("Set-Cookie") or []:
            c = SimpleCookie(); c.load(raw)
            for k, m in c.items():
                if m.value: self.cookies[k] = m.value
                else: self.cookies.pop(k, None)
        resp = Resp(r.status, r.headers, data, path, (t1 - t0) * 1000, (t2 - t0) * 1000)
        m = re.search(r'name="_csrf" value="(\w+)"', resp.text[:60000]) if "text/html" in (r.headers.get("Content-Type") or "") else None
        if m: self.token = m.group(1)
        return resp

    def fetch_token(self): self._once("GET", "/login", None, {}); return self.token

    def request(self, method, path, data=None, files=None, follow=True, headers=None, retry=True):
        hdrs, body = dict(headers or {}), None
        if method == "POST":
            data = dict(data or {}); data.setdefault("_csrf", self.token or self.fetch_token())
            body, ct = multipart(data, files) if files else (urlencode(data).encode(), "application/x-www-form-urlencoded")
            hdrs["Content-Type"] = ct
        r = self._once(method, path, body, hdrs); total, hops = r.total, 0
        if method == "POST" and r.status == 400 and retry:
            data["_csrf"] = self.fetch_token(); body, ct = multipart(data, files) if files else (urlencode(data).encode(), "application/x-www-form-urlencoded")
            hdrs["Content-Type"] = ct; r = self._once(method, path, body, hdrs); total += r.total
        first = r
        while follow and r.status in (301, 302, 303, 307, 308) and hops < 6:
            u = urlsplit(r.loc); r = self._once("GET", u.path + ("?" + u.query if u.query else ""), None, {}); total += r.total; hops += 1
        r.total = total
        self.log.append({"method": method, "path": path, "status": r.status, "ttfb": round(r.ttfb, 1), "total": round(total, 1)})
        return r

    def get(self, path, **kw): return self.request("GET", path, **kw)
    def post(self, path, data=None, **kw): return self.request("POST", path, data, **kw)

def make_png(w=1200, h=800):
    raw = b"".join(b"\x00" + b"\xc8\x1e\x1e" * w for _ in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

# ---------------------------------------------------------------- test context and helpers
class T:
    def __init__(self, base):
        self.base, self.log, self.n, self.tag, self.admin = base, [], 0, uuid.uuid4().hex[:5], None
    def anon(self): return Client(self.base, self.log)
    def user(self, label="u"):
        self.n += 1; c = self.anon(); c.name = f"{label}{self.tag}{self.n}"
        c.post("/register", {"username": c.name, "password": PW})
        assert "Your collections" in c.get("/").text, f"could not sign up {c.name}"
        return c
    def login(self, name, pw=PW): c = self.anon(); return c, c.post("/login", {"username": name, "password": pw})

def new_collection(c, name):
    c.post("/collections", {"name": name}); page = c.get("/").text
    m = re.search(rf'href="/collections/(\d+)">(?:(?!</a>).)*?<h3>{re.escape(name)}</h3>', page, re.S); assert m, f"collection {name} not listed"
    return int(m[1])

def new_type(c, pid, name="Yarn", unit="skein", fields="Brand"):
    r = c.post(f"/collections/{pid}/types", {"name": name, "unit": unit, "fields": fields}, follow=False)
    m = re.search(r"/types/(\d+)/items/new", r.loc); assert m, "material type was not created"; return int(m[1])

def new_item(c, pid, tid, name, **f):
    c.post(f"/types/{tid}/items/new", {"name": name, "qty": "1", **f})
    m = re.search(rf'href="/items/(\d+)/edit">{re.escape(name)}</a>', c.get(f"/collections/{pid}").text); assert m, f"item {name} not listed"; return int(m[1])

def new_project(c, name="Hat"):
    r = c.post("/projects", {"name": name}, follow=False); m = re.search(r"/projects/(\d+)", r.loc); assert m, "project was not created"; return int(m[1])

def setup_stash(c, tag="", qty="5", price="10", **extra):
    """A collection with a yarn type and one item. Returns (collection id, type id, item id)."""
    pid = new_collection(c, f"Stash{tag}{uuid.uuid4().hex[:4]}"); tid = new_type(c, pid)
    return pid, tid, new_item(c, pid, tid, "Merino", qty=qty, price=price, **extra)

def page(c, pid): return c.get(f"/collections/{pid}").text

CHECKS = []
def check(category, name):
    def deco(fn): CHECKS.append((category, name, fn)); return fn
    return deco

# ---------------------------------------------------------------- the checks
@check("Delivery", "Health check, stylesheet and 404 page")
def _(t):
    c = t.anon(); assert c.get("/healthz").text == "ok", "health endpoint is not ok"
    css = c.get("/static/app.css"); assert css.status == 200 and "--accent" in css.text, "stylesheet missing"
    assert t.user().get("/no-such-page").status == 404, "unknown pages should be 404"

@check("Security", "Login required, protective headers, CSRF enforced")
def _(t):
    a = t.anon(); r = a.get("/", follow=False); assert r.status == 302 and "/login" in r.loc, "home did not redirect to login"
    h = a.get("/login").headers
    assert "frame-ancestors 'none'" in (h.get("Content-Security-Policy") or ""), "no Content-Security-Policy"
    assert h.get("X-Frame-Options") == "DENY" and h.get("X-Content-Type-Options") == "nosniff", "missing framing/sniffing headers"
    assert a.post("/login", {"username": "x", "password": "y", "_csrf": "forged"}, retry=False).status == 400, "a forged CSRF token was accepted"

@check("Security", "Strangers cannot see someone else's data")
def _(t):
    a, s = t.user(), t.user(); pid, tid, iid = setup_stash(a); prj = new_project(a)
    for url in (f"/collections/{pid}", f"/items/{iid}/edit", f"/projects/{prj}", f"/collections/{pid}/activity", f"/collections/{pid}/export.csv", f"/types/{tid}/fields"):
        assert s.get(url).status == 404, f"{url} was visible to a stranger"

@check("Accounts", "First account is admin, others are not")
def _(t):
    assert t.admin.get("/admin").status == 200, "admin cannot open the Users page"
    u = t.user(); assert u.get("/admin").status == 403 and u.post("/admin/backup").status == 403, "a normal user reached admin pages"

@check("Accounts", "Sign up, log out, log in, wrong password")
def _(t):
    u = t.user(); u.post("/logout"); assert u.get("/", follow=False).status == 302, "logout did not end the session"
    c, r = t.login(u.name); assert "Your collections" in r.text, "login failed"
    assert "Wrong username or password" in t.login(u.name, "not-the-password")[1].text, "wrong password was not rejected"

@check("Accounts", "Login throttling after repeated failures")
def _(t):
    u = t.user(); c = t.anon()
    for _ in range(8): c.post("/login", {"username": u.name, "password": "nope"})
    assert "Too many failed" in c.post("/login", {"username": u.name, "password": PW}).text, "no throttling after 8 failures"

@check("Accounts", "Changing a password signs out other devices")
def _(t):
    u = t.user(); phone, _ = t.login(u.name); assert phone.get("/", follow=False).status == 200
    assert "current password is wrong" in u.post("/account", {"current": "bad", "new": "x" * 9, "confirm": "x" * 9}).text, "wrong current password accepted"
    u.post("/account", {"current": PW, "new": "another-pass-9", "confirm": "another-pass-9"})
    assert phone.get("/", follow=False).status == 302, "the old session survived a password change"
    assert u.get("/", follow=False).status == 200, "the device that changed the password was signed out"

@check("Accounts", "Admin: add, reset password, disable, promote, delete users")
def _(t):
    a = t.admin; name = f"adm{t.tag}x"; a.post("/admin/users", {"username": name, "password": PW})
    m = re.search(rf"<strong>{name}</strong>.*?/admin/users/(\d+)/password", a.get("/admin").text, re.S); assert m, "new user not listed"; uid = m[1]
    c, r = t.login(name); assert "Your collections" in r.text
    a.post(f"/admin/users/{uid}/password", {"password": "reset-pass-77"}); assert c.get("/", follow=False).status == 302, "reset did not sign the user out"
    c, r = t.login(name, "reset-pass-77"); assert "Your collections" in r.text, "reset password does not work"
    a.post(f"/admin/users/{uid}/active"); assert "disabled" in t.login(name, "reset-pass-77")[1].text and c.get("/", follow=False).status == 302, "disable did not lock the user out"
    a.post(f"/admin/users/{uid}/active"); c, r = t.login(name, "reset-pass-77"); assert "Your collections" in r.text, "enable did not restore access"
    a.post(f"/admin/users/{uid}/admin"); assert c.get("/admin").status == 200, "promote to admin failed"
    a.post(f"/admin/users/{uid}/delete"); assert name not in a.get("/admin").text, "user not deleted"

@check("Accounts", "Sign-up can be closed and reopened")
def _(t):
    a, anon = t.admin, t.anon()
    try:
        a.post("/admin/signups"); assert anon.get("/register", follow=False).status == 403, "sign-up still open after closing"
    finally:
        if anon.get("/register", follow=False).status == 403: a.post("/admin/signups")
    assert anon.get("/register").status == 200, "sign-up did not reopen"

@check("Appearance", "Theme presets, custom colours, corners, reset, per-user")
def _(t):
    u, other = t.user(), t.user(); u.post("/appearance", {"theme": "forest", "radius": "round"}); h = u.get("/").text
    assert "<style>:root{--bg:" in h and "--r-lg:22px" in h, "preset or corner style not applied"
    assert "<style>:root" not in other.get("/").text, "one user's theme leaked to another"
    u.post("/appearance", {"theme": "custom", "mode": "dark", "bg": "#101820", "surface": "#182430", "ink": "#e8f0f8", "accent": "#ffb703", "radius": "square"})
    h = u.get("/").text; assert "--accent:#ffb703" in h and "color-scheme:dark" in h, "custom colours not applied"
    u.post("/appearance", {"theme": "custom", "bg": "#fff;}</style><script>x", "surface": "red", "ink": "#12", "accent": "zzz"})
    assert "<script>x" not in u.get("/").text, "hostile colour value reached the page"
    for key in ("light", "dark", "blush", "ocean", "ember", "system"): u.post("/appearance", {"theme": key}); assert u.get("/appearance").status == 200
    u.post("/appearance", {"reset": "1"}); assert "<style>:root" not in u.get("/").text, "reset did not clear the theme"

@check("Collections", "Create, rename and delete a collection")
def _(t):
    u = t.user(); name = "Knit" + t.tag; pid = new_collection(u, name)
    u.post(f"/collections/{pid}/update", {"name": name + " room", "craft": "Knitting"}); p = page(u, pid)
    assert name + " room" in p and "Knitting" in p, "rename failed"
    u.post(f"/collections/{pid}/delete"); assert u.get(f"/collections/{pid}", follow=False).status == 404, "collection not deleted"

@check("Collections", "Material types and custom fields (text, number, dropdown)")
def _(t):
    u = t.user(); pid = new_collection(u, "Sew" + t.tag); tid = new_type(u, pid, "Fabric", "m", "Width, Fibre"); url = f"/types/{tid}/fields"
    assert "Width" in u.get(url).text and "Fibre" in u.get(url).text, "starter fields missing"
    u.post(url, {"label": "Weight", "type": "number"}); u.post(url, {"label": "Dye", "type": "choice", "options": "Solid, Print"})
    pg = u.get(url).text; assert "Weight" in pg and "Dye" in pg and "Solid, Print" in pg, "added fields missing"
    assert "already exists" in u.post(url, {"label": "width", "type": "text"}).text, "duplicate label accepted"
    fid = re.search(rf'action="/types/{tid}/fields/(\w+)/update">(?:(?!</form>).)*?value="Weight"', pg, re.S)[1]
    u.post(f"{url}/{fid}/update", {"label": "GSM", "type": "number", "options": ""}); assert "GSM" in u.get(url).text, "rename failed"
    u.post(f"{url}/{fid}/delete"); assert "GSM" not in u.get(url).text, "field not deleted"
    u.post(f"/types/{tid}/update", {"name": "Fabrics", "unit": "yd"}); assert "Fabrics" in u.get(url).text, "type rename failed"

@check("API", "Tokens: read-only and write access, shopping list, restock, use stock")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, qty="0")
    tok = lambda access: re.search(r'value="(cs_[\w-]+)"', u.post("/account/tokens", {"name": "Selftest " + access, "access": access}).text)[1]
    ro, rw = tok("read"), tok("write")
    h = lambda k: {"Authorization": "Bearer " + k}
    assert u.get("/api/v1/summary", headers={"Authorization": "Bearer cs_nope"}).status == 401, "bad token accepted"
    shop = json.loads(u.get("/api/v1/shopping", headers=h(ro)).text)
    assert any(i["id"] == iid and i["status"] == "out" for i in shop["items"]), "out-of-stock item missing from API shopping list"
    assert u.post("/api/v1/shopping/restock", {"id": iid, "amount": "3"}, headers=h(ro), retry=False).status == 403, "read-only token could write"
    r = json.loads(u.post("/api/v1/shopping/restock", {"id": iid, "amount": "3"}, headers=h(rw), retry=False).text)
    assert r["restocked"] and r["restocked"][0]["qty"] == 3, "API restock failed"
    r = json.loads(u.post(f"/api/v1/items/{iid}/adjust", {"action": "use", "amount": "1"}, headers=h(rw), retry=False).text)
    assert r["qty"] == 2, "API adjust failed"

@check("Collections", "Quick setup: new collection, preset type, typed starter fields, add another")
def _(t):
    u = t.user(); r = u.post("/collections", {"name": "Knit" + t.tag}, follow=False)
    pid = int(re.search(r"/collections/(\d+)$", r.loc)[1])
    assert "Add your first material type" in u.get(f"/collections/{pid}").text, "new collection does not open on setup"
    r = u.post(f"/collections/{pid}/types", {"preset": "yarn"}, follow=False)
    tid = int(re.search(r"/types/(\d+)/items/new", r.loc)[1]); pg = u.get(f"/types/{tid}/items/new").text
    assert ">Worsted</option>" in pg and "Save and add another" in pg, "preset fields missing from the item form"
    tid2 = new_type(u, pid, "Fabric", "m", "Width (cm)\nFibre: Cotton, Linen"); pg = u.get(f"/types/{tid2}/items/new").text
    assert 'type=number' in pg and ">Linen</option>" in pg, "typed starter fields not created"
    r = u.post(f"/types/{tid}/items/new", {"name": "First", "qty": "1", "then": "another"}, follow=False)
    assert r.loc.endswith(f"/types/{tid}/items/new"), "save and add another did not return to the form"

@check("Items", "Create, edit and delete an item with swatch, price and location")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, colour="Rose", has_hex="1", hex="#d98ca3", price="9.5", location="Drawer 3", subtype="4 ply")
    p = page(u, pid); assert all(x in p for x in ("Rose", "d98ca3", "Drawer 3", "$9.50", "4 ply")), "item details missing from the list"
    u.post(f"/items/{iid}/edit", {"name": "Merino DK", "qty": "7", "colour": "Rose"}); p = page(u, pid)
    assert "Merino DK" in p and "<strong>7</strong>" in p, "edit not saved"
    u.post(f"/items/{iid}/delete"); assert "Merino DK" not in page(u, pid), "item not deleted"

@check("Items", "Photo upload is re-encoded, shown, rejected if fake, removable")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u)
    u.post(f"/items/{iid}/edit", {"name": "Merino", "qty": "5"}, files={"photo": ("yarn.png", make_png(), "image/png")})
    p = u.get(f"/photos/{iid}"); assert p.status == 200 and p.body[:2] == b"\xff\xd8", "photo was not stored as a JPEG"
    assert f"/photos/{iid}" in page(u, pid), "photo not shown on the collection page"
    assert "be read" in u.post(f"/items/{iid}/edit", {"name": "Merino", "qty": "5"}, files={"photo": ("x.jpg", b"<script>alert(1)</script>", "image/jpeg")}).text, "fake image accepted"
    u.post(f"/items/{iid}/edit", {"name": "Merino", "qty": "5", "remove_photo": "1"}); assert u.get(f"/photos/{iid}").status == 404, "photo not removed"

@check("Items", "Duplicate an item")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, colour="Rose"); h = u.get(f"/types/{tid}/items/new?copy={iid}").text
    assert 'value="Merino copy"' in h and 'value="Rose"' in h and "Copied from Merino" in h, "duplicate form not prefilled"

@check("Search", "Text search, Field:value search and sorting")
def _(t):
    u = t.user(); pid = new_collection(u, "Find" + t.tag); tid = new_type(u, pid, "Fabric", "m", "Width")
    fid = re.search(r'name="x_(\w+)"', u.get(f"/types/{tid}/items/new").text)[1]
    new_item(u, pid, tid, "Poplin", **{"x_" + fid: "150"}, colour="White"); new_item(u, pid, tid, "Denim", **{"x_" + fid: "90"}, colour="Blue")
    q = lambda s: u.get(f"/collections/{pid}?q={s}").text
    assert "Poplin" in q("poplin") and "Denim" not in q("poplin"), "text search wrong"
    assert "Poplin" in q("Width:150") and "Denim" not in q("Width:150"), "field search wrong"
    assert "Poplin" not in q("Width:999") and "Poplin" not in q("zzzz"), "search returned non-matches"
    for s in ("name", "colour", "low", "new", "bogus"): assert u.get(f"/collections/{pid}?sort={s}").status == 200, f"sort={s} failed"

@check("Stock", "Use / add stock, never below zero, undo, activity log")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u); adj = lambda n, a: u.post(f"/items/{iid}/adjust", {"amount": str(n), "action": a})
    adj(2, "use"); assert "<strong>3</strong>" in page(u, pid), "use 2 failed"
    adj(4, "add"); assert "<strong>7</strong>" in page(u, pid), "add 4 failed"
    adj(99, "use"); assert "<strong>0</strong>" in page(u, pid), "stock went below zero or did not clamp"
    act = u.get(f"/collections/{pid}/activity").text; assert "Used 2" in act and "Added 4" in act, "activity log incomplete"
    u.post("/activity/%s/undo" % re.findall(r"/activity/(\d+)/undo", act)[0]); assert "<strong>7</strong>" in page(u, pid), "undo did not restore stock"
    assert "Undone" in u.get(f"/collections/{pid}/activity").text, "undone entry not marked"

@check("Stock", "Low-stock level feeds the shopping list; restocking clears it")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, low="2"); u.post(f"/types/{tid}/items/new", {"name": "Cotton", "qty": "10"})
    assert "Merino" not in u.get("/shopping").text, "item listed while above its low level"
    u.post(f"/items/{iid}/adjust", {"amount": "3", "action": "use"}); s = u.get("/shopping").text
    assert "Merino" in s and "Cotton" not in s and "Low stock" in page(u, pid), "low item not on the list"
    u.post(f"/items/{iid}/adjust", {"amount": "99", "action": "use"}); assert "Out of stock" in page(u, pid), "no out-of-stock label"
    u.post(f"/items/{iid}/adjust", {"amount": "10", "action": "add"}); assert "Merino" not in u.get("/shopping").text, "restock did not clear the list"

@check("CSV", "Export format, swatch column and formula safety")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, has_hex="1", hex="#112233"); u.post(f"/types/{tid}/items/new", {"name": "=SUM(1)", "qty": "1"})
    r = u.get(f"/collections/{pid}/export.csv"); txt = r.text
    assert "csv" in r.headers.get("Content-Type", "") and "Swatch (hex)" in txt and "#112233" in txt, "export columns missing"
    assert "'=SUM(1)" in txt, "spreadsheet formula was not neutralised"

@check("CSV", "Import: check-only, add, update, bad rows, semicolons, template")
def _(t):
    u = t.user(); pid = new_collection(u, "Imp" + t.tag)
    body = "Material type,Name,Colour,In stock,Unit,Price per unit,Swatch (hex),Width\nFabric,Poplin,White,3,m,\u00a312.50,#ffffff,150\nFabric,Denim,Blue,lots,m,1,,\n,,,,,,,\n"
    f = lambda s: {"file": ("s.csv", s.encode(), "text/csv")}; imp = lambda s, **kw: u.post(f"/collections/{pid}/import", {"mode": "update", **kw}, files=f(s)).text
    r = imp(body, dry="on"); assert "nothing was imported" in r and "1 added" in r and "1 skipped" in r and "Poplin" not in page(u, pid), "check-only mode misbehaved"
    imp(body); p = page(u, pid); assert "Poplin" in p and "150" in p and "Fabric" in p and "Denim" not in p, "import result wrong"
    assert "1 updated" in imp("Material type,Name,Colour,In stock\nFabric,Poplin,White,9\n") and "<strong>9</strong>" in page(u, pid), "update of an existing item failed"
    imp("Material type;Name;In stock\nThread;Silk;3,5\n"); assert "<strong>3.5</strong>" in page(u, pid), "semicolon CSV not understood"
    assert "Name column" in imp("a,b\n1,2\n"), "missing Name column not reported"
    assert "Material type,Name" in u.get("/collections/import-template.csv").text, "template download failed"

@check("Projects", "Create a project and edit its details")
def _(t):
    u = t.user(); prj = new_project(u, "Hat" + t.tag); u.post(f"/projects/{prj}/update", {"name": "Winter hat", "status": "active", "notes": "Use the blue yarn"})
    p = u.get(f"/projects/{prj}").text; assert "Winter hat" in p and "Active" in p and "Use the blue yarn" in p, "details not saved"
    assert "Winter hat" in u.get("/projects").text, "project missing from the list"

@check("Projects", "Assign and remove materials, cost estimate")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, price="10"); prj = new_project(u); u.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "2"})
    p = u.get(f"/projects/{prj}").text; assert "Merino" in p and "2 skein" in p and "$20.00" in p, "assignment or cost missing"
    assert "3 free" in page(u, pid), "reservation did not reduce free stock"
    u.post("/assignments/%s/delete" % re.search(r"/assignments/(\d+)/delete", p)[1]); assert "Nothing assigned yet" in u.get(f"/projects/{prj}").text, "remove failed"

@check("Projects", "Shortfall: over-assigning lands on the shopping list")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u); prj = new_project(u)
    assert "3 skein more than you have free" in u.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "8"}).text, "no shortfall message"
    assert "Short by 3" in page(u, pid) and "Short by 3 skein" in u.get("/shopping").text, "shortfall not shown"

@check("Projects", "Steps: add, edit, reorder, delete")
def _(t):
    u = t.user(); prj = new_project(u); u.post(f"/projects/{prj}/steps", {"title": "Cast on", "body": "40 stitches"}); u.post(f"/projects/{prj}/steps", {"title": "Knit", "body": "Rib"})
    p = u.get(f"/projects/{prj}").text; ids = re.findall(r"/steps/(\d+)/update", p); assert len(ids) == 2 and p.index("Cast on") < p.index("Knit"), "steps missing or out of order"
    u.post(f"/steps/{ids[1]}/move", {"dir": "up"}); p = u.get(f"/projects/{prj}").text; assert p.index("Knit") < p.index("Cast on"), "move up failed"
    u.post(f"/steps/{ids[0]}/update", {"title": "Cast on 40", "body": "x"}); assert "Cast on 40" in u.get(f"/projects/{prj}").text, "edit failed"
    u.post(f"/steps/{ids[0]}/delete"); assert "Cast on 40" not in u.get(f"/projects/{prj}").text, "delete failed"

@check("Projects", "Guides and patterns: upload, view, download, reject, delete")
def _(t):
    u = t.user(); prj = new_project(u); up = lambda n, b: u.post(f"/projects/{prj}/upload", {}, files={"file": (n, b, "application/octet-stream")}).text
    p = up("pattern.pdf", b"%PDF-1.4 test"); fid = re.search(r'href="/files/(\d+)"', p)[1]; r = u.get(f"/files/{fid}")
    assert r.headers.get("Content-Type") == "application/pdf" and "attachment" not in (r.headers.get("Content-Disposition") or ""), "PDF should open inline"
    assert not r.headers.get("Content-Security-Policy"), "CSP on a PDF breaks the browser viewer"
    up("notes.docx", b"PK\x03\x04"); fid2 = re.search(r'href="/files/(\d+)"[^>]*>notes\.docx', u.get(f"/projects/{prj}").text)[1]
    assert "attachment" in (u.get(f"/files/{fid2}").headers.get("Content-Disposition") or ""), "documents should download"
    assert "not allowed" in up("evil.exe", b"MZ"), "an .exe upload was accepted"
    u.post(f"/files/{fid}/delete"); assert u.get(f"/files/{fid}").status == 404, "file not deleted"

@check("Projects", "Complete deducts stock; reopen puts it back")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u); prj = new_project(u); u.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "3"})
    u.post(f"/projects/{prj}/complete"); assert "Done" in u.get(f"/projects/{prj}").text and "<strong>2</strong>" in page(u, pid), "complete did not deduct 3"
    u.post(f"/projects/{prj}/reopen"); assert "<strong>5</strong>" in page(u, pid), "reopen did not restore stock"

@check("Projects", "Duplicate a project (steps and reservations)")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u); prj = new_project(u, "Original" + t.tag); u.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "2"}); u.post(f"/projects/{prj}/steps", {"title": "Step one", "body": "x"})
    r = u.post(f"/projects/{prj}/copy", follow=False); new = re.search(r"/projects/(\d+)", r.loc)[1]; p = u.get(f"/projects/{new}").text
    assert "(copy)" in p and "Step one" in p and "2 skein" in p, "copy is missing steps or materials"

@check("Sharing", "Collection: view-only vs can-edit")
def _(t):
    a, b = t.user(), t.user(); pid, tid, iid = setup_stash(a); a.post(f"/collections/{pid}/share", {"username": b.name, "role": "view"})
    assert b.get(f"/collections/{pid}").status == 200 and "Shared with you" in b.get("/").text, "view-only share not visible"
    assert b.post(f"/types/{tid}/items/new", {"name": "x", "qty": "1"}, follow=False).status == 404, "view-only user could add items"
    assert b.post(f"/items/{iid}/adjust", {"amount": "1", "action": "use"}, follow=False).status == 404, "view-only user could change stock"
    a.post(f"/collections/{pid}/share", {"username": b.name, "role": "edit"})
    assert b.post(f"/items/{iid}/adjust", {"amount": "1", "action": "use"}, follow=False).status == 302, "editor could not change stock"

@check("Sharing", "Leaving a collection drops your reservations")
def _(t):
    a, b = t.user(), t.user(); pid, tid, iid = setup_stash(a); a.post(f"/collections/{pid}/share", {"username": b.name, "role": "edit"})
    prj = new_project(b); b.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "2"}); assert "3 free" in page(a, pid), "reservation not counted"
    b.post(f"/collections/{pid}/leave"); assert b.get(f"/collections/{pid}").status == 404, "still has access after leaving"
    assert "3 free" not in page(a, pid), "reservation survived leaving"

@check("Sharing", "Project: read-only for viewers, save a copy, stop following")
def _(t):
    a, b = t.user(), t.user(); pid, tid, iid = setup_stash(a); prj = new_project(a, "Shared" + t.tag)
    a.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "2"}); a.post(f"/projects/{prj}/steps", {"title": "Cast on", "body": "40"})
    a.post(f"/projects/{prj}/upload", {}, files={"file": ("pattern.pdf", b"%PDF-1.4", "application/pdf")}); a.post(f"/projects/{prj}/share", {"username": b.name})
    v = b.get(f"/projects/{prj}").text; assert "Cast on" in v and "Merino" in v and "Add step" not in v, "viewer page wrong"
    assert b.post(f"/projects/{prj}/update", {"name": "hacked"}, follow=False).status == 404, "viewer could edit"
    r = b.post(f"/projects/{prj}/copy", follow=False); copy_id = re.search(r"/projects/(\d+)", r.loc)[1]; mine = b.get(f"/projects/{copy_id}").text
    assert "Materials from the original" in mine and "Cast on" in mine and "pattern.pdf" in mine, "copy is incomplete"
    b.post(f"/projects/{prj}/leave"); assert b.get(f"/projects/{prj}").status == 404, "still following after leaving"

@check("Safety", "Delete warnings say what will be lost")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u); prj = new_project(u); u.post(f"/projects/{prj}/assign", {"item_id": iid, "qty": "1"})
    assert "takes them out of 1 project" in page(u, pid) and "used in 1 project" in u.get(f"/items/{iid}/edit").text, "impact text missing"

@check("Admin", "Currency symbol")
def _(t):
    u = t.user(); pid, tid, iid = setup_stash(u, price="9.5")
    try: t.admin.post("/admin/currency", {"currency": "\u00a3"}); assert "\u00a39.50" in page(u, pid), "currency symbol not applied"
    finally: t.admin.post("/admin/currency", {"currency": "$"})

@check("Admin", "Full backup download")
def _(t):
    r = t.admin.post("/admin/backup"); assert r.headers.get("Content-Type") == "application/zip", "not a zip"
    names = zipfile.ZipFile(io.BytesIO(r.body)).namelist(); assert "craft.db" in names, "database missing from the backup"

@check("Limits", "Per-user storage quota is enforced")
def _(t):
    u = t.user(); prj = new_project(u); up = lambda n: u.post(f"/projects/{prj}/upload", {}, files={"file": (n, b"x" * 600_000, "application/pdf")}).text
    up("a.pdf"); assert "storage limit" in up("b.pdf"), "second 600 KB upload should exceed the 1 MB test quota"

@check("Proxy", "Secure cookies only when the proxy says the request was HTTPS")
def _(t):
    secure = t.anon().get("/login", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "stash.example.com"}).set_cookies
    plain = t.anon().get("/login").set_cookies
    assert any("Secure" in c for c in secure), "no Secure cookie behind HTTPS proxy"; assert not any("Secure" in c for c in plain), "Secure cookie on plain HTTP"

# ---------------------------------------------------------------- performance pass
def build_csv(n):
    kinds = [("Fabric", "m"), ("Thread", "spool"), ("Yarn", "skein")]
    rows = ["Material type,Name,Colour,Variant,In stock,Unit,Price per unit,Low stock at,Location,Notes,Swatch (hex),Width"]
    for i in range(n):
        k, unit = kinds[i % 3]; h = hashlib.md5(str(i).encode()).hexdigest()[:6]
        rows.append(f"{k},{k} {i:04d},Colour {i % 12},Variant {i % 5},{(i * 7) % 25},{unit},{i % 40 + 1}.50,{8 if i % 10 == 0 else ''},Drawer {i % 9},Note {i},#{h},{100 + i % 60}")
    return "\n".join(rows) + "\n"

def pct(vals, p): s = sorted(vals); return s[min(len(s) - 1, int(p / 100 * len(s)))]

def summarize(page, path, totals, ttfbs, size, budget, ok=True):
    med = statistics.median(totals)
    return {"page": page, "path": path, "runs": len(totals), "cold": round(totals[0], 1), "min": round(min(totals), 1), "median": round(med, 1),
            "p95": round(pct(totals, 95), 1), "max": round(max(totals), 1), "ttfb": round(statistics.median(ttfbs), 1), "kb": round(size / 1024, 1),
            "slow": med > budget, "ok": ok}

def perf_pass(t, runs, items, budget):
    """Seeds a realistic stash through the CSV importer, then times the main pages. Returns (rows, user, pool id)."""
    u = t.user("perf"); pid = new_collection(u, "Perf stash"); rows = []
    t0 = time.perf_counter(); r = u.post(f"/collections/{pid}/import", {"mode": "update"}, files={"file": ("perf.csv", build_csv(items).encode(), "text/csv")}); ms = (time.perf_counter() - t0) * 1000
    assert f"{items} added" in r.text, "performance data did not import"
    rows.append(summarize(f"Import {items} CSV rows (POST)", f"/collections/{pid}/import", [ms], [ms], len(r.body), budget * 4))
    pool = u.get(f"/collections/{pid}").text; tid = int(re.search(r"/types/(\d+)/fields", pool)[1]); iid = int(re.search(r"/items/(\d+)/edit", pool)[1])
    prj = new_project(u, "Perf project")
    for i in re.findall(r"/items/(\d+)/edit", pool)[:10]: u.post(f"/projects/{prj}/assign", {"item_id": i, "qty": "1"})
    for n in range(5): u.post(f"/projects/{prj}/steps", {"title": f"Step {n + 1}", "body": "Do the thing " * 8})
    anon = t.anon()
    pages = [("Login page (not signed in)", "/login", anon), ("Static stylesheet", "/static/app.css", u), ("Home - collections", "/", u),
             (f"Collection with {items} items", f"/collections/{pid}", u), ("Collection, text search", f"/collections/{pid}?q=Colour+3", u),
             ("Collection, field search", f"/collections/{pid}?q=Width:120", u), ("Collection, sorted by stock", f"/collections/{pid}?sort=low", u),
             ("Activity history", f"/collections/{pid}/activity", u), ("CSV export", f"/collections/{pid}/export.csv", u), ("Shopping list", "/shopping", u),
             ("Projects list", "/projects", u), ("Project with materials and steps", f"/projects/{prj}", u), ("Add item form", f"/types/{tid}/items/new", u),
             ("Edit item form", f"/items/{iid}/edit", u), ("Material type fields", f"/types/{tid}/fields", u), ("Appearance", "/appearance", u),
             ("Account", "/account", u), ("Users (admin)", "/admin", t.admin)]
    for label, path, c in pages:
        totals, ttfbs, size, ok = [], [], 0, True
        for _ in range(runs):
            r = c._once("GET", path, None, {}); totals.append(r.total); ttfbs.append(r.ttfb); size = len(r.body); ok &= r.status == 200
        rows.append(summarize(label, path, totals, ttfbs, size, budget, ok))
    return rows, u, pid, prj

def browser_pass(base, user, pw, pages, out, headed):
    """Real-browser timings (needs Playwright). Not part of the default run."""
    try: from playwright.sync_api import sync_playwright
    except ImportError: return {"error": "Playwright is not installed. Run: pip install playwright && playwright install chromium"}
    shots = Path(out) / "screens"; shots.mkdir(parents=True, exist_ok=True); rows = []
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=not headed, slow_mo=200 if headed else 0)
            for label, size in (("desktop", {"width": 1200, "height": 900}), ("phone", {"width": 390, "height": 800})):
                pg = b.new_page(viewport=size); pg.goto(base + "/login"); pg.fill("[name=username]", user); pg.fill("[name=password]", pw); pg.click("button.btn")
                pg.wait_for_load_state("load")
                for name, path in pages:
                    pg.goto(base + path, wait_until="load")
                    m = pg.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0]; const f = performance.getEntriesByType('paint').find(e => e.name === 'first-contentful-paint');
                        return {ttfb: n.responseStart, dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd, fcp: f ? f.startTime : null, overflow: document.documentElement.scrollWidth > window.innerWidth + 1}; }""")
                    fn = f"{label}-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.png"; pg.screenshot(path=str(shots / fn), full_page=True)
                    rows.append({"view": label, "page": name, **{k: (round(v, 1) if isinstance(v, float) else v) for k, v in m.items()}, "shot": f"screens/{fn}"})
                pg.close()
            b.close()
    except Exception as e: return {"error": f"{type(e).__name__}: {e}"}
    return {"rows": rows}

# ---------------------------------------------------------------- reports
def ms(v): return f"{v:,.0f} ms" if v >= 10 else f"{v:.1f} ms"

def html_report(d):
    e, S = html.escape, d["summary"]
    css = """body{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f4f5f7;color:#1d2030}main{max-width:1100px;margin:0 auto;padding:24px 20px 60px}
    @media(prefers-color-scheme:dark){body{background:#13141b;color:#eceef6}.card,table{background:#1b1d27!important}th,td{border-color:#2c3041!important}}
    h1{margin:0 0 4px}.muted{color:#7a7f95}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:18px 0}
    .card{background:#fff;border-radius:12px;padding:14px 16px;border:1px solid #e1e4ec}.card b{display:block;font-size:1.6rem}table{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;margin-bottom:22px}
    th,td{text-align:left;padding:8px 12px;border-bottom:1px solid #e1e4ec;vertical-align:top}th{font-size:.82rem;color:#7a7f95}.pass{color:#1e7a4f}.fail,.error{color:#b3261e}.slow{color:#a15c00}
    .badge{font-weight:700;font-size:.8rem}.bar{height:8px;border-radius:4px;background:#3f46b8;min-width:2px}details summary{cursor:pointer;color:#3f46b8}.num{text-align:right;font-variant-numeric:tabular-nums}
    code{background:#8884;padding:1px 5px;border-radius:4px}"""
    cards = [("Checks passed", f"{S['passed']} / {S['total']}"), ("Failed", str(S["failed"])), ("Run time", f"{S['duration_s']:.1f} s"),
             ("Median page load", ms(S["median_load"])), ("95th percentile", ms(S["p95_load"])), ("Slowest page", ms(S["slowest_load"]))]
    out = [f"<!doctype html><html lang=en><meta charset=utf-8><title>Craft Stash self-test</title><style>{css}</style><main>",
           f"<h1>Craft Stash self-test</h1><div class=muted>{e(d['started'])} on {e(d['env']['platform'])}, Python {e(d['env']['python'])}, served by {e(d['env']['server'])}. Fresh temporary database, real HTTP, nothing of yours touched.</div>",
           "<div class=cards>" + "".join(f"<div class=card><span class=muted>{a}</span><b class={'fail' if a == 'Failed' and S['failed'] else ''}>{e(v)}</b></div>" for a, v in cards) + "</div>"]
    fails = [c for c in d["checks"] if c["status"] != "pass"]
    if fails: out.append("<h2 class=fail>Failures</h2><table><tr><th>Check<th>What went wrong</tr>" + "".join(f"<tr><td>{e(c['category'])}: {e(c['name'])}<td class=fail>{e(c['error'])}</tr>" for c in fails) + "</table>")
    out.append("<h2>Features</h2><table><tr><th>Area<th>Check<th>Result<th class=num>Time<th class=num>Requests<th>Slowest request</tr>")
    for c in d["checks"]:
        sl = c["slowest"]; out.append(f"<tr><td>{e(c['category'])}<td><details><summary>{e(c['name'])}</summary><table><tr><th>Request<th>Status<th class=num>First byte<th class=num>Total</tr>" +
            "".join(f"<tr><td><code>{e(r['method'])} {e(r['path'])}</code><td>{r['status']}<td class=num>{ms(r['ttfb'])}<td class=num>{ms(r['total'])}</tr>" for r in c["requests"]) + "</table></details>" +
            f"<td class={c['status']}><span class=badge>{c['status'].upper()}</span><td class=num>{ms(c['ms'])}<td class=num>{len(c['requests'])}<td>{'<code>' + e(sl['method'] + ' ' + sl['path']) + '</code> ' + ms(sl['total']) if sl else ''}</tr>")
    out.append("</table>")
    if d["perf"]:
        top = max(r["median"] for r in d["perf"]) or 1
        out.append(f"<h2>Page load times</h2><p class=muted>Server response over HTTP on this machine, {d['perf'][-1]['runs']} runs per page. The first run is the cold load. Pages slower than {d['budget_ms']} ms (median) are flagged.</p>"
                   "<table><tr><th>Page<th class=num>Cold<th class=num>Median<th class=num>95th<th class=num>Max<th class=num>First byte<th class=num>Size<th style=width:18%>&nbsp;</tr>")
        for r in d["perf"]:
            out.append(f"<tr><td>{e(r['page'])}<br><code>{e(r['path'])}</code><td class=num>{ms(r['cold'])}<td class='num {'slow' if r['slow'] else ''}'>{ms(r['median'])}{' (slow)' if r['slow'] else ''}{'' if r['ok'] else ' <span class=fail>(error status)</span>'}"
                       f"<td class=num>{ms(r['p95'])}<td class=num>{ms(r['max'])}<td class=num>{ms(r['ttfb'])}<td class=num>{r['kb']:,.1f} KB<td><div class=bar style='width:{max(2, r['median'] / top * 100):.0f}%'></div></tr>")
        out.append("</table>")
    b = d.get("browser")
    if b:
        out.append("<h2>Real browser timings</h2>")
        if b.get("error"): out.append(f"<p class=fail>{e(b['error'])}</p>")
        else:
            out.append("<table><tr><th>View<th>Page<th class=num>First byte<th class=num>First paint<th class=num>DOM ready<th class=num>Loaded<th>Sideways scroll<th>Screenshot</tr>" + "".join(
                f"<tr><td>{e(r['view'])}<td>{e(r['page'])}<td class=num>{ms(r['ttfb'])}<td class=num>{ms(r['fcp']) if r['fcp'] else '-'}<td class=num>{ms(r['dcl'])}<td class=num>{ms(r['load'])}<td class={'fail' if r['overflow'] else 'pass'}>{'yes' if r['overflow'] else 'no'}<td><a href='{e(r['shot'])}'>open</a></tr>" for r in b["rows"]) + "</table>")
    out.append("</main></html>"); return "".join(out)

def print_row(r, color, n=""):
    c = lambda code, s: f"\033[{code}m{s}\033[0m" if color else s
    tag = {"pass": c("32", "PASS"), "fail": c("31", "FAIL"), "error": c("31", "ERR ")}[r["status"]]
    sl = f"  slowest {r['slowest']['method']} {r['slowest']['path']} {ms(r['slowest']['total'])}" if r["slowest"] else ""
    print(f" {n} {tag}  {r['category']}: {r['name']:<66} {ms(r['ms']):>9}  {len(r['requests']):>3} req{sl}", flush=True)
    if r["status"] != "pass": print(c("31", f"        {r['error']}"), flush=True)

def console_report(d, color):
    c = lambda code, s: f"\033[{code}m{s}\033[0m" if color else s
    if d["perf"]:
        print();
        print(); print(c("1", "Page load times (median of runs, ms)"))
        print(f"  {'page':<38}{'cold':>9}{'median':>9}{'p95':>9}{'ttfb':>9}{'size':>10}")
        for r in d["perf"]: print(f"  {r['page'][:37]:<38}{r['cold']:>9.1f}{c('33' if r['slow'] else '0', format(r['median'], '9.1f'))}{r['p95']:>9.1f}{r['ttfb']:>9.1f}{r['kb']:>8.1f}KB{'  SLOW' if r['slow'] else ''}")
    S = d["summary"]; print()
    print(c("32" if not S["failed"] else "31", f"{S['passed']}/{S['total']} checks passed in {S['duration_s']:.1f}s. Median page load {ms(S['median_load'])}, slowest {ms(S['slowest_load'])}."))

# ---------------------------------------------------------------- sandbox server + main
def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

SANDBOX_DROP = ("SECRET_KEY", "PUID", "PGID", "UPLOADS_DIR", "PHOTOS_DIR", "BACKUP_DIR", "TMP_DIR", "SELFTEST_DIR",
                "DATABASE_URL", "DATABASE_URL_FILE", "DB_TYPE", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
                "DB_PASSWORD_FILE", "DB_SSLMODE")   # the sandbox always uses its own SQLite database

def sandbox_env(data_dir):
    """The throw-away app's environment. It must never see the real folders, so every storage setting is dropped and
    they all default to places inside the sandbox."""
    env = {**os.environ, "DATA_DIR": str(data_dir), "BACKUP_KEEP": "0", "USER_QUOTA_MB": "1", "TRUST_PROXY": "1", "ALLOW_SIGNUP": "1"}
    for k in SANDBOX_DROP: env.pop(k, None)
    return env

def start_server(port, data_dir, kind):
    env = sandbox_env(data_dir)
    try: import gunicorn  # noqa
    except ImportError: kind = "flask" if kind == "auto" else kind
    kind = "gunicorn" if kind == "auto" else kind
    cmd = ([sys.executable, "-m", "gunicorn", "-b", f"127.0.0.1:{port}", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"] if kind == "gunicorn"
           else [sys.executable, "-c", f"import app; app.app.run(host='127.0.0.1', port={port}, threaded=True)"])
    log = open(Path(data_dir) / "server.log", "w"); proc = subprocess.Popen(cmd, cwd=APP_DIR, env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"; end = time.time() + 40
    while time.time() < end:
        if proc.poll() is not None: break
        try:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=2); c.request("GET", "/healthz")
            if c.getresponse().status == 200: return proc, base, kind
        except OSError: time.sleep(0.2)
    raise RuntimeError("test server did not start:\n" + (Path(data_dir) / "server.log").read_text()[-1500:])

def describe(e):
    msg = str(e).strip()
    if isinstance(e, AssertionError):
        if not msg: fr = traceback.extract_tb(e.__traceback__)[-1]; msg = (fr.line or "assertion failed").strip()
        return msg
    fr = traceback.extract_tb(e.__traceback__)[-1]; return f"{type(e).__name__}: {msg} (line {fr.lineno})"

def main():
    global JSON_LINES
    fast = bool(os.environ.get("SELFTEST_FAST"))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="where to write report.html and report.json (default: SELFTEST_DIR, <DATA_DIR>/selftest or ./selftest-report)")
    ap.add_argument("--only", help="run only checks whose area/name contains this text")
    ap.add_argument("--perf-runs", type=int, default=2 if fast else 5, help="requests per page in the load-time pass")
    ap.add_argument("--perf-items", type=int, default=20 if fast else 200, help="items to seed for the load-time pass")
    ap.add_argument("--budget-ms", type=int, default=800, help="flag pages whose median load is slower than this")
    ap.add_argument("--no-perf", action="store_true", help="skip the load-time pass")
    ap.add_argument("--server", choices=["auto", "gunicorn", "flask"], default="auto")
    ap.add_argument("--browser", action="store_true", help="also load pages in a real browser (needs Playwright)")
    ap.add_argument("--headed", action="store_true", help="with --browser: show the browser window")
    ap.add_argument("--json-lines", action="store_true", help="machine-readable progress (used by the in-app page)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary database and server log")
    a = ap.parse_args(); JSON_LINES = a.json_lines
    default = (os.environ.get("SELFTEST_DIR") or "").strip() or (str(Path(os.environ["DATA_DIR"]) / "selftest") if os.environ.get("DATA_DIR") else "selftest-report")
    out = Path(a.out or default); out.mkdir(parents=True, exist_ok=True)
    sandbox = Path(tempfile.mkdtemp(prefix="craftstash-selftest-")); started = datetime.now(timezone.utc); t_all = time.perf_counter()
    chosen = [c for c in CHECKS if not a.only or a.only.lower() in f"{c[0]} {c[1]}".lower()]
    proc = None; d = {"checks": [], "perf": [], "browser": None, "budget_ms": a.budget_ms}
    try:
        port = free_port(); proc, base, kind = start_server(port, sandbox, a.server)
        d["env"] = {"platform": platform.platform(), "python": platform.python_version(), "server": kind}
        emit(event="start", total=len(chosen), server=kind)
        t = T(base); t.admin = t.anon(); t.admin.name = f"admin{t.tag}"; t.admin.post("/register", {"username": t.admin.name, "password": PW})
        assert "Your collections" in t.admin.get("/").text, "could not create the first (admin) account"
        for i, (cat, name, fn) in enumerate(chosen, 1):
            t.log.clear(); t0 = time.perf_counter(); status, err = "pass", ""
            try: fn(t)
            except AssertionError as ex: status, err = "fail", describe(ex)
            except Exception as ex: status, err = "error", describe(ex)
            reqs = list(t.log); slow = max(reqs, key=lambda r: r["total"]) if reqs else None
            row = {"category": cat, "name": name, "status": status, "ms": round((time.perf_counter() - t0) * 1000, 1), "error": err, "requests": reqs[:80], "slowest": slow}
            d["checks"].append(row); emit(event="check", i=i, category=cat, name=name, status=status, ms=row["ms"], requests=len(reqs), slowest=slow, error=err)
            if not JSON_LINES: print_row(row, sys.stdout.isatty(), f"{i:>2}/{len(chosen)}")
        if not a.no_perf:
            try:
                rows, pu, ppid, pprj = perf_pass(t, a.perf_runs, a.perf_items, a.budget_ms); d["perf"] = rows
                for r in rows: emit(event="perf", **r)
                if a.browser:
                    pages = [("Home", "/"), ("Collection", f"/collections/{ppid}"), ("Shopping list", "/shopping"), ("Projects", "/projects"), ("Project", f"/projects/{pprj}"), ("Appearance", "/appearance")]
                    d["browser"] = browser_pass(base, pu.name, PW, pages, out, a.headed)
            except Exception as ex:
                d["perf"] = d["perf"] or []; d["checks"].append({"category": "Performance", "name": "Load-time pass", "status": "error", "ms": 0, "error": describe(ex), "requests": [], "slowest": None})
                emit(event="check", i=len(chosen) + 1, category="Performance", name="Load-time pass", status="error", ms=0, requests=0, slowest=None, error=describe(ex))
    except Exception as ex:
        d["env"] = d.get("env") or {"platform": platform.platform(), "python": platform.python_version(), "server": "none"}
        d["checks"].append({"category": "Setup", "name": "Start the test server", "status": "error", "ms": 0, "error": describe(ex), "requests": [], "slowest": None})
        emit(event="check", i=1, category="Setup", name="Start the test server", status="error", ms=0, requests=0, slowest=None, error=describe(ex))
    finally:
        if proc and proc.poll() is None: proc.terminate(); proc.wait(timeout=10)
        if not a.keep: shutil.rmtree(sandbox, ignore_errors=True)
    loads = [r["total"] for c in d["checks"] for r in c["requests"] if r["method"] == "GET" and r["status"] == 200 and not r["path"].startswith("/static")] or [0]
    n_pass = sum(c["status"] == "pass" for c in d["checks"])
    d["started"] = started.strftime("%Y-%m-%d %H:%M UTC")
    d["summary"] = {"total": len(d["checks"]), "passed": n_pass, "failed": len(d["checks"]) - n_pass, "duration_s": round(time.perf_counter() - t_all, 1),
                    "median_load": round(statistics.median(loads), 1), "p95_load": round(pct(loads, 95), 1), "slowest_load": round(max(loads), 1)}
    (out / "report.json").write_text(json.dumps(d, indent=1)); (out / "report.html").write_text(html_report(d))
    emit(event="done", summary=d["summary"], report=str(out / "report.html"))
    if not JSON_LINES: console_report(d, sys.stdout.isatty()); print(f"\nReport: {out / 'report.html'}\nData:   {out / 'report.json'}")
    sys.exit(1 if d["summary"]["failed"] else 0)

if __name__ == "__main__":
    main()
