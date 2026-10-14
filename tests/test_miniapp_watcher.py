#!/usr/bin/env python3
"""The Mini App menu button must survive a tunnel reap.

## What is being tested

A quick tunnel is reaped every half hour and each reap mints a new hostname. The
bot reads the published file, so the URL it *would* send is always current -- but
the menu button is a value Telegram holds, and it was set exactly once at
startup. After the first reap the button opens a name that no longer resolves,
and nothing logs it, because the bot did send a valid button.

`MiniAppUrlWatcher` re-publishes the button when the resolved URL changes. These
tests drive it directly rather than waiting on a reap, because a reap cannot be
scheduled and the behaviour is a pure function of "did the URL change".

## The cases that matter

- **Unchanged URL does nothing.** The common case, every 30 seconds, forever. A
  watcher that re-published on every tick would spam Telegram's API for nothing.
- **A change re-publishes.** The whole point.
- **The seed is not a change.** The watcher starts after `_post_init` has already
  set the button; if it treated that initial state as a change it would
  re-publish immediately and on every start.
- **A URL becoming empty is a change.** A tunnel that has gone down must clear
  the button rather than leave the last good name in the client.
- **`stop` actually stops.** A watcher left running after shutdown is a task
  that outlives the application.

`resolved_url` and `set_menu_button` are monkeypatched in the `bot.miniapp`
module, because the watcher calls them as module-level names. Patching the
module is what makes the test about the watcher's logic rather than about the
file on disk or Telegram's API.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bot import miniapp as miniapp_mod  # noqa: E402
from bot.miniapp import MiniAppUrlWatcher  # noqa: E402


class _Settings:
    """The only attribute the watcher reads."""

    def __init__(self, url: str) -> None:
        self.miniapp_url = url


async def _drive(watcher: MiniAppUrlWatcher, ticks: int = 1) -> None:
    """Run the watcher's check the given number of times, without sleeping.

    The real loop sleeps `interval` seconds between checks; a test that waited on
    the real interval would take 30 seconds per case. `_check` is the part with
    behaviour, so the tests call it directly and let `start`/`stop` be tested
    separately for the sleeping and the cancellation.
    """
    for _ in range(ticks):
        await watcher._check()


@pytest.mark.asyncio
async def test_an_unchanged_url_does_nothing() -> None:
    """The common case: every 30 seconds, forever, with no change.

    Re-publishing on every tick would hammer Telegram's API for nothing, and the
    menu button is the kind of thing that draws rate-limit attention.
    """
    bot = mock.Mock()
    with mock.patch.object(miniapp_mod, "resolved_url", return_value="https://a.trycloudflare.com"), \
         mock.patch.object(miniapp_mod, "set_menu_button", new=mock.AsyncMock()) as publish:
        watcher = MiniAppUrlWatcher(bot, _Settings("https://a.trycloudflare.com"))
        watcher._last_url = "https://a.trycloudflare.com"
        await _drive(watcher, ticks=5)
        publish.assert_not_called()


@pytest.mark.asyncio
async def test_a_changed_url_re_publishes_the_button() -> None:
    """The whole point of the watcher.

    This is what a reap looks like from the bot's side: the published file now
    holds a different hostname, and the menu button must follow it.
    """
    bot = mock.Mock()
    with mock.patch.object(miniapp_mod, "resolved_url", return_value="https://b.trycloudflare.com"), \
         mock.patch.object(miniapp_mod, "set_menu_button", new=mock.AsyncMock()) as publish:
        watcher = MiniAppUrlWatcher(bot, _Settings("https://a.trycloudflare.com"))
        watcher._last_url = "https://a.trycloudflare.com"
        await _drive(watcher)
        publish.assert_awaited_once_with(bot, watcher._settings)
        # The tracked URL advances, so a second identical tick is a no-op rather
        # than a second publish.
        await _drive(watcher)
        publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_seed_is_not_treated_as_a_change() -> None:
    """The watcher starts after _post_init has already set the button.

    If the seed were not recorded, the first tick would see
    "None -> https://..." and re-publish a button that was set moments earlier --
    on every start, forever, and the log line would be wrong about why.
    """
    bot = mock.Mock()
    with mock.patch.object(miniapp_mod, "resolved_url", return_value="https://a.trycloudflare.com"), \
         mock.patch.object(miniapp_mod, "set_menu_button", new=mock.AsyncMock()) as publish:
        watcher = MiniAppUrlWatcher(bot, _Settings("https://a.trycloudflare.com"))
        # Drive only the seed, by running the loop's first half.
        watcher._last_url = miniapp_mod.resolved_url(watcher._settings)
        await _drive(watcher, ticks=3)
        publish.assert_not_called()
        assert watcher._last_url == "https://a.trycloudflare.com"


@pytest.mark.asyncio
async def test_a_url_that_becomes_empty_is_a_change() -> None:
    """A tunnel that has gone down must clear the button.

    Leaving the last good hostname in place would be worse than clearing it: the
    button would still be there, still labelled "Open app", and still broken.
    """
    bot = mock.Mock()
    with mock.patch.object(miniapp_mod, "resolved_url", return_value=""), \
         mock.patch.object(miniapp_mod, "set_menu_button", new=mock.AsyncMock()) as publish:
        watcher = MiniAppUrlWatcher(bot, _Settings("https://a.trycloudflare.com"))
        watcher._last_url = "https://a.trycloudflare.com"
        await _drive(watcher)
        publish.assert_awaited_once()
        assert watcher._last_url == ""


@pytest.mark.asyncio
async def test_a_failing_check_does_not_kill_the_loop() -> None:
    """The menu button must never be the reason the bot stops answering.

    The broad except lives in `_run`, not `_check`, so this drives `_run` and
    confirms a raising `_check` is swallowed rather than propagated. If it were
    propagated the task would die, the button would go stale, and the failure
    would be silent -- the same shape of bug the heartbeat was written to avoid.
    """
    bot = mock.Mock()

    async def raising_check(_self):  # noqa: ANN001, ANN202
        raise RuntimeError("file vanished mid-read")

    with mock.patch.object(miniapp_mod, "resolved_url", return_value="https://a.trycloudflare.com"), \
         mock.patch.object(MiniAppUrlWatcher, "_check", new=raising_check):
        # A short interval: the real one is 30s, and waiting on that to prove a
        # point about exception handling would make the test take half a minute.
        watcher = MiniAppUrlWatcher(bot, _Settings("https://a.trycloudflare.com"), interval=0.01)
        watcher.start()
        try:
            # Let at least one tick happen. The interval is 30s, so rather than
            # wait on it, confirm the task survives past the sleep by giving it
            # time to attempt a check and fail.
            for _ in range(50):
                await asyncio.sleep(0.05)
                if watcher._task is None or watcher._task.done():
                    pytest.fail("the watcher task died on a failing check")
        finally:
            await watcher.stop()


@pytest.mark.asyncio
async def test_stop_cancels_the_running_task() -> None:
    """A watcher left running after shutdown outlives the application.

    `stop` cancels the task and awaits it, so by the time `_post_shutdown`
    returns there is nothing left beating.
    """
    bot = mock.Mock()
    with mock.patch.object(miniapp_mod, "resolved_url", return_value="https://a.trycloudflare.com"):
        watcher = MiniAppUrlWatcher(bot, _Settings("https://a.trycloudflare.com"))
        watcher.start()
        assert watcher._task is not None and not watcher._task.done()
        await watcher.stop()
        assert watcher._task is None


def test_the_watcher_is_wired_into_the_application() -> None:
    """A class that exists but is never started fixes nothing.

    The start must come after the initial `set_menu_button`, because the watcher
    seeds itself from the current URL; started before it, the seed would be None
    and the first tick would re-publish.
    """
    src = (pathlib.Path(__file__).resolve().parents[1] / "bot" / "app.py").read_text(
        encoding="utf-8"
    )
    assert "miniapp_ui.MiniAppUrlWatcher(app.bot, settings)" in src, (
        "the application does not construct the watcher"
    )
    assert "miniapp_watcher.start()" in src, "the application does not start the watcher"
    assert 'app.bot_data.get("miniapp_watcher")' in src, (
        "shutdown does not look the watcher up"
    )
    assert "await watcher.stop()" in src, "shutdown does not stop the watcher"

    i_set = src.find("await miniapp_ui.set_menu_button")
    i_start = src.find("miniapp_watcher.start()")
    assert 0 < i_set < i_start, (
        "the watcher must start after the initial button is set, or its seed "
        "reads None and re-publishes on the first tick"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
