"""Composition root for the Telegram application.

Wires configuration, database, security, target registry, scan pipeline,
the shared background worker, the scheduler, and runtime settings in one
place. Handlers reach shared objects through ``bot_data`` rather than
importing globals, which keeps the wiring swappable and testable.
"""

from __future__ import annotations

import logging

import httpx
import telegram.error
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

from admin.services import audit as audit_service
from admin.services.settings_store import SettingsStore, seed as seed_settings
from bot.handlers import export as export_handlers
from bot.handlers import ops as ops_handlers
from bot.handlers import scan as scan_handlers
from bot.handlers import schedule as schedule_handlers
from bot.handlers import start as start_handlers
from bot.handlers import status as status_handlers
from bot.handlers import target as target_handlers
from bot.messages import reports
from config.settings import Settings
from core.i18n import normalize_lang, reload_translations
from core.rate_limit import RateLimiter
from core.scan_manager import ScanManager
from core.scheduler import ScanScheduler
from core.structured_logging import bind
from core.target_manager import TargetRegistry
from database.database import Database
from parser.nmap_runner import NmapRunner
from security.authentication import Authenticator
from security.authorization import Authorizer
from workers.scan_worker import ScanJob, ScanWorker

log = logging.getLogger(__name__)

# Connection attempts before httpx gives up on a request.
#
# The bot's link is not reliable: api.telegram.org resolves to an AAAA record and
# the container has no IPv6 route, so bursts of ConnectError are routine here and
# each one used to cost a whole update. Connection failures only -- a retry that
# also re-sent a request which may already have been processed could duplicate a
# Telegram action, and that risk is not worth taking to paper over a flaky link.
_HTTP_CONNECT_RETRIES = 3


async def _deliver(app, chat_id: int, text: str) -> None:
    """Best-effort single-message delivery, splitting if needed."""
    if len(text) <= reports.MAX_MESSAGE:
        await app.bot.send_message(chat_id=chat_id, text=text)
        return
    for start in range(0, len(text), reports.MAX_MESSAGE):
        await app.bot.send_message(
            chat_id=chat_id, text=text[start:start + reports.MAX_MESSAGE]
        )


async def _on_scan_complete(app, job: ScanJob, outcome, error: str | None) -> None:
    """Single delivery path for every scan, manual or scheduled.

    Scheduled jobs carry ``chat_id=0`` because they have no originating
    conversation; those alerts go to the remembered operator chat instead,
    in the operator's own language.
    """
    try:
        lang = job.lang or normalize_lang(
            app.bot_data["settings_store"].get("bot_language")
        )
        db = app.bot_data.get("database")

        # "panel" is routed with "scheduled" because it is the same situation:
        # a scan with no originating conversation, so there is no chat to reply
        # to. Without this the code below would send_message(chat_id=0), which
        # Telegram rejects, and because that send happens *before* the
        # scan.completed audit row the row would never be written either.
        if job.source in ("scheduled", "panel"):
            scheduler = app.bot_data.get("scheduler")
            if outcome is None:
                return
            text = reports.scheduled_alert(outcome, lang)
            if text and scheduler is not None:
                delivered = await scheduler.send_scheduled_alert(text)
                if db is not None:
                    audit_service.record_standalone(
                        db,
                        audit_service.AuditEntry(
                            action="scan.completed",
                            actor_type=audit_service.ACTOR_SYSTEM,
                            target_type="target",
                            target_id=outcome.target_name,
                            details={
                                "scan_id": outcome.scan_id,
                                # The job's real source, not the literal
                                # "scheduled" this branch was matched on.
                                "source": job.source,
                                "changes": len(outcome.changes),
                                "delivered": delivered,
                                "lang": lang,
                            },
                            success=True,
                        ),
                    )
            return

        if error:
            await app.bot.send_message(
                chat_id=job.chat_id, text=reports.scan_failed(lang, error)
            )
            if db is not None:
                audit_service.record_standalone(
                    db,
                    audit_service.AuditEntry(
                        action="scan.failed",
                        actor_id=job.requested_by,
                        actor_username=job.actor_username,
                        actor_type=audit_service.ACTOR_TELEGRAM,
                        target_type="target",
                        target_id=job.target_name,
                        details={"job_id": job.job_id, "error": error},
                        success=False,
                    ),
                )
            return
        if outcome is None:  # pragma: no cover - defensive
            return

        await app.bot.send_message(
            chat_id=job.chat_id, text=reports.scan_completed(outcome, lang)
        )
        change_text = reports.change_report(outcome, lang)
        if change_text:
            await _deliver(app, job.chat_id, change_text)

        if db is not None:
            audit_service.record_standalone(
                db,
                audit_service.AuditEntry(
                    action="scan.completed",
                    actor_id=job.requested_by,
                    actor_username=job.actor_username,
                    actor_type=audit_service.ACTOR_TELEGRAM,
                    target_type="target",
                    target_id=outcome.target_name,
                    details={
                        "scan_id": outcome.scan_id,
                        "profile": outcome.profile,
                        "hosts": outcome.host_count,
                        "services": outcome.service_count,
                        "changes": len(outcome.changes),
                    },
                    success=True,
                ),
            )
    except Exception:  # pragma: no cover - network failure
        log.exception("Failed to deliver scan result to chat %s", job.chat_id)


async def _post_init(app) -> None:
    """Start the worker and scheduler on the running loop."""
    worker: ScanWorker = app.bot_data["scan_worker"]
    scheduler: ScanScheduler | None = app.bot_data.get("scheduler")

    async def handler(job, outcome, error):
        await _on_scan_complete(app, job, outcome, error)

    await worker.start(handler)

    if scheduler is not None:
        async def alert_sink(chat_id: int, text: str) -> None:
            await _deliver(app, chat_id, text)

        await scheduler.start(alert_sink)

    log.info("Background scan worker started")

    # Mini App affordances. The bot object exists from here on, so the
    # menu button cannot be set any earlier. Both calls swallow their own
    # failures: a menu button that could not be set must not stop the bot
    # from answering /scan.
    from bot import miniapp as miniapp_ui

    settings = app.bot_data.get("settings")
    if settings is not None:
        await miniapp_ui.set_menu_button(app.bot, settings)
        await miniapp_ui.publish_commands(app.bot, settings)


async def _post_shutdown(app) -> None:
    """Stop in dependency order so nothing is left mid-flight."""
    scheduler: ScanScheduler | None = app.bot_data.get("scheduler")
    if scheduler is not None:
        await scheduler.stop()
    await app.bot_data["scan_worker"].stop()
    database: Database = app.bot_data.get("database")
    if database is not None:
        database.dispose()
    log.info("Shutdown complete")


async def on_unhandled_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Catch-all for exceptions no handler anticipated.

    Without this, python-telegram-bot logs the traceback and drops it â€” the
    user sees nothing at all, which is indistinguishable from an
    unresponsive bot.
    """
    error = context.error

    # A dropped connection is not a defect in a handler.
    #
    # This was logged as "Unhandled exception while processing update" with a
    # full traceback, which is wrong -- the cause was the link, not the code --
    # and actively misleading, because it points the reader at the handlers.
    if isinstance(error, (telegram.error.NetworkError, telegram.error.TimedOut)):
        log.warning(
            "Transient Telegram network failure while processing an update: "
            "%s: %s",
            type(error).__name__,
            error,
        )
    else:
        log.exception("Unhandled exception while processing update", exc_info=error)

    message = getattr(update, "effective_message", None)
    if message is None:
        update_obj = getattr(update, "effective_update", None)
        message = getattr(update_obj, "message", None)
    if message is None:
        return

    lang = "fa"
    try:
        from bot.handlers.common import lang_of_update

        lang = lang_of_update(update, context)
    except Exception:  # pragma: no cover - never mask the original error
        pass

    try:
        await message.reply_text(
            reports.unhandled_error(
                lang, type(error).__name__ if error else "unknown error"
            )
        )
    except Exception:  # pragma: no cover - reply itself failed
        log.exception("Could not deliver error notice to user")


def build_application(settings: Settings):
    """Construct and configure the ``Application``."""
    database = Database(settings.database_url)
    database.create_all()

    # Settings rows must exist before anything reads them.
    with database.session() as session:
        seed_settings(session, actor="bootstrap")
    settings_store = SettingsStore(database)

    # Make sure every allow-listed ID is visible in the panel before
    # anyone has run /start.
    try:
        from admin.services import users as user_service

        user_service.sync_from_allowed(database, settings.allowed_user_ids)
    except Exception:
        log.warning("Could not sync allowed users", exc_info=True)

    reload_translations()
    bot_language = normalize_lang(settings_store.get("bot_language"))

    authenticator = Authenticator(settings)
    authorizer = Authorizer(settings.allowed_cidrs)
    registry = TargetRegistry(database, authorizer)
    runner = NmapRunner(settings.nmap_binary, settings.scan_timeout_seconds)
    scan_manager = ScanManager(database, runner)
    worker = ScanWorker(
        scan_manager=scan_manager,
        max_concurrency=settings.max_concurrent_scans,
    )
    rate_limiter = RateLimiter(settings.rate_limit_seconds)
    scheduler = ScanScheduler(
        database,
        worker,
        enabled=settings.schedule_enabled,
        default_interval_hours=settings.schedule_interval_hours,
        default_profile=settings.schedule_profile,
        retention_days=settings.retention_days,
        retention_max_per_target=settings.retention_max_scans_per_target,
    )

    # A transport-level retry for connection failures, applied to both the
    # request client and the long-polling client, since either can be the one
    # holding a dropped connection.
    #
    # These must be `HTTPXRequest`, not a bare `httpx.AsyncClient`.
    # `ApplicationBuilder.request()` expects a `BaseRequest`, which owns an
    # initialize()/shutdown() lifecycle the Application calls on both clients;
    # an httpx client has neither, so the bot died at startup with
    # "'AsyncClient' object has no attribute 'initialize'" before it ever
    # reached getUpdates.
    #
    # `httpx_kwargs` is forwarded into the httpx.AsyncClient that HTTPXRequest
    # builds, so the retrying transport still gets through.
    #
    # `AsyncHTTPTransport`, not `HTTPTransport`. PTB drives an AsyncClient, and
    # handing it the synchronous transport gets as far as the first request and
    # then fails with "'HTTPTransport' object has no attribute
    # 'handle_async_request'" -- which is a different crash from the one that
    # got us here, and just as total.
    #
    # Where the retry count lives is also worth knowing: HTTPTransport passes
    # `retries` straight to its ConnectionPool, so it is on `transport._pool`
    # and not anywhere obvious. Two probes reported a correctly configured
    # client as unconfigured before that was found.
    api_client = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        httpx_kwargs={
            "transport": httpx.AsyncHTTPTransport(retries=_HTTP_CONNECT_RETRIES),
        },
    )
    updates_client = HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        httpx_kwargs={
            "transport": httpx.AsyncHTTPTransport(retries=_HTTP_CONNECT_RETRIES),
        },
    )

    app = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .request(api_client)
        .get_updates_request(updates_client)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    app.bot_data["settings"] = settings
    app.bot_data["database"] = database
    app.bot_data["settings_store"] = settings_store
    app.bot_data["authenticator"] = authenticator
    app.bot_data["authorizer"] = authorizer
    app.bot_data["target_registry"] = registry
    app.bot_data["scan_manager"] = scan_manager
    app.bot_data["scan_worker"] = worker
    app.bot_data["rate_limiter"] = rate_limiter
    app.bot_data["scheduler"] = scheduler
    app.bot_data["bot_language"] = bot_language

    app.add_handler(CommandHandler("start", start_handlers.start))
    app.add_handler(CommandHandler("app", start_handlers.open_app))
    app.add_handler(CommandHandler("help", start_handlers.help_command))
    app.add_handler(CommandHandler("tlang", start_handlers.set_language))
    app.add_handler(CommandHandler("scan", scan_handlers.scan))
    app.add_handler(CommandHandler("addtarget", target_handlers.addtarget))
    app.add_handler(CommandHandler("targets", target_handlers.listtargets))
    app.add_handler(CommandHandler("deltarget", target_handlers.deltarget))
    app.add_handler(CommandHandler("purge", target_handlers.purge))
    app.add_handler(CommandHandler("scans", status_handlers.history))
    app.add_handler(CommandHandler("status", status_handlers.status))
    app.add_handler(CommandHandler("health", ops_handlers.health))
    app.add_handler(CommandHandler("cleanup", ops_handlers.cleanup))
    app.add_handler(CommandHandler("export", export_handlers.export))
    app.add_handler(CommandHandler("report", export_handlers.report))
    app.add_handler(CommandHandler("schedule", schedule_handlers.schedule))

    # Must be registered last: it is the fallback for every other handler.
    app.add_error_handler(on_unhandled_error)

    return app