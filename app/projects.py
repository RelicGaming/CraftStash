"""Projects, steps and attachments."""
import core, dbx, shutil, uuid
from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename
from core import ALLOWED, app, CAN_EDIT, clean, content_ok, db, FILE_CSP, file_of, find_user, get, GROUP_HEX, INLINE, item_of, log, num, PHOTOS, project_of, purge_files, purge_step_photos, q, run, save_photo, step_of, uid, UPLOADS, used_bytes
from items import fit_places

# ---------- projects ----------
@app.get("/projects")
def projects():
    me = uid()
    rows = q(f"""SELECT p.*, u.username AS owner,
                (SELECT {GROUP_HEX} FROM assignments a JOIN stock s ON s.id=a.item_id WHERE a.project_id=p.id AND s.hex<>'') AS hexes,
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
                           steps=q("SELECT * FROM steps WHERE project_id=? ORDER BY pos", id), max_photos=STEP_PHOTOS,
                           photos=photos_by_step(id),
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
        run(dbx.upsert("assignments", ["project_id", "item_id", "qty"], ["project_id", "item_id"], {"qty": "assignments.qty+excluded.qty"}), id, it["id"], n)
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
            if used: d.execute("UPDATE items SET qty=qty-? WHERE id=?", (used, a["item_id"])); fit_places(a["item_id"], None)
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
    if mine: d.execute(f"""INSERT INTO assignments(project_id,item_id,qty) SELECT ?, a.item_id, a.qty FROM assignments a
                           JOIN stock s ON s.id=a.item_id WHERE a.project_id=? AND {CAN_EDIT}""", (nid, id, uid()))
    over = missing = 0; copied = []
    try:
        for s in q("SELECT * FROM steps WHERE project_id=? ORDER BY pos", id):
            sid = d.execute("INSERT INTO steps(project_id,pos,title,body) VALUES(?,?,?,?)", (nid, s["pos"], s["title"], s["body"])).lastrowid
            for ph in q("SELECT * FROM step_photos WHERE step_id=? ORDER BY id", s["id"]):
                if core.QUOTA and used_bytes(uid()) + ph["size"] > core.QUOTA: over += 1; continue
                stored = uuid.uuid4().hex + ".jpg"
                try: shutil.copyfile(PHOTOS / ph["stored"], PHOTOS / stored)
                except FileNotFoundError: missing += 1; continue
                copied.append("photos/" + stored)
                d.execute("INSERT INTO step_photos(step_id,stored,size) VALUES(?,?,?)", (sid, stored, ph["size"]))
        for f in q("SELECT * FROM files WHERE project_id=?", id):
            if core.QUOTA and used_bytes(uid()) + f["size"] > core.QUOTA: over += 1; continue
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
    project_of(id, True); purge_files("project", id); purge_step_photos("project", id); run("DELETE FROM projects WHERE id=?", id)
    return redirect(url_for("projects"))

@app.post("/projects/<int:id>/share")
def share_project(id):
    project_of(id, True); u = find_user()
    if not u or u["id"] == uid(): flash("No such user.")
    else: run(dbx.insert_ignore("project_shares", ["project_id", "user_id"]), id, u["id"])
    return redirect(url_for("project", id=id))

@app.post("/projects/<int:id>/unshare/<int:user>")
def unshare_project(id, user):
    project_of(id, True); run("DELETE FROM project_shares WHERE project_id=? AND user_id=?", id, user)
    return redirect(url_for("project", id=id))

# ---------- step-by-step ----------
@app.post("/projects/<int:id>/steps")
def add_step(id):
    project_of(id, True)
    if clean("title", 100) or clean("body", 3000) or any(u.filename for u in request.files.getlist("photos")):
        c = run("""INSERT INTO steps(project_id,pos,title,body)
                   VALUES(?, COALESCE((SELECT MAX(pos) FROM steps WHERE project_id=?),0)+1, ?, ?)""",
                id, id, clean("title", 100), clean("body", 3000))
        if not add_step_photos({"id": c.lastrowid}) and not (clean("title", 100) or clean("body", 3000)):
            run("DELETE FROM steps WHERE id=?", c.lastrowid)          # only photos, and none of them could be saved
    return redirect(url_for("project", id=id) + "#steps")

@app.post("/steps/<int:sid>/update")
def update_step(sid):
    s = step_of(sid)
    run("UPDATE steps SET title=?, body=? WHERE id=?", clean("title", 100), clean("body", 3000), sid)
    return redirect(url_for("project", id=s["project_id"]) + "#steps")

STEP_PHOTOS = 6

def photos_by_step(project_id):
    out = {}
    for r in q("SELECT sp.id, sp.step_id FROM step_photos sp JOIN steps s ON s.id=sp.step_id WHERE s.project_id=? ORDER BY sp.id", project_id):
        out.setdefault(r["step_id"], []).append(r)
    return out

def add_step_photos(s):
    """Save the photos uploaded with a step form (field "photos"). Returns how many were saved."""
    have = db().execute("SELECT COUNT(*) FROM step_photos WHERE step_id=?", (s["id"],)).fetchone()[0]; saved = bad = 0
    ups = [u for u in request.files.getlist("photos") if u and u.filename]
    for up in ups:
        if have + saved >= STEP_PHOTOS: flash(f"A step can have up to {STEP_PHOTOS} photos."); break
        name = save_photo(up)
        if not name: bad += 1; continue
        size = (PHOTOS / name).stat().st_size
        if core.QUOTA and used_bytes(uid()) + size > core.QUOTA:
            (PHOTOS / name).unlink(); flash(f"That would go over your {core.QUOTA // 1048576} MB storage limit."); break
        run("INSERT INTO step_photos(step_id,stored,size) VALUES(?,?,?)", s["id"], name, size); saved += 1
    if bad: flash(f"{bad} photo(s) couldn't be read. Use JPEG, PNG, WebP or GIF images.")
    return saved

@app.post("/steps/<int:sid>/photos")
def step_photos_add(sid):
    s = step_of(sid); add_step_photos(s)
    return redirect(url_for("project", id=s["project_id"]) + f"#step-{sid}")

@app.get("/step-photos/<int:pid>")
def step_photo(pid):
    ph = get("SELECT sp.*, s.project_id FROM step_photos sp JOIN steps s ON s.id=sp.step_id WHERE sp.id=?", pid)
    project_of(ph["project_id"])
    r = send_from_directory(PHOTOS, ph["stored"], mimetype="image/jpeg"); r.headers["Cache-Control"] = "private, max-age=3600"
    return r

@app.post("/step-photos/<int:pid>/delete")
def step_photo_delete(pid):
    ph = get("SELECT * FROM step_photos WHERE id=?", pid); s = step_of(ph["step_id"])
    (PHOTOS / ph["stored"]).unlink(missing_ok=True); run("DELETE FROM step_photos WHERE id=?", pid)
    return redirect(url_for("project", id=s["project_id"]) + f"#step-{s['id']}")

@app.post("/steps/<int:sid>/delete")
def delete_step(sid):
    s = step_of(sid); purge_step_photos("step", sid); run("DELETE FROM steps WHERE id=?", sid)
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
    elif not content_ok(ext, f.stream.read(2048)):
        flash(f"That file doesn't look like a real .{ext} file, so it wasn't added.")
    else:
        f.stream.seek(0)
        stored = f"{uuid.uuid4().hex}.{ext}"
        f.save(UPLOADS / stored)
        size = (UPLOADS / stored).stat().st_size
        if core.QUOTA and used_bytes(uid()) + size > core.QUOTA:
            (UPLOADS / stored).unlink(); flash(f"That would go over your {core.QUOTA // 1048576} MB storage limit.")
        else: run("INSERT INTO files(project_id,name,stored,size) VALUES(?,?,?,?)", id, name, stored, size)
    return redirect(url_for("project", id=id))

@app.get("/files/<int:fid>")
def file(fid):
    f = file_of(fid)
    ext = f["stored"].rsplit(".", 1)[-1]
    inline = ext in INLINE
    if inline:      # files added before content checks existed: shown only if they really are what they say
        with open(UPLOADS / f["stored"], "rb") as fh: inline = content_ok(ext, fh.read(2048))
    r = send_from_directory(UPLOADS, f["stored"], download_name=f["name"], as_attachment=not inline,
                            **({} if inline else {"mimetype": "application/octet-stream"}))
    if ext != "pdf" or not inline: r.headers["Content-Security-Policy"] = FILE_CSP
    r.headers["X-Frame-Options"] = "SAMEORIGIN"
    r.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    return r

@app.post("/files/<int:fid>/delete")
def del_file(fid):
    f = file_of(fid, True)
    (UPLOADS / f["stored"]).unlink(missing_ok=True)
    run("DELETE FROM files WHERE id=?", fid)
    return redirect(url_for("project", id=f["project_id"]))
