"""Resolve the Mini App's public URL, preferring the live one.

`MINIAPP_URL` is a static setting, and a Cloudflare *quick* tunnel's hostname
changes every time the tunnel restarts. The two drift apart, and when they do the
bot's WebApp button points at a name that no longer resolves: `/app` in Telegram
opens nothing, and no error is raised anywhere, because from the bot's point of
view it sent a perfectly valid button.

The tunnel writes the hostname it was actually given to a shared file, and that
file is the source of truth here. `MINIAPP_URL` remains the fallback, so a
deployment with no tunnel, or one that predates the file, behaves exactly as it
did before.
"""

from __future__ import annotations

import logging
import os
import pathlib

log = logging.getLogger(__name__)

# Where the tunnel writes it. A path rather than an env var inside the bot
# because both containers mount the same volume at the same place.
DEFAULT_URL_FILE = "/tunnel/tunnel-url.conf"

# A quick-tunnel hostname, or a named-tunnel hostname. Anything else is treated
# as a bad write and ignored: this value is handed to Telegram and ends up in a
# user's client, so it is validated rather than trusted.
_ALLOWED_SUFFIXES = (".trycloudflare.com", ".cfargotunnel.com")


def read_tunnel_url(path: str | None = None) -> str | None:
    """The hostname the tunnel published, or None if there is not a usable one."""
    target = pathlib.Path(path or os.environ.get("TUNNEL_URL_FILE")
                          or DEFAULT_URL_FILE)
    try:
        raw = target.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.debug("could not read the tunnel url file: %s", exc)
        return None

    if not raw:
        return None
    if not raw.startswith("https://"):
        log.warning("tunnel url file does not hold an https URL; ignoring it")
        return None
    host = raw[len("https://"):].split("/")[0]
    if not host.endswith(_ALLOWED_SUFFIXES):
        # Refuse rather than forward: this string becomes a button URL in a
        # Telegram client, so an unexpected value is a reason to stop.
        log.warning("tunnel url file holds an unexpected host; ignoring it")
        return None
    return f"https://{host}"


def resolve_miniapp_url(configured: str | None,
                        path: str | None = None) -> str | None:
    """The Mini App URL to send, preferring the live tunnel over the setting.

    `configured` is `settings.miniapp_url`. A value read from the file wins,
    because it is written by the process currently serving traffic and so cannot
    be stale. The setting is the fallback.
    """
    live = read_tunnel_url(path)
    if live:
        if configured and configured.rstrip("/") != live.rstrip("/"):
            log.info(
                "Mini App URL from the live tunnel (%s) supersedes the "
                "configured %s", live, configured,
            )
        return live
    if configured:
        return configured.rstrip("/")
    return None
