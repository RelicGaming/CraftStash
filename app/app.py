"""Craft Stash. `gunicorn app:app` (and the tests) start here.

The code lives in small modules that share one Flask app from core.py. Importing a route module registers its
routes, so the order below doesn't matter to the app, but each module only imports from the ones above it:
core -> auth, admin, items -> pools, projects, wishlist -> linkimport (with linkfetch), patterns (with patternkit) -> api
"""
from core import app, P, startup_summary   # noqa: F401  (re-exported: gunicorn and the tests use them)
import auth, admin, items, pools, projects, wishlist, linkimport, patterns, api   # noqa: F401  (importing registers their routes)
