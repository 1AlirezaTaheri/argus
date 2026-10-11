#!/bin/bash
# Capture the panel's pages in both themes, for judging a visual change.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

SHOTS="${SHOT_DIR:-/tmp/argus-shots}"
mkdir -p "$SHOTS"
# The container writes as root; without this the next run cannot clean up.
chmod 777 "$SHOTS"

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -e SHOT_DIR=/shots \
    -v "$REPO"/.env:/env/.env:ro \
    -v "$HERE/shoot_pages.js:/app/shoot_pages.js:ro" \
    -v "$SHOTS:/shots" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/shoot_pages.js
    '

echo
echo "captured into $SHOTS:"