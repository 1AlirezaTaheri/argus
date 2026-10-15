"""Admin panel -- manage Argus from inside Telegram.

A Telegram-native management interface, built on inline keyboards and callback
queries rather than a web view, so the same screen is available to whoever is
holding the phone. It is read from ``admin.services.stats`` wherever that
service already exposes a query, so the numbers here cannot drift from the
numbers on the web dashboard.

## Access

Admin-role only. The role is resolved from the ``telegram_users`` row rather
than from ``Authenticator``, which returns ``operator`` for every allow-listed
user and so cannot distinguish an admin from an operator. A caller with no row,
or a disabled row, is refused with an explanation rather than a silent no-op.

## Navigation

Every button press is a callback query. The data is ``p:<action>:<param>``,
kept short because Telegram caps it at 64 bytes and a target id or a username
can be long. Screens are edited in place with ``edit_message_text`` rather
than sending a new message per tap, so a deep navigation never floods the chat.

``Back`` returns to the screen the caller came from, carried as a parameter
rather than inferred, because the same screen is reachable from two places (a
target's scans and its changes) and a single back-stack would guess wrong half
the time.

## Asserted

The module is exercised by ``tests/test_panel.py`` against a real database,
because the interesting failures are data shapes -- a scan with no hosts, a
target with no scans, a rule with a long name -- and those do not appear in a
unit test with mocks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from bot.handlers.common import (
    audit,
    authenticate_or_denounce,
    database,
    lang_of_update,
)

log = logging.getLogger(__name__)

# Callback data is capped at 64 bytes by Telegram. Every action is one or two
# characters, and the parameter is an id, a page, or a key -- never a username
# or a hostname, which would blow the budget on their own.
PREFIX = "p"
PAGE = 5  # rows per page; a phone screen shows about eight lines of text


# ---------------------------------------------------------------------------
# Access and helpers
# ---------------------------------------------------------------------------


async def _role_of(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str | None:
    """The caller's Telegram role, or ``None`` when they have no row.

    ``Authenticator`` returns ``operator`` for every allow-listed user, so the
    role column on ``telegram_users`` is the only source that can say "admin".
    A user with no row is not a viewer either: the allow-list is the gate, and
    this only refines what an allowed user may do.
    """
    try:
        from admin.services.users import get_telegram_user

        with database(context).session() as session:
            row = get_telegram_user(session, user_id)
        if row is None:
            return None
        return row.role
    except Exception:
        # A role lookup that fails must not silently grant or deny. Treating it
        # as "no role" denies, which is the safe direction.
        log.warning("Could not resolve role for %s", user_id, exc_info=True)
        return None


async def _require_admin(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> object | None:
    """Return the caller's principal when they are an admin, else ``None``.

    Refuses with an explanation. A denial that says nothing tells the user the
    button is broken, which is worse than telling them it is not theirs.
    """
    user = update.effective_user
    if user is None:
        return None

    principal = await authenticate_or_denounce(update, context)
    if principal is None:
        return None

    role = await _role_of(context, user.id)
    if role == "admin":
        return principal

    lang = lang_of_update(update, context)
    try:
        from core.i18n import t

        await update.message.reply_text(t("panel.denied", lang))
    except Exception:
        log.warning("Could not send panel denial", exc_info=True)
    return None


def _db(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["database"]


def _store(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("settings_store")


def _worker(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data.get("scan_worker")


def _ago(value: datetime | None, lang: str) -> str:
    """A short relative time, in the caller's language.

    ``date-fns`` is not available here, and the shapes the reports module uses
    are overkill for a table cell. Absent values render as a dash rather than a
    misleading "just now".
    """
    if value is None:
        return "-"
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - value
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "<1m"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def _rows(page: int, total: int) -> tuple[int, int]:
    """Clamp a page into range and return ``(page, total_pages)``."""
    total_pages = max(1, (total + PAGE - 1) // PAGE)
    return min(max(1, page), total_pages), total_pages


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


def _main_keyboard(lang: str = "fa") -> InlineKeyboardMarkup:
    """The top-level menu.

    Two columns, so each button is wide enough to read on a phone. The order is
    by how often each screen is opened: dashboard and targets first, because
    they answer "what is happening and what am I watching".
    """
    from core.i18n import t

    def label(key: str) -> str:
        return t(key, lang)

    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(label("panel.menu.dashboard"), callback_data=f"{PREFIX}:d")],
            [InlineKeyboardButton(label("panel.menu.targets"), callback_data=f"{PREFIX}:t")],
            [InlineKeyboardButton(label("panel.menu.scans"), callback_data=f"{PREFIX}:s")],
            [InlineKeyboardButton(label("panel.menu.rules"), callback_data=f"{PREFIX}:r")],
            [InlineKeyboardButton(label("panel.menu.settings"), callback_data=f"{PREFIX}:g")],
            [InlineKeyboardButton(label("panel.menu.users"), callback_data=f"{PREFIX}:u")],
            [InlineKeyboardButton(label("panel.menu.audit"), callback_data=f"{PREFIX}:a")],
            [InlineKeyboardButton(label("panel.menu.system"), callback_data=f"{PREFIX}:y")],
        ]
    )


async def _screen_main(context: ContextTypes.DEFAULT_TYPE, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    from core.i18n import t

    return t("panel.title", lang), _main_keyboard(lang)


async def _screen_dashboard(context: ContextTypes.DEFAULT_TYPE, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Live numbers, plus the targets that changed most.

    Read from ``stats.dashboard`` and ``stats.top_changed_targets`` so the
    panel cannot disagree with the web dashboard about a count.
    """
    from core.i18n import t
    from admin.services import stats

    lines = [t("panel.dashboard.title", lang), ""]
    with _db(context).session() as session:
        s = stats.dashboard(session)
        top = stats.top_changed_targets(session, limit=3)
        worker = stats.worker_status(_worker(context))
        store = _store(context)
        schedule = (
            stats.schedule_status(store)
            if store is not None
            else {"enabled": False, "interval_hours": None,
                  "next_run_at": None, "next_run_known": False}
        )

    lines.append(
        f"{t('panel.dashboard.targets', lang)}: {s.targets}   "
        f"{t('panel.dashboard.scans', lang)}: {s.scans}"
    )
    lines.append(
        f"{t('panel.dashboard.scans_24h', lang)}: {s.scans_24h}   "
        f"{t('panel.dashboard.failed_24h', lang)}: {s.scans_24h_failed}"
    )
    lines.append(
        f"{t('panel.dashboard.changes_24h', lang)}: {s.changes_24h}   "
        f"{t('panel.dashboard.hosts', lang)}: {s.hosts}   "
        f"{t('panel.dashboard.services', lang)}: {s.services}"
    )
    lines.append(f"{t('panel.dashboard.last_scan', lang)}: {_ago(s.last_scan_at, lang)}")
    lines.append(f"{t('panel.dashboard.telegram_users', lang)}: {s.telegram_users}   "
                 f"{t('panel.dashboard.admins', lang)}: {s.admins}")

    lines.append("")
    lines.append(t("panel.dashboard.top_changed", lang))
    if top:
        for i, row in enumerate(top, 1):
            lines.append(f"{i}. {row['name']} — {row['changes']}")
    else:
        lines.append(t("panel.dashboard.no_changes", lang))

    lines.append("")
    lines.append(
        f"{t('panel.dashboard.worker', lang)}: "
        f"{t('panel.dashboard.running', lang) if worker.get('running') else t('panel.dashboard.idle', lang)}"
    )
    lines.append(
        f"{t('panel.dashboard.schedule', lang)}: "
        f"{t('panel.dashboard.on', lang) if schedule.get('enabled') else t('panel.dashboard.off', lang)}"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t("panel.refresh", lang), callback_data=f"{PREFIX}:d")],
            [InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m")],
        ]
    )
    return "\n".join(lines), keyboard


async def _screen_targets(context: ContextTypes.DEFAULT_TYPE, lang: str, page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    """Targets with their scan totals, paginated.

    Pagination matters here because a long list is the one screen that cannot
    be scanned: five rows per page keeps the message short enough to read and
    the callback data short enough to fit.
    """
    from core.i18n import t
    from admin.services import stats

    with _db(context).session() as session:
        rows = stats.targets_with_counts(session)

    page, total_pages = _rows(page, len(rows))
    start = (page - 1) * PAGE
    chunk = rows[start : start + PAGE]

    lines = [t("panel.targets.title", lang), ""]
    if not chunk:
        lines.append(t("panel.targets.empty", lang))
    for row in chunk:
        pct = 0
        if row["scan_count"]:
            pct = round(100 * row["succeeded_count"] / row["scan_count"])
        name = row["name"]
        if len(name) > 24:
            name = name[:23] + "…"
        lines.append(
            f"• {name} — {row['scan_count']} ({pct}%)"
        )

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("‹", callback_data=f"{PREFIX}:t:page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"{PREFIX}:t:page:{page}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("›", callback_data=f"{PREFIX}:t:page:{page + 1}"))
    nav.append(InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m"))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(row["name"], callback_data=f"{PREFIX}:t:{row['id']}")
         for row in chunk],
        nav,
    ])
    return "\n".join(lines), keyboard


async def _screen_target(context: ContextTypes.DEFAULT_TYPE, lang: str, target_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """One target's identity and counts, with its scans and changes."""
    from core.i18n import t
    from admin.services import stats

    with _db(context).session() as session:
        row = stats.target_detail(session, target_id)

    if row is None:
        return t("panel.target.unknown", lang), InlineKeyboardMarkup(
            [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:t")]]
        )

    schedule = row.get("schedule")
    lines = [t("panel.target.title", lang), ""]
    lines.append(f"{t('panel.target.name', lang)}: {row['name']}")
    lines.append(f"{t('panel.target.value', lang)}: {row['value']}")
    lines.append(f"{t('panel.target.scans', lang)}: {row['scan_count']}")
    if schedule:
        lines.append(
            f"{t('panel.target.schedule', lang)}: {schedule.get('profile', '-')} "
            f"({schedule.get('interval_hours', '-')}h)"
        )
    lines.append(f"{t('panel.target.last_scan', lang)}: {_ago(row.get('created_at'), lang)}")

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t("panel.target.scans", lang), callback_data=f"{PREFIX}:t:{target_id}:s")],
            [InlineKeyboardButton(t("panel.target.changes", lang), callback_data=f"{PREFIX}:t:{target_id}:c")],
            [InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:t")],
        ]
    )
    return "\n".join(lines), keyboard


async def _screen_target_scans(context: ContextTypes.DEFAULT_TYPE, lang: str, target_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """A target's most recent scans."""
    from core.i18n import t
    from admin.services import stats

    with _db(context).session() as session:
        rows = stats.target_scans(session, target_id, limit=PAGE)

    lines = [t("panel.scans.title", lang), ""]
    if not rows:
        lines.append(t("panel.scans.empty", lang))
    for row in rows:
        status = row.get("status", "-")
        lines.append(f"• #{row['id']} {status} — {_ago(row.get('finished_at'), lang)}")

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:t:{target_id}")]]
    )
    return "\n".join(lines), keyboard


async def _screen_target_changes(context: ContextTypes.DEFAULT_TYPE, lang: str, target_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """A target's most recent change events."""
    from core.i18n import t
    from admin.services import stats

    with _db(context).session() as session:
        rows = stats.target_changes(session, target_id, limit=PAGE)

    lines = [t("panel.changes.title", lang), ""]
    if not rows:
        lines.append(t("panel.changes.empty", lang))
    for row in rows:
        host = row.get("host", "-")
        ctype = row.get("change_type", "-")
        lines.append(f"• {ctype} {host}")

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:t:{target_id}")]]
    )
    return "\n".join(lines), keyboard


async def _screen_scans(context: ContextTypes.DEFAULT_TYPE, lang: str, page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    """The most recent scans across every target, newest first.

    A target filter would need the scan to carry its target name, which the
    list query does not return; the per-target screens are where a single
    target's history is read.
    """
    from core.i18n import t
    from sqlalchemy import desc, select
    from database.models import Scan

    with _db(context).session() as session:
        total = int(session.scalar(select(__import__('sqlalchemy').func.count()).select_from(Scan)) or 0)
        rows = session.scalars(
            select(Scan).order_by(desc(Scan.started_at)).limit(PAGE)
        ).all()

    page, total_pages = _rows(page, total)
    lines = [t("panel.scans.title", lang), ""]
    if not rows:
        lines.append(t("panel.scans.empty", lang))
    for row in rows:
        status = row.status or "-"
        lines.append(f"• #{row.id} {status} — {_ago(row.finished_at, lang)}")

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("‹", callback_data=f"{PREFIX}:s:page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"{PREFIX}:s:page:{page}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("›", callback_data=f"{PREFIX}:s:page:{page + 1}"))
    nav.append(InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m"))

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"#{row.id} {row.status or '-'}", callback_data=f"{PREFIX}:s:{row.id}")
         for row in rows],
        nav,
    ])
    return "\n".join(lines), keyboard


async def _screen_scan(context: ContextTypes.DEFAULT_TYPE, lang: str, scan_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """One scan's hosts and port distribution.

    Read from ``stats.scan_hosts``, which returns ``None`` for a scan that does
    not exist so this can refuse rather than render an empty table.
    """
    from core.i18n import t
    from admin.services import stats

    with _db(context).session() as session:
        data = stats.scan_hosts(session, scan_id)

    if data is None:
        return t("panel.scan.unknown", lang), InlineKeyboardMarkup(
            [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:s")]]
        )

    scan = data["scan"]
    lines = [t("panel.scan.title", lang), ""]
    lines.append(f"{t('panel.scan.status', lang)}: {scan.get('status', '-')}")
    lines.append(f"{t('panel.scan.hosts', lang)}: {scan.get('host_count', 0)}   "
                 f"{t('panel.scan.services', lang)}: {scan.get('service_count', 0)}")

    ports = data.get("port_distribution", [])[:5]
    if ports:
        lines.append("")
        lines.append(t("panel.scan.top_ports", lang))
        for p in ports:
            lines.append(f"• {p['port']} — {p['count']}")

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:s")]]
    )
    return "\n".join(lines), keyboard


async def _screen_rules(context: ContextTypes.DEFAULT_TYPE, lang: str, page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    """Rules with a toggle and a delete on each.

    Both actions are inline rather than separate screens, because toggling a
    rule is a one-tap operation and sending it to a sub-menu would cost a round
    trip for nothing. Deletion asks for a confirm word, matching the web panel's
    destructive-action pattern.
    """
    from core.i18n import t
    from database.repository import RuleRepository

    with _db(context).session() as session:
        rules = RuleRepository(session).list_rules()

    page, total_pages = _rows(page, len(rules))
    start = (page - 1) * PAGE
    chunk = rules[start : start + PAGE]

    lines = [t("panel.rules.title", lang), ""]
    if not chunk:
        lines.append(t("panel.rules.empty", lang))
    for rule in chunk:
        state = t("panel.rules.enabled", lang) if rule.enabled else t("panel.rules.disabled", lang)
        lines.append(f"• {rule.name} — {state}")

    keyboard_rows = []
    for rule in chunk:
        toggle = "✓" if rule.enabled else "✗"
        keyboard_rows.append([
            InlineKeyboardButton(toggle, callback_data=f"{PREFIX}:r:{rule.id}:t"),
            InlineKeyboardButton(t("panel.rules.delete", lang), callback_data=f"{PREFIX}:r:{rule.id}:d"),
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("‹", callback_data=f"{PREFIX}:r:page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"{PREFIX}:r:page:{page}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("›", callback_data=f"{PREFIX}:r:page:{page + 1}"))
    nav.append(InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m"))

    keyboard_rows.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


async def _screen_settings(context: ContextTypes.DEFAULT_TYPE, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Settings with their current values.

    Choice-valued settings (``bot_language``, ``default_profile``) are
    changeable inline, because the set of values is small and fixed. Numeric
    and free-text settings are shown but not changed here: changing a number
    from a keyboard means either a long-press conversation or a set of
    presets, and a setting typed wrong is worse than one left alone.
    """
    from core.i18n import t
    from admin.services.settings_store import SETTING_SPECS, SettingsStore

    store = _store(context)
    lines = [t("panel.settings.title", lang), ""]

    keyboard_rows = []
    for key, (_default, kind, _desc) in SETTING_SPECS.items():
        value = store.get(key) if store is not None else None
        if value is None:
            value = t("panel.settings.unset", lang)
        lines.append(f"• {key} = {value}")
        if kind.startswith("choice:"):
            body = kind.split(":", 1)[1]
            options = [o.split(":")[0] for o in body.split(",") if o.split(":")[0]]
            if options:
                keyboard_rows.append(
                    [InlineKeyboardButton(
                        f"{key}: {opt}",
                        callback_data=f"{PREFIX}:g:{key}:{opt}",
                    ) for opt in options]
                )

    keyboard_rows.append([InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m")])
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


async def _screen_users(context: ContextTypes.DEFAULT_TYPE, lang: str, page: int = 1) -> tuple[str, InlineKeyboardMarkup]:
    """Telegram users with their role and an enable toggle.

    A role change is a cycle through the three roles rather than a picker,
    because there are only three and the common case is "make this person an
    admin" or "demote this person", both of which are one tap.
    """
    from core.i18n import t
    from admin.services.users import list_telegram_users

    with _db(context).session() as session:
        rows = list_telegram_users(session)

    page, total_pages = _rows(page, len(rows))
    start = (page - 1) * PAGE
    chunk = rows[start : start + PAGE]

    lines = [t("panel.users.title", lang), ""]
    if not chunk:
        lines.append(t("panel.users.empty", lang))
    for row in chunk:
        username = row.get("username") or str(row["telegram_user_id"])
        if len(username) > 24:
            username = username[:23] + "…"
        state = "✓" if row["enabled"] else "✗"
        lines.append(f"• {username} ({row['role']}) {state} — {row['scan_count']} scans")

    keyboard_rows = []
    for row in chunk:
        username = row.get("username") or str(row["telegram_user_id"])
        if len(username) > 20:
            username = username[:19] + "…"
        keyboard_rows.append([
            InlineKeyboardButton(username, callback_data=f"{PREFIX}:u:{row['telegram_user_id']}:r"),
            InlineKeyboardButton(
                "✓" if row["enabled"] else "✗",
                callback_data=f"{PREFIX}:u:{row['telegram_user_id']}:e",
            ),
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("‹", callback_data=f"{PREFIX}:u:page:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page}/{total_pages}", callback_data=f"{PREFIX}:u:page:{page}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton("›", callback_data=f"{PREFIX}:u:page:{page + 1}"))
    nav.append(InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m"))

    keyboard_rows.append(nav)
    return "\n".join(lines), InlineKeyboardMarkup(keyboard_rows)


async def _screen_audit(context: ContextTypes.DEFAULT_TYPE, lang: str, limit: int = 10) -> tuple[str, InlineKeyboardMarkup]:
    """The most recent audit entries, newest first.

    A fixed limit rather than pagination: the audit screen answers "what
    happened recently", and a page of ten answers that. Deeper history belongs
    to the web panel's filterable table.
    """
    from core.i18n import t
    from admin.services import stats

    with _db(context).session() as session:
        rows = stats.recent_audit(session, limit=limit)

    lines = [t("panel.audit.title", lang), ""]
    if not rows:
        lines.append(t("panel.audit.empty", lang))
    for row in rows:
        actor = row.get("actor_username") or row.get("actor_type", "-")
        success = "✓" if row.get("success") else "✗"
        lines.append(f"• {actor} {row.get('action', '-')} {success}")

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m")]]
    )
    return "\n".join(lines), keyboard


async def _screen_system(context: ContextTypes.DEFAULT_TYPE, lang: str) -> tuple[str, InlineKeyboardMarkup]:
    """Database reachability, worker state, and the scheduler switch.

    Deliberately not a health endpoint: the container's health check already
    answers "is the process alive", and this answers "is the system well",
    which is a different question and the reason the panel exists.
    """
    from core.i18n import t
    from admin.services import stats

    lines = [t("panel.system.title", lang), ""]

    db_ok = False
    try:
        from sqlalchemy import text
        with _db(context).session() as session:
            session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        log.warning("Panel system check: database unreachable", exc_info=True)
    lines.append(f"{t('panel.system.database', lang)}: "
                 f"{t('panel.system.ok', lang) if db_ok else t('panel.system.down', lang)}")

    worker = stats.worker_status(_worker(context))
    if worker.get("available"):
        lines.append(
            f"{t('panel.system.worker', lang)}: "
            f"{t('panel.system.running', lang)} ({worker.get('active', 0)} active, "
            f"{worker.get('pending', 0)} pending)"
        )
    else:
        lines.append(f"{t('panel.system.worker', lang)}: {t('panel.system.absent', lang)}")

    store = _store(context)
    schedule = (
        stats.schedule_status(store)
        if store is not None
        else {"enabled": False, "interval_hours": None,
              "next_run_at": None, "next_run_known": False}
    )
    lines.append(
        f"{t('panel.system.schedule', lang)}: "
        f"{t('panel.system.on', lang) if schedule.get('enabled') else t('panel.system.off', lang)}"
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton(t("panel.back", lang), callback_data=f"{PREFIX}:m")]]
    )
    return "\n".join(lines), keyboard


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


async def _toggle_rule(context: ContextTypes.DEFAULT_TYPE, lang: str, rule_id: int, principal) -> tuple[str, InlineKeyboardMarkup]:
    """Flip a rule's enabled flag and re-render the rules screen.

    The new state is read back from the row rather than assumed, so the screen
    shows what the database holds and not what the toggle was asked to do.
    """
    from core.i18n import t
    from database.repository import RuleRepository

    with _db(context).session() as session:
        rule = RuleRepository(session).set_enabled(rule_id, True)
        if rule is None:
            rule = RuleRepository(session).set_enabled(rule_id, False)
        audit(context, "panel.rule.toggle",
               actor_id=principal.user_id, actor_username=principal.username,
               success=rule is not None)

    return await _screen_rules(context, lang)


async def _toggle_user(context: ContextTypes.DEFAULT_TYPE, lang: str, user_id: int, principal) -> tuple[str, InlineKeyboardMarkup]:
    """Flip a user's enabled flag.

    Read back rather than assumed, for the same reason as the rule toggle.
    """
    from admin.services.users import get_telegram_user, update_telegram_user

    with _db(context).session() as session:
        row = get_telegram_user(session, user_id)
        new_state = not (row["enabled"] if row else True)
        update_telegram_user(session, user_id, enabled=new_state)
        audit(context, "panel.user.toggle",
               actor_id=principal.user_id, actor_username=principal.username,
               success=True)

    return await _screen_users(context, lang)


async def _cycle_role(context: ContextTypes.DEFAULT_TYPE, lang: str, user_id: int, principal) -> tuple[str, InlineKeyboardMarkup]:
    """Move a user to the next role in the cycle viewer -> operator -> admin.

    A cycle rather than a picker, because there are three roles and the common
    case is one step up or down. It cannot be used to escalate past admin
    (admin is the last step), and it audits the change either way.
    """
    from core.i18n import t
    from admin.services.users import get_telegram_user, update_telegram_user, TELEGRAM_ROLES

    with _db(context).session() as session:
        row = get_telegram_user(session, user_id)
        if row is not None:
            current = row["role"] if row["role"] in TELEGRAM_ROLES else "viewer"
            idx = TELEGRAM_ROLES.index(current)
            nxt = TELEGRAM_ROLES[(idx + 1) % len(TELEGRAM_ROLES)]
            update_telegram_user(session, user_id, role=nxt)
            audit(context, "panel.user.role",
                   actor_id=principal.user_id, actor_username=principal.username,
                   success=True)

    return await _screen_users(context, lang)


async def _change_setting(context: ContextTypes.DEFAULT_TYPE, lang: str, key: str, value: str, principal) -> tuple[str, InlineKeyboardMarkup]:
    """Change a setting the server declares to be a choice.

    Validated against ``SETTING_SPECS`` rather than trusted: the callback data
    is a client-supplied string, and a setting written with a value the server
    does not accept would be rejected on the next write anyway, so refusing here
    saves a round trip and an audit row for nothing.
    """
    from admin.services.settings_store import SETTING_SPECS, SettingError

    spec = SETTING_SPECS.get(key)
    if spec is None:
        # Not a known setting, or not a choice kind. Refuse rather than write.
        log.warning("Panel setting change refused: %s is not a choice setting", key)
        return await _screen_settings(context, lang)

    store = _store(context)
    if store is None:
        return await _screen_settings(context, lang)

    try:
        with _db(context).session() as session:
            store.set(session, key, value, actor=principal.username)
            audit(context, "panel.setting.change",
                   actor_id=principal.user_id, actor_username=principal.username,
                   success=True, details={"key": key, "value": value})
    except SettingError as exc:
        log.warning("Panel setting change rejected for %s: %s", key, exc)

    return await _screen_settings(context, lang)


# ---------------------------------------------------------------------------
# Entry point and router
# ---------------------------------------------------------------------------


async def panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """``/panel`` -- the admin menu.

    Authenticated and authorised before anything is read, so a caller who is not
    an admin never learns whether there is anything to see.
    """
    principal = await _require_admin(update, context)
    if principal is None:
        return

    lang = lang_of_update(update, context)
    text, keyboard = await _screen_main(context, lang)
    await update.message.reply_text(text, reply_markup=keyboard)


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route a panel button press.

    Every button in the panel carries the ``p:`` prefix, so this handler only
    fires for the panel and the fallback never sees it. The action is parsed
    before any database work, so a malformed callback is refused cheaply.
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if not data.startswith(f"{PREFIX}:"):
        # Not ours. Leave it to the fallback rather than guessing.
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    param = parts[2] if len(parts) > 2 else None

    principal = await _require_admin_from_callback(update, context)
    if principal is None:
        return

    lang = lang_of_update(update, context)
    app = context.application

    try:
        if action == "m":
            text, keyboard = await _screen_main(context, lang)
        elif action == "d":
            text, keyboard = await _screen_dashboard(context, lang)
        elif action == "t":
            if param is None:
                text, keyboard = await _screen_targets(context, lang)
            elif param.startswith("page:"):
                text, keyboard = await _screen_targets(context, lang, page=int(param.split(":")[1]))
            elif ":" in param:
                target_id, sub = param.split(":", 1)
                if sub == "s":
                    text, keyboard = await _screen_target_scans(context, lang, int(target_id))
                elif sub == "c":
                    text, keyboard = await _screen_target_changes(context, lang, int(target_id))
                else:
                    text, keyboard = await _screen_target(context, lang, int(target_id))
            else:
                text, keyboard = await _screen_target(context, lang, int(param))
        elif action == "s":
            if param is None:
                text, keyboard = await _screen_scans(context, lang)
            elif param.startswith("page:"):
                text, keyboard = await _screen_scans(context, lang, page=int(param.split(":")[1]))
            else:
                text, keyboard = await _screen_scan(context, lang, int(param))
        elif action == "r":
            if param is None:
                text, keyboard = await _screen_rules(context, lang)
            elif param.startswith("page:"):
                text, keyboard = await _screen_rules(context, lang, page=int(param.split(":")[1]))
            elif ":" in param:
                rule_id, sub = param.split(":", 1)
                if sub == "t":
                    text, keyboard = await _toggle_rule(context, lang, int(rule_id), principal)
                else:
                    text, keyboard = await _screen_rules(context, lang)
            else:
                text, keyboard = await _screen_rules(context, lang)
        elif action == "g":
            if param is None:
                text, keyboard = await _screen_settings(context, lang)
            else:
                key, _, value = param.partition(":")
                text, keyboard = await _change_setting(context, lang, key, value, principal)
        elif action == "u":
            if param is None:
                text, keyboard = await _screen_users(context, lang)
            elif param.startswith("page:"):
                text, keyboard = await _screen_users(context, lang, page=int(param.split(":")[1]))
            elif ":" in param:
                user_id, sub = param.split(":", 1)
                if sub == "e":
                    text, keyboard = await _toggle_user(context, lang, int(user_id), principal)
                elif sub == "r":
                    text, keyboard = await _cycle_role(context, lang, int(user_id), principal)
                else:
                    text, keyboard = await _screen_users(context, lang)
            else:
                text, keyboard = await _screen_users(context, lang)
        elif action == "a":
            text, keyboard = await _screen_audit(context, lang)
        elif action == "y":
            text, keyboard = await _screen_system(context, lang)
        else:
            text, keyboard = await _screen_main(context, lang)
    except Exception:
        # A panel that raises is a panel that cannot be trusted. Say so and
        # offer the menu, rather than letting the callback hang with no reply.
        log.exception("Panel screen failed for %r", data)
        from core.i18n import t
        text = t("panel.error", lang)
        keyboard = _main_keyboard(lang)

    await query.edit_message_text(text, reply_markup=keyboard)


async def _require_admin_from_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> object | None:
    """Authorise a button press.

    The message on a callback is the one the button is attached to, which the
    original sender may no longer be able to answer. Authorisation is re-checked
    here rather than trusted from the ``/panel`` that opened it, because a role
    could have been changed in between.
    """
    user = update.effective_user
    if user is None:
        return None

    try:
        from admin.services.users import get_telegram_user

        with database(context).session() as session:
            row = get_telegram_user(session, user.id)
        if row is None or not row.enabled:
            return None
        return row if row.role == "admin" else None
    except Exception:
        return None
