# Craft Stash
Self-hosted inventory for craft supplies: what you have, where it is, what each project needs and what to buy. One
container; SQLite by default, or PostgreSQL / MariaDB / MySQL; runs behind any reverse proxy.

- [Screenshots](#screenshots)
- [Choose your setup](#choose-your-setup)
- [Install](#install): [docker run](#docker-run) · [Docker Compose](#docker-compose) · [Dockhand](#dockhand) · [Unraid](#unraid) · [Portainer](#portainer)
- [First start](#first-start)
- [Project tags and filters](#project-tags-and-filters) · [Wishlist](#wishlist)
- [Storage](#storage) · [Database](#database) · [Reverse proxy](#reverse-proxy) · [Single sign-on](#single-sign-on)
- [Environment variables](#environment-variables)
- [Backups and restore](#backups-and-restore) · [API and Home Assistant](#api-and-home-assistant) · [Self-test](#self-test)
- [Troubleshooting](#troubleshooting) · [Development](#development)

## Features
- **Collections** (one per craft) hold **material types** (own unit and custom text/number/dropdown fields) which hold **items** (colour swatch, optional photo, variant, stock, price, location, low-stock level). An item can be **split across several storage places**: use or add stock at a particular place, or move some between places (CSV export shows the main location and the total).
- **Search everything**: one search box across every collection you own or that's shared with you, plus project names and notes.
- Search a collection for any text or one field (`Width:150`), sort, export or **import CSV** (with a check-only mode), see the **activity** history and undo stock changes.
- **Shopping list** across every collection you can use: out of stock, low, or short for a project. Enter what you bought (or take the suggested amounts) and restock everything in one go. Assigning more than you have is allowed and the gap lands on the list.
- **Wishlist** next to the shopping list: snap a photo of something you want, note the shop and price, add more shops to compare, then **Bought it** turns it into stock. See [Wishlist](#wishlist).
- **Projects**: reserve supplies, **templates** (save a project as a reusable pattern, start new projects from it; templates hold no stock), numbered steps (each with up to 6 photos, counted towards your storage limit), attached guides, estimated cost, print layout, duplicate, complete (enter how much of each material you actually used and only that is deducted, or keep stock untouched) and reopen (puts it back).
- **Import ideas** from a Pinterest pin, a public Pinterest board or any web page: each becomes an **Idea** project with its picture, or a wishlist item. Share links from your phone straight into Craft Stash. See [Importing ideas from links](#importing-ideas-from-links).
- **Pixel pattern maker** (Tools): turn a picture into a cross-stitch, fuse bead, diamond painting or colourwork chart in colours from your stash or a DMC, Perler, Hama or Artkal catalogue. It shows how much of each colour you need and whether you have it, prints a PDF, and makes a project that reserves the colours. See [Pixel pattern maker](#pixel-pattern-maker).
- **Tags and filters** for projects: tag them (`gifts`, `baby`), then search, filter by status, tag or the collection they use, group and sort. A filtered view has its own address, so you can bookmark it.
- **Sharing**: collections view-only or can-edit; projects read-only, and a viewer can save their own copy. People can leave what's shared with them.
- Per-user **themes** (presets or custom colours and corner style), changeable password, admin currency symbol, per-user storage limit, disable users.
- Daily database snapshots and a one-click full backup (admin).
- **JSON API** with per-user tokens, for Home Assistant and scripts. See [API and Home Assistant](#api-and-home-assistant).

## Screenshots
![A Knitting collection: yarn with photos, colour swatches, custom fields like weight and fibre, storage places, stock and price, with "Out of stock" and "Short by 2" flags](docs/screenshots/collection.png)
*A collection: every item with its photo, swatch, custom fields, where it's kept, how much is free and what it cost.*

<table>
<tr>
<td width="50%"><img src="docs/screenshots/collections.png" alt="The Collections page with Beading, Embroidery, Knitting and Sewing cards, each with a strip of its colours and an item count"><br><em>Collections, one per craft.</em></td>
<td width="50%"><img src="docs/screenshots/presets.png" alt="An empty Embroidery collection offering one-tap material types (Fabric, Embroidery floss, Yarn, Thread, Beads and more) or a custom type with fields"><br><em>Setting up a new collection with one-tap material types.</em></td>
</tr>
<tr>
<td><img src="docs/screenshots/item-form.png" alt="Editing Merino DK: the photo first with a Suggest colour button, then name, colour, swatch, weight dropdown, fibre, length, stock, low-stock level, price and location"><br><em>Photo-first item form; the swatch colour can come from the photo.</em></td>
<td><img src="docs/screenshots/shopping.png" alt="The shopping list grouped by collection: Chunky wool out of stock, Sock yarn short by 2 for a project, Double gauze below its low-stock level, each with a suggested amount to buy"><br><em>Shopping list: out, low, or short for a project, with amounts to buy.</em></td>
</tr>
<tr>
<td><img src="docs/screenshots/project.png" alt="The Cabled beanie project: materials needed with their cost, numbered steps, project details and a place for patterns"><br><em>A project with reserved materials, steps and patterns.</em></td>
<td><img src="docs/screenshots/projects.png" alt="The Projects page listing Cabled beanie (active), Ocean socks and Weekend tote (planned) and the Basic socks template"><br><em>Projects and templates.</em></td>
</tr>
<tr>
<td><img src="docs/screenshots/search.png" alt="Searching everything for cotton finds Cotton DK in Knitting, and Double gauze and Quilting cotton in Sewing"><br><em>One search across every collection.</em></td>
<td><img src="docs/screenshots/admin-storage.png" alt="Admin Storage panel: the database in /data, uploaded files in /files, photos in /photos and backups in /backups, each on its own volume with its free space"><br><em>Admin: where each kind of file lives (here split across volumes).</em></td>
</tr>
</table>

<p align="center">
<img src="docs/screenshots/phone-collection.png" width="280" alt="The Knitting collection on a phone in dark mode, with yarn photos, stock and the bottom tab bar">
&nbsp;&nbsp;
<img src="docs/screenshots/phone-shopping.png" width="280" alt="The shopping list on a phone in dark mode, with amounts to buy and the bottom tab bar"><br>
<em>On a phone, with the dark theme.</em>
</p>

## Choose your setup
| You want... | Use | Settings to look at |
|---|---|---|
| To try it on one machine | [docker run](#docker-run) or [Compose](#docker-compose) as-is | none |
| It on a home server, reached on the LAN | [Compose](#docker-compose) or [Unraid](#unraid) | `PUID`/`PGID`, `TZ`, folder mappings |
| Photos on the array / a NAS, database on fast disk | any install + [split storage](#storage) | `PHOTOS_DIR`, `UPLOADS_DIR`, `BACKUP_DIR` |
| HTTPS from outside, nothing set up yet | Compose with the bundled **Nginx Proxy Manager** | `COMPOSE_PROFILES=npm`, `TRUST_PROXY=1` |
| HTTPS through a proxy you already run | your proxy + an example from [`deploy/`](deploy/) | `TRUST_PROXY`, `TRUSTED_PROXIES` |
| It under a path, `https://example.com/craft/` | any proxy | `URL_PREFIX=/craft` |
| A PostgreSQL / MariaDB server you already run | any install | `DATABASE_URL` |
| A database container started for you | Compose | `COMPOSE_PROFILES=postgres` (or `mariadb`), `DB_TYPE`, `DB_PASSWORD` |
| Log in with Authentik, Authelia, Keycloak, Google... | any install | `OIDC_ENABLED` and friends ([Single sign-on](#single-sign-on)) |
| Changes from git deployed for you | [Dockhand](#dockhand) or [Portainer](#portainer) (git stack) | `IMAGE` |

The image is `ghcr.io/relicgaming/craftstash`. `:latest` is the newest release; see
[Versions and upgrades](#versions-and-upgrades) for the other tags. Compose can also build it from this repository.

## Install

<details open>
<summary><h3 id="docker-run">docker run</h3></summary>

```bash
docker run -d --name craft-stash --restart unless-stopped \
  -p 8080:8000 \
  -v craft-data:/data \
  -e TZ=Europe/London \
  --memory 384m --log-opt max-size=10m --log-opt max-file=3 \
  ghcr.io/relicgaming/craftstash:latest
```

Open `http://<host>:8080`. To keep photos and files in host folders, add for example
`-v /srv/craft/photos:/photos -e PHOTOS_DIR=/photos -v /srv/craft/files:/files -e UPLOADS_DIR=/files`, and
`-e PUID=$(id -u) -e PGID=$(id -g)` so the files are yours. Any [variable](#environment-variables) works as `-e NAME=value`.
</details>

<details>
<summary><h3 id="docker-compose">Docker Compose</h3></summary>

```bash
git clone https://github.com/RelicGaming/CraftStash.git && cd CraftStash
cp .env.example .env          # then edit .env
docker compose up -d --build  # http://localhost:8080
```

Without the repository, save [`docker-compose.yml`](docker-compose.yml) and [`.env.example`](.env.example) (as
`.env`), delete the `build: .` line and set `IMAGE=ghcr.io/relicgaming/craftstash:latest`.

Turn on bundled extras with `COMPOSE_PROFILES` in `.env`: `npm` (Nginx Proxy Manager), `postgres` or `mariadb` (a
database container), e.g. `COMPOSE_PROFILES=npm,postgres`. Profiles need Docker Compose 2.20 or newer
(`docker compose version`). Update with `docker compose pull && docker compose up -d` (or `--build`).
</details>

<details>
<summary><h3 id="dockhand">Dockhand</h3></summary>

1. Add a git-backed stack: repository `https://github.com/RelicGaming/CraftStash` (or your fork), compose path
   `docker-compose.yml`.
2. Put your settings in the stack's **variables** (the same names as [`.env.example`](.env.example)), e.g. `TZ`,
   `PUID`, `PGID`, folder settings, and `COMPOSE_PROFILES=npm` for the bundled proxy.
3. Either let Dockhand build from the repository, or set `IMAGE=ghcr.io/relicgaming/craftstash:latest` to pull the CI
   image (the package must be public, or add registry credentials).
4. For host folders instead of the named volume, edit the `volumes:` lines in your fork (see [Storage](#storage)).
</details>

<details>
<summary><h3 id="unraid">Unraid</h3></summary>

**Template (recommended).** In the Unraid terminal:

```bash
wget -O /boot/config/plugins/dockerMan/templates-user/my-craft-stash.xml \
  https://raw.githubusercontent.com/RelicGaming/CraftStash/main/unraid/craft-stash.xml
```

then **Docker → Add Container → Template: craft-stash**. It maps four folders, so nothing lands on the Docker vdisk:

| Path | Default host folder |
|---|---|
| App data → `/data` | `/mnt/user/appdata/craft-stash` (keep on the cache) |
| Photos → `/photos` | `/mnt/user/craft-stash/photos` |
| Files → `/files` | `/mnt/user/craft-stash/files` |
| Backups → `/backups` | `/mnt/user/craft-stash/backups` |

`PUID 99` / `PGID 100` are already set. Remote access: install Nginx Proxy Manager from Community Applications and see
[Reverse proxy](#reverse-proxy). **Compose Manager plugin** instead: see [`unraid/README.md`](unraid/README.md).
</details>

<details>
<summary><h3 id="portainer">Portainer</h3></summary>

**Stacks → Add stack**, then either:

- **Repository**: URL `https://github.com/RelicGaming/CraftStash`, compose path `docker-compose.yml`; or
- **Web editor**: paste [`docker-compose.yml`](docker-compose.yml) and delete the `build: .` line (Portainer can't
  build from the editor).

Under **Environment variables** add `IMAGE=ghcr.io/relicgaming/craftstash:latest` plus anything from
[`.env.example`](.env.example). For bundled extras add `COMPOSE_PROFILES` too; if your Portainer version doesn't pass
it to compose, delete the services you don't want from the editor instead. (Portainer isn't part of the test setup;
these steps follow its documentation.)
</details>

## First start
The first account to register becomes the admin. Admins manage users, sign-up, the currency symbol, backups and the
self-test from the **Admin** page (account menu, top right).

> **Register straight away.** Until the first account exists, anyone who can reach the app can claim admin. If it is
> reachable from outside before you get to it, set `ALLOW_SIGNUP=0` first and create the admin from the command line:
>
>     docker exec -it craft-stash python manage.py create-user --admin <username>    # asks for the password
>
> Then open sign-up from the **Admin** page if you want it.

`docker logs craft-stash` shows what the container started with: a `[storage]` line (each folder, whether it's
mapped, free space) and a `[config]` line (database, proxy trust, path prefix, limits; never a password or key).

## Project tags and filters
Open a project and add tags in the **Tags** panel (separate several with commas; the box suggests tags you've used before). Tags are lower case, up to 30 letters, numbers, spaces, `-` or `_`, and a project holds up to 20. People a project is shared with can see its tags but not change them.

The **Projects** page has a filter bar: search (names, notes and step text), status chips, tag chips (a project must have *all* the tags you pick), **Uses collection**, **Group by** (status, tag, or nothing) and **Sort** (name, recently updated, newest). With nothing chosen it looks as it always did. The address holds your choices (`/projects?tag=gifts&status=active`), so a filtered view can be bookmarked, and the browser remembers your last choice until you press **Clear**. Saving a project as a template or copying it keeps its tags.

## Importing ideas from links
**Projects → Import from a link** takes any link: a Pinterest pin (or a `pin.it` short link), an Etsy listing, a blog post, or a link straight to a picture. Craft Stash reads the page's title, description and picture (its Open Graph tags) and makes a project with the status **Idea**: the picture becomes its cover, the description goes in its notes, and the link is kept as its source. Pinterest imports are tagged `pinterest`. Choose **A wishlist item** instead for something to buy: it becomes a wish with the picture, and the site and price (when the page lists one) as its first shop.

- **An idea holds no stock.** Materials you add to it are a list, like a template's, and it doesn't put anything on the shopping list. Set its status to **Planned** when you start it and its materials are reserved. Ideas have their own section and status chip on the Projects page.
- **Import a board**: paste a public board's link (`pinterest.com/<name>/<board>/`). Craft Stash reads the board's RSS feed, which lists its newest pins (Pinterest shows up to 25), and you tick the ones to import, up to 25 at a time. Pins you've imported before are marked and skipped, so you can come back later for new ones. Any RSS or Atom feed works the same way.
- **Share from your phone**: Craft Stash is installable as a web app (**Add to Home screen** or **Install app** in the browser menu; this needs HTTPS). Once it's installed, **Share → Craft Stash** in the Pinterest app or the browser opens the import page with the link filled in. If you're signed out, it comes back to the page after you sign in.
- **When a page can't be read** (Pinterest and shops sometimes block automatic visitors), the import says why. Give it a name or a picture yourself and it's saved anyway, with the link.
- **Cover pictures**: every project can have one, imported or not. Add, replace or remove it in the **Picture** panel on the project page. Cover pictures are kept in the photos folder (`PHOTOS_DIR`) and are part of backups and copies of the project.

**What the server fetches.** Importing means the server visits links people paste, so it only does that safely: `http` and `https` links on the usual ports (80 and 443), with no user name or password in them, to hosts whose addresses are all public. Loopback, your LAN, link-local (cloud metadata), carrier NAT and the Docker network are refused, the connection goes to the address that was checked, and every redirect (up to 5) is checked again. A page is read up to 2 MB, a picture up to 10 MB, with an 8 second timeout and 20 seconds per link. Pictures are re-encoded like every other upload. Set `LINK_IMPORT=off` to turn importing off: the pages, the share target and the API endpoints go away.

## Pixel pattern maker
**Tools → Pixel pattern maker** turns a picture into a chart: it shrinks the picture to a grid of stitches (or beads, or drills), cuts it down to a few colours, gives each colour a symbol and counts the cells. Collections whose name or craft mentions cross-stitch, DMC, beads, diamond painting, knitting or crochet also offer it under **More**, already set to their colours.

- **Craft**: cross-stitch (fabric count and strands give the skeins: about 1,800 stitches a skein on 14-count with 2 strands, or set your own number), fuse beads (one bead a cell, and how many pegboards), diamond painting (drills plus spares) and knit or crochet colourwork (metres from your gauge and an editable length a stitch). Each also gives the finished size. The amounts are estimates.
- **Colours from**: a catalogue (DMC floss, DMC diamond drills, Perler, Hama or Artkal S beads), **your stash** (only the swatch colours of a collection or one type, so you can start today), or **any colours** (the picture's own).
- **Check against my stock**: each chart colour is matched to an item of yours: by its catalogue number when the item's name says it (`DMC 310`, in a collection or type that mentions DMC), otherwise by the closest swatch colour within a small difference (marked "close match"). The list shows what you have, what you're short of, and what you'd need to buy, using your **free** stock (what projects haven't reserved) as it is now.
- **Most colours** (up to 60), **Merge colours used fewer than** (folds rarely used colours into their nearest neighbour), **Dither** (smoother shading with more scattered stitches) and **Sharp edges** (for pixel art). A transparent background stays unstitched. Charts go up to 250 x 250. Colours are compared in CIELAB (CIEDE2000), so matches look right to the eye.
- Patterns are **saved**: change the settings or the picture and choose **Make it again**. The chart shows colours, symbols or both, and zooms. **PDF to print** gives an overview with the colour key and then the chart in numbered parts (50 x 70 cells a page, with bold lines every 10 and centre arrows).
- **Make a project** makes a planned project tagged `pattern` with the chart PDF attached. It reserves the colours you have as its materials, so anything short goes on the shopping list, and (unless you untick it) puts the colours you don't have on the wishlist for the project. Only items in collections you can edit, and stock kept in the craft's unit (skeins, beads or pcs, drills, metres or yards), are reserved. Doing it again later replaces just the chart PDF.
- Patterns are private. The source picture is kept in the photos folder (`PHOTOS_DIR`, part of backups) and drawn charts are cached in `TMP_DIR`. An account keeps up to 200 patterns.

The colour catalogues are screen approximations, so check a real colour card before you buy. DMC floss and diamond drill colours: craft-color-codes by [MakeBead](https://makebead.com/), licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Perler, Hama and Artkal bead colours: [maxcleme/beadcolors](https://github.com/maxcleme/beadcolors), MIT. Details are in [`app/palettes/NOTICE.md`](app/palettes/NOTICE.md). Colour codes and product names belong to their makers, and Craft Stash isn't affiliated with any of them.

## Wishlist
The **Shopping** page has two tabs: **Restock** (the shopping list) and **Wishlist**, for things you want but don't own yet.

- **Add a wish** is made for a phone in a shop: take a photo (the swatch colour is suggested from it), type what it is, the shop and the price. **More details** holds the colour, how many, a link, the date you saw it, which collection and type it would go into, and a project it's for.
- Open a wish to add more **shops**. Each has a price, a date, an optional link and a note. The cheapest one is highlighted, and the list shows it with "+2 other shops". Prices use the currency symbol set by the admin.
- **Watch the price** on a restock row, or **Add to wishlist** on a project material you're short of, makes a wish for that item. **Add a wish for this project** on a project page makes one for something new, and the project lists its open wishes.
- **Bought it** asks how many and from which shop. A wish that watches an item adds to that item's stock and sets its price to what you paid. Any other wish becomes a new item in the type you pick, with its name, colour, photo and price, and "Bought … at …" in its notes. Either way it goes in the collection's activity history. The wish moves to **Show what you bought**, and **Put back on the wishlist** reopens it.
- A wish is private to you, unless it's filed under a type: then everyone who can see that collection sees it, and people who can edit the collection can change it or buy it. Only you see which of your projects it's for, unless you share the project.
- Wish photos are kept in the photos folder (`PHOTOS_DIR`), like item photos, and are part of backups, exports and `manage.py cleanup`. A wish keeps up to 30 shops, and an account up to 1000 wishes.

## Storage
Everything is kept under `/data` by default. Each kind of file can be mapped to its own volume or host folder instead:

| Folder | Setting | Default | Holds | Grows with |
|---|---|---|---|---|
| Database and settings | `DATA_DIR` | `/data` | `craft.db`, `secret_key`, app lock | a few MB; roughly 1 KB per item or activity entry |
| Uploaded files | `UPLOADS_DIR` | `/data/uploads` | pattern and guide files | up to `USER_QUOTA_MB` per user |
| Photos | `PHOTOS_DIR` | `$UPLOADS_DIR/photos` | item and step photos | about 50-200 KB per photo (resized to 900 px) |
| Backups | `BACKUP_DIR` | `/data/backups` | daily snapshots, pre-upgrade and pre-restore copies | database size × (`BACKUP_KEEP` + 10) |
| Temporary files | `TMP_DIR` | `/data/tmp` | uploads while they arrive, backup zips, restore staging | briefly, up to the size of a full backup |
| Self-test reports | `SELFTEST_DIR` | `/data/selftest` | the last report | under 1 MB |

To move one, map the folder and set its setting to the path inside the container:

```yaml
    environment:
      PHOTOS_DIR: /photos
      UPLOADS_DIR: /files
      BACKUP_DIR: /backups
    volumes:
      - /srv/craft-stash:/data
      - /mnt/array/craft/photos:/photos
      - /mnt/array/craft/files:/files
      - /mnt/array/craft/backups:/backups
```

**Example layouts**
- **All in one folder:** map only `/data` (a named volume, or `/mnt/user/appdata/craft-stash:/data`). Simplest; back up one folder.
- **Photos on the array, database on the cache (Unraid):** `/data` in appdata, `/photos`, `/files` and `/backups` on
  a share. This is what the Unraid template does.
- **Files on a NAS share:** mount the share on the host (NFS/SMB) and map it as `/files` (and `/photos`). Keep `/data`
  on a local disk: SQLite on a network share risks corruption. With `CHOWN=never` the container leaves the share's
  permissions alone; set `PUID`/`PGID` to an account that can write there.

**How it keeps the Docker disk clean**
- Temporary files go to `TMP_DIR`, not the container's `/tmp`, so large uploads and backup zips can't fill Docker's
  own disk (the vdisk on Unraid). `TMP_DIR` needs room for one full backup (its zip is built there).
- A folder that is **set but not mapped** (or an array share that is offline) stops the app at startup with a clear
  message, instead of it quietly writing into the container.
- In a container, the startup log and the **Admin → Storage** panel flag any folder that isn't on a mapped volume.
- The compose file caps the container log at 3 × 10 MB.
- **Read-only mode (optional):** uncomment `read_only: true` and `tmpfs: [/run]` in `docker-compose.yml` (or
  `--read-only --tmpfs /run` with docker run). Then nothing can be written inside the container at all.
- **Check it:** `docker diff craft-stash` should list nothing beyond the mount points.

**Ownership.** The container starts as root only to create the folders and fix their owner to `PUID`:`PGID`, then
drops to that user. `CHOWN=auto` (default) changes only what's wrong (the whole folder when its top level has the wrong
owner, e.g. a new mapping; otherwise just wrong entries near the top); `CHOWN=always` re-owns everything at every start;
`CHOWN=never` leaves it all alone. The log warns when a folder isn't writable.

**Moving existing photos or files** to a new mapping: stop the container, move the files, set the setting, start it.

```bash
docker compose stop craft-stash
mv /srv/craft-stash/uploads/photos/* /mnt/array/craft/photos/         # photos
mv /srv/craft-stash/uploads/*.* /mnt/array/craft/files/                # files (everything except the photos folder)
docker compose up -d                                                    # with PHOTOS_DIR / UPLOADS_DIR set
```

Photos and files are stored by file name only, so the database doesn't change.

## Database
SQLite in `/data` is the default: nothing to set up, and plenty for a household. To use a server you already run
(PostgreSQL 13+, MariaDB 10.6+ or MySQL 8.0.13+), create an empty database and user:

```sql
-- PostgreSQL
CREATE USER craftstash WITH PASSWORD 'choose-one';
CREATE DATABASE craftstash OWNER craftstash;

-- MariaDB / MySQL
CREATE DATABASE craftstash CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'craftstash'@'%' IDENTIFIED BY 'choose-one';
GRANT ALL PRIVILEGES ON craftstash.* TO 'craftstash'@'%';
```

and point the app at it with one setting:

```
DATABASE_URL=postgresql://craftstash:choose-one@192.168.1.20:5432/craftstash
DATABASE_URL=mysql://craftstash:choose-one@192.168.1.20:3306/craftstash        # MariaDB or MySQL
```

or with the parts: `DB_TYPE` (`postgres`, `mariadb` or `mysql`), `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`. Special characters in a password are fine in the parts; in a URL, percent-encode them (`@` → `%40`).
`DB_PASSWORD_FILE` and `DATABASE_URL_FILE` read the value from a file (Docker secrets). `DB_SSLMODE` sets TLS
(`disable`, `prefer`, `require`, `verify-full`).

At startup the app waits up to `DB_CONNECT_TIMEOUT` seconds (60) for the server, creates or upgrades its tables (one
copy at a time), and logs which database it uses. Notes:
- **PostgreSQL:** the app creates a `nocase` collation, which needs a build with ICU (the official images and
  distribution packages are).
- **MariaDB / MySQL:** usernames and storage places compare ignoring case (`utf8mb4_unicode_ci`), everything else
  exactly, as on SQLite. If the account may not create triggers (common with binary logging on), the log says so once;
  nothing is lost, as the app keeps that rule itself.
- `/data` is still needed (secret key, temp files, and photos and files unless [mapped elsewhere](#storage)).
- Run **one** copy of the app per database: uploads and the app lock live on its `/data`.

**Bundled database.** With Compose, `COMPOSE_PROFILES=postgres` and `DB_TYPE=postgres` (or `mariadb` for both), plus a
`DB_PASSWORD`, start a database container next to the app; the app waits until it's healthy. `DB_HOST`, `DB_NAME` and
`DB_USER` default to the right values. Its files go in a named volume, or a host folder with `POSTGRES_DATA_DIR` /
`MARIADB_DATA_DIR`.

**Moving to another database.** Stop the app, then copy everything across with the old setting still in place:

```bash
docker compose stop craft-stash
docker compose run --rm --no-deps craft-stash python manage.py move-db --to postgresql://craftstash:choose-one@192.168.1.20:5432/craftstash
```

then set `DATABASE_URL` and start it. The target must be empty (`--replace` empties it first). The same command moves
back to SQLite (`--to sqlite:///data/craft.db`) or between servers. If the app starts on an empty server database
while `/data/craft.db` still has accounts, it refuses and prints this command, so a forgotten move can't look like
lost data. `manage.py export-db file.json` and `manage.py import-db file.json` do the same through a file.

## Reverse proxy
Behind a proxy, set `TRUST_PROXY=1` so the app sees the visitor's real address and whether they used HTTPS. Also set
`TRUSTED_PROXIES` to the proxy's address or network (e.g. `172.18.0.0/16`; comma-separate several): forwarded headers
from anywhere else are then dropped, so someone reaching the app's port directly can't fake their address (and dodge
the login limiter). Without `TRUSTED_PROXIES`, only use `TRUST_PROXY=1` when the proxy is the **only** way in.
Narrower is better: `docker network inspect <network>` shows a network's subnet, and connections from the host itself
through a published port arrive from Docker's gateway address.

| Setup | `TRUST_PROXY` | `TRUSTED_PROXIES` | `COOKIE_SECURE` |
|---|---|---|---|
| LAN only, `http://host:8080` | `0` | empty | `auto` |
| HTTPS proxy is the only way in (no host port) | `1` | proxy network (recommended) | `auto` |
| HTTPS proxy, and the LAN port is still open | `1` | **required**: the proxy's address | `auto` |
| Proxy terminates HTTPS but doesn't send `X-Forwarded-Proto` | `1` | proxy address | `1` |
| Plain-HTTP testing behind a proxy | `1` | proxy address | `0` |

`COOKIE_SECURE=auto` marks the login cookie `Secure` whenever the request arrived over HTTPS, so the LAN address keeps
working over plain HTTP.

**Every proxy needs:** forward to the app's port **8000** (`craft-stash:8000` on a shared Docker network, or
`<host-ip>:8080`), allow uploads of **30 MB** (raise with `MAX_UPLOAD_MB`), and a **120 s** read timeout (big backups).

<details open>
<summary><strong>Bundled Nginx Proxy Manager</strong></summary>

Add `npm` to `COMPOSE_PROFILES` and set `TRUST_PROXY=1`. Compose starts NPM next to the app on ports 80/443, with its
admin UI on 81 (`NPM_HTTP_PORT`, `NPM_HTTPS_PORT`, `NPM_ADMIN_PORT` change them); the first login there creates
NPM's admin account. Its data and certificates go in named volumes, or host folders with `NPM_DATA_DIR` and
`NPM_LETSENCRYPT_DIR`. Then add a proxy host as below, forwarding to `craft-stash` port `8000`. You can remove the app's
own `ports:` block once NPM works.
</details>

<details>
<summary><strong>Your own Nginx Proxy Manager</strong></summary>

1. **Network.** Best: put both containers on one Docker network. Create it once (`docker network create proxy`),
   attach NPM to it, then in `docker-compose.yml` uncomment the `networks` lines and delete the `ports:` block. NPM
   then forwards to `craft-stash:8000` and nothing is exposed on the host. Otherwise keep the `ports:` block, forward
   to `<host-ip>:8080`, and set `TRUSTED_PROXIES` to NPM's address.
2. **Proxy host:** scheme `http`, forward host/port as above, tick **Block Common Exploits**. On the SSL tab request a
   certificate and tick **Force SSL** and **HSTS**.
3. **Advanced tab**, so uploads and backups work (NPM's default limit is 1 MB):

       client_max_body_size 30m;
       proxy_read_timeout 120s;
</details>

<details>
<summary><strong>Traefik, Caddy, nginx, SWAG, HAProxy, Cloudflare Tunnel</strong></summary>

Ready-to-adapt configs, each with the upload limit and timeouts set, are in [`deploy/`](deploy/):
[Traefik](deploy/traefik/docker-compose.traefik.yml) (labels, as a compose override) ·
[Caddy](deploy/caddy/Caddyfile) · [nginx](deploy/nginx/craft-stash.conf) ·
[SWAG](deploy/swag/craft-stash.subdomain.conf) · [HAProxy](deploy/haproxy/haproxy.cfg) ·
[Cloudflare Tunnel](deploy/cloudflared/config.yml).
</details>

**Under a path.** For `https://example.com/craft/`, set `URL_PREFIX=/craft` and forward `/craft/` to the app. The proxy
may strip the prefix or pass it through; links, static files, redirects and the session cookie all follow it. A
trusted proxy that sends `X-Forwarded-Prefix` works without `URL_PREFIX`.

## Single sign-on
Craft Stash can log people in through any OpenID Connect provider: Authentik, Authelia, Keycloak, Pocket ID, Zitadel,
Kanidm, Google, Microsoft Entra and so on. Passwords keep working alongside it.

1. **At the provider**, create an application / client for Craft Stash: type *confidential* (a client secret), grant
   *authorization code*, scopes `openid profile email`, and the redirect URI
   `https://craft.example.com/login/sso/callback` (with your `URL_PREFIX` in front of `/login`, if you use one).
2. **On the app**, set:

   ```
   OIDC_ENABLED=1
   OIDC_ISSUER=https://auth.example.com/application/o/craft-stash     # the provider's issuer URL (see below)
   OIDC_CLIENT_ID=craft-stash
   OIDC_CLIENT_SECRET=...                                            # or OIDC_CLIENT_SECRET_FILE
   OIDC_NAME=Authentik                                               # the button says "Log in with Authentik"
   ```

   Behind a proxy, also set `OIDC_REDIRECT_URI` to the exact address registered in step 1 (or make sure
   `TRUST_PROXY` is right, so the app knows its public `https://` address).

The issuer URL is the address whose `/.well-known/openid-configuration` the provider publishes, e.g.
Authentik `https://auth.example.com/application/o/<slug>`, Keycloak `https://auth.example.com/realms/<realm>`,
Authelia and Pocket ID `https://auth.example.com`, Google `https://accounts.google.com`.

**Who gets in.** A provider account is tied to one Craft Stash user by its issuer and subject (never by name alone):
- **Existing users** log in with their password once, then **Account → Link** the provider. From then on the button
  works for them. They can unlink it there too.
- **New people**: with `OIDC_AUTO_PROVISION=1`, the first SSO login creates an account, named from
  `OIDC_USERNAME_CLAIM` (default `preferred_username`; `email` uses the part before the @). The very first account of
  a fresh install becomes the admin, as with sign-up. Leave it at `0` (the default) to let only linked accounts in,
  which is the right choice for public providers like Google, where anyone has an account.
- **Same username already here, not linked:** refused with a message to log in with the password and link, so a
  provider account can't take over a local one just by having the same name. If your provider's usernames are
  controlled by you (users can't pick their own), `OIDC_LINK_EXISTING=1` links them automatically instead.
- Disabled accounts can't log in either way, and the API still needs a token: an SSO session can't call `/api/`.

**How it's checked.** The authorization code flow with PKCE (S256), a random `state` (tied to the session, valid for
10 minutes) and `nonce`. The code is exchanged directly with the provider's token endpoint over HTTPS, with the client
secret, and the ID token's issuer, audience/`azp`, expiry, issue time and nonce are verified. Its signature isn't checked:
OpenID Connect allows relying on the TLS connection to the token endpoint instead (Core 3.1.3.7), which keeps extra
cryptography libraries out of the image. So the issuer must be `https://` (plain `http://` only for `localhost`), and
the container must trust the provider's certificate. A provider with a private CA needs that CA in the container, e.g.
by mounting it and setting `SSL_CERT_FILE`.

## Environment variables
Set them in `.env` (Compose), the stack variables (Dockhand, Portainer), the template (Unraid) or `-e` (docker run).
Empty means the default. A setting ending in `_FILE` reads the value from that file (Docker secrets): `SECRET_KEY_FILE`,
`DATABASE_URL_FILE`, `DB_PASSWORD_FILE`. [`.env.example`](.env.example) lists them all with comments.

### General
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `PORT` | `8080` | port | `8090` | Host port (compose); the container listens on 8000 |
| `PUID` / `PGID` | `10001` | user/group id | `99` / `100` | Owner of the files in the mapped folders. Unraid: 99/100 |
| `TZ` | `UTC` | tz name | `Australia/Brisbane` | Times in the activity log |
| `IMAGE` | `craft-stash:latest` | image | `ghcr.io/relicgaming/craftstash:sha-6c8e385` | Compose: build locally or pull from GHCR |

### Security
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `ALLOW_SIGNUP` | `1` | `0`, `1` | `0` | Starting default for open sign-up; the Admin page overrides it. `0` also closes the first-admin sign-up |
| `COOKIE_SECURE` | `auto` | `auto`, `1`, `0` | `1` | See the table in [Reverse proxy](#reverse-proxy) |
| `SECRET_KEY` | generated | text | (64 random hex) | Signs sessions. Generated once into `/data/secret_key` if empty |
| `MAX_UPLOAD_MB` | `25` | number | `50` | Largest single upload. Raise the proxy's limit too |
| `USER_QUOTA_MB` | `500` | number, `0` | `0` | Uploaded files and step photos per user; `0` = unlimited |
| `LINK_IMPORT` | `on` | `on`, `off` | `off` | [Importing ideas from links](#importing-ideas-from-links). `off`: the server never fetches pasted links |

### Storage
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `UPLOADS_DIR` | `/data/uploads` | container path | `/files` | Must be a mapped folder when set |
| `PHOTOS_DIR` | `$UPLOADS_DIR/photos` | container path | `/photos` | Must be a mapped folder when set |
| `BACKUP_DIR` | `/data/backups` | container path | `/backups` | Must be a mapped folder when set |
| `TMP_DIR` | `/data/tmp` | container path | `/scratch` | A folder of its own, with room for one full backup |
| `SELFTEST_DIR` | `/data/selftest` | container path | `/data/selftest` | Rarely changed |
| `CHOWN` | `auto` | `auto`, `always`, `never` | `never` | Ownership fixes at start (see [Storage](#storage)) |

### Reverse proxy
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `TRUST_PROXY` | `0` | number of proxies | `1` | Believe `X-Forwarded-*` from that many proxies |
| `TRUSTED_PROXIES` | empty | IPs / CIDR ranges | `172.18.0.0/16, 10.0.0.5` | Only those addresses' headers are believed. Implies `TRUST_PROXY=1` |
| `URL_PREFIX` | empty | path | `/craft` | Serve under a path |

### Database
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `DATABASE_URL` | empty (SQLite) | `postgresql://`, `mysql://`, `mariadb://`, `sqlite:///` URL | `postgresql://cs:pw@db:5432/cs` | Wins over the parts below |
| `DB_TYPE` | empty | `postgres`, `mariadb`, `mysql` | `postgres` | Use the parts instead of a URL |
| `DB_HOST` | `postgres` / `mariadb` | host | `192.168.1.20` | Defaults suit the bundled containers |
| `DB_PORT` | `5432` / `3306` | port | `5433` | |
| `DB_NAME` | `craftstash` | name | `stash` | |
| `DB_USER` | `craftstash` | name | `stash` | |
| `DB_PASSWORD` | empty | text | | Or `DB_PASSWORD_FILE` |
| `DB_SSLMODE` | empty | `disable`, `prefer`, `require`, `verify-full` | `require` | TLS to the server |
| `DB_CONNECT_TIMEOUT` | `60` | seconds | `120` | How long startup waits for the server |

### Single sign-on
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `OIDC_ENABLED` | `0` | `0`, `1` | `1` | Show "Log in with ..." and accept SSO logins |
| `OIDC_ISSUER` | empty | `https://` URL | `https://auth.example.com/realms/home` | Required when enabled |
| `OIDC_CLIENT_ID` | empty | text | `craft-stash` | Required when enabled |
| `OIDC_CLIENT_SECRET` | empty | text | | Required when enabled. Or `OIDC_CLIENT_SECRET_FILE` |
| `OIDC_SCOPES` | `openid profile email` | space-separated, with `openid` | `openid profile email groups` | |
| `OIDC_REDIRECT_URI` | worked out from the request | full URL | `https://craft.example.com/login/sso/callback` | Must match the provider exactly |
| `OIDC_USERNAME_CLAIM` | `preferred_username` | claim name | `email` | Username for new accounts |
| `OIDC_AUTO_PROVISION` | `0` | `0`, `1` | `1` | Create accounts at first SSO login |
| `OIDC_LINK_EXISTING` | `0` | `0`, `1` | `1` | Link a same-named local account automatically (only for providers whose usernames you control) |
| `OIDC_NAME` | `single sign-on` | text | `Authentik` | Button text |
| `OIDC_ALLOW_HTTP` | `0` | `0`, `1` | | Allow a plain `http://` issuer on another machine. **Unsafe**: the ID token is trusted because it arrives over TLS, so without TLS anyone on that network can log in as anyone. The log and the Admin page warn while it's on. A provider on `localhost` never needs it. |

### Backups
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `BACKUP_KEEP` | `7` | number, `0` | `14` | Daily database snapshots to keep; `0` = off |

### Compose (bundled extras)
| Variable | Default | Allowed | Example | Notes |
|---|---|---|---|---|
| `COMPOSE_PROFILES` | empty | `npm`, `postgres`, `mariadb` | `npm,postgres` | Which bundled services start |
| `NPM_HTTP_PORT` / `NPM_HTTPS_PORT` / `NPM_ADMIN_PORT` | `80` / `443` / `81` | port | `8443` | Bundled Nginx Proxy Manager |
| `NPM_DATA_DIR` / `NPM_LETSENCRYPT_DIR` | named volumes | volume name or host path | `/mnt/user/appdata/npm/data` | Where NPM keeps its data and certificates |
| `POSTGRES_DATA_DIR` / `MARIADB_DATA_DIR` | named volumes | volume name or host path | `/mnt/user/appdata/craft-pg` | Where the bundled database keeps its files |
| `NPM_IMAGE` / `POSTGRES_IMAGE` / `MARIADB_IMAGE` | `jc21/nginx-proxy-manager:2` / `postgres:17-alpine` / `mariadb:11` | image | `postgres:16-alpine` | Pin or change the bundled images |
| `PROXY_NETWORK` | `proxy` | network name | `npm_default` | Your own proxy's Docker network, when joining it |

## Versions and upgrades
Releases are numbered `MAJOR.MINOR.PATCH` and listed in [CHANGELOG.md](CHANGELOG.md) and on the
[Releases page](https://github.com/RelicGaming/CraftStash/releases). The running version is shown on the Admin page, in
the first `[config]` log line and as `app_version` in `GET /api/v1`.

| Tag | What it is | Use it for |
|---|---|---|
| `:1` | the newest 1.x release | **most installs**: fixes and new features, never a breaking change |
| `:1.0` | the newest 1.0.x release | fixes only |
| `:1.0.0` | exactly that release | pinning; change it by hand |
| `:latest` | the newest release, including a future 2.0 | if you always want the newest |
| `:1.0.0-rc.1` | a release candidate | trying a release before it ships |
| `:edge` | every merge to `main` | testing unreleased work; may break |
| `:sha-<commit>` | one exact build | rolling back to a specific build |

**What each kind of release may change:**
- **Patch** (1.0.0 → 1.0.1): bug and security fixes only.
- **Minor** (1.0 → 1.1): new features. The database upgrades itself on start, no setting is removed or changes meaning,
  and the API stays `v1` (it may gain fields and endpoints).
- **Major** (1.x → 2.0): may need a manual step, remove a setting or change the API. The changelog says what to do.

Any 1.x can upgrade straight to any later 1.x. To upgrade, take a backup (Admin → Backup), pull the new image and
recreate the container; the database is upgraded on start. A SQLite database is also copied to the backups folder as
`pre-migrate-*.db` before any upgrade; for PostgreSQL or MariaDB, keep your own dump as well.

An older version refuses to start on a database a newer one upgraded, and says so in the log. To go back, run the older
image with a backup taken before the upgrade (see [Backups and restore](#backups-and-restore)).

## Backups and restore
**What to back up:** `/data` and every folder you [mapped separately](#storage) (photos, files, backups), plus the
database server's own backup if you use one. On Unraid, include the appdata folder and the craft-stash share.

- **Full backup:** **Admin → Download full backup**: a zip of the database and every uploaded file and photo, wherever
  they're mapped. On SQLite it holds `craft.db`; on PostgreSQL/MariaDB a portable `craft-dump.json`.
- **Daily snapshots** go in `BACKUP_DIR` (`BACKUP_KEEP` of them): `auto-*.db` on SQLite, `auto-*.json` on a server
  database. Upgrades and restores also leave a safety copy there first (`pre-migrate-*`, `pre-restore-*`).
- **Restore** (the app must be stopped). It takes a full-backup zip, a `.db` snapshot or a `.json` export, made on any
  database engine, and puts files back wherever the folders are mapped now:

  ```bash
  docker compose stop craft-stash
  docker cp backup.zip craft-stash:/data/backup.zip
  docker compose run --rm --no-deps craft-stash python manage.py restore /data/backup.zip
  docker compose start craft-stash
  ```

  A snapshot restores the same way but only brings back the database.
- **Server databases** can also use their own tools (`pg_dump`, `mariadb-dump`); `manage.py export-db` / `import-db`
  give a portable copy that loads into any engine.
- **Locked out?** From the command line:

  ```bash
  docker exec -it craft-stash python manage.py reset-password <username>    # asks for the new password
  docker exec -it craft-stash python manage.py create-user [--admin] <username>
  docker exec -it craft-stash python manage.py list-users
  docker exec -it craft-stash python manage.py make-admin <username>
  ```
- **Housekeeping:** `python manage.py cleanup --dry-run` lists uploaded files and photos nothing refers to.

## API and Home Assistant
Make a token under **Account → API tokens**. A token acts as you, so it sees exactly what you see. It is **read only** unless you choose **Read and write**, and only a hash of it is stored, so copy it when it's shown. Revoke a token there to cut off whatever uses it. A disabled account's tokens stop working too.

Send the token with every request as `Authorization: Bearer cs_…`. Responses are JSON. Request bodies can be JSON or form fields. Behind `URL_PREFIX`, the API is under the prefix too (`/craft/api/v1`).

| Request | What it does |
|---|---|
| `GET /api/v1` | Who the token belongs to, whether it can write, and the endpoint list |
| `GET /api/v1/summary` | Counts for sensors: `shopping_list`, `out_of_stock`, `low_stock`, `short`, `items`, `stash_value`, `projects_active` and more |
| `GET /api/v1/shopping` | The shopping list. Each item has `status` (`out`, `low` or `short`) and a `suggested` amount to buy |
| `POST /api/v1/shopping/restock` | Add what you bought: `{"items": [{"id": 3, "amount": 2}]}`, or `{"id": 3}` for the suggested amount, or `{"all": true}` for everything on the list |
| `GET /api/v1/items?q=&status=` | Search every item, like the app does (`Label:value` works). `status` is one of `ok`, `low`, `out` or `short` |
| `GET /api/v1/items/<id>` | One item, including its places |
| `POST /api/v1/items/<id>/adjust` | `{"action": "use", "amount": 2}` or `"add"`. Add `"place": "Shelf B"` for one of its other places |
| `GET /api/v1/collections`, `GET /api/v1/collections/<id>` | Collections, and one collection with its types, fields and items |
| `GET /api/v1/projects`, `GET /api/v1/projects/<id>` | Projects (each with its tags and dates), and one project with its materials and steps. Filters: `status=` (repeat for several), `tag=` (repeat: all must match), `q=` (name, notes and steps), `pool=<collection id>` (uses supplies from it), `sort=name\|updated\|newest` |
| `GET /api/v1/wishlist`, `GET /api/v1/wishlist/<id>` | Open wishes (`?got=1` for the ones bought), each with its `sightings` (shops), cheapest first, and `cheapest` |
| `POST /api/v1/wishlist` | Add a wish: `{"name": "Chunky wool", "qty": 2, "shop": "Spotlight", "price": 12.5}`. Also `colour`, `hex`, `unit`, `notes`, `type_id`, `item_id` (watch an item), `project_id`, and for the shop `url`, `seen` (`YYYY-MM-DD`) and `shop_notes` |
| `POST /api/v1/wishlist/<id>/sightings` | Add a shop: `{"shop": "Lincraft", "price": 9.95, "url": "https://…", "seen": "2026-11-25", "notes": "On sale"}` |
| `POST /api/v1/wishlist/<id>/bought` | Mark it bought: `{"qty": 2, "sighting": <shop id> or "none", "type_id": 1}` (all optional: the cheapest shop and the wish's own type are used). Returns the wish and the item |
| `POST /api/v1/import/link` | Import a link as an idea: `{"url": "https://pin.it/…"}`, or `"to": "wishlist"` for a wish. Optional `"name"`. Returns the `project` (with `source` and a `photo` link) or the `wish`, and `notes` about anything left out |
| `GET /api/v1/import/board?url=` | The pins on a public board (or any feed): `title`, `link`, `image`, and `imported` (the project id when you have it already) |
| `GET /api/v1/patterns`, `GET /api/v1/patterns/<id>` | Your pixel patterns, and one with its `settings` and `legend`: each colour's `symbol`, `hex`, `label`, `cells`, `need` and `unit`, the matched `item` and `status` (`have`, `short`, `buy` or `units`) |
| `POST /api/v1/patterns` | Make a pattern. Send a multipart form with `photo` (the picture) and any settings: `name`, `craft` (`cross-stitch`, `beads`, `diamond`, `colourwork`), `palette` (`catalogue:dmc-floss`, `pool:<id>`, `type:<id>` or `free`), `width`, `height`, `colours`, `min_count`, `dither`, `sharp`, `check`, and the craft's own fields (`count`, `strands`, `per_unit`, `board`, `spare`, `drill_mm`, `sts10`, `rows10`, `cm_per_st`) |
| `POST /api/v1/patterns/<id>/project` | Make a project from it (or update the chart on its project). `{"wishes": false}` leaves colours you don't have off the wishlist |
| `POST /api/v1/import/board` | Import pins as ideas: `{"url": "<board>", "pins": ["<pin link>", …]}`, or leave out `pins` for every new one. Up to 25 per call; `remaining` says how many are left |

Stock changes made through the API go into the collection's activity history under your name, so they can be undone there. After 8 wrong tokens from one address in 10 minutes, that address gets `429` responses until the 10 minutes are up.

```bash
curl -H "Authorization: Bearer $TOKEN" https://stash.example.com/api/v1/shopping
```

### Home Assistant
Put the token in `secrets.yaml` as `craft_stash_auth: "Bearer cs_…"` (with `Bearer `). Then in `configuration.yaml`:

```yaml
rest:
  - resource: https://stash.example.com/api/v1/summary
    headers: { Authorization: !secret craft_stash_auth }
    scan_interval: 300
    sensor:
      - name: Craft shopping list
        value_template: "{{ value_json.shopping_list }}"
        unit_of_measurement: items
      - name: Craft stash value
        value_template: "{{ value_json.stash_value }}"
  - resource: https://stash.example.com/api/v1/shopping
    headers: { Authorization: !secret craft_stash_auth }
    scan_interval: 300
    sensor:
      - name: Craft shopping items
        value_template: "{{ value_json.count }}"
        json_attributes: [items]

rest_command:          # needs a "Read and write" token
  craft_restock_all:
    url: https://stash.example.com/api/v1/shopping/restock
    method: post
    headers: { Authorization: !secret craft_stash_auth }
    content_type: application/json
    payload: '{"all": true}'
  craft_use:
    url: "https://stash.example.com/api/v1/items/{{ item_id }}/adjust"
    method: post
    headers: { Authorization: !secret craft_stash_auth }
    content_type: application/json
    payload: '{"action": "use", "amount": {{ amount }}}'
```

To list the shopping list on a dashboard, use a Markdown card:

```yaml
type: markdown
content: >
  {% for i in state_attr('sensor.craft_shopping_items', 'items') or [] %}
  - **{{ i.name }}** ({{ i.collection }}): buy {{ i.suggested }} {{ i.unit }}{{ ', out of stock' if i.status == 'out' }}
  {% endfor %}
```

To get a notification when something runs out:

```yaml
automation:
  - alias: Craft supplies need buying
    triggers: [{ trigger: numeric_state, entity_id: sensor.craft_shopping_list, above: 0 }]
    actions: [{ action: notify.notify, data: { message: "{{ states('sensor.craft_shopping_list') }} craft supplies on the shopping list" } }]
```

## Self-test
Checks that every feature works and times the pages, without touching your data. It starts a temporary copy of the app with its own empty SQLite database (whatever database the real app uses), signs up test users and drives the site over real HTTP.

- **In the app:** **Admin → Open self-test → Run self-test**. Results appear live; open or download the full report when it finishes.
- **Headless, from a terminal:**

  ```bash
  docker exec -it craft-stash python selftest.py            # prints a table; report in SELFTEST_DIR (/data/selftest)
  docker cp craft-stash:/data/selftest ./selftest-report     # copy report.html and report.json out
  ```

- **Options:** `--only Sharing` (just matching checks), `--perf-items 500` (bigger stash for the load-time pass), `--budget-ms 500` (flag slower pages), `--no-perf`, `--server flask`.
- **Real browser (optional):** on your own computer, `pip install playwright && playwright install chromium`, then `python app/selftest.py --browser` (add `--headed` to watch it). Adds real first-paint and loaded times plus desktop and phone screenshots, and flags pages that scroll sideways.
- Exit code is 1 if any check fails, so it also works in scripts. GitHub Actions runs it on every push and keeps the report as a downloadable artifact.

## Troubleshooting
<details>
<summary><strong>The Docker vdisk / Docker's disk is filling up</strong></summary>

Look at the startup log (`docker logs craft-stash`) or **Admin → Storage**: a folder marked *not mapped* is inside the
container. Map it (and set its `*_DIR`), or map `/data` itself. Run `docker diff craft-stash` to see what has been
written inside the container. If the log is the culprit, the compose file caps it; with `docker run` add
`--log-opt max-size=10m --log-opt max-file=3`. Turning on [read-only mode](#storage) makes any such write fail loudly.
</details>

<details>
<summary><strong>"Can't write to PHOTOS_DIR=..." / permission denied on a mapped folder</strong></summary>

The folder's owner doesn't match `PUID`/`PGID`. Either set `PUID`/`PGID` to the folder's owner (`ls -ln` on the host
shows the numbers; Unraid shares are 99/100), or let the container fix it with `CHOWN=auto` / `always`. On NFS with
root squash the container can't change ownership: set `CHOWN=never` and `PUID`/`PGID` to an account the share allows.
</details>

<details>
<summary><strong>"PHOTOS_DIR=/photos does not exist"</strong></summary>

The setting is there but nothing is mapped at that path (or the share is offline). Add the volume mapping, or remove
the setting to use the default inside `/data`. This check is what stops files quietly landing on Docker's own disk.
</details>

<details>
<summary><strong>Logging in behind the proxy just reloads the login page</strong></summary>

The session cookie isn't coming back. Usually `COOKIE_SECURE=1` while the browser reaches the app over plain HTTP, or
the app doesn't know the request was HTTPS: set `TRUST_PROXY=1` (and `TRUSTED_PROXIES` to the proxy) so it reads
`X-Forwarded-Proto`, and leave `COOKIE_SECURE=auto`. Under a sub-path, set `URL_PREFIX` so the cookie's path matches.
</details>

<details>
<summary><strong>Uploads fail with 413 (Request Entity Too Large)</strong></summary>

The proxy's body limit is smaller than the file. Nginx Proxy Manager's default is 1 MB: add `client_max_body_size 30m;`
on the proxy host's Advanced tab (see [Reverse proxy](#reverse-proxy) for other proxies). If the app itself says the
file is too large, raise `MAX_UPLOAD_MB` (and the proxy limit with it).
</details>

<details>
<summary><strong>"The database ... is empty, but /data/craft.db has N account(s)"</strong></summary>

You pointed the app at a new server database while your data is still in SQLite. Stop the app and copy it across with
`manage.py move-db --to <your DATABASE_URL>` (see [Moving to another database](#database)), then start it with
`DATABASE_URL` set. To really start empty instead, move `/data/craft.db` out of the data folder.
</details>

<details>
<summary><strong>"Can't connect to the database ..." / the app keeps restarting</strong></summary>

The server isn't reachable from the container. Check `DB_HOST` resolves from inside it (a container name only works
on a shared Docker network; otherwise use the host's IP), the port, user and password, and the server's access rules
(`pg_hba.conf`, `'user'@'%'`). A slow first start of a database container may need a longer `DB_CONNECT_TIMEOUT`.
</details>

<details>
<summary><strong>Single sign-on fails: "set up wrongly", "can't be reached" or "couldn't be verified"</strong></summary>

`docker logs craft-stash` has a `[sso]` line with the reason. **Set up wrongly**: `OIDC_ISSUER` must be exactly the
`issuer` in the provider's `/.well-known/openid-configuration` (watch the trailing path, e.g. Keycloak's
`/realms/<realm>`). **Can't be reached**: the container can't connect to the provider, or doesn't trust its certificate
(a private CA needs `SSL_CERT_FILE`). **Refused** after the login: the redirect URI registered at the provider must
match `OIDC_REDIRECT_URI` (or the address the app works out) exactly, including `https` and any `URL_PREFIX`.
**Couldn't be verified**: usually the clock on the server or provider is more than two minutes off, or the client ID
is wrong.
</details>

<details>
<summary><strong>Pages or images don't load under a path like /craft</strong></summary>

Set `URL_PREFIX=/craft` on the app and make sure the proxy forwards `/craft/...` (with or without the prefix). Every
link the app makes then starts with `/craft`.
</details>

## Development
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q                                                                  # SQLite
TEST_DATABASE_URL=postgresql://postgres:pw@localhost:5432/test pytest -q   # the whole suite on PostgreSQL (wiped per test)
TEST_DATABASE_URL=mysql://root:pw@127.0.0.1:3306/test pytest -q            # ... or MariaDB / MySQL
CRAFT_DOCKER_TESTS=1 pytest tests/test_container.py                        # builds the image: nothing written inside it
```

GitHub Actions runs the tests on SQLite, PostgreSQL and MariaDB (Python 3.15, plus 3.12 on SQLite), then publishes
`:edge` on every push to `main`. Pushing a `v*` tag publishes the release tags and a GitHub Release from
[CHANGELOG.md](CHANGELOG.md); the tag must match `app/version.py`. `screenshots.yml` is a manual workflow that uploads screenshots of each page.
