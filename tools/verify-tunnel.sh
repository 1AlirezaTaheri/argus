#!/bin/bash
# Verify the Mini App through the public tunnel -- the only path Telegram uses.
#
# ## Why this script is separate from verify-miniapp.sh
#
# Every other probe in this repository runs against `https://127.0.0.1:8443`,
# which is local Caddy. It never touches Cloudflare. So the whole suite reported
# all green through an eleven-hour outage in which the tunnel logged
#
#     ERR Register tunnel error ... Unauthorized: Tunnel not found
#
# every few seconds and the Mini App could not be opened from Telegram at all.
# The component that was broken was not in the path being tested.
#
# This is the only check that exercises the tunnel, and it is deliberately not
# merged into the Mini App check: a deployment without a tunnel would then fail
# both, and the two failures would look like one problem.
#
# ## What it asserts
#
#   1. The tunnel container reports healthy to Docker.
#   2. The wrapper's status file says `state=registered`.
#   3. The hostname in that file is the one the bot puts in its button --
#      which is what proves MINIAPP_URL can no longer drift out of sync.
#   4. The Mini App is reachable at that hostname over the public internet.
#
# Point 3 is the one that matters most. A stale .env is silent: the bot sends a
# well-formed button, nothing raises, and the button simply points at a host
# that no longer resolves.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

FAILED=0
fail() { echo "  FAIL: $*"; FAILED=1; }
ok()   { echo "  ok:   $*"; }

echo "=== 1. does Docker think the tunnel is healthy? ==="
STATUS="$(docker compose ps --format '{{.Service}}\t{{.Status}}' tunnel 2>/dev/null)"
echo "  $STATUS"
case "$STATUS" in
    *"(healthy)"*) ok "the tunnel is healthy" ;;
    *) fail "the tunnel is not healthy: $STATUS" ;;
esac

echo
echo "=== 2. did the wrapper publish a hostname? ==="
URL="$(docker compose exec -T tunnel sh -c \
    'cat /tunnel/tunnel-url.conf 2>/dev/null' | tr -d '\r\n ')"
STATE="$(docker compose exec -T tunnel sh -c \
    'cat /tunnel/tunnel-status.conf 2>/dev/null' | tr -d '\r')"
echo "  status: ${STATE:-<none>}"
echo "  url:    ${URL:-<none>}"

if [ -z "$URL" ]; then
    fail "no hostname was published; the wrapper never saw the banner"
    echo
    echo "  the tunnel log is the place to look:"
    docker compose logs --tail=20 tunnel 2>&1 | tail -8
    exit 1
fi

case "$STATE" in
    state=registered*) ok "the wrapper reports a registered tunnel" ;;
    *) fail "the status is '${STATE:-<none>}', not state=registered" ;;
esac

case "$URL" in
    *.trycloudflare.com|*.cfargotunnel.com)
        ok "the published host is a Cloudflare tunnel" ;;
    *)
        fail "published host '$URL' is not a tunnel hostname"
        ;;
esac

# The specific wrong answer that shipped once: cloudflared logs documentation
# links on the same suffix, and the first extraction published
# `api.trycloudflare.com` as the tunnel's address. The bot's button then pointed
# at Cloudflare's API host, which answers 405 to everything.
case "$URL" in
    https://api.*|https://developers.*|https://update.*)
        fail "published host is a Cloudflare service host, not a tunnel" ;;
    *) ok "the host is not a Cloudflare service domain" ;;
esac

echo
echo "=== 3. does the bot agree with the tunnel? ==="
BOT_URL="$(docker compose exec -T bot python -c '
from config.settings import Settings
from bot.miniapp import app_url
print(app_url(Settings.from_env()))
' 2>/dev/null | tr -d '\r\n ')"
echo "  tunnel: $URL"
echo "  bot:    $BOT_URL"

if [ "$BOT_URL" = "$URL/app" ]; then
    ok "the bot's button points at the live tunnel"
else
    fail "the bot publishes '$BOT_URL' but the tunnel is at '$URL'"
    echo "       MINIAPP_URL in .env is stale, or the bot predates the volume."
fi

echo
echo "=== 4. can the public internet reach the Mini App? ==="
# TLS is verified permissively on purpose: Cloudflare's certificate is valid,
# but this host's clock and CA store are not the subject under test. Reachability
# is, and a certificate failure would mask it as a network one.
probe() {
    curl -sk -o /dev/null -w '%{http_code}' --max-time 40 "$1"
}

for path in /app /app/theme.js /app/favicon.svg; do
    code="$(probe "$URL$path")"
    case "$code" in
        200) ok "GET $path -> 200" ;;
        *)   fail "GET $path -> $code (expected 200)" ;;
    esac
done

# The panel must be reachable too: the tunnel's origin is admin:8080, so a
# broken tunnel breaks the panel for anyone on the public hostname even though
# `https://127.0.0.1:8443/admin` still answers perfectly.
admin_code="$(probe "$URL/admin/")"
case "$admin_code" in
    200) ok "GET /admin/ -> 200" ;;
    *)   fail "GET /admin/ -> $admin_code (expected 200)" ;;
esac

echo
if [ "$FAILED" -eq 0 ]; then
    echo "the Mini App is reachable at $URL"
    exit 0
fi
echo "the public tunnel path is broken (see FAIL above)"
exit 1
