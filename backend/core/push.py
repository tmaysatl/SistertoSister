"""Emergent push-notification integration client.

New file (additive). `server.py` and `routers/shifts.py` already import
`send_push` / `_push_client` from here -- that import had no file to resolve
to, which raised `ModuleNotFoundError` the instant either module loaded and
crashed the entire backend before it could even start serving requests
(independent of Mongo/Supabase config -- this is a pure missing-file bug,
not a configuration issue). See server.py's defensive try/except around
this import for the belt-and-suspenders side of that fix; this file is the
real implementation so push notifications actually work again too.

Talks to Emergent's hosted push-relay service at PUSH_BASE_URL using
EMERGENT_PUSH_KEY (both already defined in core.settings, provisioned by the
Emergent platform the same way EMERGENT_LLM_KEY is). If the key is unset
(default placeholder value), every call below is a local no-op -- logged,
never attempted -- rather than a network round-trip that's guaranteed to
fail. Every call site in the app already wraps `send_push()` in
try/except and treats a failure as non-fatal, so raising here (e.g. a real
network error) is safe and requires no other code to change.

NOTE on the send endpoint: only `/api/v1/push/users/register` is a
confirmed-real path (server.py's existing `POST /api/register-push` already
uses it successfully). SEND_PATH below is inferred from that same naming
convention, not confirmed against Emergent's own docs (no public
documentation for this integration was found). If push notifications don't
arrive on-device once a real EMERGENT_PUSH_KEY is configured, check
Emergent's integration dashboard/support for the actual send endpoint and
adjust SEND_PATH -- nothing else in the app needs to change.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from core.settings import EMERGENT_PUSH_KEY, PUSH_BASE_URL

logger = logging.getLogger(__name__)

_UNCONFIGURED_KEYS = {"", "placeholder"}
SEND_PATH = "/api/v1/push/notifications/send"  # see NOTE above -- inferred, not confirmed

# Long-lived client (mirrors core.db's module-level Mongo client) -- cheap to
# construct even when unconfigured, so callers never need a None-check.
_push_client = httpx.AsyncClient(
    base_url=PUSH_BASE_URL,
    headers={"Authorization": f"Bearer {EMERGENT_PUSH_KEY}"},
    timeout=10.0,
)


def _configured() -> bool:
    return EMERGENT_PUSH_KEY not in _UNCONFIGURED_KEYS


async def send_push(recipients: List[str], data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Send a push notification to one or more user ids. `data` is a small
    dict of display fields -- every existing caller passes
    {"title", "message", "action_url"}.

    Best-effort by design (matches every call site's own try/except):
    returns None if there was nothing to do (no recipients) or nothing
    configured to send with (no real EMERGENT_PUSH_KEY) -- both logged, not
    raised. A real send attempt that fails raises, same as any other httpx
    call, for the caller's existing try/except to catch and log.
    """
    if not recipients:
        return None
    if not _configured():
        logger.info(
            "send_push: EMERGENT_PUSH_KEY not configured -- skipping (recipients=%s, title=%r)",
            recipients, data.get("title"),
        )
        return None
    resp = await _push_client.post(SEND_PATH, json={"recipients": recipients, **data})
    if resp.status_code == 401:
        logger.warning("send_push: 401 from push provider -- EMERGENT_PUSH_KEY missing or invalid")
    elif resp.status_code == 404:
        logger.warning(
            "send_push: 404 from push provider at %s -- SEND_PATH may be wrong, "
            "see the NOTE in core/push.py", SEND_PATH,
        )
    resp.raise_for_status()
    try:
        return resp.json()
    except Exception:
        return None
