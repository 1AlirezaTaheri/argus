"""Mini App affordances for the Telegram bot.

All of it lives here so ``/start``, ``/app`` and the menu button cannot drift
apart on what "configured" means.

Telegram only loads a Mini App over HTTPS, so :func:`is_configured` rejects a
plain-HTTP URL rather than publishing a button that opens a blank WebView.
"""

from __future__ import annotations

import asyncio
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    WebAppInfo,
)

from bot.tunnel_url import resolve_miniapp_url
from core.miniapp_auth import is_miniapp_enabled, mini_app_url

log = logging.getLogger(__name__)


def resolved_url(settings) -> str:
    """The Mini App base URL, preferring the live tunnel over the setting.

    Read through here rather than from ``settings.miniapp_url`` directly, because
    a quick tunnel's hostname changes on every restart and the setting does not
    follow it.
    """
    return resolve_miniapp_url(getattr(settings, "miniapp_url", "")) or ""


def is_configured(settings) -> bool:
    """Whether Mini App affordances should be shown at all.

    Deliberately tested against the *resolved* URL. A stale setting still begins
    with https://, so testing the setting alone kept returning True and the bot
    kept advertising a button that opened a hostname that no longer resolved.
    """
    return is_miniapp_enabled(resolved_url(settings))


def app_url(settings, start_param: str | None = None) -> str:
    """The Mini App URL, or "" when not configured."""
    base = resolved_url(settings)
    if not is_miniapp_enabled(base):
        return ""
    return mini_app_url(base, start_param)


def web_app_info(settings) -> WebAppInfo | None:
    url = app_url(settings)
    return WebAppInfo(url=url) if url else None


def menu_button(settings, text: str = "Open app") -> MenuButtonWebApp | None:
    info = web_app_info(settings)
    return MenuButtonWebApp(text=text, web_app=info) if info else None


def inline_keyboard(
    settings, text: str = "📱 Open the dashboard"
) -> InlineKeyboardMarkup | None:
    """A one-button keyboard for /start and /app, or None when disabled."""
    info = web_app_info(settings)
    if info is None:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text=text, web_app=info)]]
    )


async def set_menu_button(bot, settings) -> bool:
    """Point the chat menu button at the Mini App, or clear it.

    Returns True when the button is live. Never raises: a failure here must
    not stop the bot from starting, and Telegram tolerates a missing menu
    button perfectly well.
    """
    try:
        button = menu_button(settings)
        await bot.set_chat_menu_button(menu_button=button)
        if button is not None:
            log.info("Mini App menu button set: %s", button.web_app.url)
        else:
            log.info("Mini App not configured; menu button cleared")
        return button is not None
    except Exception:
        log.warning("Could not set the Mini App menu button", exc_info=True)
        return False


async def publish_commands(bot, settings) -> None:
    """Register /app in the Telegram command list, when configured.

    Replaces the whole list rather than appending, because the command set is
    defined in bot/app.py and a partial update would leave /app listed after
    it was disabled.
    """
    try:
        from telegram import BotCommand

        from bot.handlers import start as start_handlers
        from core.profiles import profile_names

        commands = [
            BotCommand("start", "Open the dashboard / شروع"),
            BotCommand("help", "What this bot can do"),
        ]
        if is_configured(settings):
            commands.append(BotCommand("app", "Open the dashboard"))
        commands.extend(
            [
                BotCommand("scan", "Scan a target"),
                BotCommand("addtarget", "Register a target"),
                BotCommand("targets", "List targets"),
                BotCommand("status", "Current activity"),
                BotCommand("tlang", "Change language"),
            ]
        )
        await bot.set_my_commands(commands)
        del profile_names, start_handlers
    except Exception:
        log.warning("Could not publish the command list", exc_info=True)


class MiniAppUrlWatcher:
    """Re-publish the chat menu button whenever the tunnel hostname changes.

    A quick tunnel's hostname changes every time Cloudflare reaps it. The bot
    reads the published file rather than the stale setting, so the URL it would
    send is always current -- but the menu button is a value Telegram holds, not
    a value the bot re-reads, and it was set exactly once at startup. After the
    first reap the button opens a name that no longer resolves, and nothing
    says so, because the bot did send a valid button.

    Watching the resolved URL and re-sending on change is what makes the button
    survive a reap without restarting the bot. The check is one file read, so
    the cost of running it every half second would be nothing; it is spaced to
    match how fast a reap can plausibly matter to someone with the app open.
    """

    def __init__(self, bot, settings, *, interval: float = 30.0) -> None:
        self._bot = bot
        self._settings = settings
        self._interval = interval
        self._task: asyncio.Task[None] | None = None
        self._last_url: str | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="argus-miniapp-watcher")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        # Seed with the current URL so the initial state is not read as a
        # change. Without this the first tick would see "None -> https://..."
        # and re-publish a button that was just published in _post_init.
        self._last_url = resolved_url(self._settings)
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._check()
            except Exception:
                # A watcher that raises would take the event loop down with it.
                # The menu button is a convenience; it must never be the reason
                # the bot stops answering.
                log.warning("miniapp url watcher: check failed", exc_info=True)

    async def _check(self) -> None:
        current = resolved_url(self._settings)
        if current == self._last_url:
            return
        log.info(
            "miniapp url watcher: tunnel hostname changed %s -> %s; "
            "re-publishing the menu button",
            self._last_url,
            current,
        )
        self._last_url = current
        # Reuses the startup path rather than calling set_chat_menu_button
        # directly, so the two can never disagree about what a button is.
        await set_menu_button(self._bot, self._settings)
