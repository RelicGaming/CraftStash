#!/bin/sh
# Starts as root only to create the storage folders and fix their ownership, then drops to PUID:PGID.
#   CHOWN=auto    (default) change the owner only where it is wrong: the whole folder if its top level is wrong
#                 (a fresh mapping), otherwise just the wrong entries in its first two levels
#   CHOWN=always  change the owner of everything in every folder, every start
#   CHOWN=never   leave ownership alone (NAS shares that manage their own permissions)
set -e
PUID="${PUID:-10001}"; PGID="${PGID:-10001}"; CHOWN="${CHOWN:-auto}"
cd /app
# Temp files (uploads while they arrive, gunicorn's worker files, backup zips) go to TMP_DIR, a mapped folder,
# never the container's own disk.
TMPDIR="$(python paths.py --tmp)"; export TMPDIR
if [ "$(id -u)" = "0" ]; then
  python paths.py | while IFS= read -r dir; do
    mkdir -p "$dir"
    case "$CHOWN" in
      never) ;;
      always) chown -R "$PUID:$PGID" "$dir" || echo "[storage] could not change the owner of $dir" >&2 ;;
      *)
        if [ "$(stat -c %u "$dir")" != "$PUID" ]; then
          chown -R "$PUID:$PGID" "$dir" || echo "[storage] could not change the owner of $dir" >&2
        else
          find "$dir" -mindepth 1 -maxdepth 2 \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown "$PUID:$PGID" {} + 2>/dev/null \
            || echo "[storage] could not change the owner of some files in $dir" >&2
        fi ;;
    esac
    if ! setpriv --reuid="$PUID" --regid="$PGID" --clear-groups test -w "$dir"; then
      echo "[storage] WARNING: $dir is not writable by $PUID:$PGID. Set PUID/PGID to the folder's owner, or CHOWN=always." >&2
    fi
  done
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
