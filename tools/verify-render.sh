#!/bin/bash
# Load every panel route in a real browser and require that each one renders.
#
# This is the check that would have caught the blank target detail page: React
# error #310 from `useMemo`/`useEffect` called after an early return, so the
# loading render ran zero hooks there and the loaded render ran two.
#
# Nothing else caught it. `tsc --noEmit` passed, because hook order is not a type
# error. Every API test passed, because the endpoints were always correct. A
# source-level heuristic for hook order was written, and it passed the buggy file
# too -- which is the reason this checks the browser instead: the invariant only
# exists at render time.
#
# It has already earned its place. Adding a chart-width hook to the same page
# re-introduced the violation, and this reported the blank route on the next run.
#
# Credentials are read from .env *inside* the container and exported to node, so
# they never appear on a command line or in any output.
#
# The browser image is pulled on demand; it is only needed for this check, which
# is why it is not part of the compose stack.
set -uo pipefail

# Absolute, because this is mounted into a container: a relative path from
# `bash tools/verify-render.sh` is not a valid volume name.
HERE="$(cd "$(dirname "$0")" && pwd)"

cd /home/alireza/nmap-bot

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE (first run only)"
    docker pull --quiet "$IMAGE" >/dev/null || {
        echo "SKIP: could not pull $IMAGE, cannot run the render check"
        exit 0
    }
fi

BASE="${TARGET_BASE:-https://127.0.0.1:8443}"

docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="$BASE" \
    -v /home/alireza/nmap-bot/.env:/env/.env:ro \
    -v "$HERE/render_check.js:/app/render_check.js:ro" \
    -w /usr/src/app \
    --entrypoint sh "$IMAGE" -c '
        set -eu
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD
        node /app/render_check.js
    '