import json, math, os, re, secrets, sqlite3, uuid
from pathlib import Path
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

DATA = Path(os.environ.get("DATA_DIR", "/data"))
UPLOADS = DATA / "uploads"
UPLOADS.mkdir(parents=True, exist_ok=True)
ALLOWED = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt", "md", "doc", "docx", "odt", "xls", "xlsx", "zip"}
INLINE = {"pdf", "png", "jpg", "jpeg", "gif", "webp", "txt"}  # everything else downloads

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

with sqlite3.connect(DATA / "craft.db") as _c:
    _c.execute("PRAGMA journal_mode=WAL")
    _c.executescript((Path(__file__).parent / "schema.sql").read_text())

def num(v, default=0.0):
    try: x = float(v)
    except (TypeError, ValueError): return default
    return x if math.isfinite(x) and x >= 0 else default

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
        g.user = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    if request.method == "POST" and not secrets.compare_digest(
            request.form.get("_csrf", "").encode(), session["csrf"].encode()):
        abort(400)
    if not g.user and request.endpoint not in ("login", "register"):
        return redirect(url_for("login"))

@app.after_request
def headers(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["Referrer-Policy"] = "same-origin"
    return r

@app.context_processor
def ctx():
    return {"csrf": Markup(f'<input type="hidden" name="_csrf" value="{session.get("csrf", "")}">')}

@app.get("/healthz")
def healthz(): return "ok"

# ---------- auth ----------
def no_users(): return db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = db().execute("SELECT * FROM users WHERE username=?", (clean("username", 40),)).fetchone()
        if u and check_password_hash(u["pw"], request.form.get("password", "")):
            session.clear(); session["uid"] = u["id"]
            return redirect(url_for("home"))
        flash("Wrong username or password.")
    return render_template("auth.html", mode="login", can_register=no_users() or signups_open())

@app.route("/register", methods=["GET", "POST"])
def register():
    first = no_users()
    if not first and not signups_open(): abort(403)
    if request.method == "POST":
        name, pw = clean("username", 40), request.form.get("password", "")
        if valid_account(name, pw):
            try:
                c = run("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", name, generate_password_hash(pw), int(first))
            except sqlite3.IntegrityError:
                flash("That username is taken.")
            else:
                session.clear(); session["uid"] = c.lastrowid
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
                 (SELECT COUNT(*) FROM projects WHERE user_id=u.id) AS projects FROM users u ORDER BY username""")
    return render_template("admin.html", users=users, signups=signups_open())

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
    else: run("UPDATE users SET pw=? WHERE id=?", generate_password_hash(pw), id); flash("Password updated.")
    return redirect(url_for("admin"))

@app.post("/admin/users/<int:id>/admin")
def admin_toggle(id):
    admin_only(); get("SELECT 1 FROM users WHERE id=?", id)
    if id == uid(): flash("You can't change your own admin status.")
    else: run("UPDATE users SET is_admin = 1 - is_admin WHERE id=?", id)
    return redirect(url_for("admin"))

@app.post("/admin/users/<int:id>/delete")
def admin_delete(id):
    admin_only(); get("SELECT 1 FROM users WHERE id=?", id)
    if id == uid(): flash("You can't delete yourself.")
    else:
        purge_files("p.user_id=?", id); run("DELETE FROM users WHERE id=?", id)
    return redirect(url_for("admin"))

@app.post("/admin/signups")
def admin_signups():
    admin_only()
    run("INSERT INTO settings(key,value) VALUES('signups',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        "0" if signups_open() else "1")
    return redirect(url_for("admin"))

# ---------- pools ----------
@app.get("/")
def home():
    pools = q("""SELECT p.*, a.is_owner, a.can_edit, u.username AS owner,
                        (SELECT COUNT(*) FROM stock s WHERE s.pool_id=p.id) AS n
                 FROM pools p JOIN access a ON a.pool_id=p.id JOIN users u ON u.id=p.user_id
                 WHERE a.user_id=? ORDER BY a.is_owner DESC, p.name""", uid())
    return render_template("home.html", pools=pools)

@app.post("/pools")
def new_pool():
    if clean("name", 80):
        run("INSERT INTO pools(user_id,name,craft) VALUES(?,?,?)", uid(), clean("name", 80), clean("craft", 80))
    return redirect(url_for("home"))

@app.post("/pools/<int:pid>/delete")
def del_pool(pid):
    pool_of(pid, owner=True); run("DELETE FROM pools WHERE id=?", pid)
    return redirect(url_for("home"))

@app.get("/pools/<int:pid>")
def pool(pid):
    p, term, groups = pool_of(pid), request.args.get("q", "").strip(), []
    for t in q("SELECT * FROM types WHERE pool_id=? ORDER BY name", pid):
        sql, args = "SELECT * FROM stock WHERE type_id=?", [t["id"]]
        if term:
            sql += " AND (name LIKE ? OR colour LIKE ? OR subtype LIKE ? OR notes LIKE ? OR extra LIKE ?)"
            args += [f"%{term}%"] * 5
        groups.append((t, q(sql + " ORDER BY name", *args)))
    shares = q("""SELECT u.id, u.username, s.can_edit FROM pool_shares s JOIN users u ON u.id=s.user_id
                  WHERE s.pool_id=? ORDER BY u.username""", pid) if p["is_owner"] else []
    return render_template("pool.html", pool=p, groups=groups, term=term, shares=shares)

@app.post("/pools/<int:pid>/share")
def share_pool(pid):
    pool_of(pid, owner=True); u = find_user()
    if not u or u["id"] == uid(): flash("No such user.")
    else:
        run("""INSERT INTO pool_shares(pool_id,user_id,can_edit) VALUES(?,?,?)
               ON CONFLICT(pool_id,user_id) DO UPDATE SET can_edit=excluded.can_edit""",
            pid, u["id"], int(request.form.get("role") == "edit"))
    return redirect(url_for("pool", pid=pid))

@app.post("/pools/<int:pid>/unshare/<int:user>")
def unshare_pool(pid, user):
    pool_of(pid, owner=True); run("DELETE FROM pool_shares WHERE pool_id=? AND user_id=?", pid, user)
    return redirect(url_for("pool", pid=pid))

# ---------- material types ----------
@app.post("/pools/<int:pid>/types")
def new_type(pid):
    pool_of(pid, write=True)
    fields = [f.strip()[:40] for f in re.split(r"[,\n]", request.form.get("fields", "")) if f.strip()][:12]
    if clean("name", 60):
        run("INSERT INTO types(pool_id,name,unit,fields) VALUES(?,?,?,?)",
            pid, clean("name", 60), clean("unit", 20) or "pcs", json.dumps(list(dict.fromkeys(fields))))
    return redirect(url_for("pool", pid=pid))

@app.post("/types/<int:tid>/delete")
def del_type(tid):
    t = type_of(tid, True); run("DELETE FROM types WHERE id=?", tid)
    return redirect(url_for("pool", pid=t["pool_id"]))

# ---------- items ----------
def save_item(t, item_id=None):
    extra = {f: (request.form.get(f"x{n}") or "").strip()[:200] for n, f in enumerate(json.loads(t["fields"]))}
    hexv = request.form.get("hex", "") if request.form.get("has_hex") else ""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", hexv): hexv = ""
    price = request.form.get("price", "").strip()
    vals = (clean("name") or "Unnamed", clean("colour", 60), hexv, clean("subtype", 60),
            num(request.form.get("qty")), num(price) if price else None,
            clean("location", 80), clean("notes", 1000), json.dumps(extra))
    if item_id:
        run("UPDATE items SET name=?,colour=?,hex=?,subtype=?,qty=?,price=?,location=?,notes=?,extra=? WHERE id=?", *vals, item_id)
    else:
        run("INSERT INTO items(name,colour,hex,subtype,qty,price,location,notes,extra,type_id) VALUES(?,?,?,?,?,?,?,?,?,?)", *vals, t["id"])
    return redirect(url_for("pool", pid=t["pool_id"]))

@app.route("/types/<int:tid>/items/new", methods=["GET", "POST"])
def item_new(tid):
    t = type_of(tid, True)
    if request.method == "POST": return save_item(t)
    return render_template("item_form.html", t=t, fields=json.loads(t["fields"]), i=None, extra={})

@app.route("/items/<int:iid>/edit", methods=["GET", "POST"])
def item_edit(iid):
    i = item_of(iid, True); t = type_of(i["type_id"], True)
    if request.method == "POST": return save_item(t, iid)
    return render_template("item_form.html", t=t, fields=json.loads(t["fields"]), i=i, extra=json.loads(i["extra"]))

@app.post("/items/<int:iid>/delete")
def item_del(iid):
    i = item_of(iid, True); run("DELETE FROM items WHERE id=?", iid)
    return redirect(url_for("pool", pid=i["pool_id"]))

# ---------- projects ----------
@app.get("/projects")
def projects():
    me = uid()
    rows = q("""SELECT p.*, u.username AS owner FROM projects p JOIN users u ON u.id=p.user_id
                WHERE p.user_id=? OR p.id IN (SELECT project_id FROM project_shares WHERE user_id=?)
                ORDER BY p.user_id<>?, p.status='done', p.name""", me, me, me)
    return render_template("projects.html", rows=rows)

@app.post("/projects")
def new_project():
    c = run("INSERT INTO projects(user_id,name) VALUES(?,?)", uid(), clean("name", 100) or "Untitled project")
    return redirect(url_for("project", id=c.lastrowid))

@app.get("/projects/<int:id>")
def project(id):
    pr = project_of(id); mine = pr["user_id"] == uid()
    assigned = q("""SELECT a.id, a.qty, a.consumed, s.name, s.colour, s.hex, s.subtype, s.tname, s.unit, s.pname
                    FROM assignments a JOIN stock s ON s.id=a.item_id
                    WHERE a.project_id=? ORDER BY s.pname, s.tname, s.name""", id)
    avail = q("""SELECT s.* FROM stock s JOIN access a ON a.pool_id=s.pool_id
                 WHERE a.user_id=? AND a.can_edit=1 AND s.free>0 ORDER BY s.pname, s.tname, s.name""", uid()) if mine else []
    shares = q("""SELECT u.id, u.username FROM project_shares s JOIN users u ON u.id=s.user_id
                  WHERE s.project_id=? ORDER BY u.username""", id) if mine else []
    return render_template("project.html", p=pr, mine=mine, assigned=assigned, avail=avail, shares=shares,
                           steps=q("SELECT * FROM steps WHERE project_id=? ORDER BY pos", id),
                           files=q("SELECT * FROM files WHERE project_id=? ORDER BY name", id))

@app.post("/projects/<int:id>/update")
def project_update(id):
    pr = project_of(id, True)
    status = request.form.get("status")
    if pr["status"] == "done" or status not in ("planned", "active"): status = pr["status"]
    run("UPDATE projects SET name=?, status=?, notes=? WHERE id=?",
        clean("name", 100) or pr["name"], status, clean("notes", 5000), id)
    return redirect(url_for("project", id=id))

@app.post("/projects/<int:id>/assign")
def assign(id):
    pr = project_of(id, True)
    it = item_of(request.form.get("item_id", type=int), True)
    n = num(request.form.get("qty"))
    if pr["status"] == "done": abort(400)
    if n <= 0 or n > it["free"] + 1e-9:
        flash(f"Only {it['free']:g} {it['unit']} of {it['name']} is free.")
    else:
        run("""INSERT INTO assignments(project_id,item_id,qty) VALUES(?,?,?)
               ON CONFLICT(project_id,item_id) DO UPDATE SET qty=qty+excluded.qty""", id, it["id"], n)
    return redirect(url_for("project", id=id))

@app.post("/assignments/<int:aid>/delete")
def unassign(aid):
    a = get("SELECT * FROM assignments WHERE id=? AND consumed=0", aid); project_of(a["project_id"], True)
    run("DELETE FROM assignments WHERE id=?", aid)
    return redirect(url_for("project", id=a["project_id"]))

@app.post("/projects/<int:id>/complete")
def complete(id):
    project_of(id, True); d = db()
    d.execute("""UPDATE items SET qty=MAX(0, qty-(SELECT SUM(qty) FROM assignments
                 WHERE item_id=items.id AND project_id=? AND consumed=0))
                 WHERE id IN (SELECT item_id FROM assignments WHERE project_id=? AND consumed=0)""", (id, id))
    d.execute("UPDATE assignments SET consumed=1 WHERE project_id=?", (id,))
    d.execute("UPDATE projects SET status='done' WHERE id=?", (id,))
    d.commit()
    return redirect(url_for("project", id=id))

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
        run("INSERT INTO files(project_id,name,stored,size) VALUES(?,?,?,?)", id, name, stored, (UPLOADS / stored).stat().st_size)
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
