"""Craft tools, starting with the pixel pattern maker: a picture becomes a cross-stitch, fuse bead, diamond painting or
colourwork chart, checked against your stash. Patterns are saved (picture, settings and chart), so they can be reopened,
tweaked and remade, and a pattern can become a project with its chart attached and its colours as materials.

The engine is patternkit (pure Pillow). Source pictures live in the photos folder; drawn charts are cached in TMP_DIR.
"""
import hashlib, io, json, math, re, uuid
from flask import abort, flash, redirect, render_template, request, Response, send_from_directory, url_for
from PIL import Image, ImageOps, UnidentifiedImageError
import core, dbx, patternkit as pk
import wishlist as wl
from core import app, CAN_EDIT, db, drop_pattern_cache, get, PHOTOS, q, run, uid, UPLOADS, used_bytes

PATTERNS_PER_USER = 200
SOURCE_SIDE = 1200                   # a source picture is kept at most this big (more than a 250-stitch chart needs)
STYLES = ("both", "colour", "symbols")
CACHE = core.PATTERN_CACHE

class Invalid(ValueError):
    """Settings that can't be used. The message says why, on the page or in the API."""

# ---------- the tools menu ----------
TOOLS = [{"endpoint": "pattern_new", "name": "Pixel pattern maker",
          "text": "Turn a picture into a chart for cross-stitch, fuse beads, diamond painting or knit and crochet colourwork, "
                  "in colours from your stash or a thread or bead catalogue, with how much of each you need and have.",
          "words": ("cross", "stitch", "embroider", "floss", "dmc", "bead", "perler", "hama", "artkal", "diamond", "drill",
                    "knit", "crochet", "colourwork", "latch")}]

def tools_for(pool):
    """The tools that suit a collection, by its name and craft."""
    words = f"{pool['name']} {pool['craft']}".lower()
    return [t for t in TOOLS if any(w in words for w in t["words"])]

@app.context_processor
def tools_ctx(): return {"tools_for": tools_for}

# ---------- settings from a form or a JSON body ----------
def number(d, key, default, lo, hi):
    try: v = float(d.get(key)) if d.get(key) not in (None, "") else default
    except (TypeError, ValueError): v = default
    v = min(hi, max(lo, v)); return int(v) if float(v).is_integer() else v

def scope_ok(v):
    """'pool:3' or 'type:5' the user can see, else ''."""
    m = re.fullmatch(r"(pool|type):(\d+)", str(v or ""))
    if not m: return ""
    sql = ("SELECT 1 FROM access WHERE pool_id=? AND user_id=?" if m[1] == "pool"
           else "SELECT 1 FROM types t JOIN access a ON a.pool_id=t.pool_id WHERE t.id=? AND a.user_id=?")
    return v if db().execute(sql, (int(m[2]), uid())).fetchone() else ""

def settings_of(d, old=None):
    """The checked settings from d (form fields or JSON); anything missing keeps old's value, or the default."""
    old = old or {}
    def pick(key, default): return d.get(key) if key in d else old.get(key, default)
    craft = pick("craft", "cross-stitch")
    if craft not in pk.CRAFTS: raise Invalid("Choose a craft: " + ", ".join(pk.CRAFTS) + ".")
    c = pk.CRAFTS[craft]
    palette = str(pick("palette", "catalogue:" + c["catalogue"] if c["catalogue"] else "free"))
    if palette.startswith("catalogue:"):
        if palette[10:] not in pk.CATALOGUES: raise Invalid("That colour catalogue doesn't exist.")
    elif palette != "free" and not scope_ok(palette): raise Invalid("Choose a collection or type you can see for the colours.")
    check = str(pick("check", "all"))
    if check != "all" and check != "none" and not scope_ok(check): check = "all"
    flag = lambda key: str(pick(key, "")).lower() in ("1", "true", "on", "yes")
    s = {"craft": craft, "palette": palette, "check": check,
         "width": number({"w": pick("width", 80)}, "w", 80, pk.MIN_SIDE, pk.MAX_SIDE),
         "height": number({"h": pick("height", 0)}, "h", 0, 0, pk.MAX_SIDE),
         "colours": number({"c": pick("colours", 20)}, "c", 20, 2, pk.MAX_COLOURS),
         "min_count": number({"m": pick("min_count", 0)}, "m", 0, 0, 500),
         "dither": flag("dither"), "sharp": flag("sharp")}
    if s["height"] and s["height"] < pk.MIN_SIDE: s["height"] = pk.MIN_SIDE
    for key, (_, default, lo, hi) in c["fields"].items(): s[key] = number({"v": pick(key, default)}, "v", default, lo, hi)
    return s

# ---------- pictures ----------
def save_source(up):
    """A picture upload (or anything with .stream), re-encoded as PNG in the photos folder, alpha kept: transparent
    parts of a picture become cells that aren't stitched. The file name, or None."""
    try:
        im = core.open_small(up.stream, SOURCE_SIDE)
        if im is None: return None
        im = im.convert("RGBA")
        name = f"pat-{uuid.uuid4().hex}.png"; im.save(PHOTOS / name, "PNG", optimize=True); return name
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError): return None

drop_cache = drop_pattern_cache

# ---------- colours to choose from, and the stash to check against ----------
def stash(scope, with_hex=True):
    """Stock rows the user can see, in a collection ('pool:3'), a type ('type:5') or everywhere ('all')."""
    m = re.fullmatch(r"(pool|type):(\d+)", scope or "")
    col = {"pool": "s.pool_id", "type": "s.type_id"}[m[1]] if m else "0"
    return q(f"""SELECT s.id, s.name, s.colour, s.hex, s.subtype, s.unit, s.free, s.tname, s.pname, p.craft
                 FROM stock s JOIN access a ON a.pool_id=s.pool_id JOIN pools p ON p.id=s.pool_id
                 WHERE a.user_id=? AND (?='' OR s.hex<>'') AND (?=0 OR {col}=?) ORDER BY s.free DESC, s.id""",
             uid(), "x" if with_hex else "", int(m[2]) if m else 0, int(m[2]) if m else 0)

def item_label(r): return r["name"] + (f" ({r['colour']})" if r["colour"] else "")

def candidates(s):
    """What the chart may use: a catalogue's colours, your stock's swatches, or None (any colours)."""
    if s["palette"] == "free": return None
    if s["palette"].startswith("catalogue:"):
        cat = pk.catalogue(s["palette"][10:])
        return [{"code": code, "name": name, "hex": h, "brand": cat["brand"]} for code, name, h in cat["colors"]]
    seen, out = set(), []
    for r in stash(s["palette"]):
        if r["hex"].upper() in seen or not re.fullmatch(r"#[0-9a-fA-F]{6}", r["hex"]): continue
        seen.add(r["hex"].upper()); out.append({"hex": r["hex"].upper(), "item_id": r["id"], "name": item_label(r)})
    if len(out) < 2: raise Invalid("That collection or type has fewer than 2 items with a swatch colour. Add swatch colours, or use a catalogue.")
    return out

def match_stash(palette, s):
    """For catalogue or free colours, the stock item each one is: one with the same catalogue code (and brand), or the
    closest swatch within pk.CLOSE. Stash colours are their own item."""
    if s["check"] == "none": rows = []
    else: rows = [r for r in stash(s["check"]) if re.fullmatch(r"#[0-9a-fA-F]{6}", r["hex"] or "")]
    opts = [{"lab": pk.lab(pk.hex_rgb(r["hex"])), "row": r} for r in rows]
    for p in palette:
        if p.get("item_id"): p["match"] = "stash"; continue
        p["item_id"] = None; p["match"] = "none"
        if p.get("code"):
            code = re.compile(rf"(?<![\w.]){re.escape(p['code'])}(?![\w.])", re.I)
            for r in rows:
                text = " ".join([r["name"], r["subtype"] or "", r["colour"] or ""])
                if code.search(text) and p["brand"].lower() in f"{text} {r['tname']} {r['pname']} {r['craft']}".lower():
                    p["item_id"], p["match"] = r["id"], "code"; break
        if not p["item_id"] and opts:
            i, d = pk.nearest(pk.hex_rgb(p["hex"]), opts)
            if d <= pk.CLOSE: p["item_id"], p["match"], p["de"] = opts[i]["row"]["id"], "close", round(d, 1)
    return palette

def generate(photo, s):
    """The chart for a stored source picture and settings (packed, ready for the chart column)."""
    with Image.open(PHOTOS / photo) as im: im.load(); chart = pk.make(im, s["width"], s["height"], s["colours"], candidates(s),
                                                                       s["dither"], s["sharp"], s["min_count"])
    if not chart["palette"]: raise Invalid("That picture is all transparent, so there's nothing to chart.")
    match_stash(chart["palette"], s); return pk.pack(chart)

# ---------- reading ----------
def pattern_of(pid):
    p = get("SELECT * FROM patterns WHERE id=?", pid)
    if p["user_id"] != uid(): abort(404)          # patterns are private
    return p

def legend(p, chart, s):
    """One row a colour: symbol, swatch, label, cells, how much is needed, and how that compares with your stock now."""
    craft = pk.CRAFTS[s["craft"]]; unit = craft["unit"]
    ids = [c["item_id"] for c in chart["palette"] if c.get("item_id")]
    items = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in q(f"""SELECT s.id, s.name, s.colour, s.unit, s.free, s.pname, s.tname, {CAN_EDIT} AS can_edit
                       FROM stock s JOIN access a ON a.pool_id=s.pool_id WHERE a.user_id=? AND s.id IN ({marks})""", uid(), uid(), *ids):
            items[r["id"]] = r
    rows = []
    for c in chart["palette"]:
        n = pk.need(s["craft"], s, c["count"]); it = items.get(c.get("item_id"))
        if c.get("code"): label = f"{c['brand']} {c['code']} {c['name']}"
        elif c.get("name"): label = c["name"]
        else: label = c["hex"]
        row = {"symbol": c["symbol"], "hex": c["hex"], "ink": pk.rgb_hex(pk.ink(c["hex"])), "label": label, "count": c["count"], "need": n, "unit": unit,
               "item": it, "match": c.get("match", "none") if it else "none", "de": c.get("de"), "status": "buy", "short": None}
        if it: row["in_item_unit"] = pk.in_unit(n, unit, it["unit"])
        rows.append(row)
    total = {}                     # two chart colours can fall back on the same item: compare its stock with both together
    for r in rows:
        if r["item"] and r["in_item_unit"] is not None: total[r["item"]["id"]] = total.get(r["item"]["id"], 0) + r["in_item_unit"]
    for r in rows:
        it = r["item"]
        if not it: continue
        if r["in_item_unit"] is None: r["status"] = "units"; continue
        want = total[it["id"]]; r["shared"] = want - r["in_item_unit"] > 1e-9
        if it["free"] + 1e-9 >= want: r["status"] = "have"
        else: r["status"], r["short"] = "short", want - max(it["free"], 0)
    return rows

def amount(r):
    """How much a row needs, in words: '0.24 skein', '1.30 skeins', '1,240 beads', '12.5 m'."""
    n, u = r["need"], r["unit"]
    if u == "skein": return f"{n:.2f} skein" + ("" if n <= 1 else "s")
    if u == "m": return f"{n:g} m"
    return f"{int(n):,} {u}{'s' if n != 1 else ''}"

def to_buy(r):
    """What to buy for a colour you don't have: whole skeins; beads, drills and metres as they are."""
    if r["unit"] == "skein": k = math.ceil(r["need"] - 1e-9) or 1; return f"{k} skein{'s' if k != 1 else ''}"
    return amount(r)

def short_text(r):
    u = r["item"]["unit"] if r["item"] else r["unit"]
    return f"{r['short']:.2f}".rstrip("0").rstrip(".") + f" {u}"

@app.template_filter("amount")
def amount_filter(r): return amount(r)

def chart_and_settings(p): return pk.unpack(p["chart"]), json.loads(p["settings"])

# ---------- pages ----------
@app.get("/tools")
def tools():
    rows = q("SELECT id, name, settings, updated, project_id FROM patterns WHERE user_id=? ORDER BY updated DESC, id DESC", uid())
    pats = [{**dict(zip(r.keys(), r)), "craft": pk.CRAFTS.get(json.loads(r["settings"]).get("craft"), {}).get("label", "")} for r in rows]
    return render_template("tools.html", tools=TOOLS, patterns=pats)

def form_choices():
    return {"crafts": pk.CRAFTS, "catalogues": pk.CATALOGUES,
            "pools": q("SELECT p.id, p.name FROM pools p JOIN access a ON a.pool_id=p.id WHERE a.user_id=? ORDER BY p.name", uid()),
            "types": q("""SELECT t.id, t.name, p.name AS pname FROM types t JOIN pools p ON p.id=t.pool_id JOIN access a ON a.pool_id=p.id
                          WHERE a.user_id=? ORDER BY p.name, t.name""", uid())}

def create(d, up):
    """A new pattern from a picture upload and settings d. Returns its id; raises Invalid."""
    if db().execute("SELECT COUNT(*) FROM patterns WHERE user_id=?", (uid(),)).fetchone()[0] >= PATTERNS_PER_USER:
        raise Invalid(f"You have {PATTERNS_PER_USER} patterns already. Delete some first.")
    if not (up and getattr(up, "filename", "")): raise Invalid("Choose a picture to make the pattern from.")
    s = settings_of(d)
    photo = save_source(up)
    if not photo: raise Invalid("That picture couldn't be read. Use a JPEG, PNG, WebP or GIF image.")
    try: chart = generate(photo, s)
    except Exception: (PHOTOS / photo).unlink(missing_ok=True); raise
    name = str(d.get("name") or "").strip()[:100] or re.sub(r"\.\w+$", "", up.filename)[:100] or "Pattern"
    return run("INSERT INTO patterns(user_id,name,photo,settings,chart,created,updated) VALUES(?,?,?,?,?,?,?)",
               uid(), name, photo, json.dumps(s), chart, dbx.now(), dbx.now()).lastrowid

def update(p, d, up=None):
    """New settings (and maybe a new picture) for a pattern: the chart is made again."""
    s = settings_of(d, json.loads(p["settings"])); photo = p["photo"]
    if up and getattr(up, "filename", ""):
        photo = save_source(up)
        if not photo: raise Invalid("That picture couldn't be read. Use a JPEG, PNG, WebP or GIF image.")
    try: chart = generate(photo, s)
    except Exception:
        if photo != p["photo"]: (PHOTOS / photo).unlink(missing_ok=True)
        raise
    run("UPDATE patterns SET name=?, photo=?, settings=?, chart=?, updated=? WHERE id=?",
        str(d.get("name") or p["name"]).strip()[:100] or p["name"], photo, json.dumps(s), chart, dbx.now(), p["id"])
    if photo != p["photo"]: (PHOTOS / p["photo"]).unlink(missing_ok=True)
    drop_cache(p["id"])

@app.route("/patterns/new", methods=["GET", "POST"])
def pattern_new():
    if request.method == "POST":
        try: pid = create(request.form, request.files.get("photo"))
        except Invalid as e:
            flash(str(e)); return render_template("pattern_form.html", s=settings_of({}), p=None, name=request.form.get("name", ""), **form_choices()), 400
        return redirect(url_for("pattern", id=pid))
    pre = {k: v for k, v in request.args.items() if k in ("craft", "palette", "check")}
    try: s = settings_of(pre)
    except Invalid: s = settings_of({})
    return render_template("pattern_form.html", s=s, p=None, name="", **form_choices())

@app.get("/patterns/<int:id>")
def pattern(id):
    p = pattern_of(id); chart, s = chart_and_settings(p); rows = legend(p, chart, s)
    project = db().execute("SELECT id, name FROM projects WHERE id=? AND user_id=?", (p["project_id"], uid())).fetchone() if p["project_id"] else None
    style = request.args.get("style") if request.args.get("style") in STYLES else "both"
    return render_template("pattern.html", p=p, s=s, chart=chart, rows=rows, project=project, style=style,
                           lines=pk.size_lines(s["craft"], s, chart["w"], chart["h"]), craft=pk.CRAFTS[s["craft"]],
                           total=sum(r["count"] for r in rows), short_text=short_text, to_buy=to_buy, **form_choices())

@app.post("/patterns/<int:id>")
def pattern_update(id):
    p = pattern_of(id)
    try: update(p, request.form, request.files.get("photo"))
    except Invalid as e: flash(str(e))
    else: flash("Pattern made again with the new settings.")
    return redirect(url_for("pattern", id=id))

def cached_png(p, style, thumb=False):
    """The drawn chart, cached in TMP_DIR under a key that changes whenever the chart does."""
    key = hashlib.sha1(p["chart"].encode()).hexdigest()[:12]
    f = CACHE / f"{p['id']}-{'thumb' if thumb else style}-{key}.png"
    if not f.is_file():
        CACHE.mkdir(parents=True, exist_ok=True); chart = pk.unpack(p["chart"])
        img = pk.preview(chart, max(1, 240 // max(chart["w"], chart["h"]))) if thumb else pk.render(chart, style)
        tmp = f.with_suffix(".part"); tmp.write_bytes(pk.png(img)); tmp.replace(f)
    return f

@app.get("/patterns/<int:id>/chart.png")
def pattern_png(id):
    p = pattern_of(id); style = request.args.get("style") if request.args.get("style") in STYLES else "both"
    f = cached_png(p, style, request.args.get("thumb") == "1")
    r = send_from_directory(f.parent, f.name, mimetype="image/png"); r.headers["Cache-Control"] = "private, max-age=300"
    if request.args.get("download"): r.headers["Content-Disposition"] = f'attachment; filename="{safe_name(p["name"])}-chart.png"'
    return r

@app.get("/patterns/<int:id>/source")
def pattern_source(id):
    p = pattern_of(id); r = send_from_directory(PHOTOS, p["photo"], mimetype="image/png"); r.headers["Cache-Control"] = "private, max-age=3600"
    return r

def safe_name(name): return re.sub(r"[^\w\-]+", "-", name).strip("-")[:60] or "pattern"

def pdf_bytes(p):
    chart, s = chart_and_settings(p); rows = legend(p, chart, s)
    return pk.pdf(chart, p["name"], pk.size_lines(s["craft"], s, chart["w"], chart["h"]),
                  [(r["symbol"], r["hex"], r["label"], r["count"], amount(r)) for r in rows])

@app.get("/patterns/<int:id>/pattern.pdf")
def pattern_pdf(id):
    p = pattern_of(id)
    return Response(pdf_bytes(p), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{safe_name(p["name"])}.pdf"', "Cache-Control": "private, no-store"})

def to_project(p, wishes=True):
    """A project from the pattern: the chart PDF attached, matched colours you can edit reserved as materials, and the
    rest added to the wishlist for it. When the pattern already has a project, only its chart PDF is replaced.
    Returns (project id, notes)."""
    chart, s = chart_and_settings(p); rows = legend(p, chart, s); notes = []
    data = pdf_bytes(p)
    if core.QUOTA and used_bytes(uid()) + len(data) > core.QUOTA:
        raise Invalid(f"The chart PDF would go over your {core.QUOTA // 1048576} MB storage limit.")
    proj = db().execute("SELECT id FROM projects WHERE id=? AND user_id=?", (p["project_id"], uid())).fetchone() if p["project_id"] else None
    new = proj is None
    if new:
        pid = run("INSERT INTO projects(user_id,name,notes,status,created,updated) VALUES(?,?,?,?,?,?)", uid(), p["name"][:100],
                  "\n".join(pk.size_lines(s["craft"], s, chart["w"], chart["h"])) + "\n\nMade with the pixel pattern maker.",
                  "planned", dbx.now(), dbx.now()).lastrowid
        run(dbx.insert_ignore("project_tags", ["project_id", "tag"]), pid, "pattern")
        pv = pk.preview(chart, max(1, 600 // max(chart["w"], chart["h"]))); cover = f"{uuid.uuid4().hex}.jpg"
        pv.save(PHOTOS / cover, "JPEG", quality=90); run("UPDATE projects SET photo=? WHERE id=?", cover, pid)   # the chart as its cover
    else: pid = proj["id"]
    stored = f"{uuid.uuid4().hex}.pdf"; (UPLOADS / stored).write_bytes(data)
    fid = run("INSERT INTO files(project_id,name,stored,size) VALUES(?,?,?,?)", pid, f"{safe_name(p['name'])}-chart.pdf", stored, len(data)).lastrowid
    if p["file_id"]:
        old = db().execute("SELECT id, stored FROM files WHERE id=? AND project_id=?", (p["file_id"], pid)).fetchone()
        if old: (UPLOADS / old["stored"]).unlink(missing_ok=True); run("DELETE FROM files WHERE id=?", old["id"])
    run("UPDATE patterns SET project_id=?, file_id=? WHERE id=?", pid, fid, p["id"])
    if not new: return pid, ["The chart PDF on the project is up to date. Its materials weren't changed."]
    reserved = listed = 0
    for r in rows:
        it = r["item"]
        if it and it["can_edit"] and r.get("in_item_unit"):
            qty = round(r["in_item_unit"], 2) if r["unit"] in ("skein", "m") else int(r["in_item_unit"])
            run(dbx.upsert("assignments", ["project_id", "item_id", "qty"], ["project_id", "item_id"], {"qty": "assignments.qty+excluded.qty"}),
                pid, it["id"], qty); reserved += 1
        elif wishes and not it:
            try:
                wl.add_wish({"name": r["label"][:120], "hex": r["hex"], "qty": math.ceil(r["need"] - 1e-9) or 1,
                             "unit": r["unit"], "project_id": pid,
                             "notes": f"For the pattern {p['name']}."})
                listed += 1
            except wl.Invalid as e: notes.append(str(e)); break
    if reserved: notes.append(f"{reserved} {'colour is' if reserved == 1 else 'colours are'} reserved from your stash.")
    if listed: notes.append(f"{listed} {'colour you don' if listed == 1 else 'colours you don'}'t have {'is' if listed == 1 else 'are'} on the wishlist for this project.")
    skipped = sum(1 for r in rows if r["item"] and not (r["item"]["can_edit"] and r.get("in_item_unit")))
    if skipped: notes.append(f"{skipped} matched {'colour isn' if skipped == 1 else 'colours aren'}'t reserved: the item is in a collection you can only view, or its unit isn't {rows[0]['unit']}s.")
    return pid, notes

@app.post("/patterns/<int:id>/project")
def pattern_project(id):
    p = pattern_of(id)
    ticks = request.form.getlist("wishes")              # the form sends a hidden 0 and, when ticked, a 1
    try: pid, notes = to_project(p, "1" in ticks if ticks else True)
    except Invalid as e: flash(str(e)); return redirect(url_for("pattern", id=id))
    for n in notes: flash(n)
    return redirect(url_for("project", id=pid))

@app.post("/patterns/<int:id>/delete")
def pattern_delete(id):
    p = pattern_of(id); (PHOTOS / p["photo"]).unlink(missing_ok=True); drop_cache(id); run("DELETE FROM patterns WHERE id=?", id)
    flash("Pattern deleted. A project made from it keeps its chart.")
    return redirect(url_for("tools"))
