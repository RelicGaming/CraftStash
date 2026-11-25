"""Log in, register, log out and single sign-on."""
import dbx, oidc, secrets, sys
from flask import abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from core import app, clean, db, FAILS, pwv, record_fail, run, signups_open, SSO, throttled, uid, valid_account

# ---------- auth ----------
def no_users(): return db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        name = clean("username", 40); ip = request.remote_addr or "?"
        if throttled(ip, name.lower()):
            flash("Too many failed attempts. Wait a few minutes and try again.")
        else:
            u = db().execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()
            if u and check_password_hash(u["pw"], request.form.get("password", "")):
                if not u["active"]: flash("This account is disabled. Ask an admin.")
                else:
                    FAILS.pop(f"{ip}|{name.lower()}", None); session.clear(); session["uid"] = u["id"]
                    session["pwv"] = pwv(u["pw"]); session.permanent = True
                    return redirect(url_for("home"))
            else:
                record_fail(ip, name.lower()); flash("Wrong username or password.")
    return render_template("auth.html", mode="login", can_register=signups_open(), first_run=no_users(), sso=SSO)

@app.route("/register", methods=["GET", "POST"])
def register():
    first = no_users()
    if not signups_open(): abort(403)     # ALLOW_SIGNUP=0 also closes the first-admin sign-up (use manage.py create-user)
    if request.method == "POST":
        name, pw = clean("username", 40), request.form.get("password", "")
        if valid_account(name, pw):
            h = generate_password_hash(pw)
            try: c = run("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", name, h, int(first))
            except dbx.IntegrityError: flash("That username is taken.")
            else:
                session.clear(); session["uid"] = c.lastrowid; session["pwv"] = pwv(h); session.permanent = True
                return redirect(url_for("home"))
    return render_template("auth.html", mode="register", can_register=True, sso=SSO)

@app.post("/logout")
def logout():
    session.clear(); return redirect(url_for("login"))

# ---------- single sign-on (OpenID Connect; see oidc.py) ----------

def sso_redirect_uri():
    return SSO.redirect_uri or url_for("sso_callback", _external=True)

def sign_in(u):
    session.clear(); session["uid"] = u["id"]; session["pwv"] = pwv(u["pw"]); session.permanent = True

def sso_fail(e, to="login"):
    print(f"[sso] {e.detail or e}", file=sys.stderr, flush=True)
    flash(str(e)); return redirect(url_for(to))

@app.get("/login/sso")
def sso_login():
    if not SSO.enabled: abort(404)
    try: url, saved = oidc.start(SSO, sso_redirect_uri(), "login")
    except oidc.Error as e: return sso_fail(e)
    session["oidc"] = saved
    return redirect(url)

@app.post("/account/sso/link")
def sso_link():
    if not SSO.enabled: abort(404)
    try: url, saved = oidc.start(SSO, sso_redirect_uri(), "link")
    except oidc.Error as e: return sso_fail(e, "account")
    session["oidc"] = saved
    return redirect(url)

@app.post("/account/sso/<int:iid>/unlink")
def sso_unlink(iid):
    run("DELETE FROM user_identities WHERE id=? AND user_id=?", iid, uid())
    flash(f"Unlinked. Log in with your password from now on (or link {SSO.name} again)."); return redirect(url_for("account"))

@app.get("/login/sso/callback")
def sso_callback():
    if not SSO.enabled: abort(404)
    saved = session.pop("oidc", None)
    linking = bool(saved and saved.get("mode") == "link" and g.user)
    back = "account" if linking else "login"
    try: claims = oidc.finish(SSO, saved, request.args)
    except oidc.Error as e: return sso_fail(e, back)
    if SSO.insecure: print(f"[sso] WARNING: sign-in for sub {claims.get('sub')!r} arrived over plain http (OIDC_ALLOW_HTTP)", file=sys.stderr, flush=True)
    iss, sub = SSO.issuer, claims["sub"]
    label = str(claims.get("email") or claims.get("preferred_username") or claims.get("name") or "")[:120]
    known = db().execute("SELECT i.user_id, u.* FROM user_identities i JOIN users u ON u.id=i.user_id WHERE i.issuer=? AND i.subject=?",
                         (iss, sub)).fetchone()
    if linking:
        if known and known["user_id"] != uid(): flash(f"That {SSO.name} account is already linked to another user.")
        elif known: flash(f"That {SSO.name} account is already linked to yours.")
        else:
            run("INSERT INTO user_identities(user_id,issuer,subject,label) VALUES(?,?,?,?)", uid(), iss, sub, label)
            flash(f"Linked. You can now log in with {SSO.name}.")
        return redirect(url_for("account"))
    if known:
        if not known["active"]: flash("This account is disabled. Ask an admin."); return redirect(url_for("login"))
        sign_in(known); return redirect(url_for("home"))
    name = oidc.username_from(SSO, claims)
    if not name:
        return sso_fail(oidc.Error(f"Your {SSO.name} account has no usable username. Ask an admin.",
                                   f"claim {SSO.username_claim!r} missing or invalid for sub {sub}"))
    same = db().execute("SELECT * FROM users WHERE username=?", (name,)).fetchone()   # case-insensitive column
    if same:
        if not SSO.link_existing:
            # Never take over a local account just because a provider reports the same name.
            flash(f"An account called {same['username']} already exists. Log in with its password, then link "
                  f"{SSO.name} from your Account page."); return redirect(url_for("login"))
        if not same["active"]: flash("This account is disabled. Ask an admin."); return redirect(url_for("login"))
        run("INSERT INTO user_identities(user_id,issuer,subject,label) VALUES(?,?,?,?)", same["id"], iss, sub, label)
        sign_in(same); return redirect(url_for("home"))
    if not SSO.auto_provision:
        flash(f"There's no account for {name} yet. Ask an admin to create one, then link {SSO.name} from your Account page.")
        return redirect(url_for("login"))
    first = no_users()
    if first and not signups_open(): flash("No accounts exist yet and sign-up is off. Create the first admin with manage.py."); return redirect(url_for("login"))
    h = generate_password_hash(secrets.token_urlsafe(32))     # no usable password: this account signs in through SSO
    try:
        c = db().execute("INSERT INTO users(username,pw,is_admin) VALUES(?,?,?)", (name, h, int(first)))
        db().execute("INSERT INTO user_identities(user_id,issuer,subject,label) VALUES(?,?,?,?)", (c.lastrowid, iss, sub, label))
        db().commit()
    except dbx.IntegrityError:
        db().rollback(); flash("That account was just created. Try logging in again."); return redirect(url_for("login"))
    sign_in(db().execute("SELECT * FROM users WHERE id=?", (c.lastrowid,)).fetchone())
    return redirect(url_for("home"))
