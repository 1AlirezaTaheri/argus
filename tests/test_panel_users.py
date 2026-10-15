"""The panel's access gate and user actions must use the real object shapes.

## The defect this catches

`get_telegram_user` returns a SQLAlchemy model (`TelegramUser`); `list_telegram_users`
returns dicts. The panel used them interchangeably:

* `_toggle_user` and `_cycle_role` took `get_telegram_user`'s model and
  subscripted it -- `row["enabled"]`, `row["role"]` -- which raises
  `TypeError: 'TelegramUser' object is not subscriptable`.
* the callback gate handed that same model onward as the `principal`, and the
  action handlers read `principal.user_id` -- but the model field is
  `telegram_user_id`, so that raises `AttributeError`.

Every one of those raised inside `panel_callback`'s broad `except`, so the user
saw the generic "something went wrong" screen and the action never happened.
Nothing was logged as an error, because the handler reported it as handled.

## Why the first version of the suite passed

`test_toggling_a_rule_reports_the_new_state` defined its own `_P` class with
`user_id` and `username` rather than using the row the router really passes. A
fake that matches the call site proves the call site is called; it cannot prove
the object handed to it has the right shape. These tests use real objects
throughout -- a real `Authenticator`, a real `Principal`, real ORM rows.

## The third bug, found while fixing the second

`_toggle_rule` did `set_enabled(id, True)`, falling back to `set_enabled(id, False)`
if that returned nothing. For a rule that exists the first branch always
succeeds, so the fallback was dead code and no rule could ever be switched off --
the toggle forced every rule on. `test_a_toggle_actually_toggles` presses twice
and checks it lands back where it started, which a force-on implementation cannot
pass.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from bot.handlers import panel  # noqa: E402


# ---------------------------------------------------------------------------
# Stand-ins: real collaborators, minimal plumbing
# ---------------------------------------------------------------------------

class _Settings:
    """Just enough for `Authenticator`, which only reads the allow-list."""

    def __init__(self, allowed) -> None:
        self.allowed_user_ids = tuple(allowed)


class _User:
    def __init__(self, user_id: int, username: str | None) -> None:
        self.id = user_id
        self.username = username


class _Update:
    """A callback-shaped update: `effective_user` and nothing else.

    Deliberately no `.message`. The command path replies on `update.message`; the
    callback path must not, and a stand-in that always has one would hide that.
    """

    def __init__(self, user_id: int, username: str | None = None) -> None:
        self.effective_user = _User(user_id, username)


class _Application:
    def __init__(self, bot_data) -> None:
        self.bot_data = bot_data


class _Context:
    def __init__(self, bot_data) -> None:
        self.application = _Application(bot_data)


@pytest.fixture()
def database(tmp_path):
    from database.database import Database
    from database.models import Base

    db = Database(f"sqlite:///{tmp_path / 'panel-users.db'}")
    Base.metadata.create_all(db.engine)
    return db


@pytest.fixture()
def ctx(database):
    """A context carrying the collaborators the panel actually reads."""
    from admin.services.settings_store import SettingsStore
    from security.authentication import Authenticator

    with database.session() as session:
        from database.models import Rule, TelegramUser

        session.add_all([
            TelegramUser(telegram_user_id=1, username="boss", role="admin",
                         language="en", enabled=True),
            TelegramUser(telegram_user_id=2, username="op", role="operator",
                         language="en", enabled=True),
            TelegramUser(telegram_user_id=3, username="viewer1", role="viewer",
                         language="en", enabled=True),
            TelegramUser(telegram_user_id=4, username="suspended", role="admin",
                         language="en", enabled=False),
            Rule(name="r1", rule_type="deny_port", value="23",
                 scope="global", enabled=False),
        ])
        session.commit()

    return _Context({
        "database": database,
        "authenticator": Authenticator(_Settings(allowed=[1, 2, 3, 4])),
        "settings_store": SettingsStore(database),
        "scan_worker": None,
    })


def _principal(user_id: int, username: str, role: str = "admin"):
    """A real `Principal`, as `_admin_principal` now returns one."""
    from security.authentication import Principal

    return Principal(user_id=user_id, username=username, role=role)


def _role_of(database, user_id: int):
    from admin.services.users import get_telegram_user

    with database.session() as session:
        return get_telegram_user(session, user_id)


# ---------------------------------------------------------------------------
# The row shape the conversion exists for
# ---------------------------------------------------------------------------

def test_the_user_row_is_a_model_named_telegram_user_id(ctx) -> None:
    """Pins why the gate converts instead of passing the row through.

    `get_telegram_user` returns a model whose id field is `telegram_user_id`.
    Downstream code reads `principal.user_id`, so anything handing on this row
    unchanged breaks -- and breaks inside a broad `except`, so quietly.
    """
    row = _role_of(ctx.application.bot_data["database"], 1)
    assert row.__class__.__name__ == "TelegramUser"
    assert row.telegram_user_id == 1
    assert not hasattr(row, "user_id")


def test_the_user_row_is_not_subscriptable(ctx) -> None:
    """Guards the access pattern itself, so it cannot regress to `row["role"]`."""
    row = _role_of(ctx.application.bot_data["database"], 1)
    with pytest.raises(TypeError):
        _ = row["role"]  # type: ignore[index]


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_admin_gets_a_principal(ctx) -> None:
    """An enabled admin on the allow-list is let through, as a Principal."""
    principal = await panel._admin_principal(_Update(1, "boss"), ctx)
    assert principal is not None
    assert principal.user_id == 1
    assert principal.username == "boss"
    # The role must come from the row, not Authenticator's "operator" default,
    # or every audit row would name an operator as the actor.
    assert principal.role == "admin"


@pytest.mark.asyncio
async def test_an_operator_is_refused(ctx) -> None:
    principal = await panel._admin_principal(_Update(2, "op"), ctx)
    assert principal is None


@pytest.mark.asyncio
async def test_a_disabled_admin_is_refused(ctx) -> None:
    """Disabling a user in the panel takes effect on the next button press."""
    principal = await panel._admin_principal(_Update(4, "suspended"), ctx)
    assert principal is None


@pytest.mark.asyncio
async def test_a_user_with_no_row_is_refused(ctx) -> None:
    principal = await panel._admin_principal(_Update(999, "stranger"), ctx)
    assert principal is None


@pytest.mark.asyncio
async def test_someone_off_the_allow_list_is_refused_even_with_an_admin_row(ctx) -> None:
    """The allow-list is checked before the row.

    `sync_from_allowed` creates rows for allow-listed users but never deletes
    them, so a row outlives removal from `ALLOWED_USER_IDS`. Checking the row
    first would keep granting panel access to someone who had been taken off
    the list -- the row would still say `admin`.
    """
    from database.models import TelegramUser

    database = ctx.application.bot_data["database"]
    with database.session() as session:
        session.add(TelegramUser(telegram_user_id=500, username="ex", role="admin",
                                 language="en", enabled=True))
        session.commit()

    # The row says admin, but 500 is not in the allow-list this context holds.
    principal = await panel._admin_principal(_Update(500, "ex"), ctx)
    assert principal is None


# ---------------------------------------------------------------------------
# The actions, with a real principal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_toggling_a_user_flips_enabled(ctx) -> None:
    database = ctx.application.bot_data["database"]
    before = _role_of(database, 2).enabled

    await panel._toggle_user(ctx, "en", 2, _principal(1, "boss"))

    assert _role_of(database, 2).enabled is not before


@pytest.mark.asyncio
async def test_cycling_a_role_advances_viewer_to_operator(ctx) -> None:
    database = ctx.application.bot_data["database"]
    assert _role_of(database, 3).role == "viewer"

    await panel._cycle_role(ctx, "en", 3, _principal(1, "boss"))

    assert _role_of(database, 3).role == "operator"


@pytest.mark.asyncio
async def test_cycling_a_role_wraps_after_admin(ctx) -> None:
    """The cycle is modulo the role list, so admin advances to viewer."""
    database = ctx.application.bot_data["database"]

    await panel._cycle_role(ctx, "en", 1, _principal(1, "boss"))
    assert _role_of(database, 1).role == "viewer"


@pytest.mark.asyncio
async def test_a_toggle_actually_toggles(ctx) -> None:
    """Two presses must land back where they started.

    The defect this catches set every rule to enabled on the first press and
    had dead fallback code, so a second press was a no-op and the rule could
    never be turned off. One press proves nothing; a round trip does.
    """
    database = ctx.application.bot_data["database"]
    from database.repository import RuleRepository

    with database.session() as session:
        rule = RuleRepository(session).list_rules()[0]
        rule_id, original = rule.id, rule.enabled

    await panel._toggle_rule(ctx, "en", rule_id, _principal(1, "boss"))
    with database.session() as session:
        assert RuleRepository(session).get_rule(rule_id).enabled is not original

    await panel._toggle_rule(ctx, "en", rule_id, _principal(1, "boss"))
    with database.session() as session:
        assert RuleRepository(session).get_rule(rule_id).enabled is original


@pytest.mark.asyncio
async def test_toggling_a_missing_rule_does_not_raise(ctx) -> None:
    """A stale button for a deleted rule must not blow up the screen.

    `set_enabled` raises `RepositoryError` for a missing rule, which the router
    would turn into the generic error screen. The row is read first, so a stale
    id simply changes nothing.
    """
    text, _ = await panel._toggle_rule(ctx, "en", 999999, _principal(1, "boss"))
    assert text


@pytest.mark.asyncio
async def test_toggling_a_missing_user_does_not_raise(ctx) -> None:
    text, _ = await panel._toggle_user(ctx, "en", 999999, _principal(1, "boss"))
    assert text


@pytest.mark.asyncio
async def test_changing_a_choice_setting(ctx) -> None:
    """A declared choice setting can be changed from the panel."""
    from admin.services.settings_store import seed

    database = ctx.application.bot_data["database"]
    store = ctx.application.bot_data["settings_store"]
    with database.session() as session:
        seed(session, actor="test")

    await panel._change_setting(ctx, "en", "bot_language", "en", _principal(1, "boss"))

    store.invalidate()
    assert store.get("bot_language") == "en"


@pytest.mark.asyncio
async def test_changing_a_setting_the_server_does_not_declare_is_refused(ctx) -> None:
    """Callback data is client-supplied; an unknown key must not be written."""
    from admin.services.settings_store import seed

    database = ctx.application.bot_data["database"]
    store = ctx.application.bot_data["settings_store"]
    with database.session() as session:
        seed(session, actor="test")

    store.invalidate()
    before = dict(store.all())

    await panel._change_setting(ctx, "en", "not_a_setting", "x", _principal(1, "boss"))

    store.invalidate()
    assert dict(store.all()) == before, "an undeclared setting key was written"


@pytest.mark.asyncio
async def test_the_users_screen_renders(ctx) -> None:
    text, _ = await panel._screen_users(ctx, "en")
    assert "boss" in text
    assert "operator" in text


# ---------------------------------------------------------------------------
# Deleting a rule
# ---------------------------------------------------------------------------

def _rule_id(ctx) -> int:
    from database.repository import RuleRepository

    database = ctx.application.bot_data["database"]
    with database.session() as session:
        return RuleRepository(session).list_rules()[0].id


def _rule_exists(ctx, rule_id: int) -> bool:
    from database.repository import RuleRepository

    database = ctx.application.bot_data["database"]
    with database.session() as session:
        return RuleRepository(session).get_rule(rule_id) is not None


def test_the_router_has_a_branch_for_the_delete_button() -> None:
    """The Delete button must not fall through to a bare re-render.

    `p:r:<id>:d` used to land on the `else` branch and re-render the list, so
    tapping a destructive control did nothing at all. Asserted on the router's
    source because the failure is a missing branch, not a wrong one.
    """
    source = pathlib.Path(panel.__file__).read_text(encoding="utf-8")
    assert 'sub == "d"' in source, "the router has no branch for the delete button"
    assert 'sub == "dx"' in source, "the router has no branch for the confirmed delete"


@pytest.mark.asyncio
async def test_the_first_press_only_asks(ctx) -> None:
    """Confirm must not delete anything."""
    rule_id = _rule_id(ctx)

    text, keyboard = await panel._confirm_delete_rule(ctx, "en", rule_id)

    assert _rule_exists(ctx, rule_id), "the rule was deleted before confirmation"
    assert text
    assert len(keyboard.inline_keyboard) == 1


@pytest.mark.asyncio
async def test_confirming_deletes_the_rule(ctx) -> None:
    rule_id = _rule_id(ctx)
    assert _rule_exists(ctx, rule_id)

    await panel._delete_rule(ctx, "en", rule_id, _principal(1, "boss"))

    assert not _rule_exists(ctx, rule_id), "the rule survived a confirmed delete"


@pytest.mark.asyncio
async def test_confirming_a_deleted_rule_does_not_raise(ctx) -> None:
    """A stale Yes button -- pressed after the rule is already gone."""
    text, _ = await panel._delete_rule(ctx, "en", 999999, _principal(1, "boss"))
    assert text


@pytest.mark.asyncio
async def test_confirming_a_missing_rule_says_so(ctx) -> None:
    text, _ = await panel._confirm_delete_rule(ctx, "en", 999999)
    assert "no longer exists" in text


# ---------------------------------------------------------------------------
# No dead buttons
# ---------------------------------------------------------------------------

def _callback_data(markup) -> list:
    """Every callback the keyboard carries, flattened."""
    out = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data:
                out.append(button.callback_data)
    return out


async def _all_screens(ctx) -> list:
    """Every keyboard the panel can draw, with the screen each came from."""
    from database.repository import RuleRepository

    with ctx.application.bot_data["database"].session() as session:
        rule_id = RuleRepository(session).list_rules()[0].id

    return [
        ("menu", panel._main_keyboard("en")),
        ("dashboard", (await panel._screen_dashboard(ctx, "en"))[1]),
        ("targets", (await panel._screen_targets(ctx, "en"))[1]),
        ("scans", (await panel._screen_scans(ctx, "en"))[1]),
        ("rules", (await panel._screen_rules(ctx, "en"))[1]),
        ("settings", (await panel._screen_settings(ctx, "en"))[1]),
        ("users", (await panel._screen_users(ctx, "en"))[1]),
        ("audit", (await panel._screen_audit(ctx, "en"))[1]),
        ("system", (await panel._screen_system(ctx, "en"))[1]),
        ("confirm-delete", (await panel._confirm_delete_rule(ctx, "en", rule_id))[1]),
    ]


@pytest.mark.asyncio
async def test_no_button_the_panel_renders_is_unroutable(ctx) -> None:
    """Every button the panel draws must be handled by the router.

    The Delete button on the rules screen shipped with no branch in the router:
    it fell through to a bare re-render of the list, so tapping a destructive
    control did nothing and looked like a rule that had been deleted and come
    back. Nothing failed -- a missing branch is not an error, it is a button
    that silently does nothing.

    So every screen is rendered, every callback it carries is collected, and
    each is checked against the router's own grammar. Adding a button without a
    branch fails here rather than in someone's hand.

    The grammar, which the router parses by splitting on ":" -- so the position
    of each part is what matters:

        p:<action>                     the menu and the refresh/back buttons
        p:<action>:<n>                 a bare id, e.g. a scan or a target
        p:<action>:page:<n>            pagination
        p:t:<id>:s | p:t:<id>:c        a target's scans / changes
        p:r:<id>:t | :d | :dx          toggle / confirm-delete / delete
        p:u:<id>:e | :r                enable toggle / cycle role
        p:g:<key>:<value>              a choice setting; the value is not a
                                       sub-action but a value the store accepts

    The first version of this check treated parts[2] as the sub-action, which is
    an id -- so it reported every per-id button as unroutable. A check that
    fires on correct code is a check that gets switched off.
    """
    from admin.services.settings_store import SETTING_SPECS

    # Per-action sub-actions, mirroring the router's branches.
    subs = {"t": {"s", "c"}, "r": {"t", "d", "dx"}, "u": {"e", "r"}}
    actions = {"m", "d", "t", "s", "r", "g", "u", "a", "y"}

    seen = 0
    unroutable: list = []

    for name, markup in await _all_screens(ctx):
        for data in _callback_data(markup):
            seen += 1
            parts = data.split(":")

            if parts[0] != "p":
                unroutable.append(f"{name}: {data!r} is not a panel callback")
                continue

            action = parts[1]
            if action not in actions:
                unroutable.append(f"{name}: {data!r} -> unknown action {action!r}")
                continue

            if len(parts) == 2:
                continue  # a bare action; the router handles it

            if parts[2] == "page":
                if len(parts) != 4 or not parts[3].isdigit():
                    unroutable.append(f"{name}: {data!r} -> malformed page data")
                continue

            if len(parts) == 3:
                # A bare id: the target, scan, or detail screen.
                if not parts[2].isdigit():
                    unroutable.append(f"{name}: {data!r} -> param is not an id")
                continue

            if len(parts) != 4:
                unroutable.append(f"{name}: {data!r} -> {len(parts)} parts")
                continue

            # A setting change is `<key>:<value>`; the value is data, not a verb.
            if action == "g":
                if parts[2] not in SETTING_SPECS:
                    unroutable.append(
                        f"{name}: {data!r} -> {parts[2]!r} is not a declared setting"
                    )
                continue

            if not parts[2].isdigit():
                unroutable.append(f"{name}: {data!r} -> param is not an id")
                continue
            if parts[3] not in subs.get(action, set()):
                unroutable.append(
                    f"{name}: {data!r} -> {action!r} has no branch for {parts[3]!r}"
                )

    assert seen > 0, "no buttons were rendered at all -- the check proved nothing"
    assert not unroutable, "unroutable buttons:\n  " + "\n  ".join(unroutable)


@pytest.mark.asyncio
async def test_every_setting_button_offers_a_value_the_store_accepts(ctx) -> None:
    """A setting button must carry a value the store will actually accept.

    The keys are validated by the unroutable-button check; this checks the
    value. `_change_setting` catches `SettingError` and re-renders, so a button
    carrying an undeclared value looks exactly like a setting that refuses to
    change -- the failure mode is silence, not an error, which is why it needs
    an explicit test rather than a try/except.

    The option list is parsed out of the same `choice:` kind string the panel
    itself parses, rather than a hand-written copy, so the two cannot drift.
    """
    from admin.services.settings_store import SETTING_SPECS

    _, keyboard = await panel._screen_settings(ctx, "en")

    bad = []
    seen = 0
    for data in _callback_data(keyboard):
        parts = data.split(":")
        if parts[1] != "g" or len(parts) != 4:
            continue
        seen += 1
        key, value = parts[2], parts[3]
        spec = SETTING_SPECS.get(key)
        if spec is None:
            bad.append(f"{data!r}: {key!r} is not a declared setting")
            continue
        kind = spec[1]
        if not kind.startswith("choice:"):
            bad.append(f"{data!r}: {key!r} is not a choice setting")
            continue
        options = [o.split(":")[0] for o in kind.split(":", 1)[1].split(",") if o]
        if value not in options:
            bad.append(f"{data!r}: {value!r} is not one of {options}")

    assert seen > 0, "the settings screen offered no changeable values"
    assert not bad, "setting buttons with unusable values:\n  " + "\n  ".join(bad)


@pytest.mark.asyncio
async def test_every_panel_callback_fits_telegram_s_64_byte_limit(ctx) -> None:
    """Telegram rejects callback data over 64 bytes.

    A hard API constraint: a keyboard that renders and then fails on send breaks
    whichever screen hits it first. The target buttons carry the target's name,
    which is user-supplied, so this is checked against real rendered data rather
    than assumed.
    """
    """Telegram rejects callback data over 64 bytes.

    A hard API constraint: a keyboard that renders and then fails on send breaks
    whichever screen hits it first. The target buttons carry the target's name,
    which is user-supplied, so this is checked against real rendered data rather
    than assumed.
    """
    checked = 0
    for name, markup in await _all_screens(ctx):
        for data in _callback_data(markup):
            checked += 1
            size = len(data.encode("utf-8"))
            assert size <= 64, f"{name}: {data!r} is {size} bytes"
    assert checked > 0, "no callbacks were checked -- the test proved nothing"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))