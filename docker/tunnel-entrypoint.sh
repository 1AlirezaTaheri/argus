#!/bin/sh
# Run the quick tunnel, publish the hostname it was assigned, restart when the
# published URL stops answering, and give up cleanly when Cloudflare has reaped
# the tunnel.
#
# ## Why this wrapper exists
#
# `MINIAPP_URL` is static and a Cloudflare *quick* tunnel is not: it mints a
# random hostname every time the container starts. The two drift apart and the
# bot's Web App button ends up pointing at a name that no longer resolves, so
# `/app` in Telegram opens nothing and nothing raises anywhere -- the bot sent a
# perfectly valid button to a host that is gone.
#
# That has happened on this deployment many times. The first was repaired by
# editing .env. The second left a resolver that preferred a file nobody wrote.
# The third went unnoticed for eleven hours because the health check ran
# `cloudflared tunnel --help`, which exits 0 whenever the binary exists. Each
# time the same manual step stood between a restarted tunnel and a working Mini
# App.
#
# ## How the hostname is published
#
# cloudflared announces the assigned URL on stderr, and there is no API to ask
# for it -- a quick tunnel's name exists only in that output. So the output is
# filtered and the URL written to a shared volume the bot mounts read-only.
# `bot/tunnel_url.py` prefers that file over `MINIAPP_URL`. It cannot be stale,
# because it is written by the process currently serving the hostname.
#
# ## The three ways this reports its state
#
# The health check greps for `state=registered`, so anything else is unhealthy.
# Which of the three it is says what to do about it:
#
#   starting   the tunnel has not announced a hostname yet.
#   registered a hostname is published and connections are registering.
#   degraded   Cloudflare is refusing registration, but has not named the
#              reason. Usually the start of a reap; sometimes a Cloudflare-side
#              outage. Worth waiting through, worth watching.
#   stale      the tunnel reports itself connected, but the URL it published
#              does not answer. This is the state the watchdog below exists to
#              produce, and it is the one the previous version could not reach.
#   reaped     `Tunnel not found`. This tunnel name will never come back, so
#              the wrapper exits and Docker starts a new one.
#   exited     cloudflared itself has stopped. The wrapper exits, and Docker's
#              restart policy starts a new one.
#   no-binary  the image is missing cloudflared. Nothing can follow from here.
#
# ## Why the watchdog exists, and why it probes rather than watches the log
#
# The previous version deliberately had no way to notice a tunnel that had gone
# quiet, and said so in a comment: detecting it needed a timer the read loop
# could not provide under busybox ash. That reasoning was half right, and the
# half that was wrong cost the Mini App several hours at a time.
#
# A quiet tunnel is not detectable from cloudflared's output. A healthy quick
# tunnel logs one registration and then says nothing: observed here, a tunnel
# registered at 13:19:56 and logged nothing further until it *lost* the edge at
# 14:58:47. So a "time since last log line" watchdog would fire on a perfectly
# working tunnel. Any interval short enough to catch a real outage is an
# outage factory.
#
# What is detectable is the thing a user would notice: the published URL stops
# answering. That is a property of the whole path -- hostname, edge, connector,
# origin -- and it is exactly the failure the Mini App user hits. So the
# watchdog makes the same request a browser would, from outside, and restarts
# the tunnel when that request fails repeatedly.
#
# Why a quiet reconnect is not enough: when Cloudflare discards a quick tunnel,
# cloudflared's connector often reconnects happily, because it is still talking
# to the edge. The *name* is gone. Logs from that event show
# `Lost connection with the edge`, a retry, and a fresh `Registered tunnel
# connection` sixteen seconds later -- a container that looks entirely healthy
# and answers 530 at its own hostname. No amount of watching cloudflared would
# have noticed, because from cloudflared's side nothing was wrong.
set -eu

URL_FILE="${TUNNEL_URL_FILE:-/tunnel/tunnel-url.conf}"
STATUS_FILE="${TUNNEL_STATUS_FILE:-/tunnel/tunnel-status.conf}"

# --- watchdog configuration ------------------------------------------------
# Every value is an environment variable with a default, so a deployment can
# loosen or disable the probe without editing this file. Defaults are chosen so a
# healthy tunnel is never restarted: three consecutive failures, roughly four and
# a half minutes apart.
PROBE_ENABLED="${TUNNEL_PROBE_ENABLED:-1}"
PROBE_PATH="${TUNNEL_PROBE_PATH:-/api/health}"
PROBE_INTERVAL="${TUNNEL_PROBE_INTERVAL:-90}"
PROBE_TIMEOUT="${TUNNEL_PROBE_TIMEOUT:-20}"
PROBE_FAILURES="${TUNNEL_PROBE_FAILURES:-3}"

mkdir -p "$(dirname "$URL_FILE")" "$(dirname "$STATUS_FILE")"

FIFO="${TUNNEL_FIFO:-/tmp/tunnel-output}"
rm -f "$FIFO"
mkfifo "$FIFO"

now() { date +%s; }

write_status() {
    printf '%s\n' "$1" > "$STATUS_FILE".tmp && mv "$STATUS_FILE".tmp "$STATUS_FILE"
}
log() { printf '%s tunnel: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1"; }

# "starting", not a leftover success from a previous run of this container: a
# health check that could read a stale `registered` would miss exactly the
# outage it exists to catch.
write_status "state=starting url=- age=0"

# A missing binary must not become a hang. `> "$FIFO"` is evaluated before the
# command is looked up, so an absent cloudflared would block the child in
# open() and the parent in its own open(), both forever.
command -v cloudflared >/dev/null 2>&1 || {
    write_status "state=no-binary url=- age=-"
    log "cloudflared is not installed in this image"
    rm -f "$FIFO"
    exit 76
}

# The write end is held by cloudflared alone, the read end is opened here, and
# neither blocks for long: the child waits for a reader and this is the reader.
#
# Both earlier directions are recorded because each failed in a way that looked
# healthy. `exec 3>"$FIFO"` (write-only, opened before any reader existed)
# deadlocked before cloudflared started -- empty log, no process, status stuck
# at "starting". `exec 3<>"$FIFO"` fixed that and hid the next problem: holding
# a write end means the loop never sees EOF, so a cloudflared that died on its
# own would leave a running container serving nothing.
#
# Reading only, so cloudflared's exit closes the pipe and the loop falls through
# to the restart path.
cloudflared "$@">"$FIFO" 2>&1 &
TUNNEL_PID=$!
exec 3<"$FIFO"

forward() { kill -TERM "$TUNNEL_PID" 2>/dev/null || true; }

WATCHDOG_PID=""
stop_watchdog() {
    [ -n "$WATCHDOG_PID" ] || return 0
    kill -TERM "$WATCHDOG_PID" 2>/dev/null || true
    WATCHDOG_PID=""
}
trap 'stop_watchdog; forward; wait "$TUNNEL_PID" 2>/dev/null || true; rm -f "$FIFO"; exit 0' \
    TERM INT

# ---------------------------------------------------------------------------
# The end-to-end watchdog
# ---------------------------------------------------------------------------
#
# Runs as a subshell so it cannot disturb the read loop below. It sleeps first:
# before a hostname is published there is nothing to probe, and starting
# immediately would only produce noise about a missing file.
#
# The probe goes to the *published* hostname, from outside, through the edge --
# not to localhost. Localhost would be up while the tunnel is dead, which is
# precisely the blind spot this closes.
start_watchdog() {
    [ "$PROBE_ENABLED" = "1" ] || {
        log "the end-to-end probe is disabled (TUNNEL_PROBE_ENABLED=$PROBE_ENABLED)"
        return 0
    }
    command -v curl >/dev/null 2>&1 || {
        log "no curl in this image; the end-to-end probe cannot run"
        return 0
    }

    (
        fails=0
        while :; do
            sleep "$PROBE_INTERVAL"
            url=$(cat "$URL_FILE" 2>/dev/null || true)
            case "$url" in
                http://*|https://*) ;;
                *) continue ;;
            esac

            code=$(curl -s -o /dev/null -w '%{http_code}' \
                --max-time "$PROBE_TIMEOUT" "${url}${PROBE_PATH}" 2>/dev/null || printf '000')

            case "$code" in
                2*|3*)
                    if [ "$fails" -ne 0 ]; then
                        log "the published URL answers again ($code); resetting the failure count"
                        # Back to registered, and only then: a tunnel that was
                        # failing and now answers is healthy again, and saying so
                        # is the whole point of recording state here.
                        [ -n "$HOST" ] && set_state registered
                    fi
                    fails=0
                    ;;
                *)
                    fails=$((fails + 1))
                    log "probe $code from ${url}${PROBE_PATH} (${fails}/${PROBE_FAILURES})"
                    # The state is dropped on the FIRST failure, not when the
                    # threshold is reached. The first version wrote `stale` only
                    # at the moment it decided to restart, so for the whole
                    # window between the first failed probe and the restart the
                    # file still said `registered` -- and the health check, which
                    # greps for exactly that word, reported a tunnel whose URL
                    # was returning 404 as healthy. Marking it on the first
                    # failure is what makes the failure window visible.
                    if [ "$fails" -eq 1 ]; then
                        set_state degraded
                        log "the published URL is not answering; reporting degraded until it does or the restart fires"
                    fi
                    if [ "$fails" -ge "$PROBE_FAILURES" ]; then
                        log "the published URL has not answered ${PROBE_FAILURES} times; restarting for a fresh hostname"
                        write_status "state=stale url=$url age=-"
                        kill -TERM "$TUNNEL_PID" 2>/dev/null || true
                        exit 0
                    fi
                    ;;
            esac
        done
    ) &
    WATCHDOG_PID=$!
    log "watching ${PROBE_PATH} every ${PROBE_INTERVAL}s; restarting after ${PROBE_FAILURES} failures"
}

HOST=""
LAST_OK=0
LAST_STATE=""

# Record where we are, and log only on a change.
#
# Written every time rather than once, because the health check greps this file
# and a state recorded at startup is a statement about the past. Every failure
# mode this wrapper exists to catch is one where the process kept running
# without the thing it is supposed to be doing working.
set_state() {
    LAST_STATE="$1"
    if [ -n "$HOST" ]; then
        write_status "state=$1 url=$HOST age=$(( $(now) - LAST_OK ))"
    else
        write_status "state=$1 url=- age=-"
    fi
}

# The assigned URL appears on the line *after*
#
#     INF |  Your quick Tunnel has been created! Visit it at ...:  |
#     INF |  https://some-slug.trycloudflare.com                     |
#
# Matching "any https://*.trycloudflare.com in the output" is wrong, and was:
# cloudflared also logs documentation links, and the first version of this
# wrapper published
#
#     https://api.trycloudflare.com
#
# as the tunnel's address -- so the bot's Web App button pointed at Cloudflare's
# own API host, which answers 405 to everything. Keying off the preceding
# banner line identifies it by what it means rather than by how it is
# decorated, so a change to the box-drawing or the surrounding prose cannot
# silently break extraction the way a looser match already did once.
previous=""

while IFS= read -r line; do
    printf '%s\n' "$line"

    # --- a reap is terminal ---------------------------------------------
    # Checked first: it names the reason, and nothing else on the line matters
    # once it has appeared.
    case "$line" in
        *"Tunnel not found"*)
            write_status "state=reaped url=${HOST:--} age=-"
            log "reaped by Cloudflare; exiting so a new hostname is assigned"
            stop_watchdog
            forward
            exit 75
            ;;
    esac

    # --- the banner announces the hostname --------------------------------
    case "$previous" in
        *"quick Tunnel has been created"*)
            previous="$line"
            candidate=$(printf '%s' "$line" \
                | grep -oE 'https://[a-z0-9-]+\.(trycloudflare\.com|cfargotunnel\.com)' \
                | head -1 || true)
            # Cloudflare's own hosts share the suffix, so a stray documentation
            # or API link is rejected outright.
            case "$candidate" in
                https://api.*|https://developers.*|https://update.*) candidate="" ;;
            esac
            if [ -n "$candidate" ]; then
                HOST="$candidate"
                printf '%s\n' "$HOST" > "$URL_FILE".tmp
                mv "$URL_FILE".tmp "$URL_FILE"
                LAST_OK=$(now)
                set_state registered
                log "published $HOST"
                # Started once, and only once a URL exists to probe.
                [ -n "$WATCHDOG_PID" ] || start_watchdog
            fi
            continue
            ;;
    esac
    previous="$line"

    # --- a connection registered is the heartbeat --------------------------
    # This is what keeps `registered` a statement about the present. Without it
    # the state recorded at the banner would outlive the registration it
    # described, which is the exact defect this whole file was rewritten to
    # remove.
    case "$line" in
        *"Connection registered"*|*"Registered tunnel connection"*)
            LAST_OK=$(now)
            set_state registered
            continue
            ;;
    esac

    # --- registration refused, for some reason other than a reap -----------
    # Cloudflare answers this way for more than one reason, and only one of
    # them is permanent. Degrading rather than exiting keeps a transient
    # Cloudflare-side problem from causing a restart loop, while making the
    # container honestly unhealthy so it is not reported as working.
    case "$line" in
        *"Register tunnel error"*|*"failed to register"*|*"connection refused"*)
            if [ "$LAST_STATE" != "degraded" ]; then
                set_state degraded
                log "registration refused; waiting rather than restarting"
            fi
            continue
            ;;
    esac
done <&3

# cloudflared exited on its own.
stop_watchdog
wait "$TUNNEL_PID" 2>/dev/null || true
write_status "state=exited url=${HOST:--} age=-"
log "cloudflared exited"
rm -f "$FIFO"
exit 0