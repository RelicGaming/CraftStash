#!/usr/bin/env python3
"""Print the CHANGELOG.md section for a release, for the GitHub Release body.

    python scripts/release_notes.py              # the section for app/version.py's VERSION
    python scripts/release_notes.py --tag v1.0.0 # also fail unless the tag is exactly v<VERSION>

Exits 1 with a message when the tag and app/version.py disagree, or the changelog has no section for the version.
"""
import argparse, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def version():
    return re.search(r'^VERSION = "([^"]+)"', (ROOT / "app/version.py").read_text(), re.M)[1]

def section(text, ver):
    """The body under '## [ver]' up to the next '## [' heading (or link list), or None."""
    m = re.search(rf"^## \[{re.escape(ver)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: |\Z)", text, re.M | re.S)
    return m[1].strip() if m else None

def main(argv=None):
    ap = argparse.ArgumentParser(); ap.add_argument("--tag"); a = ap.parse_args(argv)
    ver = version()
    if a.tag is not None and a.tag != f"v{ver}":
        sys.exit(f"Tag {a.tag} does not match app/version.py ({ver}). Set VERSION to {a.tag.removeprefix('v')} or retag.")
    body = section((ROOT / "CHANGELOG.md").read_text(), ver)
    if not body: sys.exit(f"CHANGELOG.md has no '## [{ver}]' section.")
    print(body)

if __name__ == "__main__": main()
