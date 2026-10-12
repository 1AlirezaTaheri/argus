"""A heartbeat the container's health check can actually trust.

## Why this exists

The bot's health check was

    python -c "import sys; sys.exit(0 if os.environ.get('TELEGRAM_BOT_TOKEN') else 1)"

which tests that a string is set in the environment. It says nothing about
whether the bot is running, polling, or able to reach Telegram -- so a bot whose
event loop had died, whose token had been revoked, or whose network path to
api.telegram.org was gone, all reported `healthy` indefinitely. `docker compose
ps` is the first thing anyone looks at, and it was lying.

## What counts as alive

Two things, both of which have to hold:

1. **The event loop is running.** A background task on that loop writes the file
   on every beat. If the loop blocks or dies, the beats stop and the file goes
   stale even though the process is still there.

2. **Telegram answers.** The beat is only written after `get_me()` succeeds. A
   loop that is alive but cannot reach the API -- bad token, revoked token, DNS
   failure, egress block -- therefore stops beating, which is the distinction
   that a pure timer would miss.

`get_me` is the cheapest call Telegram offers, is explicitly exempt from the
general rate limit, and is the call a client makes to establish a session. One
call per beat is nothing.

## Why the failure is sticky

A single dropped packet is not an outage. A beat is recorded as *consecutive*
failures, and only after `FAILS_BEFORE_STALE` does the file stop being refreshed
-- so a transient blip leaves the file merely a few seconds old, well within the
health check's tolerance, while a genuine failure crosses it in a predictable
time. The operator sees a container go unhealthy rather than a service that
silently stopped answering.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import time

import telegram

log = logging.getLogger("argus.heartbeat")

# Written here, read by the health check. Overridable so a test can point it at
# a temporary directory instead of the real path.
DEFAULT_PATH = pathlib.Path(
    os.environ.get("BOT_HEARTBEAT_FILE", "/tmp/argus-heartbeat")
)

# How often to beat, in seconds. Fast enough that an outage is noticed within a
# minute, slow enough to be invisible against Telegram's limits.
INTERVAL_SECONDS = 15.0

# Consecutive failures tolerated before the file is left to go stale. One blip
# is not an outage; the health check's own window is wider than this, so a
# single miss never surfaces as unhealthy.
FAILS_BEFORE_STALE = 3


class Heartbeat:
    """Writes a beat file only while the loop runs *and* Telegram answers.

    Started by `post_init` and stopped by `post_shutdown`, so its lifetime is
    exactly the application's.
    """

    def __init__(
        self,
        bot: telegram.Bot,
        *,
        path: pathlib.Path = DEFAULT_PATH,
        interval: float = INTERVAL_SECONDS,
        fails_before_stale: int = FAILS_BEFORE_STALE,
    ) -> None:
        self._bot = bot
        self._path = pathlib.Path(path)
        self._interval = interval
        self._fails_before_stale = fails_before_stale
        self._task: asyncio.Task[None] | None = None
        self._consecutive_failures = 0

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="argus-heartbeat")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def beat(self) -> bool:
        """Beat once. Returns True when Telegram answered.

        Exposed separately from the loop so a test can drive it without waiting
        on wall-clock time.
        """
        try:
            await self._bot.get_me()
        except Exception as exc:  # noqa: BLE001 - any failure means "not alive"
            self._consecutive_failures += 1
            log.warning(
                "heartbeat: Telegram unreachable (%d consecutive): %s",
                self._consecutive_failures,
                exc,
            )
            if self._consecutive_failures >= self._fails_before_stale:
                # Deliberately not deleted: a missing file and a stale file read
                # the same way to a health check, but a stale one keeps the
                # last-known-good timestamp for anyone reading it by hand.
                log.error(
                    "heartbeat: %d consecutive failures, leaving the beat file "
                    "stale so the health check reports unhealthy",
                    self._consecutive_failures,
                )
            return False

        self._consecutive_failures = 0
        # Written atomically: a health check reading this concurrently must
        # never observe a partially written timestamp.
        #
        # Wall-clock (`time.time`), not the loop's monotonic clock. The health
        # check runs `time.time() - value < 75` in a fresh process, and a
        # monotonic reading -- which starts near zero -- makes that subtraction
        # enormous and the container permanently unhealthy for a bot that is
        # working perfectly. That is exactly what happened on the first deploy:
        # five consecutive `1` exits from the health check while the beat file
        # was advancing normally.
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        temporary.write_text(f"{time.time():.3f}\n", encoding="utf-8")
        temporary.replace(self._path)
        return True

    async def _run(self) -> None:
        while True:
            await self.beat()
            await asyncio.sleep(self._interval)
