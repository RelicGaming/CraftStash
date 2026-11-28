"""The release number: one place for it, shown where people look, and a changelog section to match."""
import importlib.util, re, sys
from pathlib import Path
import pytest
from version import VERSION

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("release_notes", ROOT / "scripts/release_notes.py")
rn = importlib.util.module_from_spec(spec); spec.loader.exec_module(rn)

def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+(-(alpha|beta|rc)\.\d+)?", VERSION)

def test_changelog_has_a_section_for_this_version():
    body = rn.section((ROOT / "CHANGELOG.md").read_text(), VERSION)
    assert body and len(body) > 50

def test_release_notes_check_the_tag(capsys):
    rn.main(["--tag", f"v{VERSION}"]); assert "###" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e: rn.main(["--tag", "v9.9.9"])
    assert "does not match" in str(e.value)

def test_section_stops_at_the_next_release():
    text = "## [1.1.0] - 2027-01-01\n### Added\n- new\n\n## [1.0.0] - 2026-12-01\n- old\n\n[1.1.0]: x\n"
    assert rn.section(text, "1.1.0") == "### Added\n- new" and rn.section(text, "1.0.0") == "- old"
    assert rn.section(text, "2.0.0") is None

def test_version_shown_in_admin_api_and_log(env, amy, capsys):
    assert f"v{VERSION}" in amy.html("/admin")
    tok = re.search(r'value="(cs_[\w-]+)"', amy.post("/account/tokens", {"name": "t", "access": "read"}).get_data(as_text=True))[1]
    r = env.client().c.get("/api/v1", headers={"Authorization": f"Bearer {tok}"})
    assert r.json["app_version"] == VERSION and r.json["version"] == 1
    import core; core.startup_summary(sys.stdout)
    assert f"version={VERSION}" in capsys.readouterr().out
