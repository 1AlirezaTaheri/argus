#!/bin/bash
# Drive the Rules screen end to end: create a port rule through the form, prove
# the engine refuses the port, toggle, edit, delete, and read every step back
# from the server.
#
# The point is the "prove" step. A rules screen can render perfectly while the
# thing the user asked for does nothing, because "the row shows a toggle" and
# "the rule is enforced" are different claims and only one of them is a bug the
# user would notice.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE (first run only)"
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

SHOTS=/tmp/rules-shots
rm -rf "$SHOTS" && mkdir -p "$SHOTS" && chmod 777 "$SHOTS"

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v "$REPO"/.env:/env/.env:ro \
    -v "$HERE/rules_probe.js:/app/rules_probe.js:ro" \
    -v "$SHOTS:/shots" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/rules_probe.js
    '