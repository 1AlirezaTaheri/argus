#!/bin/bash
# Do any of Tailwind's hardcoded defaults reach the user's eye?
#
# Every other frontend check here asks whether something *works*. This one asks
# whether something the app never wrote is being painted instead -- preflight's
# `border-color:#e5e7eb`, its `::placeholder` gray-400, its blue-500 ring seed.
# None of them are mentioned in any source file, so no source-level test can
# see them, and this app writes `className` across lines in a way that defeats
# grep anyway.
#
# Separate from verify-guide.sh because it answers a different question: the
# guide audit judges the specimens the design system documents, this judges
# everything else on the page that the framework contributes unasked.
#
# Runs against the deployed bundle through the same origin the admin uses.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull --quiet "$IMAGE" >/dev/null 2>&1 \
        || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -v "$REPO"/.env:/env/.env:ro \
    -v "$HERE/defaults_audit.js:/app/defaults_audit.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/defaults_audit.js
    '
