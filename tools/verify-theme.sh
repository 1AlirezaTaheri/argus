#!/bin/bash
# Does the panel apply its theme before first paint?
#
# The answer cannot be read off a settled screenshot: index.html hardcodes
# data-theme="dark", so a light-theme user whose pre-paint script failed sees dark
# first and light a moment later. Nothing throws, nothing logs, no test fails --
# which is how a broken CSP hash went unnoticed while the header looked correct.
#
# This seeds a stored preference, loads the page, and reads data-theme at the
# earliest moment the <html> element exists, before React has mounted.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE (first run only)"
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v "$HERE/theme_probe.js:/app/theme_probe.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c 'node /app/theme_probe.js'