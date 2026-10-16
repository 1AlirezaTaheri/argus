#!/bin/bash
# Drive the panel's chrome controls, its accessibility, and its language switch,
# asserting each does what it exists for.
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
# attribute flips, the account menu reports aria-expanded. It then runs two more
# browser probes against the same image, so the gate gains their coverage without
# a third container pull or a third login:
#
#   a11y_check.js  the view toggle exposes its state, the grid is keyboard
#                  operable, and the copy button either copies or says so.
#   verify_lang.js the dictionaries are at parity, switching the language
#                  changes the rendered strings, and the choice persists.
#
# All three live in this step rather than in new ones so the gate keeps its
# fifteen steps. They are separate files because they are separate claims, and a
# failure in one should name itself rather than reporting "buttons failed".
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

IMAGE="${CHROME_IMAGE:-zenika/alpine-chrome:with-puppeteer}"

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    echo "pulling $IMAGE (first run only)"
    docker pull --quiet "$IMAGE" >/dev/null || { echo "SKIP: cannot pull $IMAGE"; exit 0; }
fi

# One container, all three probes. A probe that fails does not stop the others:
# a language failure should not hide an accessibility one.
docker run --rm --network host \
    -e NODE_PATH=/usr/src/app/node_modules \
    -e TARGET_BASE="${TARGET_BASE:-https://127.0.0.1:8443}" \
    -e SHOT_DIR=/shots \
    -v "$REPO"/.env:/env/.env:ro \
    -v "$HERE/button_verify.js:/app/button_verify.js:ro" \
    -v "$HERE/a11y_check.js:/app/a11y_check.js:ro" \
    -v "$HERE/verify_lang.js:/app/verify_lang.js:ro" \
    --entrypoint sh "$IMAGE" -c '
        set -u
        mkdir -p /shots
        ADMIN_USERNAME=$(grep "^ADMIN_USERNAME=" /env/.env | cut -d= -f2- | tr -d "\r")
        ADMIN_PASSWORD=$(grep "^ADMIN_PASSWORD=" /env/.env | cut -d= -f2- | tr -d "\r")
        export ADMIN_USERNAME ADMIN_PASSWORD

        rc=0
        echo "--- chrome controls ---"
        node /app/button_verify.js || rc=1
        echo "--- accessibility and feedback ---"
        node /app/a11y_check.js || rc=1
        echo "--- language ---"
        node /app/verify_lang.js || rc=1
        exit $rc
    '

exit $?