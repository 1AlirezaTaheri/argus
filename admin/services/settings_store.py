"""Database-backed runtime settings.

``config/settings.py`` reads the environment once at startup. These
settings layer on top so an operator can change a limit from the web panel
without editing ``.env`` and restarting the bot.

Precedence: database value → environment value → compiled-in default.

The bot re-reads on demand (with a short TTL) rather than subscribing to
notifications, which would need a new external service.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.profiles import PROFILES, profile_names
from database.models import SystemSetting

log = logging.getLogger(__name__)


def _profile_choice_kind() -> str:
    """The `choice:` kind for the profile settings, built from the registry.

    Each option carries the profile's description and is flagged when its port
    set is not knowable, because that decides whether an `allow_port` /
    `deny_port` rule can apply to a scan run with it -- the question this
    whole change exists to answer, and one the name alone cannot.

    Only the first sentence of the description is used, and commas are dropped:
    the kind is comma-separated, so punctuation in an operator-facing label
    would otherwise break the setting rather than decorate it.
    """
    options = []
    for name in profile_names():
        profile = PROFILES[name]
        label = profile.description.split(".")[0].replace(",", "")
        if not profile.describes_ports():
            label += " - port rules cannot apply"
        options.append(f"{name}:{label}")
    # Entries are separated by commas -- that is what _parse_choice splits on.
    # `|` separates a label from its longer description, so it cannot also be
    # doing this job.
    return "choice:" + ",".join(options)


# Built once at import: PROFILES is a module-level constant, so this cannot go
# stale without the module itself being reloaded.
PROFILE_CHOICES = _profile_choice_kind()

# key -> (default, kind, description)
# "kind" drives coercion and validation on write.
SETTING_SPECS: dict[str, tuple[Any, str, str]] = {
    "bot_language": ("fa", "choice:fa,en", "Language used by the Telegram bot"),
    "default_profile": ("service", PROFILE_CHOICES, "Profile used when none given"),
    "rate_limit_seconds": (30, "int:1:3600", "Min seconds between scans of one target"),
    "retention_days": (30, "int:1:3650", "Delete scans older than this"),
    "retention_max_scans_per_target": (100, "int:1:10000", "Cap on scans kept per target"),
    "schedule_enabled": (False, "bool", "Master switch for scheduled monitoring"),
    "schedule_interval_hours": (6, "int:1:720", "Default schedule interval"),
    "schedule_profile": ("service", PROFILE_CHOICES, "Profile for scheduled scans"),
    "scan_timeout_seconds": (60, "int:5:3600", "Per-scan timeout"),
    "max_concurrent_scans": (2, "int:1:16", "Worker concurrency"),
    "export_max_scans": (20, "int:1:500", "Scans per /export"),
    "allowed_cidrs": ("", "str", "Comma-separated CIDRs scans may target"),
    "rules_enabled": (
        True,
        "bool",
        "Evaluate policy rules before every scan",
    ),
    "rules_default_action": (
        "allow",
        "choice:allow,deny",
        "Reserved for a future global gate; informational for now",
    ),
    "rules_max_hits_per_day": (
        10000,
        "int:100:1000000",
        "Stop recording rule hits past this many per day",
    ),
}


class SettingError(ValueError):
    """Raised when a value fails validation; message is user-facing."""


def seed(session: Session, actor: str | None = None) -> int:
    """Insert any missing settings at their defaults."""
    existing = set(session.scalars(select(SystemSetting.key)))
    added = 0
    for key, (default, _kind, _desc) in SETTING_SPECS.items():
        if key in existing:
            continue
        session.add(
            SystemSetting(key=key, value=_stringify(default), updated_by=actor)
        )
        added += 1
    if added:
        session.flush()
    return added


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _parse_choice(kind: str) -> list[tuple[str, str, str]]:
    """Split a `choice:` kind into (value, label, description) triples.

    Two forms are accepted:

        choice:quick,service,deep
            Bare names. The label falls back to the value, so the panel has
            something to render without every setting having to spell it out.

        choice:quick:Fast scan|top 100 ports|service:Top 100 with versions
            An explicit label, and optionally a longer description after a
            second pipe.

    The value is whatever precedes the first colon, which is what
    :func:`coerce` compares against the incoming text. Entries are split on
    commas, so a label or description must not contain one -- which is why the
    profile descriptions are truncated to their first sentence here rather than
    trusted: an operator-facing string that can silently break a validator is
    not worth the punctuation.

    Splitting on the *first* colon is what keeps `default_profile`'s existing
    behaviour intact: `choice:` itself contains a colon, and so does
    `choice:allow,deny`, neither of which is part of an entry.
    """
    body = kind.split(":", 1)[1]
    parsed: list[tuple[str, str, str]] = []
    for entry in body.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        value = parts[0].strip()
        label = parts[1].strip() if len(parts) > 1 and parts[1].strip() else value
        description = ""
        if "|" in label:
            label, _, description = label.partition("|")
            label = label.strip()
            description = description.strip()
        parsed.append((value, label, description))
    return parsed


def coerce(key: str, raw: Any) -> Any:
    """Validate and convert ``raw`` for ``key``.

    Raises :class:`SettingError` with a message safe to show an admin.
    """
    if key not in SETTING_SPECS:
        raise SettingError(f"Unknown setting: {key}")

    _default, kind, _desc = SETTING_SPECS[key]

    if isinstance(raw, bool):
        # Already a bool (e.g. from JSON) — accept, but honour the kind.
        if kind == "bool":
            return raw
        raw = "true" if raw else "false"

    text = str(raw).strip()

    if kind == "bool":
        if text.lower() in ("1", "true", "yes", "on"):
            return True
        if text.lower() in ("0", "false", "no", "off"):
            return False
        raise SettingError(f"{key} must be a boolean, got {text!r}")

    if kind.startswith("int:"):
        _kind, low, high = kind.split(":")
        try:
            value = int(text)
        except ValueError as exc:
            raise SettingError(f"{key} must be an integer, got {text!r}") from exc
        if not (int(low) <= value <= int(high)):
            raise SettingError(
                f"{key} must be between {low} and {high}, got {value}"
            )
        return value

    if kind.startswith("choice:"):
        allowed = [entry for entry, _, _ in _parse_choice(kind)]
        if text not in allowed:
            raise SettingError(
                f"{key} must be one of {', '.join(allowed)}, got {text!r}"
            )
        return text

    return text


@dataclass
class SettingsStore:
    """Reads settings from the database with a short-lived cache."""

    database: Any
    ttl_seconds: float = 5.0
    _cache: dict[str, Any] = field(default_factory=dict)
    _cached_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self, key: str) -> Any:
        """Return one setting, falling back to its compiled default."""
        with self._lock:
            self._ensure_loaded()
            if key in self._cache:
                return self._cache[key]
        return SETTING_SPECS.get(key, (None, "str", ""))[0]

    def all(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_loaded()
            return dict(self._cache)

    def _ensure_loaded(self) -> None:
        """Load when the cache is empty *or* stale.

        Two distinct conditions: an empty cache must always be populated,
        and a populated one must be refreshed once the TTL lapses.
        """
        if not self._cache or not self._fresh():
            self._load_locked()

    def set(self, session: Session, key: str, value: Any, actor: str | None) -> Any:
        """Validate, persist and invalidate the cache."""
        coerced = coerce(key, value)
        row = session.scalar(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        if row is None:
            row = SystemSetting(key=key, value="")
            session.add(row)
        row.value = _stringify(coerced)
        row.updated_by = actor
        session.flush()
        self.invalidate()
        return coerced

    def set_many(
        self, session: Session, values: dict[str, Any], actor: str | None
    ) -> dict[str, Any]:
        """Apply several settings atomically.

        Validation happens first for every key so a bad value in the batch
        rejects the whole request rather than applying it halfway.
        """
        validated = {key: coerce(key, value) for key, value in values.items()}
        for key, coerced in validated.items():
            row = session.scalar(
                select(SystemSetting).where(SystemSetting.key == key)
            )
            if row is None:
                row = SystemSetting(key=key, value="")
                session.add(row)
            row.value = _stringify(coerced)
            row.updated_by = actor
        session.flush()
        self.invalidate()
        return validated

    def invalidate(self) -> None:
        with self._lock:
            self._cached_at = 0.0
            self._cache.clear()

    def _fresh(self) -> bool:
        """True when the cache is populated and within its TTL."""
        if not self._cache:
            return False
        return (time.monotonic() - self._cached_at) < self.ttl_seconds

    def _load_locked(self) -> None:
        try:
            with self.database.session() as session:
                rows = {r.key: r.value for r in session.scalars(select(SystemSetting))}
        except Exception:
            # A database blip must not stop the bot answering messages;
            # fall back to compiled defaults instead.
            log.warning("Could not read system settings; using defaults", exc_info=True)
            rows = {}

        resolved: dict[str, Any] = {}
        for key, (default, kind, _desc) in SETTING_SPECS.items():
            raw = rows.get(key)
            if raw is None:
                resolved[key] = default
                continue
            try:
                resolved[key] = coerce(key, raw)
            except SettingError:
                # A stored value that no longer validates (profile renamed,
                # range tightened) must not poison every read.
                log.warning("Stored value for %s is invalid; using default", key)
                resolved[key] = default

        self._cache = resolved
        self._cached_at = time.monotonic()