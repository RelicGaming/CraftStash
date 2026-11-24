# Craft Stash on Unraid

## With the template (recommended)
1. Save the template where Unraid looks for your own templates. In the Unraid terminal (or over SSH):

       wget -O /boot/config/plugins/dockerMan/templates-user/my-craft-stash.xml \
         https://raw.githubusercontent.com/RelicGaming/CraftStash/main/unraid/craft-stash.xml

   Then **Docker** tab → **Add Container** → pick **craft-stash** from the **Template** list (under User templates).
2. Check the four paths. By default:

   | Path | Host folder | Holds |
   |---|---|---|
   | App data → `/data` | `/mnt/user/appdata/craft-stash` | database, secret key, temp files (keep on the cache) |
   | Photos → `/photos` | `/mnt/user/craft-stash/photos` | item and step photos |
   | Files → `/files` | `/mnt/user/craft-stash/files` | uploaded patterns and guides |
   | Backups → `/backups` | `/mnt/user/craft-stash/backups` | daily snapshots |

   Point Photos, Files and Backups at any share (a `craft-stash` share set to the array is a good default). Want
   everything in appdata instead? Set those three paths to sub-folders of the App data path.
3. **Apply**, then open the Web UI. The first account you create is the admin.

`PUID 99` / `PGID 100` (nobody:users) is right for Unraid shares. The container fixes ownership of the four folders at
start where needed; set `CHOWN=never` for a share whose permissions you manage yourself. The startup log
(container icon → **Logs**) lists each folder with its free space and warns if one isn't mapped, so nothing
silently fills the Docker vdisk. The log itself is capped at 30 MB.

**Remote access:** put Nginx Proxy Manager (from Community Applications) in front, set `TRUST_PROXY=1`, and
`TRUSTED_PROXIES` to NPM's IP. See "Behind a reverse proxy" in the main README.

**External database:** set `DATABASE_URL` (Advanced view) to a PostgreSQL or MariaDB container you already run, e.g.
`postgresql://craftstash:secret@192.168.1.20:5432/craftstash`. See "Database" in the main README.

## With the Compose Manager plugin
Install **Docker Compose Manager** from Community Applications, then **Add New Stack** → **Edit Stack** → paste the
repository's `docker-compose.yml`, and put your settings in the stack's **.env**:

```
PUID=99
PGID=100
TZ=Australia/Brisbane
IMAGE=ghcr.io/relicgaming/craftstash:latest
PHOTOS_DIR=/photos
UPLOADS_DIR=/files
BACKUP_DIR=/backups
# COMPOSE_PROFILES=npm,postgres     (bundled Nginx Proxy Manager and/or database; see the main README)
```

and in the compose file swap the `craft-data:/data` volume for host folders:

```yaml
    volumes:
      - /mnt/user/appdata/craft-stash:/data
      - /mnt/user/craft-stash/photos:/photos
      - /mnt/user/craft-stash/files:/files
      - /mnt/user/craft-stash/backups:/backups
```

Remove the `build: .` line (Compose Manager pulls the image rather than building it). For the bundled services, set
`NPM_DATA_DIR`, `NPM_LETSENCRYPT_DIR`, `POSTGRES_DATA_DIR` or `MARIADB_DATA_DIR` to appdata folders too, so their data
also stays off the vdisk.
