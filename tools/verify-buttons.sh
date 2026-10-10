#!/bin/bash
# Drive the panel's chrome controls -- command palette, theme toggle, account
# menu -- on several pages, asserting each does what it exists for.
#
# The broad button sweep reported these three as dead, and was wrong three times
# over:
#   * it clicked "Sign out" and destroyed its own session, so every later page
#     was the login form and the login form's buttons were reported as failures;
#   * it read the command palette trigger from a stale element handle, so the
#     click failed with "not clickable" and was recorded as a dead button;
#   * it judged the theme toggle by page text, but a theme change is a CSS
#     custom property on <html> and barely touches the text.
#
# This asserts behaviour instead: the palette opens and filters, the theme
# attribute flips, the account menu reports aria-expanded.
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
    -v "$HERE/button_verify.js:/app/button_verify.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/button_verify.js
    '