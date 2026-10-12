#!/bin/bash
# Phase 1: build, deploy, then verify -- refusing to verify a stale image.
#
# The previous run built, the build FAILED on a tsc error, and the verify
# script then reported against the previously deployed bundle anyway. Its
# output looked plausible and its failures were about the old code, which is
# worse than no output. So the build's exit status is checked here, before the
# verifier is allowed to run at all.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
cd "$REPO"

# Counted steps. The old script printed "done" unconditionally, so a run that
# failed four checks above still ended looking like a pass -- which is the one
# failure mode a verification script must not have.
STEPS=0
FAILED=0
FAILING=""

run_step() {
    _name="$1"
    shift
    STEPS=$((STEPS + 1))
    if "$@" 2>&1 | tail -"${TAIL_LINES:-40}"; then
        return 0
    fi
    # A pipeline's status is the last command's, so the step's own status is
    # read from PIPESTATUS rather than from `if`.
    if [ "${PIPESTATUS[0]}" != "0" ]; then
        FAILED=$((FAILED + 1))
        FAILING="$FAILING $_name"
    fi
    return 0
}

echo "=== typecheck ==="
# The exit status of the typecheck is captured before any pipe is attached.
# `... | tail -4` inside a subshell, followed by reading PIPESTATUS, yields
# tail's status, so a genuine type error passed the gate and the build ran
# anyway -- which is how a failed build once got verified against a stale
# bundle.
tsc_out="$(cd admin/frontend && bash "$HERE/tsc.sh" 2>&1)"
tsc_rc=$?
echo "$tsc_out" | tail -4
if [ "$tsc_rc" != "0" ]; then
    echo "TYPECHECK FAILED (exit $tsc_rc) -- not deploying, not verifying"
    exit 1
fi

echo
echo "=== build ==="
docker compose build admin 2>&1 | tail -3
if [ "${PIPESTATUS[0]}" != "0" ]; then
    echo "BUILD FAILED -- not deploying, not verifying"
    exit 1
fi

echo
echo "=== deploy ==="
docker compose up -d 2>&1 | tail -3
sleep 40
docker compose ps --format 'table {{.Service}}\t{{.Status}}'

echo
echo "=== verify the deployed bundle ==="
TAIL_LINES=30 run_step "verify-ui" bash "$HERE/verify-ui.sh"

echo
echo "=== verify the live dashboard ==="
TAIL_LINES=40 run_step "verify-dashboard" bash "$HERE/verify-dashboard.sh"

echo
echo "=== verify the target detail API ==="
TAIL_LINES=45 run_step "verify-target-detail-api" bash "$HERE/verify-target-detail-api.sh"

echo
echo "=== verify the target detail UI in the bundle ==="
TAIL_LINES=30 run_step "verify-target-detail" bash "$HERE/verify-target-detail.sh"

echo
echo "=== verify panel scanning ==="
TAIL_LINES=40 run_step "verify-panel-scan" bash "$HERE/verify-panel-scan.sh"

echo
echo "=== verify mobile navigation ==="
TAIL_LINES=25 run_step "verify-mobile-nav" bash "$HERE/verify-mobile-nav.sh"

echo
echo "=== verify every route actually renders in a browser ==="
TAIL_LINES=20 run_step "verify-render" bash "$HERE/verify-render.sh"

echo
echo "=== verify the theme is applied before first paint ==="
TAIL_LINES=16 run_step "verify-theme" bash "$HERE/verify-theme.sh"

echo
echo "=== verify the dropdowns work ==="
TAIL_LINES=22 run_step "verify-selects" bash "$HERE/verify-selects.sh"

echo
echo "=== verify the style guide ==="
run_step "guide" bash "$HERE/verify-guide.sh"

echo
echo "=== verify no framework default paints over the design system ==="
# Not a feature check. Tailwind's preflight supplies a border colour, a
# placeholder colour and a ring colour that no file here mentions, so they are
# invisible to every source-level test: they are only observable as computed
# styles in a real browser, in both themes, on every page.
run_step "defaults" bash "$HERE/verify-defaults.sh"

echo
echo "=== verify the Mini App ==="
run_step "miniapp" bash "$HERE/verify-miniapp.sh"

echo
echo "=== verify the Mini App through the public tunnel ==="
# Separate from the check above, and the reason it exists is worth stating once
# more here: every other probe runs against https://127.0.0.1:8443, which is
# local Caddy and never touches Cloudflare. The suite stayed all green through an
# eleven-hour tunnel outage, because the broken component was not in the path
# being tested. This is the only step that uses the hostname Telegram does.
run_step "tunnel" bash "$HERE/verify-tunnel.sh"

echo
echo "=== verify the rules screen ==="
run_step "rules" bash "$HERE/verify-rules.sh"

echo
echo "=== verify the panel's chrome controls ==="
run_step "buttons" bash "$HERE/verify-buttons.sh"

echo
echo "================================"
if [ "$FAILED" -eq 0 ]; then
    echo "all $STEPS step(s) passed"
    exit 0
fi
echo "$FAILED of $STEPS step(s) FAILED:"
for name in $FAILING; do
    echo "  - $name"
done
echo "================================"
exit 1
