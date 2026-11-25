"""The wishlist: things to buy that aren't in the stash yet (or stock to watch the price of), and the shops they were seen at.

A wish belongs to the person who added it. Linked to a type, it is also seen (and, with edit access, changed) by
everyone who can see that type's collection. "Bought it" turns a wish into stock and keeps the wish, marked as got.
"""
import re, shutil, uuid
from datetime import datetime
from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for
import dbx
from core import (app, currency, db, EDIT_WISH, get, log, num, PHOTOS, q, run, save_photo, SEE_WISH, TZ, uid,
    wish_count)
from items import adjust_stock, suggest

WISHES_PER_USER = 1000
SIGHTINGS_PER_WISH = 30
COLS = ("name", "colour", "hex", "qty", "unit", "notes", "type_id", "item_id", "project_id")

# Every wish query starts here. Its two ? are the viewer: a project's name only shows to people who can see the project.
WISH_SELECT = """SELECT w.*, t.name AS tname, t.unit AS tunit, t.pool_id, pl.name AS pname, u.username AS owner,
    i.name AS item_name, i.photo AS item_photo,
    CASE WHEN pr.user_id=? OR EXISTS(SELECT 1 FROM project_shares ps WHERE ps.project_id=pr.id AND ps.user_id=?)
         THEN pr.name END AS project_name
    FROM wishes w JOIN users u ON u.id=w.user_id LEFT JOIN types t ON t.id=w.type_id LEFT JOIN pools pl ON pl.id=t.pool_id
    LEFT JOIN projects pr ON pr.id=w.project_id LEFT JOIN items i ON i.id=w.item_id"""

class Invalid(ValueError):
    """A wish or sighting that can't be saved. The message says why, on the page or in the API."""

# ---------- values from a form or a JSON body ----------
def text(d, key, n): return str(d.get(key) or "").strip()[:n]

def today(): return datetime.now(TZ).date().isoformat()

def web_url(v):
    v = str(v or "").strip()[:500]
    return v if re.fullmatch(r"https?://[^\s<>\"'`]+", v, re.I) else ""

def seen_date(v):
    try: return datetime.strptime(str(v or "").strip(), "%Y-%m-%d").date().isoformat()
    except ValueError: return today()

def price_of(v):
    if v in (None, ""): return None
    x = num(v, None); return round(x, 2) if x is not None else None

def as_id(v):
    try: return int(v) if v not in (None, "") else None
    except (TypeError, ValueError): return None

def editable_item(iid):
    return db().execute("""SELECT s.* FROM stock s JOIN access a ON a.pool_id=s.pool_id
                           WHERE s.id=? AND a.user_id=? AND a.can_edit=1""", (iid, uid())).fetchone() if iid else None

def editable_type(tid):
    return db().execute("""SELECT t.*, p.name AS pname FROM types t JOIN access a ON a.pool_id=t.pool_id JOIN pools p ON p.id=t.pool_id
                           WHERE t.id=? AND a.user_id=? AND a.can_edit=1""", (tid, uid())).fetchone() if tid else None

def own_project(pid):
    if not pid: return None
    return db().execute("SELECT id, name FROM projects WHERE id=? AND user_id=? AND status<>'template'", (pid, uid())).fetchone()

def wish_values(d, w=None):
    """The checked columns of a wish from d (form fields or JSON). w is the wish being edited, if any: a link that
    isn't in d stays as it is, and only a link that changes is checked (a collection editor can keep the owner's)."""
    def pick(key): return as_id(d.get(key)) if key in d else (w[key] if w else None)
    def changed(key, v): return v and not (w and v == w[key])
    item_id, type_id, project_id = pick("item_id"), pick("type_id"), pick("project_id")
    i = t = None
    if changed("item_id", item_id):
        i = editable_item(item_id)
        if not i: raise Invalid("That item doesn't exist, or you can't add stock to it.")
        type_id = i["type_id"]
    if changed("type_id", type_id):
        t = editable_type(type_id)
        if not t: raise Invalid("Choose a type in a collection you can edit.")
        if w and w["item_id"] and item_id == w["item_id"]: item_id = None     # moved to another type: no longer that item
    if w and w["user_id"] != uid(): project_id = w["project_id"]             # only the owner links their projects
    elif changed("project_id", project_id) and not own_project(project_id): raise Invalid("Choose one of your own projects.")
    if t: unit = t["unit"]
    elif type_id and w: unit = w["tunit"] or w["unit"]
    else: unit = text(d, "unit", 20) if "unit" in d or not w else w["unit"]
    name = text(d, "name", 120) or (i["name"] if i else "")
    if not name: raise Invalid("Give the wish a name.")
    hexv = text(d, "hex", 7)
    qty = num(d.get("qty"), 0) if d.get("qty") not in (None, "") else (w["qty"] if w else 1)
    return {"name": name, "colour": text(d, "colour", 60) if "colour" in d or not i else i["colour"],
            "hex": hexv if re.fullmatch(r"#[0-9a-fA-F]{6}", hexv) else (i["hex"] if i and "hex" not in d else ""),
            "qty": qty or 1, "unit": unit, "notes": text(d, "notes", 1000) if "notes" in d or not w else w["notes"],
            "type_id": type_id, "item_id": item_id, "project_id": project_id}

def add_wish(d, sighting_notes="shop_notes"):
    """A new wish, with its first sighting if d has a shop, link or price. Returns its id."""
    v = wish_values(d)
    if db().execute("SELECT COUNT(*) FROM wishes WHERE user_id=?", (uid(),)).fetchone()[0] >= WISHES_PER_USER:
        raise Invalid(f"You have {WISHES_PER_USER} wishes already. Delete some you've bought first.")
    wid = run("""INSERT INTO wishes(user_id,name,colour,hex,qty,unit,notes,type_id,item_id,project_id,created)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?)""", uid(), *(v[c] for c in COLS), dbx.now()).lastrowid
    add_sighting(wid, d, sighting_notes)
    return wid

def update_wish(w, d):
    v = wish_values(d, w)
    run("UPDATE wishes SET name=?,colour=?,hex=?,qty=?,unit=?,notes=?,type_id=?,item_id=?,project_id=? WHERE id=?",
        *(v[c] for c in COLS), w["id"])

def add_sighting(wid, d, notes="notes"):
    """Where it was seen, and for how much. Nothing is added when d has no shop, link or price: returns the new id or None."""
    shop, url, price = text(d, "shop", 80), web_url(d.get("url")), price_of(d.get("price"))
    if not (shop or url or price is not None): return None
    if db().execute("SELECT COUNT(*) FROM wish_sightings WHERE wish_id=?", (wid,)).fetchone()[0] >= SIGHTINGS_PER_WISH:
        raise Invalid(f"A wish keeps at most {SIGHTINGS_PER_WISH} shops. Delete an old one first.")
    if not shop and url: shop = re.sub(r"^www\.", "", re.split(r"[/:?#]", url.split("//", 1)[1])[0])[:80]   # the site's name
    return run("INSERT INTO wish_sightings(wish_id,shop,url,price,seen,notes) VALUES(?,?,?,?,?,?)",
               wid, shop, url, price, seen_date(d.get("seen")), text(d, notes, 300)).lastrowid

def save_wish_photo(w):
    """An uploaded photo replaces the wish's own one; the remove_photo box drops it."""
    up = request.files.get("photo")
    if up and up.filename:
        name = save_photo(up)
        if not name: flash("That photo couldn't be read. Use a JPEG, PNG, WebP or GIF image."); return
        if w["photo"]: (PHOTOS / w["photo"]).unlink(missing_ok=True)
        run("UPDATE wishes SET photo=? WHERE id=?", name, w["id"])
    elif request.form.get("remove_photo") and w["photo"]:
        (PHOTOS / w["photo"]).unlink(missing_ok=True); run("UPDATE wishes SET photo='' WHERE id=?", w["id"])

# ---------- reading ----------
def wish_of(wid, write=False):
    me = uid(); return get(WISH_SELECT + " WHERE w.id=? AND " + (EDIT_WISH if write else SEE_WISH), me, me, wid, me, me)

def can_edit_wish(wid):
    me = uid(); return bool(db().execute("SELECT 1 FROM wishes w WHERE w.id=? AND " + EDIT_WISH, (wid, me, me)).fetchone())

def visible_wishes(got=False, project_id=None):
    me = uid()
    if project_id:
        return q(WISH_SELECT + " WHERE w.got=0 AND w.project_id=? AND " + SEE_WISH + " ORDER BY w.created DESC, w.id DESC",
                 me, me, project_id, me, me)
    return q(WISH_SELECT + " WHERE w.got=? AND " + SEE_WISH + " ORDER BY w.got_at DESC, w.created DESC, w.id DESC",
             me, me, 1 if got else 0, me, me)

def sightings_for(wishes):
    """{wish id: its sightings}, cheapest first; ones without a price last, newest first."""
    out = {w["id"]: [] for w in wishes}
    if not out: return out
    ids = list(out)
    for s in q("SELECT * FROM wish_sightings WHERE wish_id IN (" + ",".join("?" * len(ids)) + ")", *ids): out[s["wish_id"]].append(s)
    for v in out.values():
        v.sort(key=lambda s: (s["seen"], s["id"]), reverse=True); v.sort(key=lambda s: (s["price"] is None, s["price"] or 0))
    return out

def edit_choices(w=None):
    """Types the user can file a wish under, and their own open projects (only the owner picks a wish's project)."""
    me = uid()
    types = q("""SELECT t.id, t.name, t.unit, p.name AS pname FROM types t JOIN access a ON a.pool_id=t.pool_id
                 JOIN pools p ON p.id=t.pool_id WHERE a.user_id=? AND a.can_edit=1 ORDER BY p.name, t.name""", me)
    mine = not w or w["user_id"] == me
    return types, q("SELECT id, name FROM projects WHERE user_id=? AND status IN ('planned','active') ORDER BY name", me) if mine else []

def wish_json(w, sightings):
    best = sightings[0] if sightings and sightings[0]["price"] is not None else None
    return {"id": w["id"], "name": w["name"], "colour": w["colour"], "hex": w["hex"] or None, "qty": w["qty"], "unit": w["unit"],
            "notes": w["notes"], "owner": w["owner"], "got": bool(w["got"]), "got_at": w["got_at"] or None, "created": w["created"] or None,
            "type_id": w["type_id"], "type": w["tname"], "collection_id": w["pool_id"], "collection": w["pname"], "item_id": w["item_id"],
            "project_id": w["project_id"] if w["project_name"] else None, "project": w["project_name"],
            "cheapest": sighting_json(best) if best else None, "sightings": [sighting_json(s) for s in sightings],
            "photo_url": url_for("wish_photo", wid=w["id"], _external=True) if w["photo"] else None,
            "url": url_for("wish", wid=w["id"], _external=True)}

def sighting_json(s):
    return {"id": s["id"], "shop": s["shop"], "url": s["url"] or None, "price": s["price"], "seen": s["seen"] or None, "notes": s["notes"]}

# ---------- buying ----------
def buy(w, d):
    """Turn a wish into stock: add to the item it watches, or make a new item in the chosen type. Returns the item id.
    d: qty (default the wish's), sighting (an id, 'none', or left out for the cheapest), type_id (for a new item)."""
    n = num(d.get("qty"), 0) if d.get("qty") not in (None, "") else w["qty"]
    if n <= 0: raise Invalid("Enter how much you bought.")
    sights = sightings_for([w])[w["id"]]; sid = str(d.get("sighting") or "")
    s = None if sid == "none" else next((x for x in sights if str(x["id"]) == sid), None) if sid else (sights[0] if sights else None)
    if sid and sid != "none" and not s: raise Invalid("That shop isn't on this wish.")
    at = f" at {s['shop']}" if s and s["shop"] else ""
    paid = f" for {currency()}{s['price']:,.2f} each" if s and s["price"] is not None else ""
    i = editable_item(w["item_id"]); tid = w["type_id"]
    if i:
        adjust_stock(i, n, True); iid = i["id"]
        if s and s["price"] is not None: run("UPDATE items SET price=? WHERE id=?", s["price"], iid)   # what it costs now
    else:
        t = editable_type(as_id(d.get("type_id")) or w["type_id"])
        if not t: raise Invalid("Choose which collection and type it goes into.")
        note = f"Bought {today()}{at}{paid}." + (f" {s['url']}" if s and s["url"] else "")
        photo = ""
        if w["photo"] and (PHOTOS / w["photo"]).is_file():      # a copy: the wish keeps its own for the history
            photo = uuid.uuid4().hex + ".jpg"; shutil.copyfile(PHOTOS / w["photo"], PHOTOS / photo)
        iid = run("INSERT INTO items(name,colour,hex,qty,price,notes,photo,type_id) VALUES(?,?,?,?,?,?,?,?)", w["name"], w["colour"],
                  w["hex"], n, s["price"] if s else None, (w["notes"] + "\n" + note).strip()[:1000], photo, t["id"]).lastrowid
        log(t["pool_id"], "add", f"Added {w['name']} ({t['name']}) from the wishlist, {n:g} {t['unit']}{at}{paid}", iid)
        tid = t["id"]
    run("UPDATE wishes SET got=1, got_at=?, item_id=?, type_id=? WHERE id=?", dbx.now(), iid, tid, w["id"])
    return iid

# ---------- pages ----------
def form_values():
    d = request.form.to_dict()
    if not d.get("has_hex"): d["hex"] = ""
    return d

@app.get("/wishlist")
def wishlist():
    got = request.args.get("show") == "got"; rows = visible_wishes(got)
    return render_template("wishlist.html", rows=rows, sights=sightings_for(rows), got=got, wish_n=wish_count())

@app.route("/wishlist/new", methods=["GET", "POST"])
def wish_new():
    """Quick add while shopping: photo, name, shop, price. ?item= watches an item, ?project= and ?type= link, ?qty= sets the amount."""
    if request.method == "POST":
        d = form_values()
        try: wid = add_wish(d)
        except Invalid as e: flash(str(e)); return redirect(url_for("wish_new", **request.args.to_dict()))
        save_wish_photo(wish_of(wid, True))
        if d.get("then") == "another":
            flash(f"Added {d.get('name') or 'it'} to the wishlist. Add the next one."); return redirect(url_for("wish_new"))
        return redirect(url_for("wish", wid=wid))
    a = request.args; i = editable_item(as_id(a.get("item"))); p = own_project(as_id(a.get("project")))
    t = editable_type(i["type_id"] if i else as_id(a.get("type")))
    proto = {"name": i["name"], "colour": i["colour"], "hex": i["hex"], "unit": i["unit"], "notes": ""} if i else {"unit": t["unit"] if t else ""}
    proto["qty"] = num(a.get("qty"), 0) or (suggest(i) if i else 1)
    types, projects = edit_choices()
    return render_template("wish_form.html", w=None, proto=proto, item=i, type_id=t["id"] if t else None,
                           project_id=p["id"] if p else None, types=types, projects=projects, today=today())

@app.get("/wishlist/<int:wid>")
def wish(wid):
    w = wish_of(wid); types, _ = edit_choices(w)
    return render_template("wish.html", w=w, sights=sightings_for([w])[wid], can_edit=can_edit_wish(wid), types=types,
                           watched=editable_item(w["item_id"]), today=today())

@app.route("/wishlist/<int:wid>/edit", methods=["GET", "POST"])
def wish_edit(wid):
    w = wish_of(wid, True)
    if request.method == "POST":
        try: update_wish(w, form_values())
        except Invalid as e: flash(str(e)); return redirect(url_for("wish_edit", wid=wid))
        save_wish_photo(w); return redirect(url_for("wish", wid=wid))
    types, projects = edit_choices(w)
    return render_template("wish_form.html", w=w, proto=w, item=None, type_id=w["type_id"], project_id=w["project_id"],
                           types=types, projects=projects, today=today())

@app.post("/wishlist/<int:wid>/delete")
def wish_del(wid):
    w = wish_of(wid, True)
    if w["photo"]: (PHOTOS / w["photo"]).unlink(missing_ok=True)
    run("DELETE FROM wishes WHERE id=?", wid); flash(f"Deleted {w['name']} from the wishlist.")
    return redirect(url_for("wishlist", show="got") if w["got"] else url_for("wishlist"))

@app.post("/wishlist/<int:wid>/sightings")
def wish_sighting(wid):
    w = wish_of(wid, True)
    try:
        if not add_sighting(w["id"], request.form.to_dict()): flash("Enter the shop, a link or a price.")
    except Invalid as e: flash(str(e))
    return redirect(url_for("wish", wid=wid, _anchor="shops"))

@app.post("/wishlist/sightings/<int:sid>/delete")
def wish_sighting_del(sid):
    s = get("SELECT * FROM wish_sightings WHERE id=?", sid); wish_of(s["wish_id"], True)
    run("DELETE FROM wish_sightings WHERE id=?", sid)
    return redirect(url_for("wish", wid=s["wish_id"], _anchor="shops"))

@app.post("/wishlist/<int:wid>/bought")
def wish_bought(wid):
    w = wish_of(wid, True)
    if w["got"]: flash("That's already marked as bought."); return redirect(url_for("wish", wid=wid))
    try: iid = buy(w, request.form.to_dict())
    except Invalid as e: flash(str(e)); return redirect(url_for("wish", wid=wid, _anchor="bought"))
    flash(f"{w['name']} is in your stash now. Check the details, like where you'll keep it.")
    return redirect(url_for("item_edit", iid=iid))

@app.post("/wishlist/<int:wid>/reopen")
def wish_reopen(wid):
    wish_of(wid, True); run("UPDATE wishes SET got=0, got_at='' WHERE id=?", wid)
    return redirect(url_for("wish", wid=wid))

@app.get("/wishlist/<int:wid>/photo")
def wish_photo(wid):
    w = wish_of(wid)
    if not w["photo"]: abort(404)
    r = send_from_directory(PHOTOS, w["photo"], mimetype="image/jpeg"); r.headers["Cache-Control"] = "private, max-age=3600"
    return r
