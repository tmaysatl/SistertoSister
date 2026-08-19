"""Background scheduled jobs (APScheduler, asyncio-native).

New file (additive). Runs recurring, non-request-triggered work -- today,
just the expiration-reminder sweep (see run_expiration_reminder_sweep()).
Kept separate from server.py so it's easy to find/extend, and deliberately
does NOT depend on routers/ms_graph.py (see server.py's defensive import
guard around that module for why -- it doesn't exist in this checkout).

start_scheduler()/stop_scheduler() names mirror the ms_graph router's own
(planned) scheduler API on purpose, in case that module gets built out
later and wants to register jobs on the same scheduler.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.db import db
from core.settings import MS_BINDER_TZ
try:
    from core.push import send_push
except Exception:  # pragma: no cover - see server.py's matching guard
    send_push = None

logger = logging.getLogger(__name__)

# Days-before-expiry checkpoints a reminder fires at -- each fires at most
# ONCE ever per document (see expiration_reminders_sent / _already_sent),
# not daily, so caregivers aren't nagged every day for a month straight.
# "expired" is a fifth, one-time checkpoint once expires_at is in the past.
_THRESHOLDS_DAYS = [30, 14, 7, 1]

_scheduler: Optional[AsyncIOScheduler] = None


async def _already_sent(document_id: str, threshold: str) -> bool:
    return bool(await db.expiration_reminders_sent.find_one(
        {"document_id": document_id, "threshold": threshold}, {"_id": 1}
    ))


async def _mark_sent(document_id: str, threshold: str, extra: Dict[str, Any]) -> None:
    await db.expiration_reminders_sent.insert_one({
        "document_id": document_id,
        "threshold": threshold,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        **extra,
    })


def _parse_expires_at(raw: Any) -> Optional[datetime]:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def run_expiration_reminder_sweep() -> Dict[str, int]:
    """Check every credential document's expires_at against fixed
    day-before-expiry checkpoints (30/14/7/1, plus a one-time "expired"
    checkpoint), push a reminder to the owning caregiver the first time
    each checkpoint is crossed, then push one digest to admins if anything
    fired (never one push per item -- that would spam admins on a bad
    week). Runs on a daily schedule (see start_scheduler) and can also be
    triggered on demand via POST /admin/run-expiration-reminders.

    Never raises: both callers want one bad row logged and skipped, not
    the whole sweep killed, and the scheduler itself would silently stop
    running future jobs if a job function raised.
    """
    fired: List[Dict[str, Any]] = []
    try:
        cutoff = (datetime.now(timezone.utc) + timedelta(days=max(_THRESHOLDS_DAYS) + 1)).isoformat()
        cursor = db.documents.find(
            {"category": "credential", "expires_at": {"$ne": None, "$lte": cutoff}, "owner_id": {"$ne": None}},
            {"_id": 0, "id": 1, "title": 1, "owner_id": 1, "expires_at": 1},
        )
        async for doc in cursor:
            try:
                exp_dt = _parse_expires_at(doc.get("expires_at"))
                if exp_dt is None:
                    continue
                days_left = (exp_dt.date() - datetime.now(timezone.utc).date()).days

                threshold: Optional[str] = None
                if days_left < 0:
                    threshold = "expired"
                elif days_left in _THRESHOLDS_DAYS:
                    threshold = str(days_left)
                if threshold is None:
                    continue
                if await _already_sent(doc["id"], threshold):
                    continue

                title = doc.get("title") or "A credential"
                message = (
                    f"{title} has expired -- please renew as soon as possible."
                    if threshold == "expired" else
                    f"{title} expires in {days_left} day{'s' if days_left != 1 else ''}."
                )
                # Only mark a checkpoint as sent if it was actually delivered
                # (or there was nothing configured to deliver it with) --
                # a transient push failure should retry on tomorrow's sweep
                # rather than silently never notifying anyone for this
                # checkpoint. Push failures self-heal across checkpoints
                # too (a missed 30-day reminder still leaves 14/7/1 ahead).
                push_delivered = True
                if send_push is not None:
                    try:
                        await send_push(
                            recipients=[doc["owner_id"]],
                            data={"title": "Credential expiring", "message": message, "action_url": "/documents"},
                        )
                    except Exception as e:
                        logger.warning("expiration sweep: push to caregiver failed for doc %s: %s", doc.get("id"), e)
                        push_delivered = False
                if push_delivered:
                    await _mark_sent(doc["id"], threshold, {"owner_id": doc["owner_id"], "title": title})
                    fired.append({"document_id": doc["id"], "title": title, "owner_id": doc["owner_id"], "threshold": threshold})
            except Exception as e:
                logger.warning("expiration sweep: skipping doc %s due to error: %s", doc.get("id"), e)

        if fired and send_push is not None:
            try:
                admin_ids = [u["id"] async for u in db.users.find({"role": "admin"}, {"_id": 0, "id": 1})]
                if admin_ids:
                    names = ", ".join(f["title"] for f in fired[:3])
                    more = f" and {len(fired) - 3} more" if len(fired) > 3 else ""
                    await send_push(
                        recipients=admin_ids,
                        data={
                            "title": "Credential expiration digest",
                            "message": f"{len(fired)} credential reminder(s) sent today: {names}{more}.",
                            "action_url": "/documents",
                        },
                    )
            except Exception as e:
                logger.warning("expiration sweep: admin digest push failed: %s", e)
    except Exception as e:
        logger.warning("expiration sweep: sweep failed entirely: %s", e)
    return {"reminders_sent": len(fired)}


def start_scheduler() -> None:
    """Idempotent -- safe to call more than once (only the first call does
    anything). Registers the daily sweep plus one immediate run so a
    checkpoint can't be missed just because the process restarted shortly
    after the scheduled hour, and so the feature has visible effect
    without waiting up to 24h after first deploy. Both are fully deduped
    against expiration_reminders_sent, so neither can ever double-send.
    """
    global _scheduler
    if _scheduler is not None:
        return
    # Reuses MS_BINDER_TZ -- already the app's one "what timezone is the
    # agency in" setting (added for the Microsoft Graph audit-binder
    # export) -- rather than inventing a second timezone setting just for
    # this. Falls back to UTC if that's ever unset.
    _scheduler = AsyncIOScheduler(timezone=MS_BINDER_TZ or "UTC")
    _scheduler.add_job(
        run_expiration_reminder_sweep,
        CronTrigger(hour=8, minute=0),
        id="expiration_reminder_sweep",
        replace_existing=True,
    )
    _scheduler.add_job(run_expiration_reminder_sweep, id="expiration_reminder_sweep_startup")
    _scheduler.start()
    logger.info("expiration reminder scheduler started (daily 08:00 %s + one run now)", MS_BINDER_TZ or "UTC")


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
