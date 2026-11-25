import pytest
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

def test_html_and_files_get_their_own_csp(env, amy):
    seed(amy); r = amy.get("/")
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"] and r.headers["X-Frame-Options"] == "DENY"
    amy.post("/projects/1/upload", {"file": (__import__("io").BytesIO(b"%PDF-1.4"), "guide.pdf")})
    f = amy.get("/files/1")
    assert f.mimetype == "application/pdf" and "inline" in f.headers["Content-Disposition"]       # the browser's viewer still opens it
    assert "Content-Security-Policy" not in f.headers and f.headers["Cross-Origin-Resource-Policy"] == "same-origin"
    amy.post("/projects/1/upload", {"file": (__import__("io").BytesIO(b"plain notes"), "notes.txt")})
    t = amy.get("/files/2"); assert "default-src 'none'" in t.headers["Content-Security-Policy"] and t.headers["X-Frame-Options"] == "SAMEORIGIN"

@pytest.mark.parametrize("name, data", [
    ("evil.pdf", b"<html><script>alert(1)</script>"), ("evil.png", b"<svg onload=alert(1)>"), ("evil.txt", b"<!DOCTYPE html><p>hi"),
    ("evil.jpg", b"GIF89a"), ("evil.webp", b"RIFF\0\0\0\0WAVE"), ("evil.docx", b"%PDF-1.4"), ("bin.txt", b"ab\x00cd"),
])
def test_uploads_must_be_what_their_name_says(env, amy, name, data):
    seed(amy); r = amy.post("/projects/1/upload", {"file": (__import__("io").BytesIO(data), name)})
    assert "look like a real" in amy.html("/projects/1") and not list((env.data / "uploads").glob("*." + name.rsplit(".")[1]))

@pytest.mark.parametrize("name, data", [
    ("notes.txt", "Café notes – 10% off".encode()), ("notes.md", b"# Pattern\n<b>bold</b> is fine mid-text"),
    ("a.png", b"\x89PNG\r\n\x1a\nrest"), ("a.webp", b"RIFF\0\0\0\0WEBPVP8 "), ("a.docx", b"PK\x03\x04rest"), ("a.doc", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
])
def test_real_files_are_accepted(env, amy, name, data):
    seed(amy); amy.post("/projects/1/upload", {"file": (__import__("io").BytesIO(data), name)})
    assert amy.get("/files/1").status_code == 200

def test_old_upload_that_isnt_a_pdf_downloads_instead_of_opening(env, amy):
    seed(amy); amy.post("/projects/1/upload", {"file": (__import__("io").BytesIO(b"%PDF-1.4"), "guide.pdf")})
    (stored,) = list((env.data / "uploads").glob("*.pdf")); stored.write_bytes(b"<html><script>alert(1)</script>")
    f = amy.get("/files/1")
    assert f.mimetype == "application/octet-stream" and f.headers["Content-Disposition"].startswith("attachment")

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
