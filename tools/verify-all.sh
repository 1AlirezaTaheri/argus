#!/bin/bash
# Phase 1: build, deploy, then verify -- refusing to verify a stale image.
#
# The previous run built, the build FAILED on a tsc error, and the verify
# script then reported against the previously deployed bundle anyway. Its
# output looked plausible and its failures were about the old code, which is
# worse than no output. So the build's exit status is checked here, before the
# verifier is allowed to run at all.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/.."

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
tsc_out="$(cd admin/frontend && bash "$DIR/tsc.sh" 2>&1)"
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
TAIL_LINES=30 run_step "verify-ui" bash "$DIR/verify-ui.sh"

echo
echo "=== verify the live dashboard ==="
TAIL_LINES=40 run_step "verify-dashboard" bash "$DIR/verify-dashboard.sh"

echo
echo "=== verify the target detail API ==="
TAIL_LINES=45 run_step "verify-target-detail-api" bash "$DIR/verify-target-detail-api.sh"

echo
echo "=== verify the target detail UI in the bundle ==="
TAIL_LINES=30 run_step "verify-target-detail" bash "$DIR/verify-target-detail.sh"

echo
echo "=== verify panel scanning ==="
TAIL_LINES=40 run_step "verify-panel-scan" bash "$DIR/verify-panel-scan.sh"

echo
echo "=== verify mobile navigation ==="
TAIL_LINES=25 run_step "verify-mobile-nav" bash "$DIR/verify-mobile-nav.sh"

echo
echo "=== verify every route actually renders in a browser ==="
TAIL_LINES=20 run_step "verify-render" bash "$DIR/verify-render.sh"

echo
echo "=== verify the theme is applied before first paint ==="
TAIL_LINES=16 run_step "verify-theme" bash "$DIR/verify-theme.sh"

echo
echo "=== verify the dropdowns work ==="
TAIL_LINES=22 run_step "verify-selects" bash "$DIR/verify-selects.sh"

echo
echo "=== verify the Mini App ==="
run_step "miniapp" bash "$DIR/verify-miniapp.sh"

echo
echo "=== verify the rules screen ==="
run_step "rules" bash "$DIR/verify-rules.sh"

echo
echo "=== verify the panel's chrome controls ==="
run_step "buttons" bash "$DIR/verify-buttons.sh"

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
