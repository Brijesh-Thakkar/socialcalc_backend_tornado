# Deploying with Docker Compose + Cloudflare Tunnel

Publish the existing Docker Compose stack on the public internet through a
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/).
No inbound port, public IP, certbot or load balancer is needed: a `cloudflared`
container opens an **outbound** connection to Cloudflare, which terminates TLS.

## Architecture

```
Browser ──https──▶ Cloudflare edge ──(tunnel, outbound from you)──▶ cloudflared
                                                                       │ http
                                                                       ▼
                                                                  nginx:80
                                                                  │      │
                                                          round-robin    │
                                                                  ▼      ▼
                                                               app1     app2   (Tornado, cloudmain-dev.py, :8888)
                                                                  │      │
                                    ┌─────────────┬───────────────┴──────┴────────────┐
                                    ▼             ▼                                   ▼
                                  MinIO / S3   memcache (sessions)    meshkit / kubo sidecars (5050, 5051, …)
```

- `cloudflared` only sees `nginx:80`; the app containers and sidecars stay private.
- The tunnel delivers **plain HTTP** to nginx. nginx forwards `X-Forwarded-Proto: https`
  (from Cloudflare) and `CF-Connecting-IP` (as `X-Real-IP`) to Tornado, which runs with
  `xheaders=True`, so generated links use `https://<public host>`.
- Nginx round-robins between `app1` and `app2`; this is unaffected by the tunnel because
  all state is in S3 (files) and memcache (sessions), not in a container.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.cloudflare.yml` | Overlay with `cloudflared` (profile `tunnel`) and `cloudflared-quick` (profile `tunnel-quick`). Pinned image `cloudflare/cloudflared:2026.9.3`. |
| `scripts/cloudflare-tunnel.sh` | `up`, `quick`, `down [--all]`, `logs`, `status` |
| `configs/nginx.docker.conf` | Proxy-aware `X-Forwarded-Proto` / `X-Real-IP` |
| `.env` | `CLOUDFLARE_TUNNEL_TOKEN`, optional `PUBLIC_BASE_URL` |

The overlay uses profiles so a plain `docker compose up` (and CI) never starts `cloudflared`.
The container receives **only** `TUNNEL_TOKEN`; it has no `env_file`, so none of the app
secrets in `.env` reach it.

## Option A — named tunnel (stable hostname)

Requires a Cloudflare account with a domain on Cloudflare DNS.

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel**
   → connector type **Cloudflared**. Name it (e.g. `socialcalc`).
2. On the install page, copy the **token** (the long string after `--token` / `TUNNEL_TOKEN`).
   Do not run the install command shown there; Docker Compose does that for you.
3. Put the token in `.env` on the machine running Docker (never commit it):
   ```
   CLOUDFLARE_TUNNEL_TOKEN=<token>
   ```
4. Tunnel → **Public Hostname** → **Add**:
   - Subdomain/Domain: e.g. `sheets.example.com`
   - Service **Type** `HTTP`, **URL** `nginx:80`  (i.e. `http://nginx:80`)
   - Do **not** use `localhost`; inside the container that is cloudflared itself.
5. Start everything:
   ```bash
   scripts/cloudflare-tunnel.sh up
   scripts/cloudflare-tunnel.sh status      # cloudflared should be "healthy"
   curl -sI https://sheets.example.com/login
   ```
6. Optional: set `PUBLIC_BASE_URL=https://sheets.example.com` in `.env` and
   `docker compose up -d` again. Links are already built from the forwarded scheme and
   Host, so this is only needed if you want to pin the origin.

Equivalent plain command:
```bash
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml --profile tunnel up -d
```

## Option B — quick tunnel (testing, no account)

```bash
scripts/cloudflare-tunnel.sh quick
# https://random-words-1234.trycloudflare.com
```

The URL is random, changes on every start and has no uptime guarantee. Don't use it for
production. Without the script:
```bash
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml --profile tunnel-quick up -d
docker compose -f docker-compose.yml -f docker-compose.cloudflare.yml --profile tunnel-quick logs cloudflared-quick | grep trycloudflare
```

## Environment variables

| Variable | Where | Notes |
|----------|-------|-------|
| `CLOUDFLARE_TUNNEL_TOKEN` | `.env` | Named tunnel only. Passed to cloudflared as `TUNNEL_TOKEN`. Secret. |
| `PUBLIC_BASE_URL` | `.env` | Optional. Public origin used in generated PDF/share URLs; no trailing slash. |
| `CLOUDFLARED_PROTOCOL` | shell / `.env` | `auto` (default), `quic` or `http2`. Set `http2` if the tunnel never becomes ready because UDP/7844 is blocked. |
| `COMPOSE_EXTRA_FILES` | shell | Extra compose files for the script, e.g. a local port override. |

If port 8080 is already used on the host, put a local, uncommitted override in
`COMPOSE_EXTRA_FILES` (the tunnel itself does not need a host port):
```yaml
services:
  nginx:
    ports: !override
      - "18080:80"
```

## Troubleshooting

- **Redirect loop (`ERR_TOO_MANY_REDIRECTS`)** — happens when the origin redirects HTTP→HTTPS
  while Cloudflare talks plain HTTP to it. `configs/nginx.docker.conf` has no such redirect;
  if you add one, base it on `$forwarded_proto`, not `$scheme`. Also make sure Cloudflare
  SSL/TLS mode is not set to *Flexible* in combination with an origin redirect.
- **502 Bad Gateway / Error 1033** — the tunnel is up but cannot reach the service (wrong
  service URL; use `http://nginx:80`) or the tunnel is down (`scripts/cloudflare-tunnel.sh logs`).
  Error 1033 means no connector is currently connected for that tunnel.
- **Links in the app show `http://`** — nginx is not forwarding `X-Forwarded-Proto`, or the
  app was started without `xheaders=True` (both `cloudmain-dev.py` and `cloudmain.py` set it).
- **WebSockets / long-polling** — `/updates`, `/broadcast` and `/collaborate` have upgrade
  headers set in nginx and Cloudflare proxies WebSockets by default. For plain HTTP requests
  (including long-polling) Cloudflare returns error 524 if the origin has not started a response
  within 125 s by default; this cannot be raised on Free/Pro plans, so make long-poll handlers
  answer sooner than that
  ([Cloudflare: error 524](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/)).
- **413 / upload fails** — nginx allows 50 MB (`client_max_body_size`). Cloudflare's Free and
  Pro plans cap request bodies at **100 MB** (Business 200 MB), so raising nginx above that gains
  nothing ([limits](https://developers.cloudflare.com/workers/platform/limits/)).
- **Tunnel never becomes ready / `readyConnections: 0`** — the log shows
  `Failed to dial a quic connection ... timeout`. The network blocks UDP/7844 (QUIC). Set
  `CLOUDFLARED_PROTOCOL=http2` (outbound TCP 7844 only) and restart the tunnel.
- **Container unhealthy** — `docker compose ... --profile tunnel ps`; the healthcheck runs
  `cloudflared tunnel ready` against the metrics endpoint on port 2000 (container-internal).
- **Header trust** — nginx trusts `X-Forwarded-Proto` / `CF-Connecting-IP` from whoever calls it.
  Behind the tunnel only cloudflared can reach nginx, but if you also publish port 8080
  directly, a client can spoof them (affects generated links and logged IPs only).

## Rollback

1. Stop only the tunnel (stack keeps running locally):
   ```bash
   scripts/cloudflare-tunnel.sh down
   ```
2. Remove the public hostname (or delete the tunnel) in the Cloudflare dashboard.
3. Stop everything: `scripts/cloudflare-tunnel.sh down --all`.

The nginx/app changes are backwards compatible: with no proxy headers they behave as before
(`$scheme` and the peer address are used), so the existing EC2 / direct deployment is
unaffected.
