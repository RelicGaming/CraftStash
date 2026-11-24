# Reverse proxy examples

Ready-to-adapt configs for putting Craft Stash behind a reverse proxy. Each one:

- forwards to the app on port **8000** (the container's port; `craft-stash:8000` on a shared Docker network, or
  `<host-ip>:8080` if you publish the port),
- allows uploads up to **30 MB** (the app's own limit is `MAX_UPLOAD_MB`, 25 by default; raise both together),
- waits up to **120 s** for a response (big backups and imports),
- passes the `X-Forwarded-*` headers, so set `TRUST_PROXY=1` on the app (and ideally `TRUSTED_PROXIES` to the proxy's
  address or Docker network, e.g. `172.16.0.0/12`).

| Proxy | File |
|---|---|
| Nginx Proxy Manager (bundled or your own) | see the README, "Reverse proxy" (no file needed: paste the Advanced snippet) |
| Traefik | [`traefik/docker-compose.traefik.yml`](traefik/docker-compose.traefik.yml) |
| Caddy | [`caddy/Caddyfile`](caddy/Caddyfile) |
| nginx | [`nginx/craft-stash.conf`](nginx/craft-stash.conf) |
| SWAG (linuxserver.io) | [`swag/craft-stash.subdomain.conf`](swag/craft-stash.subdomain.conf) |
| HAProxy | [`haproxy/haproxy.cfg`](haproxy/haproxy.cfg) |
| Cloudflare Tunnel | [`cloudflared/config.yml`](cloudflared/config.yml) |

**Sub-path instead of a subdomain** (`https://example.com/craft/`): set `URL_PREFIX=/craft` on the app and forward
`/craft/` to it. The proxy may pass the prefix through or strip it; both work. Each file shows where.
