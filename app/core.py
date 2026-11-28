"""Shared setup: configuration, the Flask app, database and access helpers, request hooks."""
import backup, dbx, hashlib, json, math, migrations, oidc, os, paths, re, secrets, sys, themes, time, uuid
from PIL import Image, ImageOps, UnidentifiedImageError
from datetime import datetime, timezone
from flask import abort, flash, Flask, g, jsonify, redirect, request, session, url_for
from version import VERSION
from flask.sessions import SecureCookieSessionInterface
from markupsafe import Markup
from types import MappingProxyType
from werkzeug.middleware.proxy_fix import ProxyFix
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

P = paths.Paths(); P.check()        # every folder can be mapped on its own; refuses to start if one is missing or read-only
DATA, UPLOADS, PHOTOS = P.data, P.uploads, P.photos
ALLOWED = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "md", "doc", "docx", "odt", "xls", "xlsx", "zip"}
INLINE = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt"}  # everything else downloads
# What each allowed type's first bytes must look like, so a page or script renamed to .pdf or .png is refused.
MAGIC = {"pdf": (b"%PDF-",), "png": (b"\x89PNG\r\n\x1a\n",), "jpg": (b"\xff\xd8\xff",), "jpeg": (b"\xff\xd8\xff",),
         "gif": (b"GIF87a", b"GIF89a"), "webp": (b"RIFF",), "zip": (b"PK\x03\x04", b"PK\x05\x06"),
         "docx": (b"PK\x03\x04",), "xlsx": (b"PK\x03\x04",), "odt": (b"PK\x03\x04",),
         "doc": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",), "xls": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)}
# Attachments open in their own tab, never framed by another site, and can't load or run anything if a browser ever
# rendered one as a page. PDFs get none: browsers' PDF viewers can refuse to show a PDF that carries a CSP (Chrome
# with "sandbox" for one), and they already run a PDF's scripts walled off from this site.
FILE_CSP = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'"

def content_ok(ext, head):
    """True when the start of an upload matches its extension. Text must be UTF-8 without NULs or markup-looking starts."""
    if ext in ("txt", "md"):
        try: text = head.decode("utf-8")
        except UnicodeDecodeError as e:
            if e.start < len(head) - 4: return False        # only a character cut off at the end of the sample is fine
            text = head[:e.start].decode("utf-8")
        return "\x00" not in text and not re.match(r"\s*<(?:!doctype|html|script|svg|\?xml)", text, re.I)
    if ext == "webp" and head[8:12] != b"WEBP": return False
    return head.startswith(MAGIC.get(ext, (b"",)))
QUOTA = int(os.environ.get("USER_QUOTA_MB", "500") or 0) * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

app = Flask(__name__)
key_file = DATA / "secret_key"
if not key_file.exists():
    key_file.write_text(secrets.token_hex(32))
app.secret_key = dbx.secret("SECRET_KEY") or key_file.read_text().strip()      # SECRET_KEY, SECRET_KEY_FILE or the generated one
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1")
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s or "{}")
app.jinja_env.filters["money"] = lambda v: f"{currency()}{float(v):,.2f}"
app.jinja_env.filters["local"] = lambda ts: local_time(ts)
app.jinja_env.filters["gb"] = lambda n: "?" if n is None else (f"{n / 1e9:,.1f} GB" if n >= 1e9 else f"{n / 1e6:,.0f} MB")
# ---------- reverse proxies ----------
# TRUST_PROXY: how many proxies are in front (1 behind Nginx Proxy Manager, Traefik, Caddy...). Their X-Forwarded-*
# headers are believed, which gives the real client address, https and host. TRUSTED_PROXIES (addresses or ranges,
# comma separated) narrows that to requests that actually come from those addresses; from anywhere else the headers
# are dropped, so a client that reaches the port directly can't pretend to be someone else.
# URL_PREFIX hosts the app under a path, e.g. /craft for https://example.com/craft/ (the proxy may strip it or not).
import ipaddress
def _nets(v):
    out = []
    for part in re.split(r"[,\s]+", v or ""):
        if not part: continue
        try: out.append(ipaddress.ip_network(part, strict=False))
        except ValueError: raise SystemExit(f"TRUSTED_PROXIES: '{part}' isn't an IP address or range (like 172.16.0.0/12).")
    return out
TRUSTED_NETS = _nets(os.environ.get("TRUSTED_PROXIES", ""))
_hops = int(os.environ.get("TRUST_PROXY", "0") or 0) or (1 if TRUSTED_NETS else 0)
_prefix = (os.environ.get("URL_PREFIX") or "").strip().strip("/")
if _prefix and (not re.fullmatch(r"[A-Za-z0-9._~-]+(/[A-Za-z0-9._~-]+)*", _prefix) or any(set(p) == {"."} for p in _prefix.split("/"))):
    raise SystemExit("URL_PREFIX: use a plain path like /craft (letters, numbers, - . _ ~ and /).")
URL_PREFIX = "/" + _prefix if _prefix else ""

class TrustedProxies:
    """Apply ProxyFix only to requests from TRUSTED_PROXIES (or from anywhere, when that is empty)."""
    def __init__(self, wsgi, hops, nets):
        self.plain, self.nets = wsgi, nets
        self.fixed = ProxyFix(wsgi, x_for=hops, x_proto=hops, x_host=hops, x_port=hops, x_prefix=hops)
    def trusted(self, addr):
        if not self.nets: return True
        try: ip = ipaddress.ip_address((addr or "").split("%")[0])
        except ValueError: return False
        if ip.version == 6 and ip.ipv4_mapped: ip = ip.ipv4_mapped
        return any(ip in n for n in self.nets)
    def __call__(self, environ, start):
        if self.trusted(environ.get("REMOTE_ADDR")): return self.fixed(environ, start)
        for k in [k for k in environ if k.startswith("HTTP_X_FORWARDED_")]: del environ[k]
        return self.plain(environ, start)

class Prefix:
    """Serve the app under URL_PREFIX whether or not the proxy strips it, so links, static files, redirects and the
    session cookie path all carry it. Paths without it (the container's own health check) still work."""
    def __init__(self, wsgi, prefix): self.wsgi, self.prefix = wsgi, prefix
    def __call__(self, environ, start):
        path = environ.get("PATH_INFO", "")
        if path == self.prefix or path.startswith(self.prefix + "/"): environ["PATH_INFO"] = path[len(self.prefix):] or "/"
        environ["SCRIPT_NAME"] = self.prefix
        return self.wsgi(environ, start)

if URL_PREFIX:
    app.wsgi_app = Prefix(app.wsgi_app, URL_PREFIX)
    app.config.update(APPLICATION_ROOT=URL_PREFIX, SESSION_COOKIE_PATH=URL_PREFIX)
if _hops: app.wsgi_app = TrustedProxies(app.wsgi_app, _hops, TRUSTED_NETS)
try: TZ = ZoneInfo(os.environ.get("TZ") or "UTC")
except ZoneInfoNotFoundError: TZ = ZoneInfo("UTC")

class _Session(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):     # COOKIE_SECURE=auto: secure cookie exactly when the request arrived over HTTPS
        mode = os.environ.get("COOKIE_SECURE", "auto")
        return request.is_secure if mode == "auto" else mode == "1"
app.session_interface = _Session()

# ---------- db helpers ----------
def db():
    if "db" not in g: g.db = dbx.connect()      # SQLite by default, or the external database in DATABASE_URL
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d: d.close()

def q(sql, *a): return db().execute(sql, a).fetchall()
def get(sql, *a): return db().execute(sql, a).fetchone() or abort(404)
def run(sql, *a):
    c = db().execute(sql, a); db().commit(); return c
def uid(): return g.user["id"]

backup.hold_app_lock(DATA)          # first, so `manage.py restore` refuses to run under a live app
DB = migrations.prepare(P)          # configure the database from the environment (waits for a server) and upgrade it
backup.clean_leftovers(P)
backup.start_snapshots(P, int(os.environ.get("BACKUP_KEEP", "7") or 0))
def startup_summary(out=None):
    """What this copy is running with, in two log lines. Never includes a password, key or token."""
    out = out or sys.stderr
    P.log_summary(out)
    proxy = ("off" if not _hops else f"{_hops} hop(s)" + (f" from {', '.join(map(str, TRUSTED_NETS))}" if TRUSTED_NETS else " from anywhere"))
    bits = [f"db={DB.describe()}", f"proxy={proxy}", f"prefix={URL_PREFIX or '/'}",
            f"cookies={os.environ.get('COOKIE_SECURE', 'auto')}", f"uploads<={app.config['MAX_CONTENT_LENGTH'] // 1048576} MB",
            f"quota={QUOTA // 1048576 or 'none'}{' MB' if QUOTA else ''}", f"snapshots={os.environ.get('BACKUP_KEEP', '7') or '0'}",
            f"tz={TZ.key}", f"secret_key={'set' if os.environ.get('SECRET_KEY') or os.environ.get('SECRET_KEY_FILE') else 'generated'}"]
    print(f"[config] version={VERSION}  " + "  ".join(bits), file=out, flush=True)
startup_summary()

_pruned = {"at": 0.0}
def prune_daily():                  # old activity goes once a day, not just when the container restarts
    if time.time() - _pruned["at"] < 86400: return
    _pruned["at"] = time.time()
    migrations.prune_activity(db()); db().commit()

def num(v, default=0.0):
    try: x = float(v)
    except (TypeError, ValueError): return default
    return x if math.isfinite(x) and x >= 0 else default

GROUP_HEX = dbx.group_concat_distinct("s.hex")   # the colour swatches on collection and project cards
LIKE = dbx.like_op()                # case-insensitive LIKE with ! as the escape, on every database
def like(term): return "%" + re.sub(r"[!%_]", lambda m: "!" + m[0], term) + "%"   # so "50%" or "a_b" match literally

def clean_tag(text):
    """A project tag: lower case, single spaces, letters/digits/space/- /_ only, up to 30 characters. '' if nothing is left."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w -]", "", (text or "").lower())).strip(" -")[:30]

def clean(name, n=120): return (request.form.get(name) or "").strip()[:n]

# ---------- access helpers: a row is only visible if the logged-in user may see it ----------
def pool_of(pid, write=False, owner=False):
    cond = " AND a.is_owner=1" if owner else " AND a.can_edit=1" if write else ""
    return get("""SELECT p.*, a.can_edit, a.is_owner, u.username AS owner FROM pools p
                  JOIN access a ON a.pool_id=p.id JOIN users u ON u.id=p.user_id
                  WHERE p.id=? AND a.user_id=?""" + cond, pid, uid())

def type_of(tid, write=False):
    return get("""SELECT t.*, a.can_edit, p.name AS pool_name FROM types t JOIN access a ON a.pool_id=t.pool_id
                  JOIN pools p ON p.id=t.pool_id WHERE t.id=? AND a.user_id=?""" + (" AND a.can_edit=1" if write else ""), tid, uid())

def item_of(iid, write=False):
    return get("""SELECT s.* FROM stock s JOIN access a ON a.pool_id=s.pool_id
                  WHERE s.id=? AND a.user_id=?""" + (" AND a.can_edit=1" if write else ""), iid, uid())

def project_of(id, write=False):
    """The project, if the user may see it (write=True: only the owner). Asking to write marks it as just updated."""
    p = get("SELECT p.*, u.username AS owner FROM projects p JOIN users u ON u.id=p.user_id WHERE p.id=?", id)
    if p["user_id"] == uid():
        if write: run("UPDATE projects SET updated=? WHERE id=?", dbx.now(), id)
        return p
    if not write and db().execute("SELECT 1 FROM project_shares WHERE project_id=? AND user_id=?", (id, uid())).fetchone():
        return p
    abort(404)

def file_of(fid, write=False):
    f = get("SELECT * FROM files WHERE id=?", fid); project_of(f["project_id"], write); return f

def step_of(sid):
    s = get("SELECT * FROM steps WHERE id=?", sid); project_of(s["project_id"], True); return s

def find_user():
    return db().execute("SELECT id FROM users WHERE username=?", (clean("username", 40),)).fetchone()

# The purge helpers delete files from disk before their rows go. The scope picks one fixed WHERE clause; nothing
# from a request ever becomes SQL text.
FILE_SCOPES = MappingProxyType({"user": "p.user_id=?", "project": "p.id=?"})
PHOTO_SCOPES = MappingProxyType({"user": "p.user_id=?", "pool": "p.id=?", "type": "t.id=?", "item": "i.id=?"})
STEP_PHOTO_SCOPES = MappingProxyType({"user": "p.user_id=?", "project": "p.id=?", "step": "s.id=?"})

def purge_files(scope, id):
    for f in q("SELECT f.stored FROM files f JOIN projects p ON p.id=f.project_id WHERE " + FILE_SCOPES[scope], id):
        (UPLOADS / f["stored"]).unlink(missing_ok=True)

def pwv(h): return hashlib.sha256(h.encode()).hexdigest()[:16]   # changes with the password, which signs out other sessions

def currency():
    if "cur" not in g:
        r = db().execute("SELECT value FROM settings WHERE key='currency'").fetchone(); g.cur = r["value"] if r else "$"
    return g.cur

def local_time(ts):
    try: return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%d %b %Y, %H:%M")
    except (TypeError, ValueError): return ts or ""

def log(pool_id, kind, text, item_id=None, delta=None, commit=True):
    db().execute("INSERT INTO activity(user_id,pool_id,item_id,kind,text,delta) VALUES(?,?,?,?,?,?)",
                 (g.user["id"] if getattr(g, "user", None) else None, pool_id, item_id, kind, text[:300], delta))
    if commit: db().commit()

def purge_photos(scope, id):
    for r in q("""SELECT i.photo FROM items i JOIN types t ON t.id=i.type_id JOIN pools p ON p.id=t.pool_id
                  WHERE i.photo<>'' AND """ + PHOTO_SCOPES[scope], id): (PHOTOS / r["photo"]).unlink(missing_ok=True)

def purge_step_photos(scope, id):
    for r in q("SELECT sp.stored FROM step_photos sp JOIN steps s ON s.id=sp.step_id JOIN projects p ON p.id=s.project_id WHERE " + STEP_PHOTO_SCOPES[scope], id):
        (PHOTOS / r["stored"]).unlink(missing_ok=True)

PROJECT_PHOTO_SCOPES = MappingProxyType({"user": "user_id=?", "project": "id=?"})

def purge_project_photos(scope, id):   # a project's cover picture
    for r in q("SELECT photo FROM projects WHERE photo<>'' AND " + PROJECT_PHOTO_SCOPES[scope], id): (PHOTOS / r["photo"]).unlink(missing_ok=True)

PATTERN_CACHE = P.tmp / "patterns"   # drawn charts; made again whenever they're missing

def drop_pattern_cache(pattern_id):
    for f in PATTERN_CACHE.glob(f"{int(pattern_id)}-*.png"): f.unlink(missing_ok=True)

def purge_pattern_files(user_id):  # before an account goes: its patterns' source pictures and drawn charts
    for r in q("SELECT id, photo FROM patterns WHERE user_id=?", user_id):
        if r["photo"]: (PHOTOS / r["photo"]).unlink(missing_ok=True)
        drop_pattern_cache(r["id"])

def purge_wish_photos(user_id):    # before an account goes: its wishes go with it (ON DELETE CASCADE)
    for r in q("SELECT photo FROM wishes WHERE user_id=? AND photo<>''", user_id): (PHOTOS / r["photo"]).unlink(missing_ok=True)

def wish_count():                  # open wishes the user can see (the Wishlist tab's count)
    me = uid(); return db().execute("SELECT COUNT(*) FROM wishes w WHERE w.got=0 AND " + SEE_WISH, (me, me)).fetchone()[0]

def open_wish_items():             # {item id: wish id} for open wishes that watch an item (the restock list links them)
    me = uid(); rows = q("SELECT w.id, w.item_id FROM wishes w WHERE w.got=0 AND w.item_id IS NOT NULL AND " + SEE_WISH, me, me)
    return {r["item_id"]: r["id"] for r in rows}

def drop_reservations(pool_id, user_id):   # someone lost access to a collection: their open reservations on it go too
    run("""DELETE FROM assignments WHERE consumed=0 AND project_id IN (SELECT id FROM projects WHERE user_id=?)
           AND item_id IN (SELECT i.id FROM items i JOIN types t ON t.id=i.type_id WHERE t.pool_id=?)""", user_id, pool_id)

def used_bytes(user_id):
    return db().execute("""SELECT (SELECT COALESCE(SUM(f.size),0) FROM files f JOIN projects p ON p.id=f.project_id WHERE p.user_id=?)
                                + (SELECT COALESCE(SUM(sp.size),0) FROM step_photos sp JOIN steps s ON s.id=sp.step_id
                                   JOIN projects p ON p.id=s.project_id WHERE p.user_id=?)""", (user_id, user_id)).fetchone()[0]

def save_photo(f):
    """Re-encode an uploaded image as a small JPEG: strips metadata and anything hidden in the file."""
    try:
        im = Image.open(f.stream)
        if im.format not in ("JPEG", "PNG", "WEBP", "GIF"): return None
        im = ImageOps.exif_transpose(im); im.thumbnail((900, 900))
        rgba = im.convert("RGBA"); flat = Image.new("RGB", rgba.size, "#ffffff"); flat.paste(rgba, mask=rgba.split()[3])
        name = uuid.uuid4().hex + ".jpg"; flat.save(PHOTOS / name, "JPEG", quality=84, optimize=True); return name
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError): return None

def pool_impact(pid):
    r = db().execute("""SELECT (SELECT COUNT(*) FROM items i JOIN types t ON t.id=i.type_id WHERE t.pool_id=:p) AS items,
                               (SELECT COUNT(DISTINCT a.project_id) FROM assignments a JOIN items i ON i.id=a.item_id
                                  JOIN types t ON t.id=i.type_id WHERE t.pool_id=:p) AS projects,
                               (SELECT COUNT(*) FROM pool_shares WHERE pool_id=:p) AS people""", {"p": pid}).fetchone()
    return dict(r)

def type_impact(tid):
    d = db()
    return {"items": d.execute("SELECT COUNT(*) FROM items WHERE type_id=?", (tid,)).fetchone()[0],
            "projects": d.execute("SELECT COUNT(DISTINCT a.project_id) FROM assignments a JOIN items i ON i.id=a.item_id WHERE i.type_id=?", (tid,)).fetchone()[0]}

def signups_open():
    r = db().execute("SELECT value FROM settings WHERE key='signups'").fetchone()
    return (r["value"] if r else os.environ.get("ALLOW_SIGNUP", "1")) == "1"

def valid_account(name, pw):
    if not re.fullmatch(r"[\w.-]{3,40}", name) or len(pw) < 8:
        flash("Username: 3-40 letters/numbers; password: 8+ characters."); return False
    return True

# ---------- request hooks ----------
@app.before_request
def gate():
    if request.endpoint in ("healthz", "static", "manifest"): return    # browsers fetch the manifest without the session cookie
    if request.path.startswith("/api/"): return api_gate()
    session.setdefault("csrf", secrets.token_hex(16))
    g.user = None
    if "uid" in session:
        u = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
        if u and u["active"] and session.get("pwv") == pwv(u["pw"]): g.user = u
        else: session.pop("uid", None)
    if request.method == "POST" and not secrets.compare_digest(
            request.form.get("_csrf", "").encode(), session["csrf"].encode()):
        abort(400)
    if not g.user and request.endpoint not in ("login", "register", "sso_login", "sso_callback"):
        if request.endpoint == "link_import" and request.method == "GET":   # shared from a phone: pick it up after signing in
            session["share"] = {k: request.args[k][:500] for k in ("url", "text", "title") if request.args.get(k)}
        return redirect(url_for("login"))
    prune_daily()

@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    if request.path.startswith("/api/"): r.headers["Cache-Control"] = "no-store"
    if r.mimetype == "text/html":       # not on files, so the browser's PDF viewer keeps working
        csp = CSP.replace("img-src 'self'", "img-src 'self' " + " ".join(g.img_hosts), 1) if g.get("img_hosts") else CSP
        r.headers["Content-Security-Policy"] = csp; r.headers["X-Frame-Options"] = "DENY"
        r.headers.setdefault("Cache-Control", "private, no-cache")
    return r

LOW = "(s.qty<=0 OR (s.low_at IS NOT NULL AND s.qty<=s.low_at) OR s.free<0)"
CAN_EDIT = "EXISTS(SELECT 1 FROM access x WHERE x.pool_id=s.pool_id AND x.user_id=? AND x.can_edit=1)"   # s = a stock row
# w = a wishes row. Its owner sees and changes it; so does anyone who can see (or edit) the collection its type is in.
SEE_WISH = "(w.user_id=? OR EXISTS(SELECT 1 FROM types wt JOIN access x ON x.pool_id=wt.pool_id WHERE wt.id=w.type_id AND x.user_id=?))"
EDIT_WISH = ("(w.user_id=? OR EXISTS(SELECT 1 FROM types wt JOIN access x ON x.pool_id=wt.pool_id WHERE wt.id=w.type_id AND x.user_id=?"
             " AND x.can_edit=1))")

@app.context_processor
def ctx():
    n = 0
    if getattr(g, "user", None):
        n = db().execute(f"SELECT COUNT(*) FROM stock s JOIN access a ON a.pool_id=s.pool_id WHERE a.user_id=? AND {LOW}", (g.user["id"],)).fetchone()[0]
    css = themes.css_for(g.user["theme"]) if getattr(g, "user", None) else ""   # built only from validated hex/enum values
    return {"csrf": Markup(f'<input type="hidden" name="_csrf" value="{session.get("csrf", "")}">'), "shop_count": n,
            "theme_css": Markup(css), "app_version": VERSION}

@app.get("/healthz")
def healthz(): return "ok"

# Recent login failures (single worker, so in-memory is enough). Three counters per attempt:
#   "ip|name" - one address guessing one account          (FAIL_LIMIT)
#   "ip|*"    - one address spraying many usernames       (IP_LIMIT)
#   "*|name"  - many addresses guessing one account       (USER_LIMIT)
FAILS = {}
FAIL_WINDOW, FAIL_LIMIT, IP_LIMIT, USER_LIMIT, FAIL_KEYS = 600, 8, 40, 50, 20000

def _fail_keys(ip, name): return (f"{ip}|{name}", f"{ip}|*", f"*|{name}")

def _recent(key, now):
    ts = [t for t in FAILS.get(key, ()) if now - t < FAIL_WINDOW]
    if ts: FAILS[key] = ts
    else: FAILS.pop(key, None)
    return len(ts)

def throttled(ip, name):
    now = time.time(); pair, by_ip, by_user = _fail_keys(ip, name)
    return _recent(pair, now) >= FAIL_LIMIT or _recent(by_ip, now) >= IP_LIMIT or _recent(by_user, now) >= USER_LIMIT

def record_fail(ip, name):
    now = time.time()
    room = FAIL_KEYS - 3            # each failure adds up to 3 keys
    if len(FAILS) > room:           # full: drop expired entries, then the least recently failed ones (never all of them)
        for k in [k for k, v in FAILS.items() if now - v[-1] >= FAIL_WINDOW]: del FAILS[k]
        while len(FAILS) > room: del FAILS[next(iter(FAILS))]
    for k in _fail_keys(ip, name): FAILS[k] = FAILS.pop(k, []) + [now]   # re-insert so dict order = recency
SSO = oidc.Config()
if SSO.warning(): print(f"[sso] WARNING: {SSO.warning()}", file=sys.stderr, flush=True)
# ---------- JSON API (for Home Assistant and other tools) ----------
# Every request sends "Authorization: Bearer <token>". Tokens are made on the account page, act as that user (so they
# see exactly what the user sees), and are read-only unless created with write access. Only a SHA-256 of each is stored.
API_PREFIX = "cs_"

def token_hash(t): return hashlib.sha256(t.encode()).hexdigest()

def api_error(status, message): return jsonify(error=message), status

def api_gate():
    g.user, g.api_write = None, False
    auth = request.headers.get("Authorization", "")
    tok = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    ip = request.remote_addr or "?"; key = f"api:{ip}"   # per address, so nobody can lock everyone out
    if throttled(ip, key): return api_error(429, "Too many failed attempts. Wait a few minutes and try again.")
    row = db().execute("""SELECT t.id AS tid, t.can_write, u.* FROM api_tokens t JOIN users u ON u.id=t.user_id
                          WHERE t.hash=?""", (token_hash(tok),)).fetchone() if tok.startswith(API_PREFIX) else None
    if not row or not row["active"]:
        if tok: record_fail(ip, key)
        r = jsonify(error="Send a valid API token: Authorization: Bearer <token>. Make one on your account page.")
        r.status_code = 401; r.headers["WWW-Authenticate"] = 'Bearer realm="craft-stash"'; return r
    g.user, g.api_write = row, bool(row["can_write"])
    if request.method not in ("GET", "HEAD", "OPTIONS") and not g.api_write:
        return api_error(403, "This token is read-only. Make one with write access to change stock.")
    db().execute("UPDATE api_tokens SET last_used=? WHERE id=? AND (last_used IS NULL OR last_used < ?)",
                 (dbx.now(), row["tid"], dbx.ago(60 / 86400))); db().commit()
