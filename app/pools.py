"""Collections (pools), sharing, material types and their custom fields."""
import csv, dbx, importer, io, json, re, secrets
from flask import abort, flash, redirect, render_template, request, Response, url_for
from types import MappingProxyType
from core import (app, clean, db, drop_reservations, find_user, get, GROUP_HEX, LIKE, like, log, pool_impact,
    pool_of, purge_photos, q, run, type_impact, type_of, uid)
from items import fit_places

# ---------- pools ----------
@app.get("/")
def home():
    pools = q(f"""SELECT p.*, a.is_owner, a.can_edit, u.username AS owner,
                        (SELECT COUNT(*) FROM stock s WHERE s.pool_id=p.id) AS n,
                        (SELECT {GROUP_HEX} FROM stock s WHERE s.pool_id=p.id AND s.hex<>'') AS hexes
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
    if not clean("name", 80): flash("Give the collection a name."); return redirect(url_for("home"))
    c = run("INSERT INTO pools(user_id,name,craft) VALUES(?,?,?)", uid(), clean("name", 80), clean("craft", 80))
    return redirect(url_for("pool", pid=c.lastrowid))          # straight into it, where the first type is set up

@app.post("/collections/<int:pid>/delete")
def del_pool(pid):
    pool_of(pid, owner=True); purge_photos("pool", pid); run("DELETE FROM pools WHERE id=?", pid)
    return redirect(url_for("home"))

def places_in(pid):
    """item id -> its extra storage places, for one collection."""
    out = {}
    for r in q("""SELECT ip.item_id, ip.place, ip.qty FROM item_places ip JOIN items i ON i.id=ip.item_id
                  JOIN types t ON t.id=i.type_id WHERE t.pool_id=? ORDER BY ip.place COLLATE NOCASE""", pid):
        out.setdefault(r["item_id"], []).append(r)
    return out

SORTS = MappingProxyType({"name": "name COLLATE NOCASE", "colour": "colour COLLATE NOCASE, name COLLATE NOCASE",
                          "low": "qty ASC, name COLLATE NOCASE", "new": "id DESC"})   # ?sort= picks one; anything else is "name"

def match(term, fields):
    """SQL condition (on a stock row) for a search term. fields maps type id -> field list.
    "Label:value" searches just that custom field when some type has it; otherwise every text column and field."""
    labels = [f["label"] for fl in fields.values() for f in fl]
    m = re.match(r"^([^:]{1,40}):\s*(.+)$", term)
    key = m[1].strip().lower() if m else ""
    if m and key in {l.lower() for l in labels}:   # "Width:150" filters that field
        ids = [f["id"] for fl in fields.values() for f in fl if f["label"].lower() == key]   # ids are unique per field
        args = []
        ids = [i for i in ids if dbx.JSON_KEY.fullmatch(str(i))] or ["-"]   # field ids are the app's own (f + hex)
        for i in ids: args += [dbx.json_key(i), like(m[2].strip())]
        return "(" + " OR ".join([f"{dbx.json_get('stock.extra')} {LIKE}"] * len(ids)) + ")", args, key
    return (f"""(stock.name {LIKE} OR stock.colour {LIKE} OR stock.subtype {LIKE} OR stock.notes {LIKE} OR stock.location {LIKE}
                OR {dbx.json_has("stock.extra", LIKE)}
                OR EXISTS(SELECT 1 FROM item_places ip WHERE ip.item_id=stock.id AND ip.place {LIKE}))""", [like(term)] * 7, "")

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
    return render_template("pool.html", pool=p, groups=groups, places=places_in(pid), term=term, shares=shares, hint=(labels or ["Width"])[0], sort=sort, impact=pool_impact(pid) if p["is_owner"] else None,
                           has_items=bool(by_type) or bool(term), presets=presets_for(p, types),
                           value=db().execute("SELECT COALESCE(SUM(qty*price),0) FROM stock WHERE pool_id=? AND price IS NOT NULL", (pid,)).fetchone()[0],
                           hexes=[r["hex"] for r in q("SELECT DISTINCT hex FROM stock WHERE pool_id=? AND hex<>'' LIMIT 40", pid)])

@app.post("/collections/<int:pid>/share")
def share_pool(pid):
    pool_of(pid, owner=True); u = find_user()
    if not u or u["id"] == uid(): flash("No such user.")
    else:
        run(dbx.upsert("pool_shares", ["pool_id", "user_id", "can_edit"], ["pool_id", "user_id"], {"can_edit": "excluded.can_edit"}),
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
        db().execute(f"UPDATE items SET qty={dbx.greatest('0', 'qty-?')} WHERE id=?", (a["delta"], it["id"])); fit_places(it["id"], None); run("UPDATE activity SET undone=1 WHERE id=?", aid)
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

# Ready-made material types, so a new collection can be set up in one tap. Fields use the starter-field syntax below.
PRESETS = {
    "yarn":    ("Yarn", "skein", "Weight: Lace, Fingering, Sport, DK, Worsted, Aran, Bulky, Super bulky\nFibre\nLength per skein (m)", "knit crochet weav"),
    "needles": ("Needles & hooks", "pcs", "Kind: Straight, Circular, Double pointed, Crochet hook\nSize (mm)\nLength (cm)", "knit crochet"),
    "fabric":  ("Fabric", "m", "Width (cm)\nFibre\nPattern", "quilt sew embroider cross stitch patchwork"),
    "thread":  ("Thread", "spool", "Brand\nWeight", "quilt sew"),
    "floss":   ("Embroidery floss", "skein", "Brand\nColour number", "embroider cross stitch"),
    "notions": ("Notions", "pcs", "Kind: Zip, Elastic, Interfacing, Ribbon, Other\nSize", "sew quilt"),
    "buttons": ("Buttons", "pcs", "Size (mm)\nMaterial", "knit sew"),
    "beads":   ("Beads", "g", "Size\nMaterial", "bead jewel"),
    "paint":   ("Paint", "tube", "Brand\nMedium: Acrylic, Oil, Watercolour, Gouache", "paint art"),
}

def presets_for(pool, types):
    """Preset keys not already used in this collection, the ones that suit its name or craft first."""
    words, have = f"{pool['name']} {pool['craft']}".lower(), {t["name"].lower() for t in types}
    keys = [k for k, p in PRESETS.items() if p[0].lower() not in have]
    return [(k, PRESETS[k]) for k in sorted(keys, key=lambda k: not any(w in words for w in PRESETS[k][3].split()))]

def starter_fields(text):
    """One field per line. "Width (cm)" makes a number field, "Fibre: Cotton, Linen" a dropdown, anything else text.
    A single line without a colon is still read as the older comma-separated list of text fields."""
    lines = text.splitlines() if "\n" in text.strip() or ":" in text else text.split(",")
    out, seen = [], set()
    for line in lines:
        label, _, opts = line.partition(":")
        label = label.strip()[:40]
        if not label or label.lower() in seen or len(out) >= 20: continue
        seen.add(label.lower())
        opts = [o.strip()[:60] for o in opts.split(",") if o.strip()][:30]
        typ = "choice" if opts else "number" if re.search(r"\(\s*[^()]{1,10}\s*\)$", label) else "text"
        out.append(new_field(label, typ, opts))
    return out

@app.post("/collections/<int:pid>/types")
def new_type(pid):
    pool_of(pid, write=True)
    preset = PRESETS.get(request.form.get("preset", ""))
    name, unit, fields = (preset[:3] if preset else (clean("name", 60), clean("unit", 20), request.form.get("fields", "")))
    if not name: flash("Give the material type a name."); return redirect(url_for("pool", pid=pid))
    c = run("INSERT INTO types(pool_id,name,unit,fields) VALUES(?,?,?,?)", pid, name, unit or "pcs", json.dumps(starter_fields(fields)))
    flash(f"{name} is ready. Add your first {name.lower()} below. You can change its fields at any time.")
    return redirect(url_for("item_new", tid=c.lastrowid))

@app.post("/types/<int:tid>/delete")
def del_type(tid):
    t = type_of(tid, True); purge_photos("type", tid); run("DELETE FROM types WHERE id=?", tid)
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
    d.execute(f"UPDATE items SET extra={dbx.json_drop('extra')} WHERE type_id=?", (dbx.json_key(fid), tid)); d.commit()
    return redirect(url_for("type_fields", tid=tid))
