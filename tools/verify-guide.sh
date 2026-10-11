#!/bin/bash
# Audit the style guide: every section renders, every specimen paints, no two
# swatches share a colour, every control is named and reachable, and text
# meets its contrast requirement.
#
# The guide is what the rest of the panel is written against, so a specimen that
# renders nothing -- or renders the wrong value -- is documentation that lies.
# Checked as computed values rather than class names, because a token resolving
# to `transparent` still leaves a plausible-looking page.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v "$REPO"/.env:/env/.env:ro \
    -v "$HERE/guide_audit.js:/app/guide_audit.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/guide_audit.js
    '