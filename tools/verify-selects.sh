#!/bin/bash
# Verify the panel's dropdowns still work after the conversion off native
# <select> elements.
#
# This is not a cosmetic change. Radix Select fires onValueChange with a string
# rather than an event, forbids the empty string as an item value, and renders
# its popup in a portal. The audit page's two filters used "" for "no filter", so
# after conversion their first option became unselectable and the trigger
# rendered blank -- caught here, not by a screenshot and not by any type check.
set -uo pipefail

cd /home/alireza/nmap-bot

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"
HERE="$(cd "$(dirname "$0")" && pwd)"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE (first run only)"
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v /home/alireza/nmap-bot/.env:/env/.env:ro \
    -v "$HERE/select_probe.js:/app/select_probe.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/select_probe.js
    '