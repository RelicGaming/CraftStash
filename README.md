# Craft Stash
Self-hosted craft supplies inventory. Flask + SQLite in one container.

## Quick start
    cp .env.example .env
    docker compose up -d --build     # http://localhost:8080

The first account to register becomes the admin. Admins manage users from the **Users** page.

> **Register straight away.** Until the first account exists, anyone who can reach the app can claim admin. If it is reachable from outside before you get to it, set `ALLOW_SIGNUP=0` first and create the admin from the command line instead:
>
>     docker exec -it craft-stash python manage.py create-user --admin <username>    # asks for the password
>
> Then open sign-up from the **Users** page if you want it.

## Features
- **Collections** (one per craft) hold **material types** (own unit and custom text/number/dropdown fields) which hold **items** (colour swatch, optional photo, variant, stock, price, location, low-stock level). An item can be **split across several storage places**: use or add stock at a particular place, or move some between places (CSV export shows the main location and the total).
- **Search everything**: one search box (home page or the header) across every collection you own or that's shared with you, plus project names and notes.
- Search a collection for any text or one field (`Width:150`), sort, export or **import CSV** (with a check-only mode), see the **activity** history and undo stock changes.
- **Shopping list** across every collection you can use: out of stock, low, or short for a project. Enter what you bought (or take the suggested amounts) and restock everything in one go. Assigning more than you have is allowed and the gap lands on the list.
- **Projects**: reserve supplies, **templates** (save a project as a reusable pattern, start new projects from it; templates hold no stock), numbered steps (each with up to 6 photos, counted towards your storage limit), attached guides, estimated cost, print layout, duplicate, complete (enter how much of each material you actually used and only that is deducted, or keep stock untouched) and reopen (puts it back).
- **Sharing**: collections view-only or can-edit; projects read-only, and a viewer can save their own copy. People can leave what's shared with them.
- Per-user **themes** (presets or custom colours and corner style), changeable password, admin currency symbol, per-user storage limit, disable users.
- Daily database snapshots and a one-click full backup (admin).
- **JSON API** with per-user tokens, for Home Assistant and scripts: shopping list, stock, collections and projects, plus restocking and using stock. See [API and Home Assistant](#api-and-home-assistant).

## Behind Nginx Proxy Manager
Set `TRUST_PROXY=1` so the app reads the visitor's real address and HTTPS status from NPM. Only do this when NPM is the **only** way to reach the app: with `TRUST_PROXY=1` anyone who can reach port 8080 directly can fake their address (and dodge the login limiter).

1. **Network.** Best: put both containers on one Docker network. Create it once (`docker network create proxy`), attach NPM to it, then in `docker-compose.yml` uncomment the `networks` lines and delete the `ports:` block. NPM then forwards to `craft-stash:8000` and nothing is exposed on the host. Otherwise keep the `ports:` block and forward to `<host-ip>:8080`, and firewall that port so only NPM can reach it.
2. **Proxy host in NPM:** Scheme `http`, forward host/port as above, tick **Block Common Exploits**. On the SSL tab request a certificate and tick **Force SSL** and **HSTS**.
3. **Advanced tab**, paste this so uploads and backups work (NPM's default limit is 1 MB):

       client_max_body_size 30m;
       proxy_read_timeout 120s;

4. Leave `COOKIE_SECURE=auto`: cookies become `Secure` automatically for HTTPS requests, and plain `http://host-ip:8080` still works on your LAN.
5. Set `TZ` (e.g. `Australia/Brisbane`) so the activity log shows local times.

## Settings (`.env` or the Dockhand stack variables)
| Variable | Default | Use |
|---|---|---|
| `PORT` | 8080 | Host port, if you publish one |
| `PUID` / `PGID` | 10001 | Who owns the files in `/data`. Unraid: `99` / `100`. Needed when `/data` is a host folder |
| `TZ` | UTC | Time zone for the activity log |
| `TRUST_PROXY` | 0 | Proxies in front. `1` behind Nginx Proxy Manager, only if the app can't be reached any other way |
| `COOKIE_SECURE` | auto | `auto`, `1` (always) or `0` (never) |
| `ALLOW_SIGNUP` | 1 | Starting default for open sign-up; the Users page overrides it. `0` also closes the first-admin sign-up (see Quick start) |
| `MAX_UPLOAD_MB` | 25 | Largest single upload |
| `USER_QUOTA_MB` | 500 | Guide/pattern storage per user, 0 = unlimited |
| `BACKUP_KEEP` | 7 | Daily database snapshots to keep, 0 = off |
| `SECRET_KEY` | auto | Session signing key; generated into the volume if empty |
| `UPLOADS_DIR` / `PHOTOS_DIR` / `BACKUP_DIR` / `TMP_DIR` | under `/data` | Put a folder on its own volume or host folder; see [Storage](#storage) |
| `DATABASE_URL` | empty (SQLite) | An external PostgreSQL or MariaDB/MySQL database; see [Database](#database). Also `DB_TYPE`, `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE`, `DB_CONNECT_TIMEOUT` |
| `CHOWN` | auto | `auto` fixes ownership only where it is wrong, `always` every start, `never` leaves it alone (NAS shares) |
| `IMAGE` | craft-stash:latest | Set to `ghcr.io/<you>/craftstash:latest` to pull the CI image. Every build is also tagged `:sha-<commit>`, so you can pin or roll back to one |

## Storage
Everything is kept under `/data` by default. The big folders can each be mapped somewhere else, for example photos on
an Unraid array share while the database stays on the cache drive, or files on a NAS:

| Folder | Setting | Default | Holds |
|---|---|---|---|
| Database and settings | `DATA_DIR` | `/data` | `craft.db`, the secret key. Keep it on a local disk, not a network share |
| Uploaded files | `UPLOADS_DIR` | `/data/uploads` | Pattern and guide files |
| Photos | `PHOTOS_DIR` | `$UPLOADS_DIR/photos` | Item and step photos (usually the biggest) |
| Backups | `BACKUP_DIR` | `/data/backups` | Daily snapshots and safety copies |
| Temporary files | `TMP_DIR` | `/data/tmp` | Uploads while they arrive, backup zips, restore staging |

Map the folder and set its setting, e.g. in `docker-compose.yml`:

```yaml
    environment:
      PHOTOS_DIR: /photos
      UPLOADS_DIR: /files
    volumes:
      - /mnt/user/appdata/craft-stash:/data
      - /mnt/user/craft-stash/photos:/photos
      - /mnt/user/craft-stash/files:/files
```

- **Nothing is written inside the container.** Temp files go to `TMP_DIR`, not the container's `/tmp`, so large
  uploads can't fill Docker's own disk (the vdisk on Unraid). The container log is capped at 30 MB. The startup log
  (`docker logs craft-stash`) lists every folder with its free space and warns if one isn't mapped; the Admin page
  shows the same.
- **A folder that is set must be mapped.** If `PHOTOS_DIR=/photos` is set but nothing is mapped there (or an array share
  is offline), the app refuses to start instead of quietly writing into the container.
- **Ownership:** at startup the container makes each folder owned by `PUID`/`PGID`. `CHOWN=never` leaves a NAS share's
  permissions alone; the startup log says if a folder is then not writable.
- **Read-only container (optional):** uncomment `read_only: true` and `tmpfs: [/run]` in `docker-compose.yml`. Anything
  not on a mapped folder then fails loudly.
- **Moving existing photos:** stop the container, move `/data/uploads/photos/*` to the new folder, set `PHOTOS_DIR`, and
  start it again. Photos are stored by file name only, so nothing in the database changes.
- **Backups** contain every file wherever it is mapped, and restore puts them back wherever the folders are mapped now.

## Database
Craft Stash uses SQLite in `/data` unless told otherwise. That needs no setup and suits a household. To use a
PostgreSQL 13+ or MariaDB 10.6+ / MySQL 8.0.13+ server you already run, create an empty database and user for it:

```sql
-- PostgreSQL
CREATE USER craftstash WITH PASSWORD 'choose-one';
CREATE DATABASE craftstash OWNER craftstash;
-- MariaDB / MySQL
CREATE DATABASE craftstash CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;
CREATE USER 'craftstash'@'%' IDENTIFIED BY 'choose-one';
GRANT ALL PRIVILEGES ON craftstash.* TO 'craftstash'@'%';
```

and point the app at it, either with one setting:

```
DATABASE_URL=postgresql://craftstash:choose-one@192.168.1.20:5432/craftstash
DATABASE_URL=mysql://craftstash:choose-one@192.168.1.20:3306/craftstash        # MariaDB or MySQL
```

or with `DB_TYPE` (`postgres`, `mariadb` or `mysql`), `DB_HOST`, `DB_PORT` (5432 / 3306), `DB_NAME`, `DB_USER` and
`DB_PASSWORD`. `DB_PASSWORD_FILE` (and
`DATABASE_URL_FILE`) read the value from a file instead, for Docker secrets. `DB_SSLMODE` sets TLS (`require`,
`verify-full`, ...). At startup the app waits up to `DB_CONNECT_TIMEOUT` seconds (60) for the server, creates its tables,
and logs which database it is using (`[db] postgres (craftstash@192.168.1.20:5432/craftstash)`, never the password).
On PostgreSQL, the `nocase` collation it creates needs a build with ICU, which the official images and
distribution packages are. On MariaDB/MySQL, usernames and storage places compare ignoring case
(`utf8mb4_unicode_ci`) and everything else exactly, as on SQLite. If the account may not create triggers (common with
binary logging on), the log says so once; nothing is lost, as the app keeps the rule the trigger backs up. `/data` is still needed for the secret key, and for photos and files unless they are
[mapped elsewhere](#storage). Run one copy of the app.

**Moving to another database.** Stop the app, then copy everything across with the old setting still in place:

    docker compose stop craft-stash
    docker compose run --rm --no-deps craft-stash python manage.py move-db --to postgresql://craftstash:choose-one@192.168.1.20:5432/craftstash

then set `DATABASE_URL` and start it again. The target must be empty (`--replace` empties it first). If the app starts
on an empty external database while `/data/craft.db` still has accounts, it refuses and prints this command, so a
forgotten move can't look like lost data. `manage.py export-db file.json` and `manage.py import-db file.json` do the
same through a file, and work between any two databases.

**Backups** on an external database: the daily snapshots are portable `auto-*.json` exports in `BACKUP_DIR`, and the full
backup zip holds `craft-dump.json` instead of `craft.db`. `manage.py restore` takes either kind of backup, from either
kind of database. Your database server's own backups (`pg_dump`, `mariadb-dump`) work too.

## API and Home Assistant
Make a token under **Account → API tokens**. A token acts as you, so it sees exactly what you see. It is **read only** unless you choose **Read and write**, and only a hash of it is stored, so copy it when it's shown. Revoke a token there to cut off whatever uses it. A disabled account's tokens stop working too.

Send the token with every request as `Authorization: Bearer cs_…`. Responses are JSON. Request bodies can be JSON or form fields.

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
| `GET /api/v1/projects?status=`, `GET /api/v1/projects/<id>` | Projects, and one project with its materials and steps |

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

## Backups and recovery
- **What is saved:** `/data` holds `craft.db`, `uploads/` (guides and photos) and `backups/` (daily snapshots and safety copies made before upgrades and restores), unless those folders are [mapped elsewhere](#storage). Back up `/data` and every mapped folder (on Unraid, include the appdata folder).
- **Full backup:** Users page, **Download full backup** (zip of database and uploads).
- **Restore** (the app must be stopped):

      docker compose stop craft-stash
      docker cp backup.zip craft-stash:/data/backup.zip
      docker compose run --rm --no-deps craft-stash python manage.py restore /data/backup.zip
      docker compose start craft-stash

  A daily snapshot (`/data/backups/auto-*.db`) restores the same way but only brings back the database.
- **Locked out?** Reset any password from the command line:

      docker exec -it craft-stash python manage.py reset-password <username>    # asks for the new password
      docker exec -it craft-stash python manage.py create-user [--admin] <username>
      docker exec -it craft-stash python manage.py list-users
      docker exec -it craft-stash python manage.py make-admin <username>
- **Housekeeping:** `python manage.py cleanup --dry-run` lists uploaded files nothing refers to.

## Self-test
Checks that every feature works and times the pages, without touching your data. It starts a temporary copy of the app with an empty database, signs up test users and drives the site over real HTTP.

- **In the app (GUI):** Users page, **Open self-test**, **Run self-test**. Results appear live; open or download the full report when it finishes.
- **Headless, from a terminal:**

      docker exec -it craft-stash python selftest.py            # prints a table; report in /data/selftest/
      docker cp craft-stash:/data/selftest ./selftest-report     # copy report.html and report.json out

- **Options:** `--only Sharing` (just matching checks), `--perf-items 500` (bigger stash for the load-time pass), `--budget-ms 500` (flag slower pages), `--no-perf`, `--server flask`.
- **Real browser (optional):** on your own computer, `pip install playwright && playwright install chromium`, then `python app/selftest.py --browser` (add `--headed` to watch it). Adds real first-paint and loaded times plus desktop and phone screenshots, and flags pages that scroll sideways.
- Exit code is 1 if any check fails, so it also works in scripts. GitHub Actions runs it on every push and keeps the report as a downloadable artifact.

## Development
    pip install -r requirements.txt -r requirements-dev.txt
    pytest -q

GitHub Actions runs the tests, then builds and publishes the image on every push to `main`. `screenshots.yml` is a manual workflow that uploads screenshots of each page.

## GitHub → Dockhand
Push to your repo, add a git-backed stack in Dockhand (compose path `docker-compose.yml`), and either let it build from the repo or set `IMAGE` to the GHCR image (make the package public or add registry credentials).
