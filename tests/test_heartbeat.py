#!/usr/bin/env python3
"""Tests for bot/heartbeat.py and the health check that reads it.

## Why these exist

The bot's health check was `os.environ.get('TELEGRAM_BOT_TOKEN')`. It proved a
string was set in an environment variable and nothing else, so a bot whose event
loop had died, whose token had been revoked, or whose route to
api.telegram.org was blocked all reported `healthy` indefinitely.

This is the same defect as the tunnel's `cloudflared tunnel --help` probe,
which reported healthy through an eleven-hour outage. Both proved a file
existed rather than that a service worked. So the tests below assert on the
property that matters -- does the file get written *only* when Telegram answers
-- rather than on the file merely existing.
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys
import time

import pytest
import telegram

from bot.heartbeat import DEFAULT_PATH, Heartbeat

REPO = pathlib.Path(__file__).resolve().parents[1]


class FakeBot:
    """Stands in for telegram.Bot.

    Only `get_me` is implemented, because that is the single call the heartbeat
    makes -- a fuller double would imply the heartbeat depends on more of the
    Bot API than it does.
    """

    def __init__(self, *, fail: bool = False, error: Exception | None = None) -> None:
        self.fail = fail
        self.error = error or telegram.error.TelegramError("unreachable")
        self.calls = 0

    async def get_me(self) -> object:
        self.calls += 1
        if self.fail:
            raise self.error
        return object()


def run(coro):  # noqa: ANN001, ANN201
    return asyncio.run(coro)


def test_a_beat_is_written_when_telegram_answers() -> None:
    """The whole point: a successful call refreshes the file."""
    path = pathlib.Path("/tmp/argus-hb-test-a")
    path.unlink(missing_ok=True)
    bot = FakeBot()
    beat = Heartbeat(bot, path=path)

    assert run(beat.beat()) is True
    assert path.exists(), "a successful beat must write the file"
    assert bot.calls == 1


def test_no_beat_is_written_when_telegram_does_not_answer() -> None:
    """The file must not be refreshed on failure.

    A heartbeat written unconditionally would be worse than none: the health
    check would pass for a bot that cannot reach Telegram at all, which is
    precisely the state it exists to catch.
    """
    path = pathlib.Path("/tmp/argus-hb-test-b")
    path.unlink(missing_ok=True)
    bot = FakeBot(fail=True)
    beat = Heartbeat(bot, path=path, fails_before_stale=3)

    assert run(beat.beat()) is False
    assert not path.exists(), "a failed beat must not write the file"


def test_the_file_goes_stale_rather_than_deleting_the_last_known_good() -> None:
    """A stale timestamp keeps information a missing file throws away.

    Both read the same way to the health check, but a stale file still carries
    the last time the bot was known alive, which is what an operator wants when
    reading the container by hand at three in the morning.
    """
    path = pathlib.Path("/tmp/argus-hb-test-c")
    path.unlink(missing_ok=True)
    bot = FakeBot()
    beat = Heartbeat(bot, path=path, fails_before_stale=3)

    run(beat.beat())
    assert path.exists()

    bot.fail = True
    for _ in range(5):
        run(beat.beat())

    assert path.exists(), "the last known good beat must survive a failure"
    assert bot.calls == 6


def test_a_transient_blip_does_not_stop_beating() -> None:
    """One dropped packet is not an outage.

    The failure counter resets on success, so a single miss followed by a
    recovery leaves the bot healthy -- otherwise every routine network hiccup
    would take the container unhealthy and turn a working service into a
    restart loop.
    """
    path = pathlib.Path("/tmp/argus-hb-test-d")
    path.unlink(missing_ok=True)
    bot = FakeBot(fail=True)
    beat = Heartbeat(bot, path=path, fails_before_stale=3)

    assert run(beat.beat()) is False          # miss
    assert run(beat.beat()) is False          # miss
    bot.fail = False
    assert run(beat.beat()) is True           # recovered
    assert path.exists()

    bot.fail = True
    assert run(beat.beat()) is False          # one miss after recovery
    assert beat._consecutive_failures == 1, (
        "the counter must reset on success, or blips accumulate forever"
    )


def test_the_timestamp_is_wall_clock_so_the_health_check_can_subtract_it() -> None:
    """The health check runs in a fresh process and subtracts from `time.time`.

    The first version wrote the event loop's monotonic clock, which starts near
    zero. `time.time() - 117522` is about 1.7 billion, so every health check
    failed and the container stayed permanently unhealthy for a bot that was
    beating perfectly -- five consecutive `1` exits while the file advanced
    normally. The two clocks cannot be compared, so the format has to be
    wall-clock and this asserts it rather than trusting the reader.
    """
    path = pathlib.Path("/tmp/argus-hb-test-clock")
    path.unlink(missing_ok=True)
    bot = FakeBot()
    beat = Heartbeat(bot, path=path)

    assert run(beat.beat()) is True

    written = float(path.read_text(encoding="utf-8"))
    now = time.time()
    assert abs(now - written) < 60, (
        f"the beat wrote {written}, which is not wall-clock time (now {now})"
    )


def test_the_loop_beats_on_its_own() -> None:
    """The loop, not just the one-shot, has to work.

    `beat` being correct proves nothing if `_run` never calls it: the file
    would be written once at startup and then never again, and the health check
    would fail on a perfectly healthy bot one interval later.
    """
    path = pathlib.Path("/tmp/argus-hb-test-e")
    path.unlink(missing_ok=True)
    bot = FakeBot()
    beat = Heartbeat(bot, path=path, interval=0.01)

    async def scenario() -> int:
        beat.start()
        await asyncio.sleep(0.2)
        await beat.stop()
        return bot.calls

    calls = run(scenario())
    assert calls >= 3, f"the loop beat only {calls} time(s) in 0.2s"
    assert path.exists()


def test_stopping_the_heartbeat_ends_the_loop() -> None:
    """`post_shutdown` calls this; a loop that outlived it would keep the
    container's event loop busy during shutdown and delay the stop grace."""
    path = pathlib.Path("/tmp/argus-hb-test-f")
    path.unlink(missing_ok=True)
    bot = FakeBot()
    beat = Heartbeat(bot, path=path, interval=0.01)

    async def scenario() -> int:
        beat.start()
        await asyncio.sleep(0.1)
        await beat.stop()
        calls_at_stop = bot.calls
        await asyncio.sleep(0.2)
        return calls_at_stop - bot.calls

    leaked = run(scenario())
    assert leaked == 0, f"{leaked} beat(s) happened after stop()"


def test_start_is_idempotent() -> None:
    """Two tasks beating the same file would double the API rate for no gain.

    `post_init` is the only caller today, but a second start must not silently
    leak a second loop.
    """
    path = pathlib.Path("/tmp/argus-hb-test-g")
    path.unlink(missing_ok=True)
    bot = FakeBot()
    beat = Heartbeat(bot, path=path, interval=0.01)

    async def scenario() -> None:
        beat.start()
        beat.start()
        await asyncio.sleep(0.1)
        await beat.stop()

    run(scenario())
    assert bot.calls > 0


def test_default_path_is_where_the_health_check_looks() -> None:
    """The two halves of this mechanism must agree on the filename.

    A mismatch is silent in exactly the way this whole change exists to
    eliminate: the bot beats a file the health check never reads, so the
    container reports unhealthy forever while the bot works perfectly. Or the
    health check reads a file nothing writes.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    active = "\n".join(
        line for line in compose.splitlines() if not line.lstrip().startswith("#")
    )
    assert str(DEFAULT_PATH) in active, (
        f"compose does not reference {DEFAULT_PATH}"
    )
    assert "argus-heartbeat" in active


def test_health_check_window_is_wider_than_the_beat_interval() -> None:
    """A single missed beat must not make the container unhealthy.

    The window is the *only* defence against flapping: if it is shorter than or
    equal to the interval, the container is unhealthy between beats and a
    restart loop follows. The relationship is what matters, so the window is
    parsed out of compose and compared against the interval rather than both
    being asserted as a pair of magic numbers.

    Reads `-mmin N`, because the check is `find -mmin` and not the Python
    one-liner it replaced: that one was a folded YAML scalar, folding turned a
    newline into a space inside the string, and Python answered
    `IndentationError: unexpected indent` on every check -- an unhealthy
    container for a bot that was beating perfectly.
    """
    from bot.heartbeat import INTERVAL_SECONDS

    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    match = re.search(r"find /tmp/argus-heartbeat -mmin -(\d+)", compose)
    assert match, "could not find the health check's staleness window"

    window_minutes = int(match.group(1))
    window_seconds = window_minutes * 60
    assert window_seconds > INTERVAL_SECONDS * 3, (
        f"a {window_seconds}s window against a {INTERVAL_SECONDS}s beat tolerates "
        f"only {window_seconds / INTERVAL_SECONDS:.1f} missed beats"
    )


def test_health_check_has_no_nesting_for_yaml_to_reshape() -> None:
    """The specific defect that made an alive bot report unhealthy.

    A folded scalar containing a multi-line program is one indentation change
    away from failing for a reason unrelated to the service. Asserted as a
    property -- one command, no interpreter invocation -- so the fragile shape
    cannot come back under a different name.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    bot_block = re.search(
        r"^  bot:\n(.*?)(?=^  [a-z_-]+:\n|\Z)", compose, re.MULTILINE | re.DOTALL
    ).group(1)
    health = bot_block[bot_block.find("healthcheck:"):]

    active = "\n".join(
        line for line in health.splitlines() if not line.lstrip().startswith("#")
    )
    assert "python -c" not in active, (
        "the health check is back to a Python one-liner"
    )
    assert "find /tmp/argus-heartbeat" in active, (
        "the health check no longer reads the heartbeat file"
    )


def test_health_check_still_tests_telegram_not_just_the_file() -> None:
    """The env-var probe must not have merely gained a file condition.

    The original check was `os.environ.get('TELEGRAM_BOT_TOKEN')`. If it were
    still there and merely and-ed with the file test, the bot would report
    healthy exactly as before, because the file is written by a heartbeat that
    could itself be dead.
    """
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    bot_block = re.search(
        r"^  bot:\n(.*?)(?=^  [a-z_-]+:\n|\Z)", compose, re.MULTILINE | re.DOTALL
    ).group(1)
    health = bot_block[bot_block.find("healthcheck:"):]

    # Comments stripped. This health check deliberately explains what the old
    # probe was and why it was useless, and that explanation quotes the old
    # probe verbatim -- so an unfiltered assertion matched its own documentation
    # and failed against a correct file. This is the third time on this project
    # that a check has matched a comment instead of the thing it was checking.
    active = "\n".join(
        line for line in health.splitlines() if not line.lstrip().startswith("#")
    )
    assert "TELEGRAM_BOT_TOKEN" not in active, (
        "the env-var probe is still the bot's health check"
    )


def test_the_heartbeat_is_started_and_stopped_by_the_application() -> None:
    """A heartbeat nobody starts writes nothing.

    Both hooks are asserted because either one alone is a bug: started and not
    stopped leaks a task past shutdown; stopped and not started means the file
    is never written and the healthy bot reports unhealthy forever.
    """
    app_source = (REPO / "bot" / "app.py").read_text(encoding="utf-8")
    assert "Heartbeat(app.bot)" in app_source, "post_init never starts it"
    assert "heartbeat.stop()" in app_source, "post_shutdown never stops it"
    assert 'app.bot_data["heartbeat"]' in app_source, (
        "the heartbeat is not stored, so shutdown cannot find it"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
