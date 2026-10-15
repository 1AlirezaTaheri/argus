"""The Mini App's `/scan` must enforce every gate the Telegram path enforces.

## Why this file exists

`tests/test_panel_scan.py` proves the *web panel's* scan endpoint refuses when a
gate says no. Its own docstring names the failure it guards: "a second front
door that quietly skips a gate the Telegram path enforces."

The Mini App is a third front door, and its scan tests covered resolution,
profile validation and viewer-refusal -- but not target scope, the rule engine,
or rate limiting. Reading the handler shows all three are applied, and reading
code is not proof. These tests exercise them through HTTP, against the real
app, the real authorizer, the real rule engine and the real rate limiter.

## Every refusal has a positive control

Each test pairs the refusal with the case that must succeed. Without that, a
gate test passes for the wrong reason: if `/scan` returned 403 for everything,
every "the gate refuses" assertion would be green while the endpoint was simply
broken. The control is what makes the refusal mean something.

## The gates, and what each raises

    target scope    TargetNotAllowedError   -> 403
    rule engine     decision not allowed    -> 403 with the reason
    rate limiter    decision not allowed    -> 429
    disabled user   miniapp_user dependency -> 403
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

os.environ.setdefault("ADMIN_USERNAME", "root")
os.environ.setdefault("ADMIN_PASSWORD", "an-initial-admin-password")
os.environ.setdefault("ADMIN_JWT_SECRET", "test-secret-not-for-production")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "777777:MINIAPP_gate_test_token")
os.environ.setdefault("ALLOWED_USER_IDS", "7575983824")
os.environ.setdefault("ADMIN_COOKIE_SECURE", "false")
os.environ.setdefault("MINIAPP_URL", "https://example.trycloudflare.com")

from admin.services.bootstrap import create_app  # noqa: E402
from config.settings import Settings  # noqa: E402
from core.miniapp_auth import sign_init_data  # noqa: E402

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
USER_ID = 7575983824
HEADER = "X-Telegram-Init-Data"

# One network inside ALLOWED_CIDRS below, one outside it.
INSIDE = "10.0.0.0/24"
OUTSIDE = "192.168.5.0/24"


def _blob(user_id: int = USER_ID) -> str:
    payload = (
        '{"id":%d,"first_name":"Alireza","last_name":"T","username":"alireza",'
        '"language_code":"en","is_premium":false}' % user_id
    )
    return sign_init_data(
        TOKEN,
        [("query_id", "AAHdF6IQ"), ("user", payload),
         ("auth_date", str(int(time.time())))],
    )


def _headers() -> dict:
    return {HEADER: _blob()}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A client whose allowed scope is 10.0.0.0/8, so the two targets differ."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{tmp_path}/gates.db")
    monkeypatch.setenv("ALLOWED_CIDRS", "10.0.0.0/8")
    app = create_app(Settings.from_env())
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def operator(client):
    """The signed-in user promoted to operator, so /scan is reachable at all."""
    from admin.services.users import update_telegram_user

    client.get("/miniapp/me", headers=_headers())
    database = client.app.state.ctx.database
    with database.session() as session:
        update_telegram_user(session, USER_ID, role="operator")
    return client


def _database(client):
    return client.app.state.ctx.database


def _add_rule(client, rule_type: str, value: str, **kwargs) -> None:
    from database.models import Rule

    with _database(client).session() as session:
        session.add(Rule(
            name=kwargs.pop("name", f"{rule_type}-{value}"),
            rule_type=rule_type,
            value=value,
            scope=kwargs.pop("scope", "global"),
            enabled=kwargs.pop("enabled", True),
            priority=kwargs.pop("priority", 50),
            **kwargs,
        ))
        session.commit()


def _drop_rules(client) -> None:
    from sqlalchemy import delete
    from database.models import Rule

    with _database(client).session() as session:
        session.execute(delete(Rule))
        session.commit()


def _scan(client, target: str):
    return client.post("/miniapp/scan", json={"target": target}, headers=_headers())


# ---------------------------------------------------------------------------
# Target scope
# ---------------------------------------------------------------------------

class TestTargetScope:
    def test_a_target_outside_the_allowed_cidrs_is_refused(self, operator):
        """The scope gate is the same `assert_target_permitted` the bot calls."""
        resp = _scan(operator, OUTSIDE)
        assert resp.status_code == 403, resp.text

    def test_a_target_inside_the_allowed_cidrs_is_accepted(self, operator):
        """The control. Without it a 403 above could just mean /scan is broken."""
        resp = _scan(operator, INSIDE)
        assert resp.status_code == 202, resp.text


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

class TestRuleEngine:
    def test_a_deny_rule_blocks_the_scan(self, operator):
        _add_rule(operator, "deny_cidr", INSIDE)
        resp = _scan(operator, INSIDE)
        assert resp.status_code == 403, resp.text

    def test_the_denial_names_the_rule_type(self, operator):
        """The operator is told which kind of rule refused, not just "denied"."""
        _add_rule(operator, "deny_cidr", INSIDE, name="no-lan")
        detail = _scan(operator, INSIDE).json()["detail"]
        assert "deny_cidr" in detail, detail

    def test_the_audit_row_names_the_actual_rule(self, operator):
        """The forensic record must identify the rule by name.

        The HTTP detail carries the rule's type and value; the rule's own name
        is what an operator needs later to find it in the rules list, and that
        is only in the audit row. An earlier version of this test asserted the
        name in the response and failed, because the response does not carry
        it -- so the two claims are asserted separately rather than merged into
        one that cannot tell them apart.
        """
        from sqlalchemy import select
        from database.models import AuditLog

        _add_rule(operator, "deny_cidr", INSIDE, name="no-lan")
        assert _scan(operator, INSIDE).status_code == 403

        with _database(operator).session() as session:
            rows = list(session.scalars(
                select(AuditLog).where(AuditLog.action == "miniapp.scan_requested")
            ))
        assert rows, "the blocked scan left no audit row"
        assert rows[-1].success is False, "a blocked scan was audited as a success"
        assert "no-lan" in str(rows[-1].details), rows[-1].details

    def test_a_disabled_rule_does_not_block(self, operator):
        """A rule switched off must not refuse; otherwise "disable" is a lie."""
        _add_rule(operator, "deny_cidr", INSIDE, enabled=False)
        resp = _scan(operator, INSIDE)
        assert resp.status_code == 202, resp.text

    def test_removing_the_rule_restores_the_scan(self, operator):
        """Proves the rule was the cause, not some coincidence in the refusal."""
        _add_rule(operator, "deny_cidr", INSIDE)
        assert _scan(operator, INSIDE).status_code == 403
        _drop_rules(operator)
        assert _scan(operator, INSIDE).status_code == 202


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class TestRateLimit:
    def test_a_second_scan_of_the_same_target_is_throttled(self, operator):
        first = _scan(operator, INSIDE)
        assert first.status_code == 202, first.text

        second = _scan(operator, INSIDE)
        assert second.status_code == 429, second.text

    def test_the_limit_is_per_target(self, operator):
        """Two different targets get two separate budgets.

        A global budget would throttle unrelated work, and the test above would
        still pass -- so the per-target claim needs its own case.
        """
        assert _scan(operator, INSIDE).status_code == 202
        assert _scan(operator, "10.1.0.0/24").status_code == 202, (
            "a scan of a different target was throttled by the first"
        )


# ---------------------------------------------------------------------------
# Disabled users
# ---------------------------------------------------------------------------

class TestDisabledUser:
    def _disable(self, client) -> None:
        from admin.services.users import update_telegram_user

        with _database(client).session() as session:
            update_telegram_user(session, USER_ID, enabled=False)

    @pytest.mark.parametrize("path", [
        "/miniapp/me",
        "/miniapp/stats",
        "/miniapp/targets",
        "/miniapp/rules",
        "/miniapp/profiles",
    ])
    def test_a_disabled_user_is_refused_on_every_read(self, operator, path):
        """The guard is a dependency, so it must hold on all of them.

        Spot-checking one endpoint would prove only that endpoint; a per-endpoint
        `Depends` that someone forgot to add is the exact failure being guarded.
        """
        assert operator.get(path, headers=_headers()).status_code == 200, path

        self._disable(operator)

        resp = operator.get(path, headers=_headers())
        assert resp.status_code == 403, f"{path} answered {resp.status_code}"

    def test_a_disabled_user_cannot_queue_a_scan(self, operator):
        assert _scan(operator, INSIDE).status_code == 202
        self._disable(operator)
        assert _scan(operator, INSIDE).status_code == 403

    def test_a_disabled_user_cannot_change_settings(self, operator):
        """The write endpoint, which is the one that matters most."""
        self._disable(operator)
        resp = operator.patch(
            "/miniapp/settings", json={"theme": "light"}, headers=_headers()
        )
        assert resp.status_code == 403, resp.text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))