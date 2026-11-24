"""Settings stay in step: .env.example, docker-compose.yml, what the app reads, the Unraid template, the startup log."""
import re, xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = {m[1] for m in re.finditer(r"^([A-Z][A-Z0-9_]*)=", (ROOT / ".env.example").read_text(), re.M)}
COMPOSE = (ROOT / "docker-compose.yml").read_text()
APP_SRC = "\n".join(p.read_text() for p in (ROOT / "app").glob("*.py"))
READ_BY_APP = set(re.findall(r"""(?:os\.environ\.get|os\.environ\[|secret|_env|_flag|getenv)\(?["']([A-Z][A-Z0-9_]+)["']""", APP_SRC))
INTERNAL = {"CRAFT_IN_CONTAINER", "SELFTEST_FAST", "TMPDIR", "DATA_DIR", "TEST_DATABASE_URL", "DATABASE_URL_FILE",
            "OIDC_ALLOW_HTTP"}      # OIDC_ALLOW_HTTP: test providers only, documented in oidc.py
COMPOSE_ONLY = {"PORT", "IMAGE", "COMPOSE_PROFILES", "PROXY_NETWORK", "NPM_HTTP_PORT", "NPM_HTTPS_PORT", "NPM_ADMIN_PORT",
                "NPM_DATA_DIR", "NPM_LETSENCRYPT_DIR", "POSTGRES_DATA_DIR", "MARIADB_DATA_DIR", "NPM_IMAGE", "POSTGRES_IMAGE",
                "MARIADB_IMAGE", "PUID", "PGID", "CHOWN"}        # used by compose or the entrypoint, not app/*.py

def test_every_example_setting_is_used():
    for var in ENV_EXAMPLE:
        assert "${" + var in COMPOSE or var in READ_BY_APP or var == "COMPOSE_PROFILES", f"{var} is in .env.example but nothing reads it"

def test_every_app_setting_is_in_the_example_and_compose():
    for var in {v for v in READ_BY_APP - INTERNAL if not v.endswith("_FILE")}:   # *_FILE: documented once, at the top
        assert var in ENV_EXAMPLE, f"the app reads {var} but .env.example doesn't mention it"
        assert "${" + var in COMPOSE, f"the app reads {var} but docker-compose.yml doesn't pass it"

def test_compose_only_settings_are_really_used():
    entry = (ROOT / "entrypoint.sh").read_text()
    for var in COMPOSE_ONLY:
        assert "${" + var in COMPOSE or var in entry or var == "COMPOSE_PROFILES", var

def test_unraid_template_matches_the_app():
    root = ET.parse(ROOT / "unraid" / "craft-stash.xml").getroot()
    cfg = {c.get("Target"): c for c in root.findall("Config")}
    for c in cfg.values():
        if c.get("Type") == "Variable": assert c.get("Target") in READ_BY_APP | COMPOSE_ONLY, c.get("Target")
    paths = {c.get("Target") for c in cfg.values() if c.get("Type") == "Path"}
    assert paths == {"/data", "/photos", "/files", "/backups"}
    assert cfg["PHOTOS_DIR"].text == "/photos" and cfg["UPLOADS_DIR"].text == "/files" and cfg["BACKUP_DIR"].text == "/backups"
    assert cfg["PUID"].text == "99" and cfg["PGID"].text == "100"
    for secret in ("DATABASE_URL", "SECRET_KEY"): assert cfg[secret].get("Mask") == "true"
    assert root.findtext("Icon").endswith("unraid/icon.png") and (ROOT / "unraid" / "icon.png").exists()

def test_startup_summary_never_shows_secrets(make_env, capsys):
    env = make_env(SECRET_KEY="sk-very-secret", TRUSTED_PROXIES="10.0.0.0/8", URL_PREFIX="/craft")
    env.mod.startup_summary()
    out = capsys.readouterr().err
    assert "[storage]" in out and "[config]" in out and "proxy=1 hop(s) from 10.0.0.0/8" in out and "prefix=/craft" in out
    assert "sk-very-secret" not in out and "secret_key=set" in out

def test_secret_key_file(make_env, tmp_path_factory):
    f = tmp_path_factory.mktemp("s") / "key"; f.write_text("from-a-file\n")
    assert make_env(SECRET_KEY_FILE=f).mod.app.secret_key == "from-a-file"

def test_readme_links_resolve():
    """Every #anchor in the README has a heading (or id) to land on, and every relative link points at a real file."""
    text = (ROOT / "README.md").read_text()
    def slug(h): return re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
    anchors = {slug(m[1]) for m in re.finditer(r"^#{1,6} (.+)$", text, re.M)} | set(re.findall(r'id="([^"]+)"', text))
    for target in re.findall(r"\]\(#([^)]+)\)", text): assert target in anchors, f"README links to #{target}, which isn't there"
    for target in re.findall(r"\]\((?!https?:|#)([^)#]+)", text): assert (ROOT / target).exists(), f"README links to {target}, which doesn't exist"
