#!/bin/bash
# Capture the design surfaces for review: the style guide's depth section, the
# rules page in both themes, and the rules page at 390px.
#
# The mobile case is measured rather than eyeballed. A full-page screenshot of
# an overflowing page just crops at the viewport edge and looks fine, so the
# overflow is read from scrollWidth against innerWidth and the offending
# elements are named.
set -uo pipefail
cd /home/alireza/nmap-bot

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

SHOTS="${SHOT_DIR:-/tmp/netsentinel-shots}"
mkdir -p "$SHOTS" && chmod 777 "$SHOTS"

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -e SHOT_DIR=/shots \
    -v /home/alireza/nmap-bot/.env:/env/.env:ro \
    -v "$HERE/shoot_design.js:/app/shoot_design.js:ro" \
    -v "$SHOTS:/shots" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/shoot_design.js
    '

echo
echo "captured into $SHOTS"