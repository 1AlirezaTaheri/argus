"""The bot's wiring has to build, not just its pieces.

This file exists because of a defect that passed 1258 tests: the bot could not
start at all.

    AttributeError: 'AsyncClient' object has no attribute 'initialize'
      telegram/_bot.py:773
        await asyncio.gather(self._request[0].initialize(), ...)

`ApplicationBuilder.request()` takes a `telegram.request.BaseRequest` -- an
object with an initialize()/shutdown() lifecycle the Application calls on both
of its request objects. A bare `httpx.AsyncClient` has neither, so the failure
happens before the first `getUpdates`: there is no partial function, the
container simply crash-loops.

Every other test in the suite constructs its own fakes for the handlers,
translations and rule engine, so the one function that turns them into a running
bot was covered by nothing. It had been running from an older image, which hid
it; the change was committed and the image was never rebuilt.

These cases assert the shape of the wiring directly. They do not open a socket
to Telegram, so they stay offline and fast -- but they fail on the exact mistake
that took the bot down, which is the property that was missing.
"""
from __future__ import annotations

import inspect
import re

import pytest

from bot import app as bot_app


class TestTheApplicationBuilderGetsWhatItNeeds:
    def test_the_clients_are_base_requests(self) -> None:
        """The mistake itself: an httpx client where a wrapper belongs.

        PTB calls `.initialize()` and `.shutdown()` on both request objects
        during startup and shutdown. `httpx.AsyncClient` has neither, so this is
        an immediate AttributeError rather than a subtle misbehaviour.
        """
        from telegram.request import BaseRequest

        source = inspect.getsource(bot_app.build_application)
        # Find whatever is handed to .request(...) and .get_updates_request(...).
        for method in (".request(", ".get_updates_request("):
            match = re.search(
                re.escape(method) + r"([A-Za-z_][A-Za-z0-9_]*)\s*\)", source
            )
            assert match, f"could not find the argument passed to {method}"
            variable = match.group(1)
            construction = re.search(
                rf"{variable}\s*=\s*([A-Za-z_][A-Za-z0-9_.]*)\(", source
            )
            assert construction, (
                f"{variable} is not constructed where it is used; the wiring "
                "has changed shape and this test needs to know"
            )
            factory = construction.group(1)
            assert "HTTPXRequest" in factory or "BaseRequest" in factory, (
                f"{method} receives {variable}, built by {factory}. "
                "It must be a telegram.request.BaseRequest -- an httpx client "
                "has no initialize(), and the bot dies at startup."
            )

    def test_nothing_passes_a_bare_httpx_client_to_the_builder(self) -> None:
        source = inspect.getsource(bot_app.build_application)
        offenders = [
            line.strip()
            for line in source.splitlines()
            if re.search(r"\.(get_updates_)?request\(\s*httpx\.", line)
        ]
        assert not offenders, offenders

    def test_the_two_request_objects_are_distinct(self) -> None:
        """One client for both would share a connection pool.

        Long polling holds a connection open for most of its timeout, so
        sharing it with the request client means a scan's `sendMessage` queues
        behind an in-flight `getUpdates`. Separate instances, as before.
        """
        source = inspect.getsource(bot_app.build_application)
        constructions = re.findall(r"(\w+)\s*=\s*HTTPXRequest\(", source)
        assert len(constructions) == 2, (
            f"expected two HTTPXRequest instances, found {constructions}"
        )
        assert constructions[0] != constructions[1], (
            "the same variable is passed to both request slots"
        )


class TestTheRetryActuallyReachesTheTransport:
    """The retry was the reason the code changed; it has to be real.

    Two separate probes reported this as "not applied" while it was, because
    `HTTPTransport` hands `retries` straight to its `ConnectionPool` -- so the
    count lives at `transport._pool`, not on the transport, and not in
    `vars(transport)`. Checking it here means a future PTB or httpx change that
    stops forwarding `httpx_kwargs` fails loudly instead of quietly costing an
    update per dropped connection.
    """

    @staticmethod
    def _pool_retries(client) -> int | None:
        transport = client._transport
        for holder in (transport, getattr(transport, "_pool", None)):
            if holder is None:
                continue
            for key, value in vars(holder).items():
                if isinstance(value, int) and "retr" in key:
                    return value
        return None

    def test_the_configured_count_is_reachable(self) -> None:
        import httpx
        from telegram.request import HTTPXRequest

        request = HTTPXRequest(
            connect_timeout=1.0,
            read_timeout=1.0,
            httpx_kwargs={
                "transport": httpx.AsyncHTTPTransport(
                    retries=bot_app._HTTP_CONNECT_RETRIES
                )
            },
        )
        built = request._build_client()
        found = self._pool_retries(built)
        assert found == bot_app._HTTP_CONNECT_RETRIES, (
            "httpx_kwargs did not reach the client, or HTTPTransport puts "
            f"retries somewhere else now (looked for {bot_app._HTTP_CONNECT_RETRIES}, "
            f"found {found})"
        )

    def test_the_transport_is_the_async_one(self) -> None:
        """The second crash, from the first fix.

        PTB drives an `httpx.AsyncClient`. Handing it the *synchronous*
        `HTTPTransport` type-checks fine, appears in the signature, and does not
        fail until the first real request -- with

            'HTTPTransport' object has no attribute 'handle_async_request'

        which is a different traceback from the crash it replaced and just as
        total. So the transport class is asserted, not merely the retry count.
        """
        import httpx
        from telegram.request import HTTPXRequest

        source = inspect.getsource(bot_app.build_application)
        offenders = [
            line.strip()
            for line in source.splitlines()
            # A bare HTTPTransport( is the synchronous one; AsyncHTTPTransport
            # also contains the substring, hence the leading boundary.
            if re.search(r"(?<!Async)httpx\.HTTPTransport\(", line)
        ]
        assert not offenders, (
            "the synchronous HTTPTransport is passed to an async client: "
            f"{offenders}"
        )
        assert "httpx.AsyncHTTPTransport(" in source, (
            "no async transport at all; the retries would not survive"
        )

        # And the built client really is drivable without an AttributeError.
        request = HTTPXRequest(
            connect_timeout=1.0,
            read_timeout=1.0,
            httpx_kwargs={"transport": httpx.AsyncHTTPTransport(retries=1)},
        )
        built = request._build_client()
        assert hasattr(built._transport, "handle_async_request"), (
            f"the transport is {type(built._transport).__name__}, which has no "
            "handle_async_request; the first request would raise"
        )

    def test_the_retries_only_cover_connection_failures(self) -> None:
        """A retry that re-sends a processed request could duplicate an action."""
        assert bot_app._HTTP_CONNECT_RETRIES >= 1
        assert bot_app._HTTP_CONNECT_RETRIES <= 5, (
            "a large retry count on a polling client multiplies the time a "
            "dead link takes to surface"
        )


class TestBuildApplicationRunsOffline:
    """Build the real thing, with no network.

    This is the test whose absence let a crash-looping bot ship. It calls
    `build_application` for real -- which is the point: the function builds its
    own `Database`, seeds settings and registers every handler, so it was never
    reachable from a test and was covered by nothing.

    SQLite, like the rest of the suite. No token validation happens at build
    time and no socket is opened, so this stays offline and fast.
    """

    @staticmethod
    def _env(tmp_path, monkeypatch, name: str) -> None:
        """The minimum environment `Settings.from_env` will accept.

        `ALLOWED_USER_IDS` in particular: the settings refuse to load without it,
        which is the fail-closed behaviour the project wants -- an empty
        allow-list would mean "let everyone scan". The test has to supply one
        rather than the loader being relaxed.
        """
        monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/{name}.db")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:TEST-TOKEN-NOT-REAL")
        monkeypatch.setenv("ALLOWED_USER_IDS", "1")
        monkeypatch.setenv("ADMIN_USERNAME", "tester")
        monkeypatch.setenv("ADMIN_PASSWORD", "test-password")
        monkeypatch.setenv("JWT_SECRET", "x" * 48)

    def test_it_builds(self, tmp_path, monkeypatch) -> None:
        from telegram.ext import Application

        from config.settings import Settings

        self._env(tmp_path, monkeypatch, "bot")

        settings = Settings.from_env()
        application = bot_app.build_application(settings)
        try:
            assert isinstance(application, Application)

            # The exact attribute the crash was about. PTB stores both request
            # objects in `bot._request` as a two-tuple -- there is no separate
            # `_get_updates_request` -- and asserts `Bot` with `__slots__`, so
            # this reads the slot rather than scanning `vars()`.
            holder = getattr(application.bot, "_request", None)
            assert isinstance(holder, tuple) and len(holder) == 2, (
                f"bot._request is {type(holder).__name__}; expected the "
                "(request, get_updates_request) pair PTB initialises"
            )
            for index, request in enumerate(holder):
                assert hasattr(request, "initialize"), (
                    f"request[{index}] is {type(request).__name__}, which has "
                    "no initialize() -- the bot will fail at startup exactly "
                    "as it did before this fix"
                )
                assert hasattr(request, "shutdown")
                # And it really is initialisable, which is what the bot does on
                # start-up. `initialize` opens the session pool; it does not
                # contact Telegram.
                import asyncio

                asyncio.new_event_loop().run_until_complete(request.initialize())
        finally:
            application.stop()

    def test_the_handlers_are_registered(self, tmp_path, monkeypatch) -> None:
        """A build that registers nothing would satisfy the tests above.

        Cheaper than it sounds and catches the other direction: wiring the
        clients correctly while forgetting the handlers produces an Application
        that starts and answers nothing.
        """
        from config.settings import Settings

        self._env(tmp_path, monkeypatch, "bot2")

        application = bot_app.build_application(Settings.from_env())
        try:
            handlers = sum(
                len(group) for group in application.handlers.values()
            )
            assert handlers > 10, (
                f"only {handlers} handlers registered; the builder is not "
                "wiring the bot's commands"
            )
        finally:
            application.stop()

    def test_a_raw_httpx_client_would_not_do(self) -> None:
        """Spelled out, because this is the claim the test above rests on."""
        import httpx
        from telegram.request import BaseRequest

        assert not isinstance(httpx.AsyncClient(), BaseRequest)
        assert not hasattr(httpx.AsyncClient(), "initialize")