"""The JSON API for Home Assistant and other tools."""
import json
from flask import g, jsonify, request, url_for
from werkzeug.exceptions import HTTPException
from core import api_error, app, currency, db, item_of, LOW, num, pool_of, project_of, q, uid
from items import adjust_stock, suggest
from pools import match, places_in, SEARCH_LIMIT
from projects import project_filters, project_sections, tags_of, visible_projects
import wishlist as wl

@app.errorhandler(HTTPException)
def http_error(e):
    if request.path.startswith("/api/"):
        return api_error(e.code or 500, {404: "Not found, or this token can't see it.", 405: "Method not allowed."}.get(e.code, e.description))
    return e

def body():
    """JSON body (or form fields), so curl -d and Home Assistant's rest_command both work."""
    return request.get_json(silent=True) if request.is_json else request.form.to_dict()

def field_map():
    return {t["id"]: json.loads(t["fields"]) for t in
            q("SELECT t.id, t.fields FROM types t JOIN access a ON a.pool_id=t.pool_id WHERE a.user_id=?", uid())}

def item_json(s, fields, places=None):
    extra = json.loads(s["extra"] or "{}")
    return {"id": s["id"], "name": s["name"], "colour": s["colour"], "hex": s["hex"] or None, "variant": s["subtype"],
            "collection": s["pname"], "collection_id": s["pool_id"], "type": s["tname"], "type_id": s["type_id"],
            "unit": s["unit"], "qty": s["qty"], "free": s["free"], "low_at": s["low_at"], "price": s["price"],
            "location": s["location"], "status": stock_status(s),
            "fields": {f["label"]: extra.get(f["id"], "") for f in fields.get(s["type_id"], []) if extra.get(f["id"], "") != ""},
            "places": [{"place": p["place"], "qty": p["qty"]} for p in (places or [])],
            "photo_url": url_for("photo", iid=s["id"], _external=True) if s["photo"] else None,
            "url": url_for("item_edit", iid=s["id"], _external=True)}

def stock_status(s):
    if s["free"] < 0: return "short"
    if s["qty"] <= 0: return "out"
    if s["low_at"] is not None and s["qty"] <= s["low_at"]: return "low"
    return "ok"

def shopping_rows():
    return q(f"""SELECT s.*, a.can_edit FROM stock s JOIN access a ON a.pool_id=s.pool_id
                 WHERE a.user_id=? AND {LOW} ORDER BY s.pname, s.tname, s.name""", uid())

def visible_item(iid, write=False):
    try: return item_of(iid, write)
    except HTTPException: return None

@app.get("/api/v1")
def api_index():
    return jsonify(version=1, user=g.user["username"], can_write=g.api_write, endpoints=[
        "GET /api/v1/summary", "GET /api/v1/shopping", "POST /api/v1/shopping/restock",
        "GET /api/v1/collections", "GET /api/v1/collections/<id>", "GET /api/v1/items?q=&status=",
        "GET /api/v1/items/<id>", "POST /api/v1/items/<id>/adjust", "GET /api/v1/projects", "GET /api/v1/projects/<id>",
        "GET /api/v1/wishlist?got=1", "POST /api/v1/wishlist", "GET /api/v1/wishlist/<id>", "POST /api/v1/wishlist/<id>/sightings",
        "POST /api/v1/wishlist/<id>/bought"])

@app.get("/api/v1/summary")
def api_summary():
    """Counts for dashboard sensors."""
    me = uid()
    s = db().execute("""SELECT COUNT(*) AS items,
                               SUM(CASE WHEN s.qty<=0 THEN 1 ELSE 0 END) AS out_of_stock,
                               SUM(CASE WHEN s.qty>0 AND s.low_at IS NOT NULL AND s.qty<=s.low_at AND s.free>=0 THEN 1 ELSE 0 END) AS low_stock,
                               SUM(CASE WHEN s.free<0 THEN 1 ELSE 0 END) AS short,
                               COALESCE(SUM(CASE WHEN s.price IS NOT NULL AND s.qty>0 THEN s.qty*s.price END),0) AS stash_value
                        FROM stock s JOIN access a ON a.pool_id=s.pool_id WHERE a.user_id=?""", (me,)).fetchone()
    pr = db().execute("""SELECT SUM(CASE WHEN status='planned' THEN 1 ELSE 0 END) AS planned, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) AS active FROM projects
                         WHERE user_id=? OR id IN (SELECT project_id FROM project_shares WHERE user_id=?)""", (me, me)).fetchone()
    return jsonify(shopping_list=len(shopping_rows()), items=s["items"], out_of_stock=s["out_of_stock"] or 0,
                   low_stock=s["low_stock"] or 0, short=s["short"] or 0, stash_value=round(s["stash_value"], 2), currency=currency(),
                   collections=db().execute("SELECT COUNT(*) FROM access WHERE user_id=?", (me,)).fetchone()[0],
                   projects_planned=pr["planned"] or 0, projects_active=pr["active"] or 0)

@app.get("/api/v1/shopping")
def api_shopping():
    rows, fields = shopping_rows(), field_map()
    return jsonify(count=len(rows), items=[{**item_json(s, fields), "suggested": suggest(s), "can_edit": bool(s["can_edit"])} for s in rows])

@app.post("/api/v1/shopping/restock")
def api_restock():
    """Add bought amounts. Body: {"items": [{"id": 3, "amount": 2}, ...]}, or {"id": 3, "amount": 2}.
    Leave out the amount to add the suggested amount; {"all": true} restocks every item on the list by its suggestion."""
    b = body() or {}
    if b.get("all") in (True, "true", "1", 1):
        wanted = [{"id": s["id"]} for s in shopping_rows() if s["can_edit"]]
    else:
        wanted = b.get("items") if isinstance(b.get("items"), list) else [b] if "id" in b else []
    if not wanted: return api_error(400, 'Send {"items": [{"id": 3, "amount": 2}]}, {"id": 3}, or {"all": true}.')
    done, skipped = [], []
    for w in wanted[:500]:
        try: iid = int(w.get("id"))
        except (TypeError, ValueError, AttributeError): skipped.append({"id": None, "reason": "no id"}); continue
        i = visible_item(iid, True)
        if not i: skipped.append({"id": iid, "reason": "not found or read-only"}); continue
        n = num(w.get("amount")) if w.get("amount") not in (None, "") else suggest(i)
        if n <= 0: skipped.append({"id": iid, "reason": "amount must be more than 0"}); continue
        new = adjust_stock(i, n, True)
        done.append({"id": iid, "name": i["name"], "added": n, "qty": new})
    return jsonify(restocked=done, skipped=skipped, shopping_list=len(shopping_rows()))

@app.get("/api/v1/collections")
def api_collections():
    rows = q("""SELECT p.id, p.name, p.craft, u.username AS owner, a.can_edit, a.is_owner,
                (SELECT COUNT(*) FROM items i JOIN types t ON t.id=i.type_id WHERE t.pool_id=p.id) AS items
                FROM pools p JOIN access a ON a.pool_id=p.id JOIN users u ON u.id=p.user_id
                WHERE a.user_id=? ORDER BY a.is_owner DESC, p.name COLLATE NOCASE""", uid())
    return jsonify(collections=[{**dict(r), "can_edit": bool(r["can_edit"]), "is_owner": bool(r["is_owner"])} for r in rows])

@app.get("/api/v1/collections/<int:pid>")
def api_collection(pid):
    p = pool_of(pid); fields = field_map(); places = places_in(pid)
    types = q("SELECT id, name, unit, fields FROM types WHERE pool_id=? ORDER BY name", pid)
    items = q("SELECT * FROM stock WHERE pool_id=? ORDER BY tname, name COLLATE NOCASE", pid)
    return jsonify(id=p["id"], name=p["name"], craft=p["craft"], owner=p["owner"], can_edit=bool(p["can_edit"]),
                   types=[{"id": t["id"], "name": t["name"], "unit": t["unit"],
                           "fields": [{"label": f["label"], "type": f["type"], "options": f["options"]} for f in json.loads(t["fields"])]} for t in types],
                   items=[item_json(s, fields, places.get(s["id"])) for s in items])

@app.get("/api/v1/items")
def api_items():
    """All items the token can see. ?q= searches like the app does (Label:value works); ?status=low|out|short|ok filters."""
    term, status = request.args.get("q", "").strip()[:100], request.args.get("status", "")
    fields = field_map(); sql, args = "SELECT stock.* FROM stock JOIN access a ON a.pool_id=stock.pool_id WHERE a.user_id=?", [uid()]
    if term: cond, more, _ = match(term, fields); sql += " AND " + cond; args += more
    rows = q(sql + " ORDER BY stock.pname COLLATE NOCASE, stock.tname COLLATE NOCASE, stock.name COLLATE NOCASE", *args)
    items = [item_json(s, fields) for s in rows if not status or stock_status(s) == status]
    return jsonify(count=len(items[:SEARCH_LIMIT]), more=len(items) > SEARCH_LIMIT, items=items[:SEARCH_LIMIT])

@app.get("/api/v1/items/<int:iid>")
def api_item(iid):
    i = item_of(iid)
    return jsonify(item_json(i, field_map(), q("SELECT place, qty FROM item_places WHERE item_id=? ORDER BY place COLLATE NOCASE", iid)))

@app.post("/api/v1/items/<int:iid>/adjust")
def api_adjust(iid):
    """Body: {"action": "use" | "add", "amount": 2, "place": "Shelf B"} (place is optional)."""
    b = body() or {}; i = item_of(iid, True)
    if b.get("action") not in ("use", "add"): return api_error(400, 'Set "action" to "use" or "add".')
    n = num(b.get("amount"))
    if n <= 0: return api_error(400, '"amount" must be a number more than 0.')
    place = str(b.get("place") or "").strip()[:80]
    if place and not db().execute("SELECT 1 FROM item_places WHERE item_id=? AND place=?", (iid, place)).fetchone():
        return api_error(400, f"This item isn't stored at {place!r}. Leave out \"place\" to use the main location.")
    adjust_stock(i, n, b["action"] == "add", place)
    return jsonify(item_json(item_of(iid), field_map(), q("SELECT place, qty FROM item_places WHERE item_id=? ORDER BY place COLLATE NOCASE", iid)))

def project_json(p, tags=()):
    return {"id": p["id"], "name": p["name"], "status": p["status"], "owner": p["owner"], "tags": list(tags),
            "created": p["created"] or None, "updated": p["updated"] or None, "url": url_for("project", id=p["id"], _external=True)}

@app.get("/api/v1/projects")
def api_projects():
    """?status=active&tag=gifts&tag=baby (all tags must match)&q=text (name, notes, steps)&pool=<collection id>"""
    me = uid(); f = project_filters(request.args); tags = tags_of(me)
    rows = [{**dict(zip(r.keys(), r)), "tags": tags.get(r["id"], [])} for r in visible_projects(me, f)]
    rows = [r for s in project_sections(rows, {**f, "group": "none"}, me) for r in s[1]]
    if f["sort"] == "name": rows.sort(key=lambda r: (r["status"] == "template", r["status"] == "done"))   # as before
    if request.args.getlist("status") and not f["status"]: rows = []                                       # an unknown status matches nothing
    return jsonify(projects=[{**project_json(r, r["tags"]), "materials": r["n_items"], "steps": r["n_steps"]} for r in rows])

@app.get("/api/v1/projects/<int:id>")
def api_project(id):
    p = project_of(id)
    mats = q("""SELECT a.qty AS reserved, a.consumed, a.used, s.id, s.name, s.colour, s.unit, s.qty, s.free, s.tname, s.pname
                FROM assignments a JOIN stock s ON s.id=a.item_id WHERE a.project_id=? ORDER BY s.tname, s.name""", id)
    steps = q("SELECT title, body FROM steps WHERE project_id=? ORDER BY pos", id)
    return jsonify({**project_json(p, [r["tag"] for r in q("SELECT tag FROM project_tags WHERE project_id=? ORDER BY tag", id)]), "notes": p["notes"],
                    "materials": [{"item_id": m["id"], "name": m["name"], "colour": m["colour"], "type": m["tname"], "collection": m["pname"],
                                   "unit": m["unit"], "reserved": m["reserved"], "used": m["used"] if m["consumed"] else None,
                                   "in_stock": m["qty"], "free": m["free"]} for m in mats],
                    "steps": [dict(s) for s in steps]})

# ---------- wishlist ----------
def wish_out(wid):
    w = wl.wish_of(wid); return wl.wish_json(w, wl.sightings_for([w])[wid])

@app.get("/api/v1/wishlist")
def api_wishlist():
    """Open wishes (?got=1: the ones bought), each with its shops, cheapest first."""
    rows = wl.visible_wishes(request.args.get("got") in ("1", "true")); s = wl.sightings_for(rows)
    return jsonify(count=len(rows), currency=currency(), wishes=[wl.wish_json(w, s[w["id"]]) for w in rows])

@app.get("/api/v1/wishlist/<int:wid>")
def api_wish(wid): return jsonify(wish_out(wid))

@app.post("/api/v1/wishlist")
def api_wish_add():
    """Body: {"name": "Chunky wool", "colour", "hex", "qty", "unit", "notes", "type_id", "item_id", "project_id",
    and the first shop: "shop", "price", "url", "seen" (YYYY-MM-DD), "shop_notes"}. Only "name" (or "item_id") is needed."""
    b = body()
    if not isinstance(b, dict): return api_error(400, 'Send a JSON object, like {"name": "Chunky wool", "shop": "Spotlight", "price": 12.5}.')
    try: wid = wl.add_wish(b)
    except wl.Invalid as e: return api_error(400, str(e))
    return jsonify(wish_out(wid)), 201

@app.post("/api/v1/wishlist/<int:wid>/sightings")
def api_wish_sighting(wid):
    """Body: {"shop": "Spotlight", "price": 12.5, "url": "https://...", "seen": "2026-11-25", "notes": "..."}."""
    w = wl.wish_of(wid, True); b = body()
    if not isinstance(b, dict): return api_error(400, "Send a JSON object.")
    try:
        if not wl.add_sighting(w["id"], b): return api_error(400, 'Send at least one of "shop", "url" or "price".')
    except wl.Invalid as e: return api_error(400, str(e))
    return jsonify(wish_out(wid)), 201

@app.post("/api/v1/wishlist/<int:wid>/bought")
def api_wish_bought(wid):
    """Body (all optional): {"qty": 2, "sighting": <shop id> or "none" (default: the cheapest), "type_id": <for a new item>}."""
    w = wl.wish_of(wid, True); b = body() or {}
    if w["got"]: return api_error(409, "That wish is already marked as bought.")
    if not isinstance(b, dict): return api_error(400, "Send a JSON object.")
    try: iid = wl.buy(w, b)
    except wl.Invalid as e: return api_error(400, str(e))
    return jsonify(wish=wish_out(wid), item=item_json(item_of(iid), field_map()))
