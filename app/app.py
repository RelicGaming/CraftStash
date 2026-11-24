import csv, hashlib, io, json, math, os, re, secrets, shutil, sqlite3, subprocess, sys, tempfile, threading, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from flask import (Flask, Response, abort, after_this_request, flash, g, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from flask.sessions import SecureCookieSessionInterface
from markupsafe import Markup
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
import backup, importer, migrations, themes

DATA = Path(os.environ.get("DATA_DIR", "/data"))
UPLOADS = DATA / "uploads"; PHOTOS = UPLOADS / "photos"
PHOTOS.mkdir(parents=True, exist_ok=True)
ALLOWED = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "md", "doc", "docx", "odt", "xls", "xlsx", "zip"}
INLINE = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt"}  # everything else downloads
QUOTA = int(os.environ.get("USER_QUOTA_MB", "500") or 0) * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

app = Flask(__name__)
key_file = DATA / "secret_key"
if not key_file.exists():
    key_file.write_text(secrets.token_hex(32))
app.secret_key = os.environ.get("SECRET_KEY") or key_file.read_text().strip()
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_UPLOAD_MB", "25")) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE") == "1")
app.jinja_env.filters["fromjson"] = lambda s: json.loads(s or "{}")
app.jinja_env.filters["money"] = lambda v: f"{currency()}{float(v):,.2f}"
app.jinja_env.filters["local"] = lambda ts: local_time(ts)
_hops = int(os.environ.get("TRUST_PROXY", "0") or 0)
if _hops: app.wsgi_app = ProxyFix(app.wsgi_app, x_for=_hops, x_proto=_hops, x_host=_hops)   # behind Nginx Proxy Manager
try: TZ = ZoneInfo(os.environ.get("TZ") or "UTC")
except ZoneInfoNotFoundError: TZ = ZoneInfo("UTC")

class _Session(SecureCookieSessionInterface):
    def get_cookie_secure(self, app):     # COOKIE_SECURE=auto: secure cookie exactly when the request arrived over HTTPS
        mode = os.environ.get("COOKIE_SECURE", "auto")
        return request.is_secure if mode == "auto" else mode == "1"
app.session_interface = _Session()

# ---------- db helpers ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATA / "craft.db")
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
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
migrations.run(DATA / "craft.db", (Path(__file__).parent / "schema.sql").read_text())
backup.clean_leftovers(DATA)
backup.start_snapshots(DATA, int(os.environ.get("BACKUP_KEEP", "7") or 0))

_pruned = {"at": 0.0}
def prune_daily():                  # old activity goes once a day, not just when the container restarts
    if time.time() - _pruned["at"] < 86400: return
    _pruned["at"] = time.time()
    migrations.prune_activity(db()); db().commit()

def num(v, default=0.0):
    try: x = float(v)
    except (TypeError, ValueError): return default
    return x if math.isfinite(x) and x >= 0 else default

LIKE = "LIKE ? ESCAPE '!'"
def like(term): return "%" + re.sub(r"[!%_]", lambda m: "!" + m[0], term) + "%"   # so "50%" or "a_b" match literally

def clean(name, n=120): return (request.form.get(name) or "").strip()[:n]

# ---------- access helpers: a row is only visible if the logged-in user may see it ----------
def pool_of(pid, write=False, owner=False):
    cond = " AND a.is_owner=1" if owner else " AND a.can_edit=1" if write else ""
    return get("""SELECT p.*, a.can_edit, a.is_owner, u.username AS owner FROM pools p
                  JOIN access a ON a.pool_id=p.id JOIN users u ON u.id=p.user_id
                  WHERE p.id=? AND a.user_id=?""" + cond, pid, uid())

def type_of(tid, write=False):
    return get("""SELECT t.*, a.can_edit FROM types t JOIN access a ON a.pool_id=t.pool_id
                  WHERE t.id=? AND a.user_id=?""" + (" AND a.can_edit=1" if write else ""), tid, uid())

def item_of(iid, write=False):
    return get("""SELECT s.* FROM stock s JOIN access a ON a.pool_id=s.pool_id
                  WHERE s.id=? AND a.user_id=?""" + (" AND a.can_edit=1" if write else ""), iid, uid())

def project_of(id, write=False):
    p = get("SELECT p.*, u.username AS owner FROM projects p JOIN users u ON u.id=p.user_id WHERE p.id=?", id)
    if p["user_id"] == uid(): return p
    if not write and db().execute("SELECT 1 FROM project_shares WHERE project_id=? AND user_id=?", (id, uid())).fetchone():
        return p
    abort(404)

def file_of(fid, write=False):
    f = get("SELECT * FROM files WHERE id=?", fid); project_of(f["project_id"], write); return f

def step_of(sid):
    s = get("SELECT * FROM steps WHERE id=?", sid); project_of(s["project_id"], True); return s

def find_user():
    return db().execute("SELECT id FROM users WHERE username=?", (clean("username", 40),)).fetchone()

def purge_files(where, *a):
    for f in q("SELECT f.stored FROM files f JOIN projects p ON p.id=f.project_id WHERE " + where, *a):
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

def purge_photos(where, *a):
    for r in q("""SELECT i.photo FROM items i JOIN types t ON t.id=i.type_id JOIN pools p ON p.id=t.pool_id
                  WHERE i.photo<>'' AND """ + where, *a): (PHOTOS / r["photo"]).unlink(missing_ok=True)

def drop_reservations(pool_id, user_id):   # someone lost access to a collection: their open reservations on it go too
    run("""DELETE FROM assignments WHERE consumed=0 AND project_id IN (SELECT id FROM projects WHERE user_id=?)
           AND item_id IN (SELECT i.id FROM items i JOIN types t ON t.id=i.type_id WHERE t.pool_id=?)""", user_id, pool_id)

def used_bytes(user_id):
    return db().execute("SELECT COALESCE(SUM(f.size),0) FROM files f JOIN projects p ON p.id=f.project_id WHERE p.user_id=?", (user_id,)).fetchone()[0]

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
    if request.endpoint in ("healthz", "static"): return
    session.setdefault("csrf", secrets.token_hex(16))
    g.user = None
    if "uid" in session:
        u = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
        if u and u["active"] and session.get("pwv") == pwv(u["pw"]): g.user = u
        else: session.pop("uid", None)
    if request.method == "POST" and not secrets.compare_digest(
            request.form.get("_csrf", "").encode(), session["csrf"].encode()):
        abort(400)
    if not g.user and request.endpoint not in ("login", "register"):
        return redirect(url_for("login"))
    prune_daily()

@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    if r.mimetype == "text/html":       # not on files, so the browser's PDF viewer keeps working
        r.headers["Content-Security-Policy"] = CSP; r.headers["X-Frame-Options"] = "DENY"
        r.headers.setdefault("Cache-Control", "private, no-cache")
    return r

LOW = "(s.qty<=0 OR (s.low_at IS NOT NULL AND s.qty<=s.low_at) OR s.free<0)"
CAN_EDIT = "EXISTS(SELECT 1 FROM access x WHERE x.pool_id=s.pool_id AND x.user_id=? AND x.can_edit=1)"   # s = a stock row

@app.context_processor
def ctx():
    n = 0
    if getattr(g, "user", None):
        n = db().execute(f"SELECT COUNT(*) FROM stock s JOIN access a ON a.pool_id=s.pool_id WHERE a.user_id=? AND {LOW}", (g.user["id"],)).fetchone()[0]
    css = themes.css_for(g.user["theme"]) if getattr(g, "user", None) else ""   # built only from validated hex/enum values
    return {"csrf": Markup(f'<input type="hidden" name="_csrf" value="{session.get("csrf", "")}">'), "shop_count": n,
            "theme_css": Markup(css)}

@app.get("/healthz")
def healthz(): return "ok"

# ---------- auth ----------
def no_users(): return db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

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

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = clean("username", 40); ip = request.remote_addr or "?"
        if throttled(ip, name.lower()):
            flash("Too many failed attempts. Wait a few minutes and try again.")
        else:
            u = db().execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
            if u and check_password_hash(u["pw"], request.form.get("password", "")):
                if not u["active"]: flash("This account is disabled. Ask an admin.")
                else:
                    FAILS.pop(f"{ip}|{name.lower()}", None); session.clear(); session["uid"] = u["id"]
                    session["pwv"] = pwv(u["pw"]); session.permanent = True
                    return redirect(url_for("home"))
            else:
                record_fail(ip, name.lower()); flash("Wrong username or password.")
    return render_template("auth.html", mode="login", can_register=signups_open(), first_run=no_users())

@app.route("/register", methods=["GET", "POST"])
def register():
    first = no_users()
    if not signups_open(): abort(403)     # ALLOW_SIGNUP=0 also closes the first-admin sign-up (use manage.py create-user)
    if request.method == "POST":
        name, pw = clean("username", 40), request.form.get("password", "")
        if valid_account(name, pw):
            h = generate_password_hash(pw)
            try: c = run("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", name, h, int(first))
            except sqlite3.IntegrityError: flash("That username is taken.")
            else:
                session.clear(); session["uid"] = c.lastrowid; session["pwv"] = pwv(h); session.permanent = True
                return redirect(url_for("home"))
    return render_template("auth.html", mode="register", can_register=True)

@app.post("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------- admin ----------
def admin_only():
    if not g.user["is_admin"]: abort(403)

@app.get("/admin")
def admin():
    admin_only()
    users = q("""SELECT u.*, (SELECT COUNT(*) FROM pools WHERE user_id=u.id) AS pools,
                 (SELECT COUNT(*) FROM projects WHERE user_id=u.id) AS projects,
                 (SELECT COALESCE(SUM(f.size),0) FROM files f JOIN projects p ON p.id=f.project_id WHERE p.user_id=u.id) AS bytes
                 FROM users u ORDER BY username""")
    snaps = sorted((DATA / "backups").glob("auto-*.db")) if (DATA / "backups").exists() else []
    last = datetime.fromtimestamp(snaps[-1].stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if snaps else None
    return render_template("admin.html", users=users, signups=signups_open(), quota=QUOTA, cur=currency(), snaps=len(snaps), last_snap=local_time(last) if last else None)

@app.post("/admin/users")
def admin_create():
    admin_only()
    name, pw = clean("username", 40), request.form.get("password", "")
    if valid_account(name, pw):
        try: run("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", name, generate_password_hash(pw), int(bool(request.form.get("is_admin"))))
        except sqlite3.IntegrityError: flash("That username is taken.")
    return redirect(url_for("admin"))

@app.post("/admin/users/<int:id>/password")
def admin_password(id):
    admin_only(); get("SELECT 1 FROM users WHERE id=?", id)
    pw = request.form.get("password", "")
    if len(pw) < 8: flash("Password must be 8+ characters.")
    else:
        h = generate_password_hash(pw); run("UPDATE users SET pw=? WHERE id=?", h, id)
        if id == uid(): session["pwv"] = pwv(h)
        flash("Password updated. That user is signed out everywhere.")
    return redirect(url_for("admin"))

@app.post("/admin/users/<int:id>/admin")
def admin_toggle(id):
    admin_only(); get("SELECT 1 FROM users WHERE id=?", id)
    if id == uid(): flash("You can't change your own admin status.")
    else: run("UPDATE users SET is_admin = 1 - is_admin WHERE id=?", id)
    return redirect(url_for("admin"))

@app.post("/admin/users/<int:id>/active")
def admin_active(id):
    admin_only(); get("SELECT 1 FROM users WHERE id=?", id)
    if id == uid(): flash("You can't disable yourself.")
    else: run("UPDATE users SET active = 1 - active WHERE id=?", id)
    return redirect(url_for("admin"))

@app.post("/admin/users/<int:id>/delete")
def admin_delete(id):
    admin_only(); get("SELECT 1 FROM users WHERE id=?", id)
    if id == uid(): flash("You can't delete yourself.")
    else:
        purge_files("p.user_id=?", id); purge_photos("p.user_id=?", id); run("DELETE FROM users WHERE id=?", id)
    return redirect(url_for("admin"))

@app.post("/admin/signups")
def admin_signups():
    admin_only()
    run("INSERT INTO settings(key,value) VALUES('signups',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        "0" if signups_open() else "1")
    return redirect(url_for("admin"))

@app.post("/admin/currency")
def admin_currency():
    admin_only(); sym = "".join(ch for ch in clean("currency", 4) if ch.isprintable()) or "$"
    run("INSERT INTO settings(key,value) VALUES('currency',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", sym)
    return redirect(url_for("admin"))

@app.post("/admin/backup")
def admin_backup():
    admin_only()
    fd, tmp = tempfile.mkstemp(prefix=".download-", suffix=".zip", dir=DATA); os.close(fd)   # on the volume, not the container layer
    backup.make_zip(DATA, tmp)
    @after_this_request
    def _remove(resp):
        try: os.unlink(tmp)
        except OSError: pass
        return resp
    return send_file(tmp, as_attachment=True, download_name=f"craft-stash-backup-{backup.stamp()}.zip", mimetype="application/zip")

# ---------- self-test (runs selftest.py against a throw-away copy of the app; never touches real data) ----------
SELFTEST = {"state": "idle", "events": []}
SELFTEST_LOCK = threading.Lock()
SELFTEST_DIR = DATA / "selftest"

def _selftest_worker():
    SELFTEST_DIR.mkdir(exist_ok=True)
    try:
        p = subprocess.Popen([sys.executable, "selftest.py", "--json-lines", "--out", str(SELFTEST_DIR)], cwd=Path(__file__).parent,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in p.stdout:
            try: ev = json.loads(line)
            except ValueError: ev = {"event": "log", "text": line.strip()[:300]}
            with SELFTEST_LOCK: SELFTEST["events"].append(ev)
        p.wait(); final = "done" if p.returncode in (0, 1) else "error"      # 1 just means some checks failed
    except Exception as e:
        with SELFTEST_LOCK: SELFTEST["events"].append({"event": "log", "text": f"Could not run the self-test: {e}"})
        final = "error"
    with SELFTEST_LOCK: SELFTEST["state"] = final

@app.get("/admin/selftest")
def selftest_page():
    admin_only()
    return render_template("selftest.html", has_report=(SELFTEST_DIR / "report.html").exists())

@app.post("/admin/selftest/run")
def selftest_run():
    admin_only()
    with SELFTEST_LOCK:
        if SELFTEST["state"] == "running": flash("A self-test is already running.")
        else:
            SELFTEST.update(state="running", events=[]); threading.Thread(target=_selftest_worker, daemon=True, name="selftest").start()
    return redirect(url_for("selftest_page"))

@app.get("/admin/selftest/status")
def selftest_status():
    admin_only(); since = request.args.get("since", 0, type=int)
    with SELFTEST_LOCK: return {"state": SELFTEST["state"], "events": SELFTEST["events"][since:]}

@app.get("/admin/selftest/report")
def selftest_report():
    admin_only(); fmt = request.args.get("format", "html")
    path = SELFTEST_DIR / ("report.json" if fmt == "json" else "report.html")
    if not path.exists(): abort(404)
    return send_file(path, mimetype="application/json" if fmt == "json" else "text/html", as_attachment=(fmt == "json"), download_name="craft-stash-selftest.json")

# ---------- pools ----------
@app.get("/")
def home():
    pools = q("""SELECT p.*, a.is_owner, a.can_edit, u.username AS owner,
                        (SELECT COUNT(*) FROM stock s WHERE s.pool_id=p.id) AS n,
                        (SELECT group_concat(DISTINCT s.hex) FROM stock s WHERE s.pool_id=p.id AND s.hex<>'') AS hexes
                 FROM pools p JOIN access a ON a.pool_id=p.id JOIN users u ON u.id=p.user_id
                 WHERE a.user_id=? ORDER BY a.is_owner DESC, p.name""", uid())
    return render_template("home.html", pools=pools)

SEARCH_LIMIT = 200

@app.get("/search")
def search():
    term = request.args.get("q", "").strip()[:100]
    items, projects, more, fields = [], [], False, {}
    if term:
        fields = {t["id"]: json.loads(t["fields"]) for t in
                  q("SELECT t.id, t.fields FROM types t JOIN access a ON a.pool_id=t.pool_id WHERE a.user_id=?", uid())}
        cond, args, _ = match(term, fields)
        items = q(f"""SELECT stock.*, a.can_edit, a.is_owner, u.username AS owner FROM stock JOIN access a ON a.pool_id=stock.pool_id
                      JOIN users u ON u.id=stock.user_id WHERE a.user_id=? AND {cond}
                      ORDER BY a.is_owner DESC, stock.pname COLLATE NOCASE, stock.pool_id, stock.tname COLLATE NOCASE, stock.name COLLATE NOCASE
                      LIMIT ?""", uid(), *args, SEARCH_LIMIT + 1)
        more = len(items) > SEARCH_LIMIT; items = items[:SEARCH_LIMIT]
        me = uid()
        projects = q(f"""SELECT p.id, p.name, p.status, u.username AS owner, p.user_id FROM projects p JOIN users u ON u.id=p.user_id
                         WHERE (p.user_id=? OR p.id IN (SELECT project_id FROM project_shares WHERE user_id=?))
                           AND (p.name {LIKE} OR p.notes {LIKE}) ORDER BY p.user_id<>?, p.status='done', p.name LIMIT 50""",
                     me, me, like(term), like(term), me)
    return render_template("search.html", term=term, items=items, projects=projects, more=more, fields=fields, limit=SEARCH_LIMIT)

@app.post("/collections")
def new_pool():
    if clean("name", 80):
        run("INSERT INTO pools(user_id,name,craft) VALUES(?,?,?)", uid(), clean("name", 80), clean("craft", 80))
    return redirect(url_for("home"))

@app.post("/collections/<int:pid>/delete")
def del_pool(pid):
    pool_of(pid, owner=True); purge_photos("p.id=?", pid); run("DELETE FROM pools WHERE id=?", pid)
    return redirect(url_for("home"))

SORTS = {"name": "name COLLATE NOCASE", "colour": "colour COLLATE NOCASE, name COLLATE NOCASE",
         "low": "qty ASC, name COLLATE NOCASE", "new": "id DESC"}

def match(term, fields):
    """SQL condition (on a stock row) for a search term. fields maps type id -> field list.
    "Label:value" searches just that custom field when some type has it; otherwise every text column and field."""
    labels = [f["label"] for fl in fields.values() for f in fl]
    m = re.match(r"^([^:]{1,40}):\s*(.+)$", term)
    key = m[1].strip().lower() if m else ""
    if m and key in {l.lower() for l in labels}:   # "Width:150" filters that field
        ids = [f["id"] for fl in fields.values() for f in fl if f["label"].lower() == key]   # ids are unique per field
        args = []
        for i in ids: args += [f'$."{i}"', like(m[2].strip())]
        return "(" + " OR ".join([f"json_extract(stock.extra, ?) {LIKE}"] * len(ids)) + ")", args, key
    return (f"""(stock.name {LIKE} OR stock.colour {LIKE} OR stock.subtype {LIKE} OR stock.notes {LIKE} OR stock.location {LIKE}
                OR EXISTS(SELECT 1 FROM json_each(stock.extra) WHERE value {LIKE}))""", [like(term)] * 6, "")

@app.get("/collections/<int:pid>")
def pool(pid):
    p, term = pool_of(pid), request.args.get("q", "").strip()
    sort = request.args.get("sort", "name"); sort = sort if sort in SORTS else "name"
    types = q("SELECT * FROM types WHERE pool_id=? ORDER BY name", pid)
    fields = {t["id"]: json.loads(t["fields"]) for t in types}
    labels = [f["label"] for fl in fields.values() for f in fl]
    sql, args = "SELECT * FROM stock WHERE pool_id=?", [pid]      # one query for the whole collection, grouped below
    key = ""
    if term:
        cond, more, key = match(term, fields); sql += " AND " + cond; args += more
    keyed = bool(key)
    by_type = {}
    for r in q(sql + " ORDER BY " + SORTS[sort], *args): by_type.setdefault(r["type_id"], []).append(r)
    groups = [(t, fields[t["id"]], by_type.get(t["id"], [])) for t in types
              if not keyed or any(f["label"].lower() == key for f in fields[t["id"]])]
    shares = q("""SELECT u.id, u.username, s.can_edit FROM pool_shares s JOIN users u ON u.id=s.user_id
                  WHERE s.pool_id=? ORDER BY u.username""", pid) if p["is_owner"] else []
    return render_template("pool.html", pool=p, groups=groups, term=term, shares=shares, hint=(labels or ["Width"])[0], sort=sort, impact=pool_impact(pid) if p["is_owner"] else None,
                           value=db().execute("SELECT COALESCE(SUM(qty*price),0) FROM stock WHERE pool_id=? AND price IS NOT NULL", (pid,)).fetchone()[0],
                           hexes=[r["hex"] for r in q("SELECT DISTINCT hex FROM stock WHERE pool_id=? AND hex<>'' LIMIT 40", pid)])

@app.post("/collections/<int:pid>/share")
def share_pool(pid):
    pool_of(pid, owner=True); u = find_user()
    if not u or u["id"] == uid(): flash("No such user.")
    else:
        run("""INSERT INTO pool_shares(pool_id,user_id,can_edit) VALUES(?,?,?)
               ON CONFLICT(pool_id,user_id) DO UPDATE SET can_edit=excluded.can_edit""",
            pid, u["id"], int(request.form.get("role") == "edit"))
        if request.form.get("role") != "edit": drop_reservations(pid, u["id"])   # view-only can't reserve or use stock
    return redirect(url_for("pool", pid=pid))

@app.post("/collections/<int:pid>/unshare/<int:user>")
def unshare_pool(pid, user):
    pool_of(pid, owner=True); run("DELETE FROM pool_shares WHERE pool_id=? AND user_id=?", pid, user); drop_reservations(pid, user)
    return redirect(url_for("pool", pid=pid))

@app.post("/collections/<int:pid>/update")
def update_pool(pid):
    pool_of(pid, owner=True)
    if clean("name", 80): run("UPDATE pools SET name=?, craft=? WHERE id=?", clean("name", 80), clean("craft", 80), pid)
    return redirect(url_for("pool", pid=pid))

@app.get("/collections/<int:pid>/export.csv")
def export_csv(pid):
    p = pool_of(pid)
    types = q("SELECT * FROM types WHERE pool_id=? ORDER BY name", pid)
    labels = list(dict.fromkeys(f["label"] for t in types for f in json.loads(t["fields"])))
    safe = lambda v: ("'" + v) if isinstance(v, str) and v and v[0] in "=+-@\t\r" else v   # blocks spreadsheet formula injection
    out = io.StringIO(); w = csv.writer(out)
    w.writerow(["Material type", "Name", "Colour", "Variant", "In stock", "Unit", "Price per unit", "Low stock at", "Location", "Notes", "Swatch (hex)"] + labels)
    for t in types:
        ids = {f["label"]: f["id"] for f in json.loads(t["fields"])}
        for i in q("SELECT * FROM stock WHERE type_id=? ORDER BY name", t["id"]):
            x = json.loads(i["extra"])
            w.writerow([safe(v) for v in [t["name"], i["name"], i["colour"], i["subtype"], i["qty"], t["unit"], i["price"], i["low_at"], i["location"], i["notes"], i["hex"]]
                        + [x.get(ids[l], "") if l in ids else "" for l in labels]])
    name = re.sub(r"[^\w.-]+", "_", p["name"]) or "collection"
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})

@app.post("/collections/<int:pid>/leave")
def leave_pool(pid):
    p = pool_of(pid)
    if not p["is_owner"]:
        run("DELETE FROM pool_shares WHERE pool_id=? AND user_id=?", pid, uid()); drop_reservations(pid, uid())
    return redirect(url_for("home"))

@app.get("/collections/<int:pid>/activity")
def activity(pid):
    p = pool_of(pid)
    rows = q("""SELECT a.*, u.username, (SELECT 1 FROM items i WHERE i.id=a.item_id) AS item_exists
                FROM activity a LEFT JOIN users u ON u.id=a.user_id WHERE a.pool_id=? ORDER BY a.id DESC LIMIT 200""", pid)
    return render_template("activity.html", pool=p, rows=rows)

@app.post("/activity/<int:aid>/undo")
def undo(aid):
    a = get("SELECT * FROM activity WHERE id=?", aid); pool_of(a["pool_id"], write=True)
    it = db().execute("SELECT * FROM items WHERE id=?", (a["item_id"],)).fetchone() if a["item_id"] else None
    if a["kind"] == "adjust" and not a["undone"] and it:
        run("UPDATE items SET qty=MAX(0, qty-?) WHERE id=?", a["delta"], it["id"]); run("UPDATE activity SET undone=1 WHERE id=?", aid)
        log(a["pool_id"], "undo", "Undid: " + a["text"], it["id"])
    return redirect(url_for("activity", pid=a["pool_id"]))

@app.get("/collections/import-template.csv")
def import_template():
    return Response("\ufeff" + importer.TEMPLATE, mimetype="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="craft-stash-import-template.csv"'})

@app.route("/collections/<int:pid>/import", methods=["GET", "POST"])
def import_csv(pid):
    p = pool_of(pid, write=True); result = None
    if request.method == "POST":
        f = request.files.get("file"); raw = f.read(2_000_001) if f else b""
        if not raw: flash("Choose a CSV file first.")
        elif len(raw) > 2_000_000: flash("That file is over 2 MB.")
        else:
            try: text = raw.decode("utf-8-sig")
            except UnicodeDecodeError: text = raw.decode("cp1252", errors="replace")
            dry, d = bool(request.form.get("dry")), db()
            try:
                result = importer.run_import(d, pid, text, request.form.get("mode") != "add", clean("default_type", 60) or "Imported")
                result["dry"] = dry
                if dry: d.rollback()
                else:
                    log(pid, "import", f"Imported from CSV: {result['added']} added, {result['updated']} updated", commit=False); d.commit()
            except ValueError as e:
                d.rollback(); flash(str(e))
    return render_template("import.html", pool=p, types=q("SELECT name FROM types WHERE pool_id=? ORDER BY name", pid), result=result)

# ---------- material types & custom fields ----------
def new_field(label, typ="text", options=()):
    return {"id": "f" + secrets.token_hex(3), "label": label, "type": typ, "options": list(options)}

def field_form():
    typ = request.form.get("type")
    opts = [o.strip()[:60] for o in request.form.get("options", "").split(",") if o.strip()][:30]
    return clean("label", 40), (typ if typ in ("text", "number", "choice") else "text"), opts

def label_taken(fl, label, skip=None):
    return any(f["label"].lower() == label.lower() and f["id"] != skip for f in fl)

def save_fields(tid, fl): run("UPDATE types SET fields=? WHERE id=?", json.dumps(fl), tid)

@app.post("/collections/<int:pid>/types")
def new_type(pid):
    pool_of(pid, write=True)
    labels = list(dict.fromkeys(f.strip()[:40] for f in re.split(r"[,\n]", request.form.get("fields", "")) if f.strip()))[:12]
    if clean("name", 60):
        c = run("INSERT INTO types(pool_id,name,unit,fields) VALUES(?,?,?,?)", pid, clean("name", 60),
                clean("unit", 20) or "pcs", json.dumps([new_field(l) for l in labels]))
        return redirect(url_for("type_fields", tid=c.lastrowid))
    return redirect(url_for("pool", pid=pid))

@app.post("/types/<int:tid>/delete")
def del_type(tid):
    t = type_of(tid, True); purge_photos("t.id=?", tid); run("DELETE FROM types WHERE id=?", tid)
    return redirect(url_for("pool", pid=t["pool_id"]))

@app.get("/types/<int:tid>/fields")
def type_fields(tid):
    t = type_of(tid, True)
    return render_template("type_fields.html", t=t, fields=json.loads(t["fields"]), impact=type_impact(tid))

@app.post("/types/<int:tid>/update")
def update_type(tid):
    type_of(tid, True)
    if clean("name", 60): run("UPDATE types SET name=?, unit=? WHERE id=?", clean("name", 60), clean("unit", 20) or "pcs", tid)
    return redirect(url_for("type_fields", tid=tid))

@app.post("/types/<int:tid>/fields")
def add_field(tid):
    t = type_of(tid, True); fl = json.loads(t["fields"]); label, typ, opts = field_form()
    if not label: flash("Give the field a label.")
    elif label_taken(fl, label): flash("A field with that label already exists.")
    elif len(fl) >= 20: flash("Maximum 20 fields per type.")
    else: fl.append(new_field(label, typ, opts)); save_fields(tid, fl)
    return redirect(url_for("type_fields", tid=tid))

@app.post("/types/<int:tid>/fields/<fid>/update")
def update_field(tid, fid):
    t = type_of(tid, True); fl = json.loads(t["fields"]); label, typ, opts = field_form()
    f = next((f for f in fl if f["id"] == fid), None) or abort(404)
    if not label or label_taken(fl, label, fid): flash("Label is empty or already used by another field.")
    else: f.update(label=label, type=typ, options=opts); save_fields(tid, fl)
    return redirect(url_for("type_fields", tid=tid))

@app.post("/types/<int:tid>/fields/<fid>/delete")
def delete_field(tid, fid):
    t = type_of(tid, True)
    if not re.fullmatch(r"f\w{1,8}", fid): abort(404)
    d = db(); d.execute("UPDATE types SET fields=? WHERE id=?",
                        (json.dumps([f for f in json.loads(t["fields"]) if f["id"] != fid]), tid))
    d.execute("UPDATE items SET extra=json_remove(extra, ?) WHERE type_id=?", (f'$."{fid}"', tid)); d.commit()
    return redirect(url_for("type_fields", tid=tid))

# ---------- items ----------
def save_item(t, item_id=None):
    extra = {}
    for f in json.loads(t["fields"]):
        v = (request.form.get("x_" + f["id"]) or "").strip()[:200]
        if f["type"] == "number" and v:
            try: x = float(v); v = f"{x:g}" if math.isfinite(x) else ""
            except ValueError: v = ""
        extra[f["id"]] = v
    hexv = request.form.get("hex", "") if request.form.get("has_hex") else ""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", hexv): hexv = ""
    price, low = request.form.get("price", "").strip(), request.form.get("low", "").strip()
    vals = (clean("name") or "Unnamed", clean("colour", 60), hexv, clean("subtype", 60), num(request.form.get("qty")),
            num(price) if price else None, num(low) if low else None, clean("location", 80), clean("notes", 1000), json.dumps(extra))
    old = db().execute("SELECT qty, photo FROM items WHERE id=?", (item_id,)).fetchone() if item_id else None
    if item_id:
        run("UPDATE items SET name=?,colour=?,hex=?,subtype=?,qty=?,price=?,low_at=?,location=?,notes=?,extra=? WHERE id=?", *vals, item_id)
    else:
        item_id = run("INSERT INTO items(name,colour,hex,subtype,qty,price,low_at,location,notes,extra,type_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", *vals, t["id"]).lastrowid
    up = request.files.get("photo")
    if up and up.filename:
        name = save_photo(up)
        if name:
            if old and old["photo"]: (PHOTOS / old["photo"]).unlink(missing_ok=True)
            run("UPDATE items SET photo=? WHERE id=?", name, item_id)
        else: flash("That photo couldn't be read. Use a JPEG, PNG, WebP or GIF image.")
    elif request.form.get("remove_photo") and old and old["photo"]:
        (PHOTOS / old["photo"]).unlink(missing_ok=True); run("UPDATE items SET photo='' WHERE id=?", item_id)
    if old is None: log(t["pool_id"], "add", f"Added {vals[0]} ({t['name']})", item_id)
    else:
        moved = f", stock {old['qty']:g} to {vals[4]:g}" if abs(old["qty"] - vals[4]) > 1e-9 else ""
        log(t["pool_id"], "edit", f"Edited {vals[0]}{moved}", item_id)
    return redirect(url_for("pool", pid=t["pool_id"]))

@app.route("/types/<int:tid>/items/new", methods=["GET", "POST"])
def item_new(tid):
    t = type_of(tid, True)
    if request.method == "POST": return save_item(t)
    cid = request.args.get("copy", type=int)
    proto = item_of(cid, True) if cid else None            # "Duplicate": prefill from an existing item
    if proto and proto["type_id"] != tid: proto = None
    return render_template("item_form.html", t=t, fields=json.loads(t["fields"]), i=None, proto=proto,
                           extra=json.loads(proto["extra"]) if proto else {})

@app.route("/items/<int:iid>/edit", methods=["GET", "POST"])
def item_edit(iid):
    i = item_of(iid, True); t = type_of(i["type_id"], True)
    if request.method == "POST": return save_item(t, iid)
    return render_template("item_form.html", t=t, fields=json.loads(t["fields"]), i=i, proto=None, extra=json.loads(i["extra"]),
                           used_in=db().execute("SELECT COUNT(*) FROM assignments WHERE item_id=?", (iid,)).fetchone()[0])

@app.post("/items/<int:iid>/delete")
def item_del(iid):
    i = item_of(iid, True); purge_photos("i.id=?", iid); run("DELETE FROM items WHERE id=?", iid); log(i["pool_id"], "delete", f"Deleted {i['name']}")
    return redirect(url_for("pool", pid=i["pool_id"]))

def safe_next(default):
    n = request.form.get("next", "")
    return n if n.startswith("/") and not n.startswith("//") and "\\" not in n else default

@app.post("/items/<int:iid>/adjust")
def item_adjust(iid):
    i = item_of(iid, True); n = num(request.form.get("amount"))
    if n > 0:
        add = request.form.get("action") == "add"; new = max(0.0, i["qty"] + (n if add else -n))
        run("UPDATE items SET qty=? WHERE id=?", new, iid)
        log(i["pool_id"], "adjust", f"{'Added' if add else 'Used'} {n:g} {i['unit']} of {i['name']} ({i['qty']:g} to {new:g})", iid, new - i["qty"])
    return redirect(safe_next(url_for("pool", pid=i["pool_id"])))

@app.get("/photos/<int:iid>")
def photo(iid):
    i = item_of(iid)
    if not i["photo"]: abort(404)
    r = send_from_directory(PHOTOS, i["photo"], mimetype="image/jpeg"); r.headers["Cache-Control"] = "private, max-age=3600"
    return r

@app.get("/shopping")
def shopping():
    rows = q(f"""SELECT s.*, a.can_edit FROM stock s JOIN access a ON a.pool_id=s.pool_id
                 WHERE a.user_id=? AND {LOW} ORDER BY s.pname, s.tname, s.name""", uid())
    return render_template("shopping.html", rows=rows, suggest=suggest)

def suggest(s):
    """How much to buy so an item is no longer on the shopping list (at least 1 when it's simply out)."""
    need = max(-s["free"], (s["low_at"] - s["qty"]) if s["low_at"] is not None else 0, 0)
    if s["low_at"] is not None and s["qty"] + need <= s["low_at"]: need += 1   # "at the level" still counts as low
    return need or 1

@app.post("/shopping/restock")
def restock():
    """Add every amount entered on the shopping list in one go."""
    done = skipped = 0
    for k, v in request.form.items():
        m = re.fullmatch(r"bought_(\d+)", k); n = num(v)
        if not m or n <= 0: continue
        i = db().execute("""SELECT s.* FROM stock s JOIN access a ON a.pool_id=s.pool_id
                            WHERE s.id=? AND a.user_id=? AND a.can_edit=1""", (int(m[1]), uid())).fetchone()
        if not i: skipped += 1; continue
        new = i["qty"] + n
        db().execute("UPDATE items SET qty=? WHERE id=?", (new, i["id"]))
        log(i["pool_id"], "adjust", f"Added {n:g} {i['unit']} of {i['name']} ({i['qty']:g} to {new:g})", i["id"], n, commit=False)
        done += 1
    db().commit()
    flash(f"Restocked {done} item{'s' if done != 1 else ''}." if done else "Enter how much you bought next to at least one item.")
    if skipped: flash(f"{skipped} item(s) were skipped: they're gone or you can no longer edit them.")
    return redirect(url_for("shopping"))

@app.route("/appearance", methods=["GET", "POST"])
def appearance():
    if request.method == "POST":
        if request.form.get("reset"): run("UPDATE users SET theme='' WHERE id=?", uid()); flash("Appearance reset to the default.")
        else: run("UPDATE users SET theme=? WHERE id=?", json.dumps(themes.from_form(request.form)), uid()); flash("Appearance saved.")
        return redirect(url_for("appearance"))
    return render_template("appearance.html", cur=themes.load(g.user["theme"]), themes=themes.preset_list(),
                           radii=list(themes.RADII), default=themes.DEFAULT_CUSTOM)

@app.route("/account", methods=["GET", "POST"])
def account():
    if request.method == "POST":
        cur, new = request.form.get("current", ""), request.form.get("new", "")
        if not check_password_hash(g.user["pw"], cur): flash("Your current password is wrong.")
        elif len(new) < 8: flash("The new password needs 8 or more characters.")
        elif new != request.form.get("confirm", ""): flash("The new passwords don't match.")
        else:
            h = generate_password_hash(new); run("UPDATE users SET pw=? WHERE id=?", h, uid()); session["pwv"] = pwv(h)
            flash("Password updated. Other devices have been signed out.")
        return redirect(url_for("account"))
    return render_template("account.html")

# ---------- projects ----------
@app.get("/projects")
def projects():
    me = uid()
    rows = q("""SELECT p.*, u.username AS owner,
                (SELECT group_concat(DISTINCT s.hex) FROM assignments a JOIN stock s ON s.id=a.item_id WHERE a.project_id=p.id AND s.hex<>'') AS hexes,
                (SELECT COUNT(*) FROM assignments WHERE project_id=p.id) AS n_items,
                (SELECT COUNT(*) FROM steps WHERE project_id=p.id) AS n_steps,
                (SELECT COUNT(*) FROM files WHERE project_id=p.id) AS n_files
                FROM projects p JOIN users u ON u.id=p.user_id
                WHERE p.user_id=? OR p.id IN (SELECT project_id FROM project_shares WHERE user_id=?)
                ORDER BY p.user_id<>?, p.status='template', p.status='done', p.name""", me, me, me)
    return render_template("projects.html", rows=rows)

@app.post("/projects")
def new_project():
    c = run("INSERT INTO projects(user_id,name) VALUES(?,?)", uid(), clean("name", 100) or "Untitled project")
    return redirect(url_for("project", id=c.lastrowid))

@app.get("/projects/<int:id>")
def project(id):
    pr = project_of(id); mine = pr["user_id"] == uid()
    assigned = q("""SELECT a.id, a.qty, a.consumed, a.used, s.name, s.colour, s.hex, s.subtype, s.tname, s.unit, s.pname, s.price, s.free
                    FROM assignments a JOIN stock s ON s.id=a.item_id
                    WHERE a.project_id=? ORDER BY s.pname, s.tname, s.name""", id)
    avail = q("""SELECT s.* FROM stock s JOIN access a ON a.pool_id=s.pool_id
                 WHERE a.user_id=? AND a.can_edit=1 ORDER BY s.pname, s.tname, s.name""", uid()) if mine else []
    shares = q("""SELECT u.id, u.username FROM project_shares s JOIN users u ON u.id=s.user_id
                  WHERE s.project_id=? ORDER BY u.username""", id) if mine else []
    return render_template("project.html", p=pr, mine=mine, assigned=assigned, avail=avail, shares=shares,
                           hexes=list(dict.fromkeys(a["hex"] for a in assigned if a["hex"])),
                           cost=sum(a["qty"] * a["price"] for a in assigned if a["price"] is not None),
                           steps=q("SELECT * FROM steps WHERE project_id=? ORDER BY pos", id),
                           files=q("SELECT * FROM files WHERE project_id=? ORDER BY name", id))

@app.post("/projects/<int:id>/update")
def project_update(id):
    pr = project_of(id, True)
    status = request.form.get("status")
    if pr["status"] in ("done", "template") or status not in ("planned", "active"): status = pr["status"]
    run("UPDATE projects SET name=?, status=?, notes=? WHERE id=?",
        clean("name", 100) or pr["name"], status, clean("notes", 5000), id)
    return redirect(url_for("project", id=id))

@app.post("/projects/<int:id>/assign")
def assign(id):
    pr = project_of(id, True)
    it = item_of(request.form.get("item_id", type=int), True)
    n = num(request.form.get("qty"))
    if pr["status"] == "done": abort(400)
    if n <= 0: flash("Enter an amount above zero.")
    else:
        short = n - max(it["free"], 0)       # more than is free is allowed: the gap lands on the shopping list
        run("""INSERT INTO assignments(project_id,item_id,qty) VALUES(?,?,?)
               ON CONFLICT(project_id,item_id) DO UPDATE SET qty=qty+excluded.qty""", id, it["id"], n)
        if short > 1e-9 and pr["status"] != "template": flash(f"That's {short:g} {it['unit']} more than you have free. It's on your shopping list.")
    return redirect(url_for("project", id=id))

@app.post("/assignments/<int:aid>/delete")
def unassign(aid):
    a = get("SELECT * FROM assignments WHERE id=? AND consumed=0", aid); project_of(a["project_id"], True)
    run("DELETE FROM assignments WHERE id=?", aid)
    return redirect(url_for("project", id=a["project_id"]))

@app.post("/projects/<int:id>/complete")
def complete(id):
    pr = project_of(id, True)
    if pr["status"] not in ("done", "template"):    # a template is never completed, only started from
        d = db()
        # reservations on collections the owner can no longer edit are stale: drop them instead of using that stock
        d.execute(f"""DELETE FROM assignments WHERE project_id=? AND consumed=0 AND item_id IN
                      (SELECT s.id FROM stock s WHERE NOT {CAN_EDIT})""", (id, pr["user_id"]))
        keep = request.form.get("stock") == "keep"   # "Complete, keep stock": close the project, leave quantities alone
        capped = 0
        for a in q("""SELECT a.id, a.item_id, a.qty, s.qty AS have, s.name, s.unit, s.pool_id FROM assignments a
                      JOIN stock s ON s.id=a.item_id WHERE a.project_id=? AND a.consumed=0""", id):
            want = 0 if keep else num(request.form.get(f"used_{a['id']}"), a["qty"])   # the amount actually used, if given
            used = min(want, a["have"])                        # used=0 also means reopening puts nothing back
            if used < want - 1e-9: capped += 1
            if used: d.execute("UPDATE items SET qty=qty-? WHERE id=?", (used, a["item_id"]))
            d.execute("UPDATE assignments SET consumed=1, used=? WHERE id=?", (used, a["id"]))
            if used: log(a["pool_id"], "project", f"{pr['name']}: used {used:g} {a['unit']} of {a['name']}", a["item_id"], -used, commit=False)
        d.execute("UPDATE projects SET status='done' WHERE id=?", (id,)); d.commit()
        if keep: flash("Project marked done. Your stock was left as it was.")
        if capped: flash(f"{capped} material(s) had less in stock than you entered, so only what was there was deducted.")
    return redirect(url_for("project", id=id))

@app.get("/projects/<int:id>/finish")
def finish(id):
    pr = project_of(id, True)
    if pr["status"] in ("done", "template"): return redirect(url_for("project", id=id))
    rows = q("""SELECT a.id, a.qty, s.name, s.colour, s.hex, s.tname, s.unit, s.qty AS have FROM assignments a
                JOIN stock s ON s.id=a.item_id WHERE a.project_id=? AND a.consumed=0 ORDER BY s.pname, s.tname, s.name""", id)
    return render_template("finish.html", p=pr, rows=rows)

@app.post("/projects/<int:id>/reopen")
def reopen(id):
    pr = project_of(id, True)
    if pr["status"] == "done":
        d = db(); kept = 0
        for a in q(f"""SELECT a.id, a.item_id, a.used, s.name, s.unit, s.pool_id, {CAN_EDIT} AS can_edit FROM assignments a
                       JOIN stock s ON s.id=a.item_id WHERE a.project_id=? AND a.consumed=1""", pr["user_id"], id):
            if not a["can_edit"]: kept += 1; continue     # no longer allowed to change that collection: leave it as used
            d.execute("UPDATE items SET qty=qty+? WHERE id=?", (a["used"], a["item_id"]))
            d.execute("UPDATE assignments SET consumed=0, used=0 WHERE id=?", (a["id"],))
            if a["used"]: log(a["pool_id"], "project", f"{pr['name']} reopened: returned {a['used']:g} {a['unit']} of {a['name']}", a["item_id"], a["used"], commit=False)
        d.execute("UPDATE projects SET status='active' WHERE id=?", (id,)); d.commit()
        if kept: flash(f"{kept} material(s) stayed used: you can no longer edit the collection they came from.")
    return redirect(url_for("project", id=id))

@app.post("/projects/<int:id>/copy")
def copy_project(id):
    pr = project_of(id); mine = pr["user_id"] == uid(); d = db(); notes = pr["notes"]
    to_template = request.form.get("as") == "template"    # "Save as template"; copying a template starts a project
    name = pr["name"] + " (copy)" if mine and not to_template and pr["status"] != "template" else pr["name"]
    mats = q("SELECT a.qty, s.name, s.colour, s.unit FROM assignments a JOIN stock s ON s.id=a.item_id WHERE a.project_id=? ORDER BY s.name", id)
    if not mine and mats:    # the new owner can't use someone else's stock, so the materials become a written list
        lines = [f"- {a['qty']:g} {a['unit']} {a['name']}" + (f" ({a['colour']})" if a["colour"] else "") for a in mats]
        notes = (notes + "\n\n" if notes else "") + "Materials from the original:\n" + "\n".join(lines)
    nid = d.execute("INSERT INTO projects(user_id,name,notes,status) VALUES(?,?,?,?)",
                    (uid(), name[:100], notes[:5000], "template" if to_template else "planned")).lastrowid
    d.execute("INSERT INTO steps(project_id,pos,title,body) SELECT ?,pos,title,body FROM steps WHERE project_id=?", (nid, id))
    if mine: d.execute(f"""INSERT INTO assignments(project_id,item_id,qty) SELECT ?, a.item_id, a.qty FROM assignments a
                           JOIN stock s ON s.id=a.item_id WHERE a.project_id=? AND {CAN_EDIT}""", (nid, id, uid()))
    over = missing = 0; copied = []
    try:
        for f in q("SELECT * FROM files WHERE project_id=?", id):
            if QUOTA and used_bytes(uid()) + f["size"] > QUOTA: over += 1; continue
            stored = uuid.uuid4().hex + "." + f["stored"].rsplit(".", 1)[-1]
            try: shutil.copyfile(UPLOADS / f["stored"], UPLOADS / stored)
            except FileNotFoundError: missing += 1; continue
            copied.append(stored)
            d.execute("INSERT INTO files(project_id,name,stored,size) VALUES(?,?,?,?)", (nid, f["name"], stored, f["size"]))
        d.commit()                                   # one commit: the copy appears whole or not at all
    except Exception:
        d.rollback()
        for s in copied: (UPLOADS / s).unlink(missing_ok=True)
        raise
    if over: flash("Some files weren't copied because you're over your storage limit.")
    if missing: flash(f"{missing} file(s) were missing from storage and weren't copied.")
    if to_template: flash("Saved as a template. Its materials are a list only: they don't hold any stock until you start a project from it.")
    elif pr["status"] == "template": flash("Project started from the template. Its materials are now reserved.")
    return redirect(url_for("project", id=nid))

@app.post("/projects/<int:id>/leave")
def leave_project(id):
    pr = project_of(id)
    if pr["user_id"] != uid(): run("DELETE FROM project_shares WHERE project_id=? AND user_id=?", id, uid())
    return redirect(url_for("projects"))

@app.post("/projects/<int:id>/delete")
def del_project(id):
    project_of(id, True); purge_files("p.id=?", id); run("DELETE FROM projects WHERE id=?", id)
    return redirect(url_for("projects"))

@app.post("/projects/<int:id>/share")
def share_project(id):
    project_of(id, True); u = find_user()
    if not u or u["id"] == uid(): flash("No such user.")
    else: run("INSERT OR IGNORE INTO project_shares(project_id,user_id) VALUES(?,?)", id, u["id"])
    return redirect(url_for("project", id=id))

@app.post("/projects/<int:id>/unshare/<int:user>")
def unshare_project(id, user):
    project_of(id, True); run("DELETE FROM project_shares WHERE project_id=? AND user_id=?", id, user)
    return redirect(url_for("project", id=id))

# ---------- step-by-step ----------
@app.post("/projects/<int:id>/steps")
def add_step(id):
    project_of(id, True)
    if clean("title", 100) or clean("body", 3000):
        run("""INSERT INTO steps(project_id,pos,title,body)
               VALUES(?, COALESCE((SELECT MAX(pos) FROM steps WHERE project_id=?),0)+1, ?, ?)""",
            id, id, clean("title", 100), clean("body", 3000))
    return redirect(url_for("project", id=id) + "#steps")

@app.post("/steps/<int:sid>/update")
def update_step(sid):
    s = step_of(sid)
    run("UPDATE steps SET title=?, body=? WHERE id=?", clean("title", 100), clean("body", 3000), sid)
    return redirect(url_for("project", id=s["project_id"]) + "#steps")

@app.post("/steps/<int:sid>/delete")
def delete_step(sid):
    s = step_of(sid); run("DELETE FROM steps WHERE id=?", sid)
    return redirect(url_for("project", id=s["project_id"]) + "#steps")

@app.post("/steps/<int:sid>/move")
def move_step(sid):
    s = step_of(sid); up = request.form.get("dir") == "up"
    n = db().execute(f"SELECT * FROM steps WHERE project_id=? AND pos{'<' if up else '>'}? ORDER BY pos {'DESC' if up else 'ASC'} LIMIT 1",
                     (s["project_id"], s["pos"])).fetchone()
    if n:
        d = db(); d.execute("UPDATE steps SET pos=? WHERE id=?", (n["pos"], s["id"]))
        d.execute("UPDATE steps SET pos=? WHERE id=?", (s["pos"], n["id"])); d.commit()
    return redirect(url_for("project", id=s["project_id"]) + "#steps")

# ---------- attachments ----------
@app.post("/projects/<int:id>/upload")
def upload(id):
    project_of(id, True)
    f = request.files.get("file")
    name = secure_filename(f.filename) if f and f.filename else ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED:
        flash("File type not allowed.")
    else:
        stored = f"{uuid.uuid4().hex}.{ext}"
        f.save(UPLOADS / stored)
        size = (UPLOADS / stored).stat().st_size
        if QUOTA and used_bytes(uid()) + size > QUOTA:
            (UPLOADS / stored).unlink(); flash(f"That would go over your {QUOTA // 1048576} MB storage limit.")
        else: run("INSERT INTO files(project_id,name,stored,size) VALUES(?,?,?,?)", id, name, stored, size)
    return redirect(url_for("project", id=id))

@app.get("/files/<int:fid>")
def file(fid):
    f = file_of(fid)
    ext = f["stored"].rsplit(".", 1)[-1]
    return send_from_directory(UPLOADS, f["stored"], download_name=f["name"], as_attachment=ext not in INLINE)

@app.post("/files/<int:fid>/delete")
def del_file(fid):
    f = file_of(fid, True)
    (UPLOADS / f["stored"]).unlink(missing_ok=True)
    run("DELETE FROM files WHERE id=?", fid)
    return redirect(url_for("project", id=f["project_id"]))
