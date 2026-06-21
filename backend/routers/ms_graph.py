"""Microsoft Graph / Outlook / OneDrive OAuth + monthly Audit Binder export.

Mounted under `/api` by server.py. The monthly scheduler is started/stopped
via `start_scheduler()` / `stop_scheduler()` called from server.py startup
events.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import msal
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from core.db import db
from core.security import require_admin
from core.settings import (
    MS_AUTHORITY, MS_BINDER_FOLDER, MS_BINDER_TZ, MS_CLIENT_ID,
    MS_CLIENT_SECRET, MS_REDIRECT_URI, MS_SCOPES, MS_TENANT_ID,
)
from models import UserPublic, now_iso

router = APIRouter(tags=['ms-graph'])
logger = logging.getLogger(__name__)

MS_CONNECTION_DOC_ID = 'ms_connection'


def _ms_configured() -> bool:
    return all([MS_TENANT_ID, MS_CLIENT_ID, MS_CLIENT_SECRET, MS_REDIRECT_URI])


def _ms_msal_app():
    return msal.ConfidentialClientApplication(
        MS_CLIENT_ID, authority=MS_AUTHORITY, client_credential=MS_CLIENT_SECRET,
    )


async def _ms_save_tokens(
    token_result: dict,
    user_email: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    existing = await db.integrations.find_one(
        {'_id': MS_CONNECTION_DOC_ID}
    ) or {}
    update = {
        '_id': MS_CONNECTION_DOC_ID,
        'provider': 'microsoft',
        'refresh_token': (
            token_result.get('refresh_token') or existing.get('refresh_token')
        ),
        'scope': token_result.get('scope', existing.get('scope')),
        'updated_at': now_iso(),
        'connected_email': user_email or existing.get('connected_email'),
    }
    if extra:
        update.update(extra)
    await db.integrations.update_one(
        {'_id': MS_CONNECTION_DOC_ID}, {'$set': update}, upsert=True,
    )


async def _ms_get_access_token() -> Optional[str]:
    doc = await db.integrations.find_one({'_id': MS_CONNECTION_DOC_ID})
    if not doc or not doc.get('refresh_token'):
        return None
    res = _ms_msal_app().acquire_token_by_refresh_token(
        doc['refresh_token'], scopes=MS_SCOPES,
    )
    if 'access_token' not in res:
        logger.error(f"MS refresh failed: {res.get('error_description', res)}")
        return None
    if res.get('refresh_token'):
        await _ms_save_tokens(res)
    return res['access_token']


async def _ms_upload_to_onedrive(
    access_token: str, folder: str, filename: str, content: bytes,
) -> dict:
    headers = {'Authorization': f'Bearer {access_token}'}
    path = f'/me/drive/root:/{folder}/{filename}:/createUploadSession'
    async with httpx.AsyncClient(timeout=120) as cx:
        sess = await cx.post(
            f'https://graph.microsoft.com/v1.0{path}',
            headers={**headers, 'Content-Type': 'application/json'},
            json={'item': {
                '@microsoft.graph.conflictBehavior': 'replace',
                'name': filename,
            }},
        )
        sess.raise_for_status()
        upload_url = sess.json()['uploadUrl']
        chunk = 5 * 1024 * 1024
        total = len(content)
        i = 0
        result = None
        while i < total:
            end = min(i + chunk, total) - 1
            part = content[i:end + 1]
            r = await cx.put(
                upload_url,
                headers={
                    'Content-Range': f'bytes {i}-{end}/{total}',
                    'Content-Length': str(len(part)),
                },
                content=part,
            )
            if r.status_code not in (200, 201, 202):
                raise RuntimeError(
                    f'OneDrive chunk failed {r.status_code}: {r.text}'
                )
            if r.status_code in (200, 201):
                result = r.json()
            i = end + 1
        return result or {}


async def _ms_send_outlook_mail(
    access_token: str, to: List[str], subject: str, body_html: str,
    attachment: Optional[dict] = None,
) -> None:
    msg: dict = {
        'subject': subject,
        'body': {'contentType': 'HTML', 'content': body_html},
        'toRecipients': [{'emailAddress': {'address': a}} for a in to],
    }
    if attachment:
        msg['attachments'] = [{
            '@odata.type': '#microsoft.graph.fileAttachment',
            'name': attachment['name'],
            'contentBytes': attachment['content_b64'],
        }]
    async with httpx.AsyncClient(timeout=60) as cx:
        r = await cx.post(
            'https://graph.microsoft.com/v1.0/me/sendMail',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            json={'message': msg, 'saveToSentItems': True},
        )
        if r.status_code not in (200, 202):
            raise RuntimeError(
                f'Outlook sendMail failed: {r.status_code} {r.text}'
            )


async def _ms_create_share_link(
    access_token: str, item_id: str,
) -> Optional[str]:
    async with httpx.AsyncClient(timeout=30) as cx:
        r = await cx.post(
            f'https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/createLink',
            headers={
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
            },
            json={'type': 'view', 'scope': 'organization'},
        )
        if r.status_code in (200, 201):
            return r.json().get('link', {}).get('webUrl')
    return None


async def _ms_run_monthly_export() -> dict:
    """Build the audit binder, upload to OneDrive, optionally email."""
    doc = await db.integrations.find_one({'_id': MS_CONNECTION_DOC_ID})
    if not doc or not doc.get('refresh_token'):
        logger.info('MS not connected \u2014 skipping monthly export')
        return {'ok': False, 'reason': 'not_connected'}
    access = await _ms_get_access_token()
    if not access:
        return {'ok': False, 'reason': 'refresh_failed'}
    # Lazy import to avoid circular dep with server.py
    from server import _build_audit_binder_bytes
    pdf_bytes = await _build_audit_binder_bytes()
    stamp = datetime.now(timezone.utc).strftime('%Y-%m')
    filename = f'SisterToSister_AuditBinder_{stamp}.pdf'
    item = await _ms_upload_to_onedrive(
        access, MS_BINDER_FOLDER, filename, pdf_bytes,
    )
    web_url = item.get('webUrl')
    share_url: Optional[str] = None
    if item.get('id'):
        try:
            share_url = await _ms_create_share_link(access, item['id'])
        except Exception as e:
            logger.warning(f'MS share link failed: {e}')
    to_list = (doc.get('email_to') or '').strip()
    if to_list:
        addrs = [a.strip() for a in to_list.split(',') if a.strip()]
        try:
            await _ms_send_outlook_mail(
                access, addrs,
                subject=f'Sister to Sister \u2014 Audit Binder ({stamp})',
                body_html=(
                    f'<p>Your monthly audit binder is ready.</p>'
                    f"<p><a href='{share_url or web_url}'>Open in OneDrive</a></p>"
                    f'<p>File: <strong>{filename}</strong></p>'
                ),
            )
        except Exception as e:
            logger.warning(f'MS sendMail failed: {e}')
    info = {
        'ok': True,
        'filename': filename,
        'size_bytes': len(pdf_bytes),
        'onedrive_web_url': web_url,
        'share_url': share_url,
        'ran_at': now_iso(),
    }
    await _ms_save_tokens({}, extra={'last_export': info})
    return info


class MsEmailReq(BaseModel):
    email_to: Optional[str] = None


@router.get('/ms/status')
async def ms_status(current: UserPublic = Depends(require_admin)):
    doc = await db.integrations.find_one(
        {'_id': MS_CONNECTION_DOC_ID}, {'_id': 0, 'refresh_token': 0},
    ) or {}
    return {
        'configured': _ms_configured(),
        'connected': bool(doc and doc.get('connected_email')),
        'connected_email': doc.get('connected_email') if doc else None,
        'last_export': (doc or {}).get('last_export'),
        'email_to': (doc or {}).get('email_to'),
        'schedule': (
            'Runs on the 1st of every month at 02:00 ' + MS_BINDER_TZ
        ),
    }


@router.get('/ms/auth-url')
async def ms_auth_url(current: UserPublic = Depends(require_admin)):
    if not _ms_configured():
        raise HTTPException(
            503, 'Microsoft integration not configured on server.'
        )
    url = _ms_msal_app().get_authorization_request_url(
        scopes=MS_SCOPES,
        redirect_uri=MS_REDIRECT_URI,
        prompt='consent',
        state=current.id,
    )
    return {'url': url}


@router.get('/ms/callback')
async def ms_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    state: Optional[str] = None,
):
    def _html_page(ok: bool, title: str, message: str) -> Response:
        color = '#285C42' if ok else '#B33A3A'
        icon = '\u2705' if ok else '\u274C'
        html = f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{title}</title>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<style>body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;
background:#F9F9F8;color:#1d2421;text-align:center;padding:48px 24px}}
.card{{max-width:420px;margin:24px auto;background:#fff;border-radius:18px;
padding:28px;border:1px solid #E3E5E3;box-shadow:0 4px 24px rgba(32,66,49,.08)}}
h1{{color:{color};margin:.2em 0}}p{{color:#3d4541;line-height:1.5}}
.btn{{display:inline-block;margin-top:14px;background:#204231;color:#fff;
padding:12px 22px;border-radius:999px;font-weight:700;text-decoration:none}}
</style></head><body><div class='card'><div style='font-size:48px'>{icon}</div>
<h1>{title}</h1><p>{message}</p>
<a class='btn' href='/'>Return to app</a></div></body></html>"""
        return Response(
            content=html, media_type='text/html',
            status_code=200 if ok else 400,
        )

    if error:
        return _html_page(
            False, 'Microsoft sign-in cancelled', error_description or error,
        )
    if not code:
        return _html_page(
            False, 'Missing code',
            'Microsoft did not return an authorization code.',
        )
    try:
        result = _ms_msal_app().acquire_token_by_authorization_code(
            code, scopes=MS_SCOPES, redirect_uri=MS_REDIRECT_URI,
        )
        if 'access_token' not in result:
            return _html_page(
                False, 'Token exchange failed',
                result.get('error_description', 'Unknown error'),
            )
        user_email = None
        try:
            async with httpx.AsyncClient(timeout=30) as cx:
                me = await cx.get(
                    'https://graph.microsoft.com/v1.0/me',
                    headers={
                        'Authorization': f"Bearer {result['access_token']}"
                    },
                )
                if me.status_code == 200:
                    j = me.json()
                    user_email = j.get('mail') or j.get('userPrincipalName')
        except Exception as e:
            logger.warning(f'MS me lookup failed: {e}')
        await _ms_save_tokens(result, user_email=user_email)
        return _html_page(
            True, 'Microsoft 365 Connected',
            f"Signed in as {user_email or 'your Microsoft account'}. "
            f'Audit binders will save to your OneDrive folder '
            f'\u201c{MS_BINDER_FOLDER}\u201d on the 1st of every month.',
        )
    except Exception as e:
        logger.exception('MS callback error')
        return _html_page(False, 'Sign-in error', str(e))


@router.post('/ms/disconnect')
async def ms_disconnect(current: UserPublic = Depends(require_admin)):
    await db.integrations.delete_one({'_id': MS_CONNECTION_DOC_ID})
    return {'ok': True}


@router.post('/ms/email-recipients')
async def ms_email_recipients(
    req: MsEmailReq, current: UserPublic = Depends(require_admin),
):
    await db.integrations.update_one(
        {'_id': MS_CONNECTION_DOC_ID},
        {'$set': {
            'email_to': (req.email_to or '').strip(),
            'updated_at': now_iso(),
        }},
        upsert=True,
    )
    return {'ok': True}


@router.post('/ms/export-now')
async def ms_export_now(current: UserPublic = Depends(require_admin)):
    res = await _ms_run_monthly_export()
    if not res.get('ok'):
        reason = res.get('reason', 'Export failed')
        msg = {
            'not_connected': 'Please connect Microsoft 365 first.',
            'refresh_failed': 'Microsoft session expired. Please reconnect.',
        }.get(reason, reason)
        raise HTTPException(400, msg)
    return res


# --- Scheduler control (called by server.py startup/shutdown) ---
_SCHEDULER: Optional[AsyncIOScheduler] = None


def start_scheduler() -> None:
    global _SCHEDULER
    if _SCHEDULER is not None:
        return
    try:
        _SCHEDULER = AsyncIOScheduler(timezone=MS_BINDER_TZ)
        _SCHEDULER.add_job(
            _ms_run_monthly_export,
            CronTrigger(day=1, hour=2, minute=0, timezone=MS_BINDER_TZ),
            id='ms_monthly_binder_export',
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        _SCHEDULER.start()
        logger.info(
            'MS Graph monthly export scheduler started '
            '(1st of month, 02:00 %s)', MS_BINDER_TZ,
        )
    except Exception as e:
        logger.warning(f'Scheduler failed to start: {e}')


def stop_scheduler() -> None:
    global _SCHEDULER
    if _SCHEDULER:
        try:
            _SCHEDULER.shutdown(wait=False)
        except Exception:
            pass
        _SCHEDULER = None
