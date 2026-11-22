# Craft Stash
Self-hosted craft supplies inventory. Flask + SQLite, one container.

## Run locally
    cp .env.example .env
    docker compose up -d --build     # http://localhost:8080

The first account to register becomes admin. After that, use **Admin** to add users, reset passwords, and open/close sign-up.

## Features
- Accounts: everyone has private collections and projects. Admins manage users; anyone can change their own password.
- Collections (one per craft, like Knitting) → material types → items. Each type has its own unit and custom fields (text, number or dropdown) with your own labels.
- Items have a colour swatch, variant, stock, price, storage location and an optional low-stock level. Quick Use/Add buttons adjust stock; Duplicate makes new colourways fast.
- Search a collection for any text, or one field with `Label:value` (e.g. `Width:150`). Sort by name, colour, lowest stock or newest. Export to CSV.
- Shopping list: everything out of stock or at its low-stock level, across every collection you can use, with a copy-as-text button.
- Sharing: collections can be shared view-only or can-edit; projects are shared read-only (materials, steps, files, not your stock).
- Projects: assign supplies, see the estimated cost, write numbered steps, attach guides and patterns, print a clean sheet. Completing a project deducts the stock.
- Appearance: each user picks a theme (match device, Indigo light or dark, Forest, Blush, Ocean, Ember) or builds a custom one from four colours, plus a corner style. Saved per account.
- Failed logins are rate limited (8 tries per 10 minutes per user and address).

## GitHub → Dockhand
1. Create an empty GitHub repo, then from this folder:

       git init -b main && git add . && git commit -m "Craft Stash"
       git remote add origin git@github.com:<you>/craft-stash.git && git push -u origin main

2. In Dockhand, add a git-backed stack pointing at the repo, compose path `docker-compose.yml`.
3. Image, pick one:
   - **Build on host:** leave as is, if your Dockhand deploys with `docker compose up --build`.
   - **Prebuilt (GHCR):** the included workflow builds `ghcr.io/<you>/craft-stash:latest` on every push to `main`. Set the stack variable `IMAGE=ghcr.io/<you>/craft-stash:latest`. Make the package public (GitHub → Packages → Package settings), or add GHCR credentials to Dockhand.
4. Stack variables (all optional): `PORT`, `COOKIE_SECURE=1` behind HTTPS, `MAX_UPLOAD_MB`.

Back up the `/data` volume (SQLite DB + uploads).
