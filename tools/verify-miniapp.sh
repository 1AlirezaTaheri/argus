#!/bin/bash
# Verify the Mini App: it responds, it has no inline script, and the Telegram
# palette reaches the element before the app mounts.
#
# The Mini App had no coverage at all. It is served by the admin process, so a
# failure in its own build or in the /app catch-all would not surface in any
# panel check.
set -uo pipefail
cd /home/alireza/nmap-bot

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE (first run only)"
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

SHOTS=/tmp/miniapp-shots
rm -rf "$SHOTS" && mkdir -p "$SHOTS" && chmod 777 "$SHOTS"

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v "$HERE/miniapp_probe.js:/app/miniapp_probe.js:ro" \
    -v "$SHOTS:/shots" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c 'node /app/miniapp_probe.js'