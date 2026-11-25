"""Admin page, backups, and the in-app self-test."""
import backup, core, dbx, json, os, subprocess, sys, tempfile, threading
from datetime import datetime, timezone
from flask import (abort, after_this_request, flash, g, redirect, render_template, request, send_file, session,
    url_for)
from pathlib import Path
from werkzeug.security import generate_password_hash
from core import (app, clean, currency, get, local_time, P, purge_files, purge_photos, purge_project_photos, purge_step_photos,
    purge_wish_photos, pwv, q, run, signups_open, SSO, uid, valid_account)

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
    snaps = sorted(P.backups.glob("auto-*")) if P.backups.exists() else []
    last = datetime.fromtimestamp(snaps[-1].stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if snaps else None
    return render_template("admin.html", users=users, signups=signups_open(), quota=core.QUOTA, cur=currency(), snaps=len(snaps), last_snap=local_time(last) if last else None,
                           storage=P.report(), sso_warning=SSO.warning())

@app.post("/admin/users")
def admin_create():
    admin_only()
    name, pw = clean("username", 40), request.form.get("password", "")
    if valid_account(name, pw):
        try: run("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", name, generate_password_hash(pw), int(bool(request.form.get("is_admin"))))
        except dbx.IntegrityError: flash("That username is taken.")
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
        purge_files("user", id); purge_photos("user", id); purge_step_photos("user", id); purge_project_photos("user", id); purge_wish_photos(id)
        run("DELETE FROM users WHERE id=?", id)
    return redirect(url_for("admin"))

SET_SETTING = dbx.upsert("settings", ["key", "value"], ["key"], {"value": "excluded.value"})

@app.post("/admin/signups")
def admin_signups():
    admin_only()
    run(SET_SETTING, "signups", "0" if signups_open() else "1")
    return redirect(url_for("admin"))

@app.post("/admin/currency")
def admin_currency():
    admin_only(); sym = "".join(ch for ch in clean("currency", 4) if ch.isprintable()) or "$"
    run(SET_SETTING, "currency", sym)
    return redirect(url_for("admin"))

@app.post("/admin/backup")
def admin_backup():
    admin_only()
    fd, tmp = tempfile.mkstemp(prefix=".download-", suffix=".zip", dir=P.tmp); os.close(fd)   # on a mapped folder, not the container layer
    backup.make_zip(P, tmp)
    @after_this_request
    def _remove(resp):
        try: os.unlink(tmp)
        except OSError: pass
        return resp
    return send_file(tmp, as_attachment=True, download_name=f"craft-stash-backup-{backup.stamp()}.zip", mimetype="application/zip")

# ---------- self-test (runs selftest.py against a throw-away copy of the app; never touches real data) ----------
SELFTEST = {"state": "idle", "events": []}
SELFTEST_LOCK = threading.Lock()
SELFTEST_DIR = P.selftest

def _selftest_worker():
    SELFTEST_DIR.mkdir(parents=True, exist_ok=True)
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
