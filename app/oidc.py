"""OpenID Connect single sign-on (Authentik, Authelia, Keycloak, Pocket ID, Zitadel, Google, Microsoft...).

The authorization code flow with PKCE, using only the standard library:

1. /login/sso sends the browser to the provider with a random state, nonce and PKCE challenge kept in the session.
2. The provider sends it back to /login/sso/callback with a code. The state must match (and be under 10 minutes old).
3. The code is exchanged at the provider's token endpoint, directly over HTTPS, with the client secret and the PKCE
   verifier. The ID token in that answer is checked: issuer, audience (and azp), expiry, issue time and nonce.
   Its signature isn't checked: OpenID Connect Core 3.1.3.7 allows TLS validation of the token endpoint in its place
   for a token received straight from it, as here, and that keeps cryptography libraries out of the image.
4. The account is the one linked to (issuer, subject). A first sign-in either links by an admin's choice or creates
   an account (OIDC_AUTO_PROVISION); an existing local account with the same name is never taken over silently.

Settings: OIDC_ENABLED, OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET (or _FILE), OIDC_SCOPES, OIDC_REDIRECT_URI,
OIDC_USERNAME_CLAIM, OIDC_AUTO_PROVISION, OIDC_LINK_EXISTING, OIDC_NAME.
"""
import base64, hashlib, json, os, re, secrets, ssl, sys, time, urllib.error, urllib.parse, urllib.request
import dbx

LEEWAY = 120                 # seconds of clock difference allowed
STATE_TTL = 600              # a login must come back within 10 minutes
TIMEOUT = 10

class Error(Exception):
    """Shown to the user (in plain words); the detail goes to the log."""
    def __init__(self, message, detail=""): super().__init__(message); self.detail = detail

def _flag(name, default="0"): return (os.environ.get(name, "").strip() or default) in ("1", "true", "yes", "on")

class Config:
    def __init__(self):
        self.enabled = _flag("OIDC_ENABLED")
        self.issuer = os.environ.get("OIDC_ISSUER", "").strip().rstrip("/")
        self.client_id = os.environ.get("OIDC_CLIENT_ID", "").strip()
        self.client_secret = dbx.secret("OIDC_CLIENT_SECRET").strip()
        self.scopes = os.environ.get("OIDC_SCOPES", "").strip() or "openid profile email"
        self.redirect_uri = os.environ.get("OIDC_REDIRECT_URI", "").strip()
        self.username_claim = os.environ.get("OIDC_USERNAME_CLAIM", "").strip() or "preferred_username"
        self.auto_provision = _flag("OIDC_AUTO_PROVISION")
        self.link_existing = _flag("OIDC_LINK_EXISTING")
        self.name = os.environ.get("OIDC_NAME", "").strip()[:40] or "single sign-on"
        u = urllib.parse.urlsplit(self.issuer)
        self.local = u.hostname in ("localhost", "127.0.0.1", "::1") or (u.hostname or "").endswith(".localhost")
        # Plain http to a provider on another machine: only allowed with OIDC_ALLOW_HTTP, and then nothing proves who answered.
        self.insecure = self.enabled and u.scheme == "http" and not self.local
        if self.enabled: self.check()

    def warning(self):
        """A loud explanation for the log and the admin page when sign-in runs over plain http to another machine."""
        if not self.insecure: return ""
        return ("OIDC_ALLOW_HTTP is on and OIDC_ISSUER is plain http. Craft Stash trusts the ID token because it came "
                "straight from the provider over TLS; without TLS anyone on the network between this server and "
                f"{urllib.parse.urlsplit(self.issuer).hostname} can read the tokens or answer in the provider's place and "
                "log in as any user. Use https (or a provider on the same machine) and turn OIDC_ALLOW_HTTP off.")

    def check(self):
        missing = [n for n, v in (("OIDC_ISSUER", self.issuer), ("OIDC_CLIENT_ID", self.client_id),
                                  ("OIDC_CLIENT_SECRET", self.client_secret)) if not v]
        if missing: raise SystemExit(f"OIDC_ENABLED=1 needs {', '.join(missing)}.")
        u = urllib.parse.urlsplit(self.issuer)
        if u.scheme != "https" and not (u.scheme == "http" and (self.local or _flag("OIDC_ALLOW_HTTP"))):
            raise SystemExit("OIDC_ISSUER must be an https:// address (the provider's identity is checked through its certificate).")
        if "openid" not in self.scopes.split(): raise SystemExit("OIDC_SCOPES must include openid.")
        if self.redirect_uri and not re.match(r"^https?://", self.redirect_uri):
            raise SystemExit("OIDC_REDIRECT_URI must be the full address of /login/sso/callback, e.g. https://craft.example.com/login/sso/callback")

# ---------------------------------------------------------------- HTTP (replaced in tests)
class _NoDowngrade(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never from https to http (the Authorization header would go out in the clear)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.startswith("https://") and not newurl.startswith("https://"):
            raise urllib.error.URLError(f"refused redirect from https to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

def http(method, url, data=None, headers=None):
    """Fetch JSON from the provider. Certificates are always verified."""
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={"Accept": "application/json", **(headers or {})})
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoDowngrade)
        with opener.open(req, timeout=TIMEOUT) as r:
            return json.loads(r.read(1_000_001)[:1_000_000] or b"{}")
    except urllib.error.HTTPError as e:
        try: err = json.loads(e.read(10_000) or b"{}")
        except ValueError: err = {}
        raise Error("The sign-in provider refused the request.", f"{method} {url}: HTTP {e.code} {err.get('error', '')} {err.get('error_description', '')}")
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Error("The sign-in provider can't be reached right now. Try again in a minute.", f"{method} {url}: {e}")

_discovery = {}
def discover(cfg):
    """The provider's endpoints, from .well-known/openid-configuration (cached for an hour)."""
    hit = _discovery.get(cfg.issuer)
    if hit and time.time() - hit[0] < 3600: return hit[1]
    d = http("GET", cfg.issuer + "/.well-known/openid-configuration")
    if (d.get("issuer") or "").rstrip("/") != cfg.issuer:
        raise Error("Single sign-on is set up wrongly.", f"discovery issuer {d.get('issuer')!r} != OIDC_ISSUER {cfg.issuer!r}")
    for k in ("authorization_endpoint", "token_endpoint"):
        if not isinstance(d.get(k), str) or not d[k].startswith(("https://", "http://")):
            raise Error("Single sign-on is set up wrongly.", f"discovery has no usable {k}")
    if not cfg.issuer.startswith("http://") and not d["token_endpoint"].startswith("https://"):
        raise Error("Single sign-on is set up wrongly.", "token_endpoint is not https")
    ui = d.get("userinfo_endpoint")
    if ui is not None and not (isinstance(ui, str) and ui.startswith("https://" if cfg.issuer.startswith("https://") else ("https://", "http://"))):
        # The access token would go to it in the clear (or somewhere odd): never use it, but sign-in can still work
        # when the username is in the ID token.
        print(f"[sso] ignoring userinfo_endpoint {ui!r}: an https issuer's userinfo must be https too", file=sys.stderr, flush=True)
        d = {k: v for k, v in d.items() if k != "userinfo_endpoint"}
    _discovery[cfg.issuer] = (time.time(), d)
    return d

# ---------------------------------------------------------------- the flow
def b64url(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

def start(cfg, redirect_uri, mode="login"):
    """(URL to send the browser to, what to keep in the session)."""
    d = discover(cfg)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    q = {"response_type": "code", "client_id": cfg.client_id, "redirect_uri": redirect_uri, "scope": cfg.scopes,
         "state": state, "nonce": nonce, "code_challenge": b64url(hashlib.sha256(verifier.encode()).digest()),
         "code_challenge_method": "S256"}
    sep = "&" if "?" in d["authorization_endpoint"] else "?"
    return d["authorization_endpoint"] + sep + urllib.parse.urlencode(q), \
        {"state": state, "nonce": nonce, "verifier": verifier, "mode": mode, "t": int(time.time()), "redirect_uri": redirect_uri}

def decode_jwt(token):
    try:
        head, payload, _sig = token.split(".")
        pad = lambda s: s + "=" * (-len(s) % 4)
        return json.loads(base64.urlsafe_b64decode(pad(head))), json.loads(base64.urlsafe_b64decode(pad(payload)))
    except (ValueError, AttributeError) as e:
        raise Error("The sign-in provider sent something unexpected.", f"ID token isn't a JWT: {e}")

def validate(cfg, claims, header, nonce, now=None):
    """The ID token checks of OpenID Connect Core 3.1.3.7 (bar the signature; see the module docstring)."""
    now = now or time.time()
    def bad(why): raise Error("The sign-in couldn't be verified. Try again.", f"ID token rejected: {why}")
    if header.get("alg", "none").lower() == "none": bad("alg none")
    if (claims.get("iss") or "").rstrip("/") != cfg.issuer: bad(f"iss {claims.get('iss')!r}")
    aud = claims.get("aud"); auds = aud if isinstance(aud, list) else [aud]
    if cfg.client_id not in auds: bad(f"aud {aud!r}")
    if len(auds) > 1 and claims.get("azp") != cfg.client_id: bad(f"azp {claims.get('azp')!r}")
    if "azp" in claims and claims["azp"] != cfg.client_id: bad(f"azp {claims['azp']!r}")
    try: exp, iat = float(claims["exp"]), float(claims["iat"])
    except (KeyError, TypeError, ValueError): bad("no exp/iat")
    if exp < now - LEEWAY: bad("expired")
    if iat > now + LEEWAY: bad("issued in the future")
    if not secrets.compare_digest(str(claims.get("nonce", "")), str(nonce)): bad("nonce mismatch")
    if not isinstance(claims.get("sub"), str) or not claims["sub"]: bad("no sub")
    return claims

def finish(cfg, saved, args):
    """Check the provider's answer and exchange the code. Returns the ID token's claims."""
    if not saved: raise Error("That sign-in link has expired. Start again.", "no OIDC state in the session")
    if args.get("error"):
        raise Error("Sign-in was cancelled or refused by the provider.", f"{args.get('error')}: {args.get('error_description', '')}")
    if not secrets.compare_digest(str(args.get("state", "")), str(saved.get("state", ""))):
        raise Error("That sign-in link has expired. Start again.", "state mismatch")
    if time.time() - saved.get("t", 0) > STATE_TTL: raise Error("That sign-in took too long. Start again.", "state expired")
    if (args.get("iss") or cfg.issuer).rstrip("/") != cfg.issuer: raise Error("The sign-in couldn't be verified.", f"iss param {args.get('iss')!r}")
    code = args.get("code") or ""
    if not code: raise Error("The sign-in provider sent no code.", "no code")
    d = discover(cfg)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": saved["redirect_uri"], "code_verifier": saved["verifier"]}
    methods = d.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    headers = {}
    if "client_secret_basic" in methods:
        raw = f"{urllib.parse.quote(cfg.client_id, safe='')}:{urllib.parse.quote(cfg.client_secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(raw.encode()).decode()
    else:
        form.update(client_id=cfg.client_id, client_secret=cfg.client_secret)
    tok = http("POST", d["token_endpoint"], form, headers)
    if not isinstance(tok.get("id_token"), str): raise Error("The sign-in provider sent something unexpected.", "no id_token")
    header, claims = decode_jwt(tok["id_token"])
    validate(cfg, claims, header, saved["nonce"])
    # Profile claims may only be in the userinfo answer (Authelia, for one, keeps the ID token small).
    if cfg.username_claim not in claims and d.get("userinfo_endpoint") and tok.get("access_token"):
        try:
            info = http("GET", d["userinfo_endpoint"], headers={"Authorization": "Bearer " + tok["access_token"]})
            if info.get("sub") == claims["sub"]:
                claims = {**info, **{k: claims[k] for k in ("iss", "aud", "sub", "exp", "iat", "nonce") if k in claims}}
        except Error: pass
    return claims

def username_from(cfg, claims):
    """The local username for these claims, or "" when there's no usable one."""
    v = claims.get(cfg.username_claim)
    if not isinstance(v, str) or not v.strip():
        v = claims.get("preferred_username") or (claims.get("email") or "").split("@")[0] or ""
    if cfg.username_claim == "email" and "@" in v: v = v.split("@")[0]
    v = re.sub(r"[^\w.-]", "", v.strip())[:40]
    return v if re.fullmatch(r"[\w.-]{3,40}", v) else ""
