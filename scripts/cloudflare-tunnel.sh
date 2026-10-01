#!/usr/bin/env bash
# Manage the Cloudflare Tunnel overlay for the Docker Compose stack.
#
#   scripts/cloudflare-tunnel.sh up      stack + named tunnel (needs CLOUDFLARE_TUNNEL_TOKEN)
#   scripts/cloudflare-tunnel.sh quick   stack + quick tunnel, prints the trycloudflare URL
#   scripts/cloudflare-tunnel.sh down    stop the tunnel container(s) only
#   scripts/cloudflare-tunnel.sh down --all   stop and remove the whole stack
#   scripts/cloudflare-tunnel.sh logs    follow cloudflared logs
#   scripts/cloudflare-tunnel.sh status  show tunnel state (and the quick-tunnel URL)
#
# Extra compose files (e.g. a local, uncommitted port override) can be added with
#   COMPOSE_EXTRA_FILES="/path/override.yml other.yml" scripts/cloudflare-tunnel.sh up
set -euo pipefail

cd "$(dirname "$0")/.."

FILES=(-f docker-compose.yml -f docker-compose.cloudflare.yml)
for f in ${COMPOSE_EXTRA_FILES:-}; do FILES+=(-f "$f"); done
PROFILES=(--profile tunnel --profile tunnel-quick)
URL_RE='https://[a-z0-9-]+\.trycloudflare\.com'

dc() { docker compose "${FILES[@]}" "$@"; }

have_token() {
  # Environment wins, then .env. The value is never printed.
  [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ] && return 0
  [ -f .env ] && grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' .env
}

quick_url() { dc "${PROFILES[@]}" logs --no-color cloudflared-quick 2>/dev/null | grep -oE "$URL_RE" | tail -1 || true; }

cmd_up() {
  if ! have_token; then
    echo "CLOUDFLARE_TUNNEL_TOKEN is not set (env or .env)." >&2
    echo "Create a named tunnel in the Cloudflare dashboard (see docs/deploy-cloudflare-tunnel.md)," >&2
    echo "or run '$0 quick' for a temporary trycloudflare.com URL." >&2
    exit 1
  fi
  dc --profile tunnel up -d --build
  echo "Stack and named tunnel started. Public hostname is configured in the Cloudflare dashboard."
}

cmd_quick() {
  dc --profile tunnel-quick up -d --build
  local url="" i
  for i in $(seq 1 60); do
    url=$(quick_url)
    [ -n "$url" ] && break
    sleep 2
  done
  if [ -z "$url" ]; then
    echo "Timed out waiting for a trycloudflare.com URL. Recent logs:" >&2
    dc "${PROFILES[@]}" logs --tail=30 cloudflared-quick >&2 || true
    exit 1
  fi
  echo "$url"
}

cmd_down() {
  if [ "${1:-}" = "--all" ]; then
    dc "${PROFILES[@]}" down --remove-orphans
  else
    dc "${PROFILES[@]}" stop cloudflared cloudflared-quick
    dc "${PROFILES[@]}" rm -f cloudflared cloudflared-quick
  fi
}

cmd_logs() { dc "${PROFILES[@]}" logs -f --tail=100 cloudflared cloudflared-quick; }

cmd_status() {
  dc "${PROFILES[@]}" ps cloudflared cloudflared-quick
  local url; url=$(quick_url)
  [ -n "$url" ] && echo "quick tunnel URL: $url"
  return 0
}

case "${1:-}" in
  up)     cmd_up ;;
  quick)  cmd_quick ;;
  down)   shift; cmd_down "$@" ;;
  logs)   cmd_logs ;;
  status) cmd_status ;;
  *) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
