"""Import ideas from a link: a Pinterest pin (or any page with a title and a picture), a public Pinterest board, or
whatever the phone's Share menu sends. A link becomes a project with the status Idea, its picture as the cover and the
link as its source; a product can go to the wishlist instead. Fetching is in linkfetch, with its safety checks.
"""
import io, re, time, types
from urllib.parse import urlsplit
from flask import abort, flash, g, jsonify, redirect, render_template, request, url_for
import dbx, linkfetch as lf
import wishlist as wl
from core import app, clean, PHOTOS, q, run, save_photo, uid, URL_PREFIX

BOARD_PICK = 25          # pins imported in one go (the rest are there to import next time)
BOARD_SECONDS = 60       # stop downloading pictures after this, so a slow site can't hold the request past gunicorn's limit
PIN_IMAGES = "https://i.pinimg.com"   # the board list shows Pinterest's own thumbnails (only on that page)

class Imported:
    def __init__(self, kind, id, notes): self.kind, self.id, self.notes = kind, id, notes

def first_link(*texts):
    """The first web link in what a phone shared: Pinterest's app puts it in the text, others in url."""
    for t in texts:
        m = re.search(r"https?://[^\s<>\"'`]+", t or "")
        if m: return m.group(0).rstrip(".,;)")
    return ""

def photo_from(data):
    """Save downloaded picture bytes like an upload (re-encoded, metadata gone). The file name, or None."""
    return save_photo(types.SimpleNamespace(stream=io.BytesIO(data))) if data else None

def download_photo(url):
    try: return photo_from(lf.fetch_image(url)) if url else None
    except lf.FetchError: return None

def known_sources(user_id):
    """{source key: project id} for the user's own projects that came from a link (what a board import skips)."""
    return {lf.source_key(r["source"]): r["id"] for r in q("SELECT id, source FROM projects WHERE user_id=? AND source<>''", user_id)}

def new_idea(name, notes, source, photo, pinterest):
    pid = run("INSERT INTO projects(user_id,name,notes,status,photo,source,created,updated) VALUES(?,?,?,?,?,?,?,?)",
              uid(), name[:100] or "Imported idea", notes[:5000], "idea", photo or "", source[:500], dbx.now(), dbx.now()).lastrowid
    if pinterest: run(dbx.insert_ignore("project_tags", ["project_id", "tag"]), pid, "pinterest")
    return pid

def idea_notes(description, url):
    return (description + "\n\n" if description else "") + "From " + url

def import_link(url, to="project", name="", upload=None):
    """One link as an idea (or, with to="wishlist", a wish). name and upload (a picture file) are the manual fallback:
    with either, a page that can't be read still makes an idea. Raises lf.FetchError or wl.Invalid."""
    url = first_link(url) or str(url or "").strip()
    lf.check_url(url)
    info, notes = {}, []
    try: info = lf.read_link(url)
    except lf.FetchError as e:
        if not (name or (upload and upload.filename)): raise
        notes.append(f"Couldn't read that page ({e}) Saved it with what you entered.")
    photo = None
    if upload and upload.filename:
        photo = save_photo(upload)
        if not photo: notes.append("That picture couldn't be read, so the page's own picture was used.")
    if not photo: photo = photo_from(info.get("image_data")) if info.get("image_data") else download_photo(info.get("image"))
    if not photo and info and not (upload and upload.filename): notes.append("The page has no picture that could be downloaded. Add one on the project page.")
    source = lf.web(url, info.get("url")) or url
    host = re.sub(r"^www\.", "", urlsplit(source).hostname or "")
    title = name or info.get("title") or host or "Imported idea"
    if to == "wishlist":
        try:
            wid = wl.add_wish({"name": title[:120], "notes": info.get("description", "")[:1000], "url": source,
                               "price": info.get("price"), "shop": info.get("site") or ""})
        except wl.Invalid:
            if photo: (PHOTOS / photo).unlink(missing_ok=True)
            raise
        if photo: run("UPDATE wishes SET photo=? WHERE id=?", photo, wid)
        return Imported("wish", wid, notes)
    return Imported("project", new_idea(title, idea_notes(info.get("description", ""), source), source, photo,
                                        lf.is_pinterest(url) or lf.is_pinterest(source)), notes)

def board_pins(url):
    """(feed title, feed link, pins) with each pin marked imported when one of the user's projects came from it."""
    feed = lf.board_feed(url)
    page = lf.fetch(feed, "application/rss+xml,application/atom+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5")
    title, pins = lf.feed_pins(page)
    have = known_sources(uid())
    for p in pins: p["imported"] = have.get(lf.source_key(p["link"]))
    return title, feed, pins

def import_pins(pins, chosen=None):
    """Import the chosen pins (links; None: every new one) that aren't imported yet, up to BOARD_PICK.
    Returns (new project ids, how many were already there, how many are left for next time, pictures that failed)."""
    todo = [p for p in pins if not p["imported"] and (chosen is None or p["link"] in chosen)]
    already = sum(1 for p in pins if p["imported"] and (chosen is None or p["link"] in chosen))
    done, failed, start = [], 0, time.monotonic()
    for p in todo[:BOARD_PICK]:
        if time.monotonic() - start > BOARD_SECONDS: break
        photo = download_photo(p["image"])
        if p["image"] and not photo: failed += 1
        done.append(new_idea(p["title"], idea_notes(p["description"], p["link"]), p["link"], photo, lf.is_pinterest(p["link"])))
        p["imported"] = done[-1]
    return done, already, len(todo) - len(done), failed

def enabled():
    if not lf.ENABLED: abort(404)

# ---------- pages ----------
@app.route("/import/link", methods=["GET", "POST"])
def link_import():
    enabled()
    if request.method == "GET":
        a = request.args; url = first_link(a.get("url"), a.get("text"), a.get("title"))
        return render_template("link_import.html", url=url, name="", to=a.get("to") if a.get("to") == "wishlist" else "project",
                               shared=bool(a.get("url") or a.get("text")))
    url, name, to = clean("url", 500), clean("name", 100), "wishlist" if request.form.get("to") == "wishlist" else "project"
    try: done = import_link(url, to, name, request.files.get("photo"))
    except (lf.FetchError, wl.Invalid) as e:
        flash(str(e))
        return render_template("link_import.html", url=url, name=name, to=to, shared=False), 400
    for n in done.notes: flash(n)
    if done.kind == "wish": return redirect(url_for("wish", wid=done.id))
    flash("Saved as an idea. Change its status to Planned when you're ready to start it.")
    return redirect(url_for("project", id=done.id))

@app.route("/import/board", methods=["GET", "POST"])
def board_import():
    enabled()
    url = (request.form.get("url") if request.method == "POST" else request.args.get("url")) or ""
    url = url.strip()[:500]
    if not url: return render_template("board_import.html", url="", pins=None)
    try: title, feed, pins = board_pins(url)
    except lf.FetchError as e:
        flash(str(e)); return render_template("board_import.html", url=url, pins=None), 400
    if request.method == "POST":
        done, already, left, failed = import_pins(pins, set(request.form.getlist("pin")))
        msg = f"Imported {len(done)} {'idea' if len(done) == 1 else 'ideas'}." if done else "Nothing new to import."
        if already: msg += f" {already} {'was' if already == 1 else 'were'} imported before."
        if left: msg += f" {left} more to go: import again to get the rest."
        if failed: msg += f" {failed} {'picture' if failed == 1 else 'pictures'} couldn't be downloaded."
        flash(msg)
        return redirect(url_for("projects", status="idea") if done else url_for("board_import", url=url))
    g.img_hosts = [PIN_IMAGES]
    return render_template("board_import.html", url=url, feed=feed, title=title, pins=pins, pick=BOARD_PICK)

@app.get("/manifest.webmanifest")
def manifest():
    """Makes Craft Stash installable on a phone, and (with link import on) a Share target for links and pins."""
    m = {"name": "Craft Stash", "short_name": "Craft Stash", "id": URL_PREFIX + "/", "start_url": url_for("home"), "scope": URL_PREFIX + "/",
         "display": "standalone", "background_color": "#f4f5f7", "theme_color": "#3f46b8",
         "icons": [{"src": url_for("static", filename=f"icon-{n}.png"), "sizes": f"{n}x{n}", "type": "image/png", "purpose": "any maskable"}
                   for n in (192, 512)]}
    if lf.ENABLED:
        m["share_target"] = {"action": url_for("link_import"), "method": "GET", "params": {"title": "title", "text": "text", "url": "url"}}
    r = jsonify(m); r.mimetype = "application/manifest+json"; r.headers["Cache-Control"] = "public, max-age=86400"
    return r

@app.context_processor
def link_import_ctx(): return {"link_import_on": lf.ENABLED}
