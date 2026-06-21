"""Emergent-managed push notifications helper."""
import logging
from typing import Optional
import httpx
from .settings import EMERGENT_PUSH_KEY, PUSH_BASE_URL

_push_client = httpx.AsyncClient(
    base_url=PUSH_BASE_URL,
    headers={'X-Push-Key': EMERGENT_PUSH_KEY},
    timeout=10.0,
)


async def send_push(
    recipients: list, data: dict, idempotency_key: Optional[str] = None
) -> None:
    if not recipients or 'title' not in data or 'message' not in data:
        return
    payload: dict = {'recipients': recipients, 'data': data}
    if idempotency_key:
        payload['$idempotency_key'] = idempotency_key
    try:
        resp = await _push_client.post('/api/v1/push/trigger', json=payload)
        if resp.status_code >= 400:
            logging.warning(
                f'Push trigger non-2xx: {resp.status_code} {resp.text[:200]}'
            )
    except Exception as e:
        logging.warning(f'Push trigger failed (non-blocking): {e}')
