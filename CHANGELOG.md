# Changelog

All notable changes to Craft Stash. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and versions follow [Semantic Versioning](https://semver.org/). What a patch, minor and major release may change is
under [Versions and upgrades](README.md#versions-and-upgrades) in the README.

## [1.0.0-rc.1] - 2026-11-28

The first numbered release. Everything up to here, grouped by area.

### Stash
- Collections, material types and items with stock, units, prices, low-stock levels, locations and custom fields.
- Stock split across several storage places (#18), quick adjust, duplicate, and the shopping list with bulk restock (#16).
- Add items photo-first, with the swatch colour suggested from the photo (#22), and touch-friendly colour picking (#13).
- Smoother setup of a new collection, its types and items (#21).
- CSV import and export.
- Search across all collections and projects (#15).
- Add from a catalogue: tick the DMC flosses (or DMC drills, Perler, Hama, Artkal beads) you own, and each is added
  with its number, name, colour family and swatch, ready for a quantity and place (#42).

### Projects
- Projects with steps, step photos (#20), attachments, reserved materials and templates (#17).
- Complete a project, recording what was actually used (#14), or keep the stock (#13).
- Tags, dates and a filter, group and sort bar (#38).
- Cover pictures and the "idea" status, filled from links, Pinterest pins and boards, and the phone's Share menu (#40).

### Wishlist and tools
- Wishlist with shops, prices and sightings next to the restock list (#39).
- Tools menu with the pixel pattern maker: charts from a picture using DMC, Perler, Hama, Artkal or your own stash
  colours, estimates for four crafts, stock check, PNG and A4 PDF, and a project from the chart (#41).

### Sharing and accounts
- Several users with an admin page, shared collections and projects with view or edit access.
- Optional OpenID Connect single sign-on (#33), hardened in #36.
- Token-based JSON API for Home Assistant and scripts (#24).

### Running it
- SQLite by default, or PostgreSQL (#28) and MariaDB / MySQL (#29).
- Each storage folder can be mapped on its own (#27); automatic snapshots, Admin backup and `manage.py restore`.
- Any reverse proxy and sub-paths, with optional bundled Nginx Proxy Manager, Postgres and MariaDB (#30).
- Unraid template, a complete `.env.example` and a startup summary in the log (#31); README for every setup (#32, #35).
- Built-in self-test, from the Admin page or `python selftest.py`.

### Security
- Access control, login limiter, CSV formula safety, backups and CSP fixes from review (#12).
- Image with no fixable high vulnerabilities, no setuid programs and no pip (#23, #26).
- SQL building hardened with injection regression tests (#34); OIDC, SQL translator and attachment checks (#36).
- Link import fetches through an SSRF-guarded fetcher (#40).
- 1.0 review: pictures are size-checked before decoding (a 250 KB PNG used to take ~1 GB and get the container
  killed); the link fetcher refuses NAT64 and IPv4-compatible forms of private addresses and enforces its 20 second
  limit on slow sites; form return paths refuse tabs and newlines that browsers drop.

[1.0.0-rc.1]: https://github.com/RelicGaming/CraftStash/releases/tag/v1.0.0-rc.1
