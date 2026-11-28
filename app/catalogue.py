"""Stash items from a bundled colour catalogue (DMC floss and the others in palettes/): pick the colours you own,
and each one is filled in with its number, name, colour family and swatch."""
import colorsys, re
import patternkit as pk

FAMILIES = ("Red", "Pink", "Orange", "Yellow", "Green", "Blue", "Purple", "Brown", "Grey", "Black", "White")

# The colour word in a catalogue name, read from the end: "Yellow Green" is a green, "Beige Gray" a grey.
# Words that cover two families pick one by lightness (Salmon Very Light is pink, Salmon Very Dark red).
WORDS = {
    "Red": "red garnet raspberry alizarin", "Pink": "pink blossom", "Orange": "orange tangerine pumpkin apricot peach shrimp spice",
    "Yellow": "yellow lemon canary topaz gold golden straw mustard", "Purple": "purple violet lavender plum grape eggplant fuschia fuchsia",
    "Green": "green grn jade celadon pistachio avocado nile emerald aquamarine chartreuse fern pine kelly parrot hunter forest moss olive khaki apple tender",
    "Blue": "blue navy delft wedgewood cornflower turquoise teal peacock sky blueberry",
    "Brown": "brown beige mocha coffee cocoa hazelnut tan sand driftwood", "Grey": "gray grey pewter tin steel pearl ash",
    "White": "white snow blanc ecru cream", "Black": "black"}
SPLIT = {"Red/Pink": "salmon coral melon rose carnation geranium cranberry mauve", "Orange/Brown": "cotta copper tawny mahogany rosewood"}
WORD = {w: f for f, ws in {**WORDS, **SPLIT}.items() for w in ws.split()}
# Families next to each other on the wheel (or neutral): a name and a hue that disagree only this far keep the name.
NEAR = {frozenset(p) for p in [("Red", "Pink"), ("Red", "Orange"), ("Red", "Purple"), ("Pink", "Purple"), ("Orange", "Yellow"),
        ("Orange", "Brown"), ("Yellow", "Green"), ("Yellow", "Brown"), ("Green", "Blue"), ("Blue", "Purple"), ("Red", "Brown"),
        ("Pink", "Orange"), ("Green", "Brown"), ("Purple", "Brown")]}

def hls(hexv):
    r, g, b = (int(hexv[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, g, b); v = max(r, g, b)
    return h * 360, l, (v - min(r, g, b)) / v if v else 0, v

def hue_family(hexv):
    """A basic family from the colour alone, for names with no colour word."""
    h, l, s, v = hls(hexv)
    if v < 0.2: return "Black"
    if s < 0.12: return "White" if l > 0.9 else "Grey"
    if 12 <= h < 45: return "Brown" if v < 0.75 or (s < 0.5 and l < 0.75) else "Orange"
    if 45 <= h < 68: return "Brown" if v < 0.6 or (s < 0.45 and l < 0.7) else "Yellow"
    if 68 <= h < 170: return "Green"
    if 170 <= h < 255: return "Blue"
    if 255 <= h < 290: return "Purple"
    if 290 <= h < 335: return "Pink" if l > 0.55 else "Purple"
    return "Pink" if l > 0.68 or (s < 0.45 and l > 0.55) else "Brown" if v < 0.45 and s < 0.6 else "Red"

def family(name, hexv):
    """Red, Pink, ... White: from the name's colour word, or the colour itself when the name has none or plainly
    disagrees with it (DMC 18 "Yellow Plum" is a yellow)."""
    h, l, s, v = hls(hexv); fam = None
    words = [w for w in re.findall(r"[a-z]+", name.lower()) if w in WORD]
    if len(words) > 1: words = [w for w in words if w != "black"]   # "Avocado Grn Black" is a very dark green
    if words: fam = WORD[words[-1]]
    if fam == "Red/Pink": fam = "Pink" if l > 0.6 else "Red"
    elif fam == "Orange/Brown": fam = "Orange" if l > 0.55 else "Brown"
    by_hue = hue_family(hexv)
    if not fam: return by_hue
    chromatic = {"Grey", "Black", "White"}.isdisjoint({fam, by_hue})
    if chromatic and fam != by_hue and frozenset((fam, by_hue)) not in NEAR and s > 0.3: return by_hue
    return fam

def colours(cid):
    """The catalogue's colours as dicts, each with its family."""
    cat = pk.catalogue(cid)
    return [{"code": code, "name": name, "hex": h.upper(), "family": family(name, h)} for code, name, h in cat["colors"]]

# ---------- adding them to the stash ----------
import json
from flask import abort, flash, redirect, render_template, request, url_for
from core import app, clean, db, log, num, q, type_of
from pools import new_field, save_fields

CODE_FIELD = re.compile(r"(colou?r )?(number|code|no\.?|#)$", re.I)
FIELDS = (("Brand", "text", ()), ("Colour number", "text", ()), ("Colour family", "choice", FAMILIES))

def default_for(t):
    """The catalogue a type most likely uses, from its own and its collection's name."""
    words = f"{t['name']} {t['pool_name']}".lower()
    if "diamond" in words or "drill" in words: return "dmc-diamond"
    for cid, w in (("hama-midi", "hama"), ("artkal-s", "artkal"), ("perler-midi", "perler"), ("perler-midi", "bead")):
        if w in words: return cid
    return "dmc-floss"

def owned(t, cid):
    """Codes of this catalogue already in the type: an item naming the code (in its name, colour, variant or a
    number field) with the brand somewhere on it, its type or its collection. Lower-cased."""
    cat = pk.catalogue(cid); brand = cat["brand"].lower(); codes = {c.lower() for c, _, _ in cat["colors"]}
    keep = {f["id"] for f in json.loads(t["fields"]) if CODE_FIELD.search(f["label"]) or f["label"].lower() == "brand"}
    have = set()
    for i in q("SELECT name, colour, subtype, extra FROM items WHERE type_id=?", t["id"]):
        x = json.loads(i["extra"] or "{}")
        text = " ".join([i["name"], i["colour"], i["subtype"], *(str(v) for k, v in x.items() if k in keep)]).lower()
        if brand in f"{text} {t['name']} {t['pool_name']}".lower(): have |= codes & set(re.findall(r"[a-z0-9]+", text))
    return have

def ensure_fields(t):
    """The type's Brand, Colour number and Colour family field ids, adding any that are missing (up to 20 fields)."""
    fl = json.loads(t["fields"]); ids = {}; before = len(fl)
    for label, typ, opts in FIELDS:
        f = next((f for f in fl if f["label"].lower() == label.lower() or (label == "Colour number" and CODE_FIELD.fullmatch(f["label"]))), None)
        if not f and len(fl) < 20: f = new_field(label, typ, opts); fl.append(f)
        if f: ids[label] = f["id"]
    if len(fl) > before: save_fields(t["id"], fl)
    return ids

@app.route("/types/<int:tid>/catalogue", methods=["GET", "POST"])
def catalogue_add(tid):
    t = type_of(tid, write=True)
    cid = request.values.get("c") or default_for(t)
    if cid not in pk.CATALOGUES: abort(404)
    cat, rows, have = pk.catalogue(cid), colours(cid), owned(t, cid)
    if request.method == "POST":
        ticked = set(request.form.getlist("pick"))
        picked = [r for r in rows if r["code"] in ticked and r["code"].lower() not in have]
        if not picked:
            flash("Tick the colours you have first." if not ticked else "Those colours are in this type already.")
            return redirect(url_for("catalogue_add", tid=tid, c=cid))
        ids, where = ensure_fields(t), clean("location", 80)
        for r in picked:
            extra = {ids[k]: v for k, v in (("Brand", cat["brand"]), ("Colour number", r["code"]), ("Colour family", r["family"])) if k in ids}
            qty = num(request.form.get(f"qty_{r['code']}"), 1.0)
            db().execute("INSERT INTO items(type_id,name,colour,hex,qty,location,extra) VALUES(?,?,?,?,?,?,?)",
                         (tid, f"{cat['brand']} {r['code']}", r["name"], r["hex"], qty, clean(f"loc_{r['code']}", 80) or where, json.dumps(extra)))
        n = len(picked); what = f"{n} {pk.CATALOGUES[cid]} colour{'s' if n != 1 else ''}"
        log(t["pool_id"], "catalogue", f"Added {what} to {t['name']} from the catalogue", commit=False); db().commit()
        flash(f"Added {what}." + (f" {len(ticked) - n} you ticked were here already." if len(ticked) > n else ""))
        return redirect(url_for("pool", pid=t["pool_id"]) + f"#type-{tid}")
    places = sorted({r["location"] for r in q("""SELECT i.location FROM items i JOIN types ty ON ty.id=i.type_id
                                                  WHERE ty.pool_id=? AND i.location<>''""", t["pool_id"])}, key=str.lower)
    return render_template("catalogue.html", t=t, cid=cid, cat=cat, rows=rows, have=have, families=FAMILIES,
                           catalogues=pk.CATALOGUES, places=places)
