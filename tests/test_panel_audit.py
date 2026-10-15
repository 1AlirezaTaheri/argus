"""Every panel mutation must leave an audit row behind.

## Why this is separate from the action tests

`tests/test_panel_users.py` proves the three mutations *change the database*.
It does not prove they are *recorded*. Those are different claims, and an audit
trail that silently stops recording is worse than no audit trail, because the
absence of entries reads as "nothing happened" rather than "the recorder broke".

## Why asserting the row is the only honest check

`admin.services.audit.record_standalone` wraps the write in a bare
`except Exception` and logs a warning -- deliberately, so that losing an audit
line cannot break the command that was being audited. That means a broken audit
write is indistinguishable from a successful one at the call site: no exception,
no failure, just a row that never appears.

So these tests assert the row *exists and says the right thing*. Not "no error was
raised", which the swallowing makes trivially true, but "after toggling rule 1
as admin 1, `audit_log` contains a `panel.rule.toggle` row with actor_id 1".

The tests also cover the failed cases, because the log stores `success`
explicitly and a refused action is meant to be as visible as an accepted one.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bot.handlers import panel  # noqa: E402


class _Application:
    def __init__(self, bot_data) -> None:
        self.bot_data = bot_data


class _Context:
    def __init__(self, bot_data) -> None:
        self.application = _Application(bot_data)


@pytest.fixture()
def ctx(tmp_path):
    from admin.services.settings_store import SettingsStore, seed
    from admin.services.users import sync_from_allowed
    from database.database import Database
    from database.models import Base
    from security.authentication import Authenticator

    class _Settings:
        allowed_user_ids = (1, 2, 3, 4)

    database = Database(f"sqlite:///{tmp_path / 'audit.db'}")
    Base.metadata.create_all(database.engine)

    # Use the real seeding path so the fixture cannot drift from what the app
    # actually puts in the table. sync_from_allowed takes the database and
    # opens its own sessions.
    sync_from_allowed(database, [1, 2, 3, 4], admin_user_id=1)
    with database.session() as session:
        seed(session, actor="test")

    return _Context({
        "database": database,
        "authenticator": Authenticator(_Settings()),
        "settings_store": SettingsStore(database),
        "scan_worker": None,
    })


def _principal(user_id: int, username: str, role: str = "admin"):
    from security.authentication import Principal

    return Principal(user_id=user_id, username=username, role=role)


def _audit_rows(database, action: str | None = None) -> list:
    from sqlalchemy import select
    from database.models import AuditLog

    with database.session() as session:
        stmt = select(AuditLog).order_by(AuditLog.id)
        if action is not None:
            stmt = stmt.where(AuditLog.action == action)
        return list(session.scalars(stmt))


def _db(ctx):
    return ctx.application.bot_data["database"]


def _first_rule_id(ctx) -> int:
    from database.repository import RuleRepository

    with _db(ctx).session() as session:
        return RuleRepository(session).list_rules()[0].id


def _seed_rule(ctx) -> int:
    from database.models import Rule

    with _db(ctx).session() as session:
        session.add(Rule(name="audited", rule_type="deny_port", value="23",
                         scope="global", enabled=False))
        session.commit()
    return _first_rule_id(ctx)


# ---------------------------------------------------------------------------
# The three mutations the definition of "working" names
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_toggling_a_rule_is_audited(ctx) -> None:
    rule_id = _seed_rule(ctx)

    await panel._toggle_rule(ctx, "en", rule_id, _principal(1, "boss"))

    rows = _audit_rows(_db(ctx), "panel.rule.toggle")
    assert len(rows) == 1, f"expected exactly one audit row, got {len(rows)}"
    row = rows[0]
    assert row.actor_id == 1, f"actor_id was {row.actor_id!r}, expected 1"
    assert row.actor_username == "boss"
    assert row.actor_type == "telegram_user", f"actor_type was {row.actor_type!r}"
    assert row.success is True
    assert str(rule_id) in str(row.details), (
        f"the audit row does not say which rule: {row.details!r}"
    )


@pytest.mark.asyncio
async def test_enabling_and_disabling_a_user_is_audited(ctx) -> None:
    from admin.services.users import get_telegram_user

    with _db(ctx).session() as session:
        before = get_telegram_user(session, 3).enabled

    await panel._toggle_user(ctx, "en", 3, _principal(1, "boss"))

    rows = _audit_rows(_db(ctx), "panel.user.toggle")
    assert len(rows) == 1, f"expected one audit row, got {len(rows)}"
    row = rows[0]
    assert row.actor_id == 1
    assert row.actor_username == "boss"
    assert row.success is True

    # The audit must sit alongside a real change, not replace it.
    with _db(ctx).session() as session:
        assert get_telegram_user(session, 3).enabled is not before


@pytest.mark.asyncio
async def test_changing_a_role_is_audited(ctx) -> None:
    await panel._cycle_role(ctx, "en", 3, _principal(1, "boss"))

    rows = _audit_rows(_db(ctx), "panel.user.role")
    assert len(rows) == 1, f"expected one audit row, got {len(rows)}"
    row = rows[0]
    assert row.actor_id == 1
    assert row.actor_username == "boss"
    assert row.success is True


# ---------------------------------------------------------------------------
# The failed cases, which must be as visible as the successful ones
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_refused_toggle_is_audited_as_a_failure(ctx) -> None:
    """A toggle that changed nothing is still recorded, marked unsuccessful.

    The log stores `success` explicitly so a refused action is as visible as an
    accepted one. If this silently succeeded instead, an operator reviewing the
    trail would read "the operator disabled user 999999" when no such action
    ever took effect.
    """
    await panel._toggle_user(ctx, "en", 999999, _principal(1, "boss"))

    rows = _audit_rows(_db(ctx), "panel.user.toggle")
    assert len(rows) == 1, "a refused action was not recorded at all"
    assert rows[0].success is False, "a refused action was recorded as a success"


@pytest.mark.asyncio
async def test_a_refused_rule_delete_is_audited_as_a_failure(ctx) -> None:
    await panel._delete_rule(ctx, "en", 999999, _principal(1, "boss"))

    rows = _audit_rows(_db(ctx), "panel.rule.delete")
    assert len(rows) == 1, "a refused delete was not recorded at all"
    assert rows[0].success is False


@pytest.mark.asyncio
async def test_the_actor_is_the_caller_not_the_target(ctx) -> None:
    """Admin 1 acting on admin 2 must be recorded as actor 1.

    This is the check that catches the worst plausible wiring mistake: reading
    the acting user out of the button's own parameter, so every privileged
    action looks like it was performed by whoever it was aimed at.
    """
    await panel._toggle_user(ctx, "en", 2, _principal(1, "boss"))

    row = _audit_rows(_db(ctx), "panel.user.toggle")[0]
    assert row.actor_id == 1, f"actor recorded as {row.actor_id!r}, expected 1 (the caller)"
    assert row.actor_id != 2, "the actor is the target, not the caller"


# ---------------------------------------------------------------------------
# Ordering and the whole trail
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_sequence_of_actions_leaves_one_row_each_in_order(ctx) -> None:
    """Four actions, four rows, newest last.

    Checks the trail is append-only per action rather than one action overwriting
    another -- `record` is called with its own session each time, so a shared
    transaction bug would show up here as fewer rows.
    """
    from admin.services.settings_store import SETTING_SPECS

    rule_id = _seed_rule(ctx)

    await panel._toggle_rule(ctx, "en", rule_id, _principal(1, "boss"))
    await panel._toggle_user(ctx, "en", 3, _principal(1, "boss"))
    await panel._cycle_role(ctx, "en", 4, _principal(1, "boss"))
    await panel._change_setting(ctx, "en", "bot_language", "en",
                                _principal(1, "boss"))

    rows = _audit_rows(_db(ctx))
    actions = [r.action for r in rows]
    assert actions == [
        "panel.rule.toggle",
        "panel.user.toggle",
        "panel.user.role",
        "panel.setting.change",
    ], f"trail was {actions}"
    # Every row is the same actor, so ordering by id must be stable.
    assert [r.actor_id for r in rows] == [1, 1, 1, 1]
    assert "bot_language" in str(rows[-1].details)


@pytest.mark.asyncio
async def test_a_setting_change_records_the_value(ctx) -> None:
    """The trail must say what changed, not just that something did."""
    await panel._change_setting(ctx, "en", "bot_language", "en",
                                _principal(1, "boss"))

    row = _audit_rows(_db(ctx), "panel.setting.change")[0]
    details = str(row.details)
    assert "bot_language" in details, details
    assert "en" in details, details


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))