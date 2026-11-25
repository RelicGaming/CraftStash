"""Single sign-on (OpenID Connect) against a fake provider."""
import base64, hashlib, json, time, urllib.parse
import pytest

ISS = "https://idp.example.com/realms/home"

def jwt(claims, alg="RS256"):
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()
    return f"{enc({'alg': alg, 'typ': 'JWT'})}.{enc(claims)}.c2lnbmF0dXJl"

class Provider:
    """Answers discovery, the token endpoint (checking PKCE, the client secret and the redirect URI) and userinfo."""
    def __init__(self):
        self.claims = {}; self.alg = "RS256"; self.pending = {}; self.calls = []; self.userinfo = None
        self.doc = {"issuer": ISS, "authorization_endpoint": ISS + "/auth", "token_endpoint": ISS + "/token",
                    "userinfo_endpoint": ISS + "/userinfo", "token_endpoint_auth_methods_supported": ["client_secret_basic"]}
    def __call__(self, method, url, data=None, headers=None):
        self.calls.append((method, url))
        if url.endswith("/.well-known/openid-configuration"): return self.doc
        if url == ISS + "/userinfo": return self.userinfo or {}
        assert method == "POST" and url == ISS + "/token"
        assert headers["Authorization"] == "Basic " + base64.b64encode(b"craft:s3cret").decode()
        p = self.pending[data["code"]]
        assert data["redirect_uri"] == p["redirect_uri"] and data["grant_type"] == "authorization_code"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        if challenge != p["code_challenge"]:
            import oidc; raise oidc.Error("refused", "PKCE mismatch")
        now = int(time.time())
        claims = {"iss": ISS, "aud": "craft", "sub": "user-1", "exp": now + 300, "iat": now, "nonce": p["nonce"],
                  "preferred_username": "amy", "email": "amy@example.com", **self.claims}
        return {"access_token": "at", "token_type": "Bearer", "id_token": jwt({k: v for k, v in claims.items() if v is not None}, self.alg)}

@pytest.fixture
def sso(make_env, monkeypatch):
    import oidc
    p = Provider(); monkeypatch.setattr(oidc, "http", p); oidc._discovery.clear()
    def make(**extra):
        env = make_env(OIDC_ENABLED="1", OIDC_ISSUER=ISS, OIDC_CLIENT_ID="craft", OIDC_CLIENT_SECRET="s3cret", OIDC_NAME="Keycloak", **extra)
        env.idp = p; return env
    return make

def sign_in(c, idp, tamper=None):
    """Start at /login/sso (or follow a Link button's redirect), 'approve' at the provider, come back."""
    r = c.get("/login/sso") if tamper != "linking" else c.post("/account/sso/link")
    assert r.status_code == 302 and r.headers["Location"].startswith(ISS + "/auth?")
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(r.headers["Location"]).query))
    assert q["code_challenge_method"] == "S256" and q["scope"] == "openid profile email" and q["client_id"] == "craft"
    idp.pending["code-1"] = q
    back = {"code": "code-1", "state": q["state"]}
    if tamper == "state": back["state"] = "forged"
    return c.get("/login/sso/callback?" + urllib.parse.urlencode(back))

def test_login_page_offers_sso_only_when_enabled(make_env, sso):
    assert "Log in with" not in make_env().client().html("/login")
    env = sso(); assert "Log in with Keycloak" in env.client().html("/login")

def test_first_login_creates_an_account_when_allowed(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); c = env.client()
    r = sign_in(c, env.idp); assert r.status_code == 302 and r.headers["Location"].endswith("/")
    assert "Your collections" in c.html("/")
    assert env.db().execute("SELECT username, is_admin FROM users").fetchall() == [("amy", 1)]   # first account = admin
    assert env.db().execute("SELECT issuer, subject, label FROM user_identities").fetchall() == [(ISS, "user-1", "amy@example.com")]
    c2 = env.client(); sign_in(c2, env.idp); assert "Your collections" in c2.html("/")         # second time: same account
    assert env.db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1

def test_no_account_without_auto_provision(sso):
    env = sso(); c = env.client()
    page = sign_in(c, env.idp).headers["Location"]; html = c.html("/login")
    assert "no account for amy" in html and env.db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

def test_existing_local_account_is_not_taken_over(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); env.client().register("Amy")                          # a local account, other case
    c = env.client(); sign_in(c, env.idp)
    assert "already exists" in c.html("/login") and c.get("/").status_code == 302              # not logged in
    assert env.db().execute("SELECT COUNT(*) FROM user_identities").fetchone()[0] == 0

def test_existing_account_links_automatically_only_when_told(sso):
    env = sso(OIDC_LINK_EXISTING="1"); env.client().register("Amy")
    c = env.client(); sign_in(c, env.idp); assert "Your collections" in c.html("/")
    assert env.db().execute("SELECT u.username FROM user_identities i JOIN users u ON u.id=i.user_id").fetchall() == [("Amy",)]

def test_link_and_unlink_from_the_account_page(sso):
    env = sso(); amy = env.client().register("amy-local")
    assert "Link Keycloak" in amy.html("/account")
    sign_in(amy, env.idp, tamper="linking"); assert "Linked" in amy.html("/account") and "amy@example.com" in amy.html("/account")
    c = env.client(); sign_in(c, env.idp); assert "Your collections" in c.html("/")              # now SSO works for it
    iid = env.db().execute("SELECT id FROM user_identities").fetchone()[0]
    amy.post(f"/account/sso/{iid}/unlink"); assert env.db().execute("SELECT COUNT(*) FROM user_identities").fetchone()[0] == 0

def test_one_provider_account_cant_be_linked_to_two_users(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); first = env.client(); sign_in(first, env.idp)
    bob = env.client().register("bob"); sign_in(bob, env.idp, tamper="linking")
    assert "already linked to another user" in bob.html("/account")

def test_disabled_account_cant_sign_in(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); sign_in(env.client(), env.idp)
    d = env.db(); d.execute("UPDATE users SET active=0"); d.commit()
    c = env.client(); sign_in(c, env.idp); assert "disabled" in c.html("/login")

@pytest.mark.parametrize("claims, why", [
    ({"iss": "https://evil.example.com"}, "issuer"), ({"aud": "someone-else"}, "audience"),
    ({"aud": ["craft", "other"], "azp": "other"}, "azp"), ({"exp": int(time.time()) - 3600}, "expired"),
    ({"iat": int(time.time()) + 3600}, "future"), ({"nonce": "replayed"}, "nonce"), ({"sub": None}, "subject"),
])
def test_bad_id_tokens_are_refused(sso, claims, why):
    env = sso(OIDC_AUTO_PROVISION="1"); env.idp.claims = claims; c = env.client()
    sign_in(c, env.idp)
    assert "be verified" in c.html("/login"), why
    assert env.db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0

def test_unsigned_tokens_are_refused(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); env.idp.alg = "none"; c = env.client(); sign_in(c, env.idp)
    assert "be verified" in c.html("/login")

def test_forged_or_replayed_state_is_refused(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); c = env.client(); sign_in(c, env.idp, tamper="state")
    assert "expired" in c.html("/login") and env.db().execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    r = c.get("/login/sso/callback?code=code-1&state=anything")                                 # no login in progress
    assert "expired" in c.html("/login")

def test_provider_errors_are_explained(sso):
    env = sso(); c = env.client(); c.get("/login/sso")
    c.get("/login/sso/callback?error=access_denied&state=x"); assert "cancelled or refused" in c.html("/login")

def test_username_from_userinfo_when_the_id_token_has_none(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); env.idp.claims = {"preferred_username": None, "email": None}
    env.idp.userinfo = {"sub": "user-1", "preferred_username": "zoe"}
    sign_in(env.client(), env.idp)
    assert env.db().execute("SELECT username FROM users").fetchall() == [("zoe",)]

def test_unusable_username_is_explained(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); env.idp.claims = {"preferred_username": "!!", "email": None}; c = env.client()
    sign_in(c, env.idp); assert "no usable username" in c.html("/login")

def test_sso_session_cant_use_the_api(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); c = env.client(); sign_in(c, env.idp)
    assert c.get("/api/v1").status_code == 401                                                 # API still needs a token

def test_sso_routes_are_off_when_disabled(make_env):
    c = make_env().client()
    assert c.get("/login/sso").status_code == 404 and c.get("/login/sso/callback?code=x&state=y").status_code == 404

@pytest.mark.parametrize("env, msg", [
    ({"OIDC_ISSUER": ""}, "OIDC_ISSUER"), ({"OIDC_CLIENT_SECRET": ""}, "OIDC_CLIENT_SECRET"),
    ({"OIDC_ISSUER": "http://idp.example.com"}, "https"), ({"OIDC_SCOPES": "profile email"}, "openid"),
    ({"OIDC_REDIRECT_URI": "/login/sso/callback"}, "OIDC_REDIRECT_URI"),
])
def test_misconfiguration_stops_the_app_with_a_clear_message(make_env, env, msg):
    base = dict(OIDC_ENABLED="1", OIDC_ISSUER=ISS, OIDC_CLIENT_ID="craft", OIDC_CLIENT_SECRET="s3cret")
    with pytest.raises(SystemExit, match=msg): make_env(**{**base, **env})

def test_discovery_must_match_the_issuer(sso):
    env = sso(); env.idp.doc["issuer"] = "https://other.example.com"; c = env.client()
    assert c.get("/login/sso").status_code == 302 and "set up wrongly" in c.html("/login")

def test_redirect_uri_follows_url_prefix_and_can_be_set(sso):
    env = sso(URL_PREFIX="/craft"); r = env.client().get("/craft/login/sso")
    assert "redirect_uri=http%3A%2F%2Flocalhost%2Fcraft%2Flogin%2Fsso%2Fcallback" in r.headers["Location"]
    import oidc; oidc._discovery.clear()
    env = sso(OIDC_REDIRECT_URI="https://craft.example.com/login/sso/callback")
    assert "redirect_uri=https%3A%2F%2Fcraft.example.com" in env.client().get("/login/sso").headers["Location"]

def test_real_client_checks_the_providers_certificate(tmp_path):
    """oidc.http (not the fake) refuses a provider whose certificate isn't trusted, and parses JSON from one that is."""
    import shutil, ssl, subprocess, threading, http.server
    if not shutil.which("openssl"): pytest.skip("needs openssl")
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=127.0.0.1",
                    "-addext", "subjectAltName=IP:127.0.0.1", "-keyout", str(tmp_path / "k.pem"), "-out", str(tmp_path / "c.pem")],
                   check=True, capture_output=True)
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b'{"ok": 1}')
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); ctx.load_cert_chain(tmp_path / "c.pem", tmp_path / "k.pem")
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    import oidc
    url = f"https://127.0.0.1:{srv.server_address[1]}/x"
    try:
        with pytest.raises(oidc.Error, match="can't be reached"): oidc.http("GET", url)     # self-signed: refused
        import os; os.environ["SSL_CERT_FILE"] = str(tmp_path / "c.pem")                      # now trusted
        try: assert oidc.http("GET", url) == {"ok": 1}
        finally: os.environ.pop("SSL_CERT_FILE")
    finally: srv.shutdown()

def test_access_token_never_goes_to_a_plain_http_userinfo(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); env.idp.claims = {"preferred_username": None, "email": None}
    env.idp.doc["userinfo_endpoint"] = "http://idp.example.com/userinfo"; env.idp.userinfo = {"sub": "user-1", "preferred_username": "zoe"}
    c = env.client(); sign_in(c, env.idp)
    assert not any("userinfo" in url for _, url in env.idp.calls)
    assert "no usable username" in c.html("/login")

def test_username_in_the_id_token_still_works_when_userinfo_is_unusable(sso):
    env = sso(OIDC_AUTO_PROVISION="1"); env.idp.doc["userinfo_endpoint"] = "http://idp.example.com/userinfo"
    sign_in(env.client(), env.idp)
    assert env.db().execute("SELECT username FROM users").fetchall() == [("amy",)]

def test_redirect_from_https_to_http_is_refused():
    import urllib.error, urllib.request, oidc
    req = urllib.request.Request("https://idp.example.com/userinfo", headers={"Authorization": "Bearer at"})
    with pytest.raises(urllib.error.URLError, match="refused redirect"):
        oidc._NoDowngrade().redirect_request(req, None, 302, "Found", {}, "http://evil.example.com/")
    assert oidc._NoDowngrade().redirect_request(req, None, 302, "Found", {}, "https://idp.example.com/u2") is not None

def test_allow_http_to_another_machine_is_shouted_about(make_env, monkeypatch, capsys):
    import oidc
    http_iss = "http://idp.example.com"
    p = Provider(); p.doc = {**p.doc, "issuer": http_iss}; monkeypatch.setattr(oidc, "http", p); oidc._discovery.clear()
    env = make_env(OIDC_ENABLED="1", OIDC_ISSUER=http_iss, OIDC_ALLOW_HTTP="1", OIDC_CLIENT_ID="craft", OIDC_CLIENT_SECRET="s3cret")
    assert "[sso] WARNING: OIDC_ALLOW_HTTP is on" in capsys.readouterr().err
    c = env.client(); c.register("admin")
    assert "Single sign-on is not secure" in c.html("/admin")

def test_no_warning_for_https_or_a_provider_on_the_same_machine(make_env, sso, capsys):
    env = sso(); c = env.client(); c.register("admin")
    assert "not secure" not in c.html("/admin") and "WARNING" not in capsys.readouterr().err
    import oidc
    monkeypatch_env = {"OIDC_ENABLED": "1", "OIDC_ISSUER": "http://localhost:8080"}
    import os; old = {k: os.environ.get(k) for k in monkeypatch_env}; os.environ.update(monkeypatch_env)
    try: assert oidc.Config().insecure is False
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
