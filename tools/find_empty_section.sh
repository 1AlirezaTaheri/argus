#!/bin/bash
set -uo pipefail
cd /home/alireza/nmap-bot
IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"
HERE="$(cd "$(dirname "$0")" && pwd)"
docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v /home/alireza/nmap-bot/.env:/env/.env:ro \
    -v "$HERE/find_empty_section.js:/app/find_empty_section.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/find_empty_section.js
    '