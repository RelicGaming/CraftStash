"""Fetch a page or picture someone pasted a link to, and read what it's about (its Open Graph tags, or a board's feed).

The server fetches URLs that users type, so every request is checked first: http or https on the usual ports, no
user name or password in the link, and a host whose every address is public (never loopback, the LAN, link-local,
the Docker network or carrier NAT). The connection goes to the address that was checked, so a second DNS answer can't
swap in a private one, and each redirect is checked the same way. Time and size are limited, and pictures are
re-encoded by save_photo like every other upload. LINK_IMPORT=off turns all of it off.
"""
import html, http.client, ipaddress, os, re, socket, ssl, time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

ENABLED = os.environ.get("LINK_IMPORT", "on").strip().lower() not in ("off", "0", "false", "no")
PORTS = {"http": 80, "https": 443}
MAX_PAGE = 2 * 1024 * 1024          # a page is cut off here: the tags we read are near the top
MAX_IMAGE = 10 * 1024 * 1024        # a bigger picture is refused
TIMEOUT = 8                         # seconds to connect, and between bytes
TOTAL = 20                          # seconds for one fetch, redirects included
REDIRECTS = 5
MAX_PINS = 50                       # pins read from one board feed
UA = "Mozilla/5.0 (compatible; CraftStash link import)"

class FetchError(Exception):
    """A link that can't be imported. The message says why, in words for the page or the API."""

class Page:
    def __init__(self, url, ctype, body): self.url, self.ctype, self.body = url, ctype, body
    @property
    def mime(self): return self.ctype.split(";")[0].strip().lower()
    def text(self):
        m = re.search(r"charset=[\"']?([\w-]+)", self.ctype, re.I)
        try: return self.body.decode(m.group(1) if m else "utf-8", "replace")
        except LookupError: return self.body.decode("utf-8", "replace")

# ---------- the checks ----------
def addr_ok(ip):
    """True for an address on the public internet. Tests swap this out to reach their local server."""
    a = ipaddress.ip_address(ip.split("%")[0])
    if a.version == 6 and a.ipv4_mapped: a = a.ipv4_mapped
    return a.is_global and not a.is_multicast

def check_url(url):
    """(scheme, host, port, path) for a link that's allowed, or FetchError."""
    try: u = urlsplit(str(url or "").strip()); scheme = u.scheme.lower(); host = u.hostname; port = u.port
    except ValueError: raise FetchError("That doesn't look like a web link.") from None
    if scheme not in PORTS or not host: raise FetchError("Use a web link that starts with http:// or https://.")
    if u.username is not None or u.password is not None: raise FetchError("Links with a user name or password in them can't be imported.")
    if (port or PORTS[scheme]) != PORTS[scheme]: raise FetchError("Only links on the usual web ports can be imported.")
    try: host = host.encode("idna").decode("ascii") if not re.fullmatch(r"[\x21-\x7e]+", host) else host
    except UnicodeError: raise FetchError("That site's name isn't valid.") from None
    return scheme, host, PORTS[scheme], (u.path or "/") + ("?" + u.query if u.query else "")

def resolve(host, port):
    """The address to connect to. Every address the name has must be public, so a mix can't sneak a private one in."""
    try: infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError): raise FetchError("That site couldn't be found. Check the link.") from None
    ips = list(dict.fromkeys(i[4][0] for i in infos))
    if not ips or not all(addr_ok(ip) for ip in ips): raise FetchError("That link points at a private or local address, which isn't allowed.")
    return ips[0]

class _Plain(http.client.HTTPConnection):
    def __init__(self, host, ip, port): super().__init__(host, port, timeout=TIMEOUT); self.ip = ip
    def connect(self): self.sock = socket.create_connection((self.ip, self.port), self.timeout)

class _TLS(http.client.HTTPSConnection):
    """HTTPS to the checked address. The certificate is still checked against the host name in the link."""
    def __init__(self, host, ip, port): super().__init__(host, port, timeout=TIMEOUT); self.ip = ip
    def connect(self):
        sock = socket.create_connection((self.ip, self.port), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)

def fetch(url, accept="text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", limit=None):
    """GET a checked link, following checked redirects. Pages are cut at MAX_PAGE; anything else over limit
    (default MAX_IMAGE) is refused."""
    deadline = time.monotonic() + TOTAL; limit = limit or MAX_IMAGE
    for _ in range(REDIRECTS + 1):
        scheme, host, port, path = check_url(url)
        conn = (_TLS if scheme == "https" else _Plain)(host, resolve(host, port), port)
        try:
            conn.request("GET", path, headers={"User-Agent": UA, "Accept": accept, "Accept-Encoding": "identity", "Accept-Language": "en"})
            r = conn.getresponse()
            if r.status in (301, 302, 303, 307, 308) and r.getheader("Location"):
                url = urljoin(url, r.getheader("Location").strip()); continue
            if r.status != 200: raise FetchError(f"The site answered with an error ({r.status}). Check the link, or add the picture yourself.")
            ctype = r.getheader("Content-Type") or ""
            page = re.match(r"\s*(text/|application/(xhtml|rss|atom)?\+?xml)", ctype, re.I)
            cap = MAX_PAGE if page else limit
            n = r.getheader("Content-Length") or ""
            if not page and n.isdigit() and int(n) > cap: raise FetchError("That file is too big to import.")
            body = bytearray()
            while len(body) <= cap:
                if time.monotonic() > deadline: raise FetchError("The site took too long to answer.")
                chunk = r.read(65536)
                if not chunk: break
                body += chunk
            if len(body) > cap:
                if not page: raise FetchError("That file is too big to import.")
                del body[cap:]
            return Page(url, ctype, bytes(body))
        except (OSError, http.client.HTTPException) as e:
            if isinstance(e, ssl.SSLCertVerificationError): raise FetchError("That site's security certificate isn't valid.") from None
            raise FetchError("That site couldn't be reached. Check the link, or try again later.") from None
        finally: conn.close()
    raise FetchError("That link redirects too many times.")

# ---------- reading a page ----------
class _Meta(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.meta, self.title, self.canonical, self._title = {}, "", "", False
    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v or "" for k, v in attrs}
        if tag == "meta":
            k = (a.get("property") or a.get("name") or a.get("itemprop") or "").strip().lower()
            if k and a.get("content", "").strip() and k not in self.meta: self.meta[k] = a["content"].strip()
        elif tag == "link" and "canonical" in a.get("rel", "").lower().split(): self.canonical = self.canonical or a.get("href", "")
        elif tag == "title": self._title = True
    def handle_endtag(self, tag):
        if tag == "title": self._title = False
    def handle_data(self, d):
        if self._title and len(self.title) < 300: self.title += d

def tidy(s, n): return re.sub(r"\s+", " ", html.unescape(str(s or ""))).strip()[:n]

def web(base, v):
    """v as an absolute http(s) link, or ''."""
    v = urljoin(base, str(v or "").strip()) if v else ""
    return v if re.match(r"https?://[^\s<>\"'`]+$", v, re.I) else ""

def page_info(page):
    """{title, description, image, url, site, price} from a page's Open Graph / Twitter / plain tags."""
    p = _Meta()
    try: p.feed(page.text()); p.close()
    except Exception: pass                                   # a broken page still gives what was read before it broke
    m = p.meta
    def first(*keys): return next((m[k] for k in keys if m.get(k)), "")
    url = web(page.url, first("og:url")) or web(page.url, p.canonical) or page.url
    price = first("product:price:amount", "og:price:amount", "price")
    title = tidy(first("og:title", "twitter:title") or p.title, 300)
    if is_pinterest(page.url) and " | " in title: title = title.split(" | ")[0]   # Pinterest adds " | keywords" to a pin's title
    return {"title": title[:100],
            "description": tidy(first("og:description", "description", "twitter:description"), 1500),
            "image": web(page.url, first("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src")),
            "url": url, "site": tidy(first("og:site_name"), 80), "price": price if re.fullmatch(r"\d+(\.\d+)?", price.replace(",", "")) else ""}

def is_image(page): return page.mime.startswith("image/")

def read_link(url):
    """What a link is about: fetches it once. A link straight to a picture gives that picture's bytes as image_data."""
    page = fetch(url)
    if is_image(page):
        name = re.sub(r"\.\w+$", "", urlsplit(page.url).path.rsplit("/", 1)[-1])
        return {"title": tidy(name.replace("-", " ").replace("_", " "), 100), "description": "", "image": page.url,
                "image_data": page.body, "url": page.url, "site": "", "price": ""}
    if not page.mime.startswith(("text/html", "application/xhtml")): raise FetchError("That link isn't a web page or a picture.")
    return page_info(page)

def fetch_image(url):
    """The bytes of a picture link (checked like any other link)."""
    page = fetch(url, "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8")
    if not is_image(page): raise FetchError("That link isn't a picture.")
    return page.body

# ---------- Pinterest ----------
PIN_HOST = re.compile(r"(^|\.)pinterest\.[a-z.]+$|^pin\.it$", re.I)

def is_pinterest(url):
    try: return bool(PIN_HOST.search(urlsplit(url).hostname or ""))
    except ValueError: return False

def source_key(url):
    """The same link written differently gives the same key (no www, query, fragment or trailing slash; any Pinterest country)."""
    try: u = urlsplit(str(url or "").strip())
    except ValueError: return ""
    host = re.sub(r"^www\.", "", (u.hostname or "").lower()); path = u.path.rstrip("/")
    m = re.match(r"/pin/(\d+)", path)
    if PIN_HOST.search(host) and m: return "pinterest/pin/" + m.group(1)
    return host + path

def board_feed(url):
    """A board link (pinterest.com/<user>/<board>/) as its public RSS feed. Any other link is taken to be a feed already."""
    try: u = urlsplit(str(url or "").strip())
    except ValueError: raise FetchError("That doesn't look like a web link.") from None
    parts = [p for p in u.path.split("/") if p]
    if PIN_HOST.search(u.hostname or "") and u.hostname != "pin.it" and len(parts) == 2 and parts[0] != "pin" and not parts[1].endswith(".rss"):
        return f"https://{u.hostname}/{parts[0]}/{parts[1]}.rss"
    return str(url).strip()

class _Img(HTMLParser):
    def __init__(self): super().__init__(convert_charrefs=True); self.src, self.words = "", []
    def handle_starttag(self, tag, attrs):
        if tag == "img" and not self.src: self.src = dict(attrs).get("src") or ""
    def handle_data(self, d): self.words.append(d)

def _local(tag): return tag.rsplit("}", 1)[-1].lower()

def feed_pins(page):
    """(feed title, [{title, link, image, description}]) from an RSS or Atom feed, newest first as the feed has them."""
    if re.search(rb"<!\s*(doctype|entity)", page.body, re.I): raise FetchError("That feed can't be read.")   # no entity tricks
    try: root = ET.fromstring(page.body)
    except ET.ParseError: root = None
    if root is None or _local(root.tag) not in ("rss", "feed", "rdf"):
        raise FetchError("That link isn't a board or a feed. Paste a public board's link, like pinterest.com/name/board.")
    chan = next((e for e in root.iter() if _local(e.tag) == "channel"), root)
    title = tidy(next((c.text for c in chan if _local(c.tag) == "title"), ""), 100)
    pins = []
    for e in root.iter():
        if _local(e.tag) not in ("item", "entry"): continue
        f = {_local(c.tag): c for c in e}
        link = f["link"].get("href") or f["link"].text if "link" in f else ""
        desc = (f.get("description") if f.get("description") is not None else f.get("summary", f.get("content")))
        img = _Img()
        try: img.feed((desc.text if desc is not None else "") or ""); img.close()
        except Exception: pass
        image = web(page.url, img.src)
        if image: image = re.sub(r"(i\.pinimg\.com/)\d+x/", r"\g<1>736x/", image)   # the feed's thumbnail, at pin size
        pins.append({"title": tidy(f["title"].text if "title" in f else "", 100) or "Pinterest idea", "link": web(page.url, link),
                     "image": image, "description": tidy(" ".join(img.words), 1500)})
        if len(pins) >= MAX_PINS: break
    return title, [p for p in pins if p["link"]]
