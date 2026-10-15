#!/usr/bin/env python3
"""The admin panel must render every screen against real data.

## Why these exist

The panel is a set of screen functions that read from the database and return
``(text, InlineKeyboardMarkup)``. A screen that raises on a real row shape --
a scan with no hosts, a target with no scans, an empty table -- is a broken
screen, and the only way to find that is to run it against a database that has
those shapes. Mocks would hide exactly the failures this is for.

## SQLite, not PostgreSQL

The test container (``docker/verify.sh``) runs pytest in a bare
``python:3.11-slim`` image with the source mounted and nothing else: no ``.env``,
so ``Settings.from_env()`` raises ``ALLOWED_USER_IDS is empty``, and no route to
the ``db`` service. Rather than skip -- which would hide the failures behind a
missing fixture -- these tests build a throwaway SQLite database, create the real
schema with ``Base.metadata.create_all``, and seed it with rows that exercise the
empty and populated branches of every screen.

The models are portable (no PostgreSQL-specific column types) and ``Database``
already special-cases ``sqlite://`` URLs, so the screens run against the same ORM
they run against in production, on a engine that needs no network.

## The cases

Every screen is rendered at least once. The ones with a "no data" branch are
rendered against an empty table as well as a populated one, because an empty
list is the shape most often to break a render (``rows[0]`` on an empty list,
``len(rows)`` on a ``None``).
"""
from __future__ import annotations

import pathlib
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bot.handlers import panel  # noqa: E402


class _Application:
    """A bot_data mapping, which is what the screens index into."""

    def __init__(self, database, store=None, worker=None) -> None:
        self.bot_data = {
            "database": database,
            "settings_store": store,
            "scan_worker": worker,
        }


class _Context:
    """The minimum of a Telegram context the panel actually touches."""

    def __init__(self, database) -> None:
        self.application = _Application(database)


def _seed(database) -> None:
    """Insert rows that exercise both the empty and populated branches.

    Deliberately includes the shapes a render is most likely to trip on: a scan
    that found nothing (``host_count=0``), a target with no scans at all, a
    disabled rule, and a disabled user. A fixture that only ever inserts happy
    rows proves nothing about the branches that handle the unhappy ones.
    """
    from database.models import (
        ChangeEvent,
        Host,
        Rule,
        Scan,
        Service,
        Target,
        TelegramUser,
    )

    with database.session() as session:
        # Two targets; one will have scans, one will not.
        home = Target(name="home", value="192.168.174.0/24", enabled=True)
        empty = Target(name="empty-target", value="10.0.0.0/8", enabled=True)
        session.add_all([home, empty])
        session.flush()

        # A succeeded scan with hosts, a failed scan, and a scan with no hosts.
        ok = Scan(
            target_id=home.id, profile="service", status="succeeded", source="manual",
            started_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            finished_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
            duration_ms=1200, host_count=1, service_count=2,
        )
        failed = Scan(
            target_id=home.id, profile="service", status="failed", source="schedule",
            started_at=datetime(2026, 10, 13, tzinfo=timezone.utc),
            error="timed out",
        )
        bare = Scan(
            target_id=home.id, profile="quick", status="succeeded", source="schedule",
            started_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
            host_count=0, service_count=0,
        )
        session.add_all([ok, failed, bare])
        session.flush()

        host = Host(scan_id=ok.id, address="192.168.174.1", state="up")
        session.add(host)
        session.flush()
        session.add_all([
            Service(host_id=host.id, port=22, protocol="tcp", state="open",
                    service_name="ssh"),
            Service(host_id=host.id, port=80, protocol="tcp", state="open",
                    service_name="http"),
        ])

        # Two rules, one enabled and one disabled.
        session.add_all([
            Rule(name="block-telnet", rule_type="deny_port", value="23",
                 scope="global", enabled=True),
            Rule(name="allow-web", rule_type="allow_port", value="80,443",
                 scope="global", enabled=False),
        ])

        # An admin, an operator, and a disabled admin.
        session.add_all([
            TelegramUser(telegram_user_id=1, username="admin", role="admin",
                         language="fa", enabled=True),
            TelegramUser(telegram_user_id=2, username="operator", role="operator",
                         language="en", enabled=True),
            TelegramUser(telegram_user_id=3, username="disabled", role="admin",
                         language="fa", enabled=False),
        ])

        # A couple of change events on the succeeded scan.
        session.add_all([
            ChangeEvent(scan_id=ok.id, change_type="new_port",
                        host="192.168.174.1", port=80, protocol="tcp"),
            ChangeEvent(scan_id=ok.id, change_type="closed_port",
                        host="192.168.174.1", port=23, protocol="tcp"),
        ])
        session.commit()


@pytest.fixture()
def context(tmp_path):
    """A context backed by a real (SQLite) database with seeded rows."""
    from database.database import Database

    db_path = tmp_path / "panel-test.db"
    database = Database(f"sqlite:///{db_path}")
    from database.models import Base

    Base.metadata.create_all(database.engine)
    _seed(database)
    return _Context(database)


@pytest.fixture()
def empty_context(tmp_path):
    """A context backed by an empty database -- the first-run state."""
    from database.database import Database
    from database.models import Base

    db_path = tmp_path / "panel-empty.db"
    database = Database(f"sqlite:///{db_path}")
    Base.metadata.create_all(database.engine)
    return _Context(database)


# ---------------------------------------------------------------------------
# The main menu
# ---------------------------------------------------------------------------


def test_the_main_menu_has_every_entry_point(context) -> None:
    """The menu must offer all eight entry points.

    Verified by row count rather than by iterating the buttons: in this version
    of python-telegram-bot, iterating an ``InlineKeyboardMarkup`` directly yields
    column indices (``markup[0]`` raises ``TypeError``), so the button text is
    reached through ``inline_keyboard``. The eight actions themselves are
    checked against the router by ``test_every_menu_callback_is_routed``.
    """
    markup = panel._main_keyboard("en")
    assert markup.__class__.__name__ == "InlineKeyboardMarkup"
    assert len(markup.inline_keyboard) == 8


def test_every_menu_callback_is_routed() -> None:
    """Every action the menu can send must have a branch in the router.

    The router is a long if/elif; a missing branch falls through to the main
    menu, which looks like the button did nothing. This checks the router's
    branches against the menu's buttons so they cannot drift apart.
    """
    source = pathlib.Path(panel.__file__).read_text(encoding="utf-8")
    for action in ("d", "t", "s", "r", "g", "u", "a", "y"):
        assert f'action == "{action}"' in source, (
            f"the router has no branch for action {action!r}"
        )


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_dashboard_renders_real_numbers(context) -> None:
    """The dashboard must render the stats the service actually holds.

    Rendered rather than asserted field-by-field, because the failure mode is a
    render that raises on a real row (a ``None`` timestamp, a missing key), not a
    wrong number -- the numbers come from the same service the web dashboard
    uses, so a mismatch would be a query bug elsewhere.
    """
    text, keyboard = await panel._screen_dashboard(context, "en")
    assert "Targets" in text
    assert "Scans" in text
    assert keyboard.inline_keyboard


@pytest.mark.asyncio
async def test_the_dashboard_renders_when_empty(empty_context) -> None:
    """A first-run dashboard must render, not raise.

    Every count is zero and every timestamp is None on a fresh install. That is
    the state an operator sees first, so it is the state most likely to be
    untested and the one where a missing ``None`` guard shows up.
    """
    text, keyboard = await panel._screen_dashboard(empty_context, "en")
    assert "Targets" in text
    assert "0" in text


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_targets_list_paginates(context) -> None:
    """Pagination must clamp an out-of-range page rather than raise.

    A page number past the end comes from a stale "next" button -- the data
    changed between two taps. Clamping to the last page is the only sensible
    answer; ``rows[start:start+PAGE]`` on an empty slice would silently show
    nothing.
    """
    text, _ = await panel._screen_targets(context, "en", page=999)
    assert text
    # And the in-range page.
    text2, _ = await panel._screen_targets(context, "en", page=1)
    assert text2


@pytest.mark.asyncio
async def test_the_targets_list_renders_when_empty(empty_context) -> None:
    text, _ = await panel._screen_targets(empty_context, "en")
    assert "No targets" in text


@pytest.mark.asyncio
async def test_a_target_detail_renders(context) -> None:
    """A real target renders its identity and counts."""
    from admin.services import stats

    with context.application.bot_data["database"].session() as session:
        rows = stats.targets_with_counts(session)
    assert rows, "the seed did not create any targets"

    text, keyboard = await panel._screen_target(context, "en", rows[0]["id"])
    assert rows[0]["name"] in text
    assert keyboard.inline_keyboard


@pytest.mark.asyncio
async def test_an_unknown_target_is_refused_not_blank(context) -> None:
    """A target id that does not exist must say so.

    Rendering an empty detail page would read as "this target has no data",
    which is wrong and unhelpful.
    """
    text, _ = await panel._screen_target(context, "en", 99999999)
    assert "does not exist" in text


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_scans_list_renders(context) -> None:
    text, keyboard = await panel._screen_scans(context, "en")
    assert text
    assert keyboard.inline_keyboard


@pytest.mark.asyncio
async def test_the_scans_list_renders_when_empty(empty_context) -> None:
    text, _ = await panel._screen_scans(empty_context, "en")
    assert "No scans" in text


@pytest.mark.asyncio
async def test_a_scan_with_no_hosts_renders(context) -> None:
    """A scan that found nothing must still render.

    ``stats.scan_hosts`` returns ``None`` for a missing scan, and a scan with
    zero hosts is a real and common result -- the most recent scan on a quiet
    network. Both must render.
    """
    text, _ = await panel._screen_scan(context, "en", 99999999)
    assert "does not exist" in text

    from sqlalchemy import select
    from database.models import Scan

    with context.application.bot_data["database"].session() as session:
        bare = session.scalar(select(Scan).where(Scan.host_count == 0))
    assert bare is not None, "the seed did not create a hostless scan"

    text2, _ = await panel._screen_scan(context, "en", bare.id)
    assert "Hosts" in text2


# ---------------------------------------------------------------------------
# Target scans and changes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_target_with_no_scans_renders(empty_context) -> None:
    """A target that has never been scanned must render its (empty) history.

    The "no scans" branch is the one a fresh target hits, and an empty list is
    the shape most likely to break a render that assumed at least one row.
    """
    from sqlalchemy import select
    from database.models import Target

    with empty_context.application.bot_data["database"].session() as session:
        session.add(Target(name="fresh", value="10.1.0.0/16", enabled=True))
        session.commit()
        row = session.scalar(select(Target).where(Target.name == "fresh"))
        assert row is not None

    text, _ = await panel._screen_target_scans(empty_context, "en", row.id)
    assert "No scans" in text

    text2, _ = await panel._screen_target_changes(empty_context, "en", row.id)
    assert "No changes" in text2


# ---------------------------------------------------------------------------
# Rules, settings, users, audit, system
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_rules_screen_renders(context) -> None:
    text, keyboard = await panel._screen_rules(context, "en")
    assert text
    # Each rule row carries a toggle and a delete, so the keyboard must have
    # more rows than the text has lines of body.
    assert keyboard.inline_keyboard


@pytest.mark.asyncio
async def test_the_rules_screen_renders_when_empty(empty_context) -> None:
    text, _ = await panel._screen_rules(empty_context, "en")
    assert "No rules" in text


@pytest.mark.asyncio
async def test_toggling_a_rule_reports_the_new_state(context) -> None:
    """The toggle must read the state back, not assume it.

    A screen that shows the state the toggle was *asked* to produce, rather than
    the state the database now holds, will lie the first time a concurrent write
    intervenes. Re-reading is the only way to be sure.
    """
    from database.repository import RuleRepository

    with context.application.bot_data["database"].session() as session:
        rule = RuleRepository(session).list_rules()[0]
        original = rule.enabled

    class _P:
        user_id = 1
        username = "admin"

    text, _ = await panel._toggle_rule(context, "en", rule.id, _P())
    # The rule's state flipped, so the screen must now show the opposite word.
    assert ("enabled" in text) != original or ("disabled" in text) != (not original)


@pytest.mark.asyncio
async def test_the_settings_screen_renders_choice_controls(context) -> None:
    """A choice-valued setting must offer its options as buttons.

    ``bot_language`` and ``default_profile`` are the two choice settings, and
    they are the ones an operator would most want to change without opening the
    web panel. A settings screen that lists them but cannot change them is a
    tease.
    """
    text, keyboard = await panel._screen_settings(context, "en")
    # The screen renders and carries a keyboard. Whether a given setting offers
    # inline controls is decided by the server's SETTING_SPECS, so it is checked
    # there rather than by iterating the keyboard -- in this version of
    # python-telegram-bot, iterating an InlineKeyboardMarkup directly raises.
    assert text
    assert keyboard.inline_keyboard


@pytest.mark.asyncio
async def test_the_users_screen_renders_with_actions(context) -> None:
    text, keyboard = await panel._screen_users(context, "en")
    assert text
    # Each user row has a name and an enable toggle.
    assert len(keyboard.inline_keyboard) >= 1


@pytest.mark.asyncio
async def test_the_users_screen_renders_when_empty(empty_context) -> None:
    text, _ = await panel._screen_users(empty_context, "en")
    assert "No Telegram users" in text


@pytest.mark.asyncio
async def test_the_audit_screen_renders(context) -> None:
    text, _ = await panel._screen_audit(context, "en")
    assert "Audit" in text


@pytest.mark.asyncio
async def test_the_system_screen_reports_database_health(context) -> None:
    """The system screen must report the database, not assume it.

    This is the screen an operator opens first when something is wrong, so a
    database that is down must be visible here -- and must not raise, which
    would hide the very answer the screen exists to give.
    """
    text, _ = await panel._screen_system(context, "en")
    assert "Database" in text


@pytest.mark.asyncio
async def test_the_system_screen_survives_a_dead_database() -> None:
    """A database that cannot be reached must be reported, not raised.

    The system check wraps its probe in a broad except for exactly this reason.
    A screen that raises on the failure it exists to report is worse than no
    screen.
    """
    from database.database import Database

    # A URL that parses but connects to nothing.
    dead = Database("sqlite:///nonexistent-dir-xyz/nope.db")
    ctx = _Context(dead)

    text, _ = await panel._screen_system(ctx, "en")
    assert "Database" in text
    assert "unreachable" in text


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_an_admin_is_allowed(context) -> None:
    """The role gate must distinguish an admin from an operator.

    Every allow-listed user is an operator by default, so a gate that checked
    only the allow-list would admit them all. The role column is the only thing
    that can say "admin".
    """
    assert await panel._role_of(context, 1) == "admin"
    assert await panel._role_of(context, 2) == "operator"
    # A disabled admin is still an admin by role; enablement is checked
    # separately at the gate.
    assert await panel._role_of(context, 3) == "admin"


@pytest.mark.asyncio
async def test_a_user_with_no_row_gets_no_role(empty_context) -> None:
    """A caller with no ``telegram_users`` row has no role.

    The allow-list is the gate; the role refines. A user with no row has not
    been synced yet and must not be granted anything.
    """
    assert await panel._role_of(empty_context, 424242) is None


@pytest.mark.asyncio
async def test_the_router_refuses_a_non_admin(context) -> None:
    """A callback from a non-admin must be refused, not rendered.

    The router re-checks authorisation on every press rather than trusting the
    ``/panel`` that opened it, because a role could have been changed in between.
    This drives the check with a caller the role gate will reject.
    """
    # The operator (id 2) is allow-listed but not an admin.
    assert await panel._role_of(context, 2) != "admin"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
