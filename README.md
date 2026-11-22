# Craft Stash
Self-hosted craft supplies inventory. Flask + SQLite, one container.

## Run locally
    cp .env.example .env
    docker compose up -d --build     # http://localhost:8080

The first account to register becomes admin. After that, use **Admin** to add users, reset passwords, and open/close sign-up.

## Features
- Accounts: each person has private pools and projects.
- Pools (one per craft) → material types → items. Each type has its own custom fields (text, number or dropdown) with your own labels, editable any time (renaming keeps data).
- Pool sharing: view-only or can-edit. Editors can change stock and assign it to their own projects.
- Projects: assign items from pools you can edit; "free" = stock minus unused reservations; completing deducts stock.
- Project sharing (read-only): shared users see materials needed, step-by-step, notes and attached files, but not your stock.
- Search a pool for any text (including field values) or one field with `Label:value`, e.g. `Width:150`.
- Attach printable guides/patterns (pdf, images, docs). The project page has a print layout.

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
