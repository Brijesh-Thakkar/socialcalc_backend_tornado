#!/usr/bin/env bash
# Manage the Cloudflare Tunnel overlay for the Docker Compose stack.
#
#   scripts/cloudflare-tunnel.sh up      stack + named tunnel (needs CLOUDFLARE_TUNNEL_TOKEN)
#   scripts/cloudflare-tunnel.sh quick   stack + quick tunnel; prints the trycloudflare URL once /login has returned 200 three times in a row (90 s timeout)
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
# api.trycloudflare.com is Cloudflare's own API host (appears in the logs), not a tunnel
URL_RE='https://[a-z0-9-]+\.trycloudflare\.com'

dc() { docker compose "${FILES[@]}" "$@"; }

have_token() {
  # Environment wins, then .env. The value is never printed.
  [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ] && return 0
  [ -f .env ] && grep -qE '^CLOUDFLARE_TUNNEL_TOKEN=.+' .env
}

quick_url() { dc "${PROFILES[@]}" logs --no-color cloudflared-quick 2>/dev/null | grep -oE "$URL_RE" | grep -v '^https://api\.' | tail -1 || true; }

# A trycloudflare URL appears in the logs before DNS/edge routing to it works. Wait until
# GET /login returns 200 READY_STREAK times in a row (default 3) within READY_TIMEOUT
# seconds (default 90) so callers (tests, curl) never race a half-ready tunnel.
wait_ready() {
  local url=$1 need=${READY_STREAK:-3} deadline=$((SECONDS + ${READY_TIMEOUT:-90}))
  local streak=0 code err last="no response yet"
  while [ "$SECONDS" -lt "$deadline" ]; do
    err=$(mktemp)
    code=$(curl -sS -o /dev/null -m 5 -w '%{http_code}' "$url/login" 2>"$err") || true
    if [ "$code" = 200 ]; then
      streak=$((streak + 1)); last="HTTP 200"
      [ "$streak" -ge "$need" ] && { rm -f "$err"; return 0; }
    else
      streak=0
      last="HTTP $code"; [ -s "$err" ] && last="$last ($(head -c 120 "$err" | tr -d '\n'))"
    fi
    rm -f "$err"
    sleep 1
  done
  echo "Quick tunnel $url did not return HTTP 200 on /login $need times in a row within ${READY_TIMEOUT:-90}s." >&2
  echo "Last result: $last" >&2
  echo "Check '$0 logs' (UDP blocked? try CLOUDFLARED_PROTOCOL=http2) and that nginx/app containers are healthy." >&2
  return 1
}

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
  # compose/build output goes to stderr so stdout is exactly the URL (scriptable: URL=$(... quick))
  dc --profile tunnel-quick up -d --build >&2
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
  echo "Waiting for $url to serve /login..." >&2
  wait_ready "$url" || exit 1
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
