#!/bin/sh
# Starts as root only to fix /data ownership (bind mounts), then drops to PUID:PGID.
set -e
PUID="${PUID:-10001}"; PGID="${PGID:-10001}"
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/uploads
  if [ "$(stat -c %u /data)" != "$PUID" ] || [ -n "$(find /data -maxdepth 2 ! -user "$PUID" -print -quit)" ]; then
    chown -R "$PUID:$PGID" /data
  fi
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi
exec "$@"
