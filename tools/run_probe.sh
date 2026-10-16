#!/bin/bash
# Run a puppeteer script against the panel with credentials from .env.
#
#   bash tools/run_probe.sh tools/page_sweep.js [OUT_SUBDIR]
#
# Generalises the invocation every existing browser probe hardcodes. The chrome
# image carries puppeteer under /usr/src/app/node_modules, so NODE_PATH has to
# point there or `require('puppeteer')` fails -- which is what happened when the
# screenshot script was run without it.
#
# Credentials are read inside the container and exported there. They are never
# echoed, never passed on the host command line, and `set +x` keeps a trace from
# leaking them.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

SCRIPT="${1:?usage: run_probe.sh <script.js> [outdir]}"
OUTNAME="${2:-argus-shots}"
IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"
OUT="${SHOT_DIR:-/tmp/$OUTNAME}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE"
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull"; exit 0; }
fi

mkdir -p "$OUT"
chmod 777 "$OUT" 2>/dev/null || true

set +x
docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -e SHOT_DIR=/shots \
    ${SWEEP_PAGES:+-e "SWEEP_PAGES=$SWEEP_PAGES"} \
    -v "$REPO"/.env:/env/.env:ro \
    -v "$REPO/$SCRIPT:/app/probe.js:ro" \
    -v "$OUT":/shots \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/probe.js
    '