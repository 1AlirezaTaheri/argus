#!/bin/sh
# Run the quick tunnel, publish the hostname it was assigned, and give up
# cleanly when Cloudflare has reaped the tunnel.
#
# ## Why this wrapper exists
#
# `MINIAPP_URL` is static and a Cloudflare *quick* tunnel is not: it mints a
# random hostname every time the container starts. The two drift apart and the
# bot's Web App button ends up pointing at a name that no longer resolves, so
# `/app` in Telegram opens nothing and nothing raises anywhere -- the bot sent a
# perfectly valid button to a host that is gone.
#
# That has happened on this deployment four times. The first was repaired by
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
#   reaped     `Tunnel not found`. This tunnel name will never come back, so
#              the wrapper exits and Docker starts a new one.
#   exited     cloudflared itself has stopped. The wrapper exits, and Docker's
#              restart policy starts a new one.
#   no-binary  the image is missing cloudflared. Nothing can follow from here.
#
# The distinction between `degraded` and `registered` is the part that took the
# longest to get right. The first version wrote `state=registered` when it saw
# the banner and never reconsidered -- so a tunnel that was subsequently refused
# registration kept reporting healthy forever, which is the same defect as the
# `tunnel --help` probe it replaced, one layer further in. A health check that
# records the past rather than the present is a decoration.
#
# Deliberately absent: a `stale` state for a tunnel that has gone quiet without
# an error. Detecting it needs the read loop to wake on a timer, which needs the
# non-POSIX `read -t` in a script that must run under busybox ash -- and a false
# unhealthy on a working tunnel is worse than a missed one, because nothing
# restarts on unhealthy here and the alert would be noise. Every failure that
# does have a signal is handled: a reaped tunnel names itself, a refused
# registration names itself, and a cloudflared that has died closes the pipe and
# ends the loop, which is caught below and restarted.
#
# ## Why a FIFO rather than a pipe
#
# The first version ran `cloudflared "$@" 2>&1 &` and read its loop from the
# script's own stdin. cloudflared's output went to the script's stdout -- which
# is why the container log looked perfectly normal -- while the loop read
# nothing and never wrote the file. The tunnel was up and registered; the health
# check correctly said `state=starting` and `unhealthy`, which is how the bug
# was caught.
#
# A pipeline cannot be used directly because `${PIPESTATUS}` is a bashism and
# this is `/bin/sh`.
#
# ## Why it exits rather than retrying forever
#
# When Cloudflare has reaped a quick tunnel it answers every registration with
#
#     ERR Register tunnel error ... Unauthorized: Tunnel not found
#
# and that name never comes back. cloudflared retries forever, which is how the
# container sat "healthy" for hours serving an error. Retrying a reaped tunnel
# is pointless, so this exits non-zero and lets Docker start a fresh one, which
# gets a new name, which gets published.
set -eu

URL_FILE="${TUNNEL_URL_FILE:-/tunnel/tunnel-url.conf}"
STATUS_FILE="${TUNNEL_STATUS_FILE:-/tunnel/tunnel-status.conf}"

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
trap 'forward; wait "$TUNNEL_PID" 2>/dev/null || true; rm -f "$FIFO"; exit 0' \
    TERM INT

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
            if [ "$LAST_STATE" != "registered" ]; then
                set_state registered
                log "connection registered${HOST:+ for $HOST}"
            else
                # Same state; refresh the age so an operator reading the file
                # can see how long ago the last registration was.
                set_state registered
            fi
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
wait "$TUNNEL_PID" 2>/dev/null || true
write_status "state=exited url=${HOST:--} age=-"
log "cloudflared exited"
rm -f "$FIFO"
exit 0
