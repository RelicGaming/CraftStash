"""Reverse proxies: URL_PREFIX (sub-path hosting) and TRUSTED_PROXIES."""
import re
import pytest
from conftest import seed

def test_url_prefix_links_assets_redirects_and_cookie(make_env):
    env = make_env(URL_PREFIX="/craft/"); c = env.client()
    page = c.html("/craft/login")
    assert 'href="/craft/static/app.css' in page and 'href="/craft/register"' in page
    assert not re.search(r'(href|src|action)="/(?!craft/)', page)                       # every link carries the prefix
    r = c.post("/craft/register", {"username": "amy", "password": "password1"})
    assert r.status_code == 302 and r.headers["Location"] in ("/craft/", "http://localhost/craft/")
    assert "Path=/craft" in r.headers["Set-Cookie"]
    assert c.get("/craft/static/app.css").status_code == 200 and "Your collections" in c.html("/craft/")

def test_url_prefix_works_when_the_proxy_strips_it_too(make_env):
    env = make_env(URL_PREFIX="/craft"); c = env.client()
    assert 'href="/craft/register"' in c.html("/login")                                 # proxy removed /craft
    assert c.get("/healthz").status_code == 200                                          # the container's own check

def test_return_address_keeps_the_prefix(make_env):
    env = make_env(URL_PREFIX="/craft"); c = env.client().register("amy"); seed(c)
    r = c.post("/craft/items/1/adjust", {"amount": "1", "action": "use", "next": "/collections/1?q=Merino"})
    assert r.headers["Location"].endswith("/craft/collections/1?q=Merino")

def test_forwarded_prefix_from_a_proxy(make_env):
    env = make_env(TRUST_PROXY="1"); c = env.client()
    assert 'href="/shop/register"' in c.get("/login", headers={"X-Forwarded-Prefix": "/shop"}).get_data(as_text=True)

@pytest.mark.parametrize("bad", ["/craft space", "/../x", "/a?b", "http://x"])
def test_bad_url_prefix_is_refused(make_env, bad):
    with pytest.raises(SystemExit, match="URL_PREFIX"): make_env(URL_PREFIX=bad)

def cookie_secure(c, **kw):
    r = c.c.post("/login", data={"username": "x", "password": "y"}, **kw)
    return "Secure" in r.headers.get("Set-Cookie", "")

def test_trusted_proxy_addresses_are_believed(make_env):
    env = make_env(TRUSTED_PROXIES="172.16.0.0/12, 10.0.0.5")
    https = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "203.0.113.9"}
    for addr in ("172.18.0.3", "10.0.0.5", "::ffff:10.0.0.5"):
        assert cookie_secure(env.client(), headers=https, environ_overrides={"REMOTE_ADDR": addr}), addr

def test_other_addresses_cant_spoof_forwarded_headers(make_env):
    env = make_env(TRUSTED_PROXIES="172.16.0.0/12"); c = env.client()
    spoof = {"X-Forwarded-Proto": "https", "X-Forwarded-For": "1.2.3.4", "X-Forwarded-Prefix": "/evil"}
    assert not cookie_secure(c, headers=spoof, environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
    page = c.get("/login", headers=spoof, environ_overrides={"REMOTE_ADDR": "192.168.1.50"}).get_data(as_text=True)
    assert "/evil" not in page

def test_spoofed_addresses_dont_dodge_the_login_limit(make_env):
    env = make_env(TRUSTED_PROXIES="172.16.0.0/12"); c = env.client()
    for n in range(12):
        r = c.c.post("/login", data={"username": "amy", "password": "wrong", "_csrf": c.token()},
                     headers={"X-Forwarded-For": f"198.51.100.{n}"}, environ_overrides={"REMOTE_ADDR": "192.168.1.50"})
    assert r.status_code == 429 or "Too many" in r.get_data(as_text=True)

def test_bad_trusted_proxies_is_refused(make_env):
    with pytest.raises(SystemExit, match="TRUSTED_PROXIES"): make_env(TRUSTED_PROXIES="10.0.0.0/8, my-proxy")
