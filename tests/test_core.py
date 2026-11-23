from conftest import seed

def test_first_user_is_admin_second_is_not(env):
    a = env.client().register("amy"); b = env.client().register("bob")
    assert a.get("/admin").status_code == 200 and b.get("/admin").status_code == 403

def test_signup_can_be_closed_by_admin(env, amy):
    amy.post("/admin/signups")
    assert env.client().c.get("/register").status_code == 403

def test_login_is_throttled_after_repeated_failures(env, amy):
    c = env.client()
    for _ in range(8): c.login("amy", "wrong-password")
    assert "Too many failed" in c.login("amy", "password1").get_data(as_text=True)

def test_changing_password_signs_out_other_devices(env, amy):
    phone = env.client(); phone.login("amy")
    assert phone.get("/").status_code == 200
    amy.post("/account", {"current": "password1", "new": "newpassword1", "confirm": "newpassword1"})
    assert phone.get("/").status_code == 302              # the old session is dead
    assert amy.get("/").status_code == 200                # the device that changed it stays in

def test_disabled_user_is_locked_out_everywhere(env, amy, bob):
    amy.post("/admin/users/2/active")
    assert bob.get("/").status_code == 302
    assert "disabled" in env.client().login("bob").get_data(as_text=True)

def test_html_has_csp_files_do_not(env, amy):
    seed(amy); r = amy.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"
    amy.post("/projects/1/upload", {"file": (__import__("io").BytesIO(b"%PDF-1.4"), "guide.pdf")})
    f = amy.get("/files/1"); assert f.mimetype == "application/pdf" and "Content-Security-Policy" not in f.headers

def test_secure_cookie_only_over_https_behind_a_proxy(make_env):
    c = make_env(TRUST_PROXY=1).client().c
    https = c.get("/login", headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "stash.example.com"})
    http = make_env(TRUST_PROXY=1).client().c.get("/login")
    assert "Secure" in https.headers["Set-Cookie"] and "Secure" not in http.headers["Set-Cookie"]

def test_login_throttle_key_uses_forwarded_address(make_env):
    e = make_env(TRUST_PROXY=1); e.client().register("amy")
    a = e.client()
    for _ in range(8): a.c.post("/login", data={"username": "amy", "password": "x", "_csrf": a.token()}, headers={"X-Forwarded-For": "10.0.0.1"})
    b = e.client()      # a different visitor behind the same proxy is not locked out
    r = b.c.post("/login", data={"username": "amy", "password": "password1", "_csrf": b.token()}, headers={"X-Forwarded-For": "10.0.0.2"})
    assert r.status_code == 302
