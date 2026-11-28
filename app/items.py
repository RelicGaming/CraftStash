"""Items, photos, stock, the shopping list and search."""
import dbx, json, math, re, secrets, themes
from flask import (abort, flash, g, make_response, redirect, render_template, request, send_from_directory,
    session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash
from core import (API_PREFIX, app, clean, db, DB, item_of, local_time, log, LOW, num, PHOTOS, purge_photos,
    open_wish_items, pwv, q, run, save_photo, SSO, token_hash, type_of, uid, wish_count)

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
    places = {}
    for name, amount in zip(request.form.getlist("place_name"), request.form.getlist("place_qty")):
        name, n = name.strip()[:80], num(amount)
        if name and n > 0 and name.lower() != vals[7].lower() and len(places) < 20:
            key = next((k for k in places if k.lower() == name.lower()), name); places[key] = places.get(key, 0) + n
    if sum(places.values()) > vals[4] + 1e-9:
        flash(f"The other places hold {sum(places.values()):g} {t['unit']}, more than the {vals[4]:g} you entered, so the total in stock is now {sum(places.values()):g}.")
        vals = vals[:4] + (sum(places.values()),) + vals[5:]
    old = db().execute("SELECT qty, photo FROM items WHERE id=?", (item_id,)).fetchone() if item_id else None
    if item_id:
        run("UPDATE items SET name=?,colour=?,hex=?,subtype=?,qty=?,price=?,low_at=?,location=?,notes=?,extra=? WHERE id=?", *vals, item_id)
    else:
        item_id = run("INSERT INTO items(name,colour,hex,subtype,qty,price,low_at,location,notes,extra,type_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", *vals, t["id"]).lastrowid
    db().execute("DELETE FROM item_places WHERE item_id=?", (item_id,))
    db().executemany("INSERT INTO item_places(item_id,place,qty) VALUES(?,?,?)", [(item_id, k, v) for k, v in places.items()])
    fit_places(item_id, vals[4])
    db().commit()
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
    if old is None and request.form.get("then") == "another":       # "Save and add another" keeps the user in the form
        flash(f"Added {vals[0]}. Add the next {t['name'].lower()}.")
        return redirect(url_for("item_new", tid=t["id"]))
    return redirect(url_for("pool", pid=t["pool_id"], _anchor=f"item-{item_id}"))

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
                           places=q("SELECT place, qty FROM item_places WHERE item_id=? ORDER BY place COLLATE NOCASE", iid),
                           used_in=db().execute("SELECT COUNT(*) FROM assignments WHERE item_id=?", (iid,)).fetchone()[0])

@app.post("/items/<int:iid>/delete")
def item_del(iid):
    i = item_of(iid, True); purge_photos("item", iid); run("DELETE FROM items WHERE id=?", iid); log(i["pool_id"], "delete", f"Deleted {i['name']}")
    return redirect(url_for("pool", pid=i["pool_id"]))

def safe_next(default):
    n = request.form.get("next", "")      # a path inside the app (request.full_path), so URL_PREFIX is added here
    # Browsers drop tabs and newlines from a URL, so "/<tab>/evil.example" would become "//evil.example": refuse any
    # whitespace or control character as well as "//" and backslashes.
    ok = re.fullmatch(r"/(?![/\\])[^\s\x00-\x1f\x7f\\]*", n)
    return request.script_root + n if ok else default

@app.post("/items/<int:iid>/adjust")
def item_adjust(iid):
    i = item_of(iid, True)
    adjust_stock(i, num(request.form.get("amount")), request.form.get("action") == "add", request.form.get("place") or "")
    return redirect(safe_next(url_for("pool", pid=i["pool_id"])))

def lock_item(iid):
    """An item's current quantity. On a server database the row stays locked until the commit, so two people using
    the same stock at once can't both start from the same number."""
    return db().execute("SELECT qty FROM items WHERE id=?" + (" FOR UPDATE" if DB.external else ""), (iid,)).fetchone()["qty"]

def fit_places(iid, total):
    """The other places never hold more than the item's total: when they would, they shrink in proportion (the
    places_fit trigger does the same as a backstop for any other way qty changes)."""
    dbx.fit_places(db(), iid, total)

def adjust_stock(i, n, add, place=""):
    """Use or add n of a stock row i, optionally at one of its other places. Returns the new total."""
    if n <= 0: return i["qty"]
    iid = i["id"]; have = lock_item(iid)
    pl = db().execute("SELECT * FROM item_places WHERE item_id=? AND place=?", (iid, place)).fetchone() if place else None
    if pl and not add: n = min(n, pl["qty"])               # can't take more from a place than it holds
    new = max(0.0, have + (n if add else -n))
    if pl:                                                 # change the place first, so the total never drops below the places
        if add: db().execute("UPDATE item_places SET qty=qty+? WHERE id=?", (n, pl["id"]))
        else:
            db().execute("UPDATE item_places SET qty=qty-? WHERE id=?", (n, pl["id"]))
            db().execute("DELETE FROM item_places WHERE id=? AND qty<1e-9", (pl["id"],))
    fit_places(iid, new)
    run("UPDATE items SET qty=? WHERE id=?", new, iid)
    where = f" {'to' if add else 'from'} {pl['place']}" if pl else ""
    log(i["pool_id"], "adjust", f"{'Added' if add else 'Used'} {n:g} {i['unit']} of {i['name']}{where} ({have:g} to {new:g})", iid, new - have)
    return new

@app.post("/items/<int:iid>/move")
def item_move(iid):
    """Move some stock from one place to another (the main location is the empty name). The total doesn't change."""
    i = item_of(iid, True); n = num(request.form.get("amount"))
    src, dst = (request.form.get("from") or "").strip()[:80], (request.form.get("to") or "").strip()[:80]
    if dst.lower() in ((i["location"] or "").lower(), "main", "main location"): dst = ""
    if src.lower() == (i["location"] or "").lower(): src = ""
    places = {r["place"].lower(): r for r in q("SELECT * FROM item_places WHERE item_id=?", iid)}
    have = places[src.lower()]["qty"] if src else i["qty"] - sum(r["qty"] for r in places.values())
    if src and src.lower() not in places: flash("That place doesn't hold any of this item.")
    elif n <= 0 or src.lower() == dst.lower(): flash("Choose an amount and two different places.")
    elif n > have + 1e-9: flash(f"{src or i['location'] or 'The main location'} only has {max(have, 0):g} {i['unit']}.")
    elif not src and not dst: pass
    else:
        d = db()
        if src:
            d.execute("UPDATE item_places SET qty=qty-? WHERE id=?", (n, places[src.lower()]["id"]))
            d.execute("DELETE FROM item_places WHERE id=? AND qty<1e-9", (places[src.lower()]["id"],))
        if dst:
            if dst.lower() not in places and len(places) >= 20: d.rollback(); flash("An item can be in at most 20 other places."); return redirect(safe_next(url_for("pool", pid=i["pool_id"])))
            d.execute(dbx.upsert("item_places", ["item_id", "place", "qty"], ["item_id", "place"], {"qty": "item_places.qty+excluded.qty"}), (iid, dst, n))
        log(i["pool_id"], "move", f"Moved {n:g} {i['unit']} of {i['name']} from {src or i['location'] or 'main location'} to {dst or i['location'] or 'main location'}", iid, commit=False)
        d.commit()
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
    return render_template("shopping.html", rows=rows, suggest=suggest, wish_n=wish_count(), watching=open_wish_items())

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
    return account_page()

def account_page(new_token=None, new_name=""):
    tokens = q("SELECT id, name, can_write, created, last_used FROM api_tokens WHERE user_id=? ORDER BY id DESC", uid())
    ids = q("SELECT id, label, created FROM user_identities WHERE user_id=? ORDER BY id", uid())
    return render_template("account.html", tokens=tokens, new_token=new_token, new_name=new_name, when=local_time,
                           api_base=url_for("api_index", _external=True), sso=SSO, identities=ids)

API_TOKENS_PER_USER = 20

@app.post("/account/tokens")
def new_token():
    name = clean("name", 60) or "API token"
    if db().execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=?", (uid(),)).fetchone()[0] >= API_TOKENS_PER_USER:
        flash(f"You can have at most {API_TOKENS_PER_USER} tokens. Revoke one you no longer use."); return redirect(url_for("account"))
    tok = API_PREFIX + secrets.token_urlsafe(32)
    run("INSERT INTO api_tokens(user_id,name,hash,can_write) VALUES(?,?,?,?)", uid(), name, token_hash(tok), int(request.form.get("access") == "write"))
    r = make_response(account_page(tok, name)); r.headers["Cache-Control"] = "no-store"   # shown once, never stored
    return r

@app.post("/account/tokens/<int:tid>/delete")
def del_token(tid):
    run("DELETE FROM api_tokens WHERE id=? AND user_id=?", tid, uid()); flash("Token revoked. Anything using it can no longer connect.")
    return redirect(url_for("account"))
