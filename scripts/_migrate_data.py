"""Phase 3 Data Migration: MongoDB -> Supabase (Postgres + Storage).

Idempotent. Re-running is safe; rows are upserted; documents already uploaded
to Storage are skipped.

Strategy for user IDs:
  - Each Mongo user already has a stable UUID (str(uuid.uuid4())).
  - We create matching Supabase Auth users using THE SAME UUID via the
    gotrue admin REST endpoint (`POST /auth/v1/admin/users` with `id` field).
  - All other tables FK to profiles(id) which references auth.users(id) -- so
    once we preserve the Mongo UUID, every foreign key becomes a no-op copy.
  - The two previously-seeded admin/caregiver Supabase users (with different
    UUIDs) are deleted and re-created with their Mongo UUIDs.
"""
from __future__ import annotations
import os, asyncio, base64, sys, json, httpx
from pathlib import Path
from datetime import datetime
from typing import Optional, Any

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')

from motor.motor_asyncio import AsyncIOMotorClient
import asyncpg
from supabase import create_client
import uuid as _uuid


def _is_valid_uuid(v):
    if v is None or not isinstance(v, str):
        return False
    try:
        _uuid.UUID(v)
        return True
    except Exception:
        return False


def _uuid_or_none(v):
    return v if _is_valid_uuid(v) else None



SUPABASE_URL = os.environ['SUPABASE_URL']
SERVICE_KEY = os.environ['SUPABASE_SERVICE_ROLE_KEY']
DB_URL = os.environ['SUPABASE_DIRECT_URL']
BUCKET = os.environ.get('SUPABASE_STORAGE_BUCKET', 'documents')
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']

DEFAULT_PASSWORD = 'Phcp2026!Temp'   # only used for non-seeded users; they should reset
SEED_PASSWORDS = {
    'admin@healthguard.com': 'AdminPassword123!',
    'caregiver@healthguard.com': 'Caregiver123!',
}


def parse_ts(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00'))
        except Exception:
            return None
    return None


async def admin_create_user(http: httpx.AsyncClient, *, user_id: str, email: str,
                            password: str, name: str, role: str) -> dict:
    """POST /auth/v1/admin/users with explicit id field (supabase-py doesn't expose id arg)."""
    r = await http.post(
        f"{SUPABASE_URL}/auth/v1/admin/users",
        headers={'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}'},
        json={
            'id': user_id,
            'email': email,
            'password': password,
            'email_confirm': True,
            'user_metadata': {'name': name, 'role': role},
        },
    )
    if r.status_code >= 400:
        raise RuntimeError(f'admin_create_user {email}: {r.status_code} {r.text}')
    return r.json()


async def admin_delete_user(http: httpx.AsyncClient, user_id: str) -> None:
    r = await http.delete(
        f"{SUPABASE_URL}/auth/v1/admin/users/{user_id}",
        headers={'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}'},
    )
    if r.status_code >= 400 and r.status_code != 404:
        raise RuntimeError(f'admin_delete_user {user_id}: {r.status_code} {r.text}')


async def list_auth_users(http: httpx.AsyncClient) -> list[dict]:
    out = []
    page = 1
    while True:
        r = await http.get(
            f"{SUPABASE_URL}/auth/v1/admin/users",
            headers={'apikey': SERVICE_KEY, 'Authorization': f'Bearer {SERVICE_KEY}'},
            params={'page': page, 'per_page': 200},
        )
        r.raise_for_status()
        users = r.json().get('users', [])
        if not users:
            break
        out.extend(users)
        if len(users) < 200:
            break
        page += 1
    return out


# ----------------------------------------------------------------------------
# Migration steps
# ----------------------------------------------------------------------------

async def migrate_users(mongo, pg: asyncpg.Connection, http: httpx.AsyncClient) -> dict[str, str]:
    """Returns mapping: mongo_uid -> supabase_uid (always identical post-migration)."""
    print('\n[1/11] USERS -> auth.users + profiles')
    existing = {u['email'].lower(): u['id'] for u in await list_auth_users(http) if u.get('email')}

    mongo_users = await mongo.users.find({}, {'_id': 0}).to_list(length=None)
    mapping: dict[str, str] = {}
    created = updated = 0

    for u in mongo_users:
        mongo_id = u['id']
        email = u['email'].lower()
        name = u.get('name') or email.split('@')[0]
        role = u.get('role', 'caregiver')

        existing_id = existing.get(email)
        if existing_id and existing_id != mongo_id:
            # Drop & recreate with Mongo UUID so FKs line up.
            print(f'  - re-creating {email} (was {existing_id[:8]}, want {mongo_id[:8]})')
            await admin_delete_user(http, existing_id)
            existing_id = None

        if existing_id is None:
            password = SEED_PASSWORDS.get(email, DEFAULT_PASSWORD)
            await admin_create_user(http, user_id=mongo_id, email=email,
                                    password=password, name=name, role=role)
            created += 1
        else:
            updated += 1

        # Ensure profile row reflects current name/role.
        await pg.execute(
            """insert into public.profiles(id, email, name, role, created_at)
               values($1, $2, $3, $4, coalesce($5, now()))
               on conflict (id) do update set email=excluded.email,
                                              name=excluded.name,
                                              role=excluded.role""",
            mongo_id, email, name, role, parse_ts(u.get('created_at')),
        )
        mapping[mongo_id] = mongo_id

    print(f'  users created={created}, updated={updated}, total mapping={len(mapping)}')
    return mapping


async def migrate_clients(mongo, pg: asyncpg.Connection) -> int:
    print('\n[2/11] CLIENTS')
    rows = await mongo.clients.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.clients(id, name, address, phone, notes, photo_base64, created_at)
               values($1,$2,$3,$4,$5,$6, coalesce($7, now()))
               on conflict (id) do update set name=excluded.name,
                                              address=excluded.address,
                                              phone=excluded.phone,
                                              notes=excluded.notes,
                                              photo_base64=excluded.photo_base64""",
            r['id'], r['name'], r.get('address') or '', r.get('phone') or '',
            r.get('notes') or '', r.get('photo_base64'),
            parse_ts(r.get('created_at')),
        )
    print(f'  clients migrated: {len(rows)}')
    return len(rows)


async def migrate_documents(mongo, pg: asyncpg.Connection, sb) -> int:
    print('\n[3/11] DOCUMENTS (+ Storage uploads)')
    rows = await mongo.documents.find({}, {'_id': 0}).to_list(length=None)
    # List once
    try:
        existing_files = sb.storage.from_(BUCKET).list('documents', {'limit': 10000})
        existing_names = {e['name'] for e in (existing_files or [])}
    except Exception:
        existing_names = set()

    # Build set of valid user IDs so uploaded_by FK is safe
    valid_user_ids = {r['id'] for r in await pg.fetch('select id::text as id from public.profiles')}
    valid_client_ids = {r['id'] for r in await pg.fetch('select id::text as id from public.clients')}

    uploaded = skipped = errors = 0
    for r in rows:
        doc_id = r['id']
        if not _is_valid_uuid(doc_id):
            errors += 1
            continue
        b64 = r.get('file_base64') or ''
        mime = r.get('mime_type') or 'application/pdf'
        storage_path = None
        if b64:
            ext = 'pdf' if mime == 'application/pdf' else (mime.split('/')[-1] or 'bin')
            target_name = f'{doc_id}.{ext}'
            storage_path = f'documents/{target_name}'
            if target_name not in existing_names:
                try:
                    binary = base64.b64decode(b64)
                    sb.storage.from_(BUCKET).upload(
                        path=storage_path,
                        file=binary,
                        file_options={'content-type': mime, 'upsert': 'true'},
                    )
                    uploaded += 1
                except Exception as e:
                    print(f'    ! upload failed for {doc_id}: {str(e)[:120]}')
            else:
                skipped += 1

        owner_id = _uuid_or_none(r.get('owner_id'))
        # Validate owner_id against the right table
        owner_type = r.get('owner_type') or 'agency'
        if owner_id and owner_type == 'caregiver' and owner_id not in valid_user_ids:
            owner_id = None
        if owner_id and owner_type == 'client' and owner_id not in valid_client_ids:
            owner_id = None
        uploaded_by = _uuid_or_none(r.get('uploaded_by'))
        if uploaded_by and uploaded_by not in valid_user_ids:
            uploaded_by = None

        try:
            await pg.execute(
                """insert into public.documents(id, title, category, owner_id, owner_type,
                                                storage_path, mime_type, notes, uploaded_by,
                                                uploaded_at, expires_at, seq, is_template, meta)
                   values($1,$2,$3,$4,$5,$6,$7,$8,$9, coalesce($10, now()), $11, $12, $13, $14)
                   on conflict (id) do update set title=excluded.title,
                                                  category=excluded.category,
                                                  owner_id=excluded.owner_id,
                                                  owner_type=excluded.owner_type,
                                                  storage_path=excluded.storage_path,
                                                  mime_type=excluded.mime_type,
                                                  notes=excluded.notes,
                                                  meta=excluded.meta""",
                doc_id, r.get('title') or '', r.get('category') or 'caregiver',
                owner_id, owner_type,
                storage_path, mime, r.get('notes') or '',
                uploaded_by,
                parse_ts(r.get('uploaded_at')),
                parse_ts(r.get('expires_at')),
                r.get('seq'), bool(r.get('is_template', False)),
                json.dumps({k: v for k, v in r.items() if k in
                           ('signature_image', 'signed_at', 'signed_by', 'form_data',
                            'public_url', 'public_token', 'watermark')}),
            )
        except Exception as e:
            print(f'    ! row failed {doc_id}: {str(e)[:120]}')
            errors += 1
    print(f'  documents migrated: {len(rows) - errors}/{len(rows)} (uploads: {uploaded}, skipped existing: {skipped}, errors: {errors})')
    return len(rows) - errors


async def migrate_assignments(mongo, pg) -> int:
    print('\n[4/11] ASSIGNMENTS')
    rows = await mongo.assignments.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.assignments(id, caregiver_id, client_id, schedule, notes, created_at)
               values($1,$2,$3,$4,$5, coalesce($6, now()))
               on conflict (id) do update set schedule=excluded.schedule, notes=excluded.notes""",
            r['id'], r['caregiver_id'], r['client_id'],
            r.get('schedule') or '', r.get('notes') or '',
            parse_ts(r.get('created_at')),
        )
    print(f'  assignments migrated: {len(rows)}')
    return len(rows)


async def migrate_shifts(mongo, pg) -> int:
    print('\n[5/11] SHIFTS')
    rows = await mongo.shifts.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.shifts(id, caregiver_id, client_id, kind, date, weekdays,
                                         recurring_until, parent_shift_id, start_time, end_time,
                                         notes, service_type, status, clocked_in_at, clocked_out_at,
                                         clock_location, created_by, created_at, updated_at)
               values($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17, coalesce($18, now()), $19)
               on conflict (id) do update set status=excluded.status,
                                              clocked_in_at=excluded.clocked_in_at,
                                              clocked_out_at=excluded.clocked_out_at,
                                              notes=excluded.notes""",
            r['id'], r['caregiver_id'], r['client_id'],
            r.get('kind') or 'one_off',
            parse_ts(r.get('date')).date() if r.get('date') else None,
            r.get('weekdays'),
            parse_ts(r.get('recurring_until')).date() if r.get('recurring_until') else None,
            r.get('parent_shift_id'),
            r.get('start_time') or '', r.get('end_time') or '',
            r.get('notes') or '', r.get('service_type') or '',
            r.get('status') or 'scheduled',
            parse_ts(r.get('clocked_in_at')),
            parse_ts(r.get('clocked_out_at')),
            json.dumps(r.get('clock_location')) if r.get('clock_location') else None,
            r.get('created_by'),
            parse_ts(r.get('created_at')),
            parse_ts(r.get('updated_at')),
        )
    print(f'  shifts migrated: {len(rows)}')
    return len(rows)


async def migrate_chat_messages(mongo, pg) -> int:
    print('\n[6/11] CHAT_MESSAGES (AI assistant sessions)')
    rows = await mongo.chat_messages.find({}, {'_id': 0}).to_list(length=None)
    inserted = 0
    for r in rows:
        try:
            await pg.execute(
                """insert into public.chat_messages(id, session_id, user_id, role, content, created_at)
                   values($1,$2,$3,$4,$5, coalesce($6, now()))
                   on conflict (id) do nothing""",
                r['id'], r['session_id'], r['user_id'],
                r.get('role') or 'user', r.get('content') or '',
                parse_ts(r.get('created_at')),
            )
            inserted += 1
        except Exception as e:
            print(f'    ! skip chat_message {r.get("id")}: {e}')
    print(f'  chat_messages migrated: {inserted}/{len(rows)}')
    return inserted


async def migrate_chat_dms(mongo, pg) -> int:
    print('\n[7/11] CHAT_DMS')
    rows = await mongo.chat_dms.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.chat_dms(id, from_id, from_name, to_id, to_name, text, read, created_at)
               values($1,$2,$3,$4,$5,$6,$7, coalesce($8, now()))
               on conflict (id) do update set read=excluded.read""",
            r['id'], r['from_id'], r.get('from_name') or '',
            r['to_id'], r.get('to_name') or '',
            r.get('text') or '', bool(r.get('read', False)),
            parse_ts(r.get('created_at')),
        )
    print(f'  chat_dms migrated: {len(rows)}')
    return len(rows)


async def migrate_client_tasks(mongo, pg) -> int:
    print('\n[8/11] CLIENT_TASKS')
    rows = await mongo.client_tasks.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.client_tasks(id, client_id, title, description, seq,
                                               completed, completed_at, created_at)
               values($1,$2,$3,$4,$5,$6,$7, coalesce($8, now()))
               on conflict (id) do update set completed=excluded.completed,
                                              completed_at=excluded.completed_at""",
            r['id'], r['client_id'], r.get('title') or '', r.get('description') or '',
            r.get('seq'), bool(r.get('completed', False)),
            parse_ts(r.get('completed_at')),
            parse_ts(r.get('created_at')),
        )
    print(f'  client_tasks migrated: {len(rows)}')
    return len(rows)


async def migrate_document_views(mongo, pg) -> int:
    print('\n[9/11] DOCUMENT_VIEWS')
    rows = await mongo.document_views.find({}, {'_id': 0}).to_list(length=None)
    inserted = 0
    for r in rows:
        try:
            await pg.execute(
                """insert into public.document_views(id, document_id, viewer_id, viewer_name, viewed_at)
                   values($1,$2,$3,$4, coalesce($5, now()))
                   on conflict (id) do nothing""",
                r['id'], r['document_id'], r.get('viewer_id'),
                r.get('viewer_name'),
                parse_ts(r.get('viewed_at')),
            )
            inserted += 1
        except Exception as e:
            # Likely viewer_id references a deleted user; null it
            try:
                await pg.execute(
                    """insert into public.document_views(id, document_id, viewer_id, viewer_name, viewed_at)
                       values($1,$2,$3,$4, coalesce($5, now()))
                       on conflict (id) do nothing""",
                    r['id'], r['document_id'], None,
                    r.get('viewer_name'),
                    parse_ts(r.get('viewed_at')),
                )
                inserted += 1
            except Exception as e2:
                print(f'    ! skip view {r.get("id")}: {e2}')
    print(f'  document_views migrated: {inserted}/{len(rows)}')
    return inserted


async def migrate_onboarding(mongo, pg) -> int:
    print('\n[10/11] ONBOARDING')
    rows = await mongo.onboarding.find({}, {'_id': 0}).to_list(length=None)
    inserted = 0
    for r in rows:
        try:
            await pg.execute(
                """insert into public.onboarding(id, caregiver_id, title, description,
                                                 completed, completed_at, created_at)
                   values($1,$2,$3,$4,$5,$6, coalesce($7, now()))
                   on conflict (id) do update set completed=excluded.completed,
                                                  completed_at=excluded.completed_at""",
                r['id'], r['caregiver_id'], r.get('title') or '', r.get('description') or '',
                bool(r.get('completed', False)),
                parse_ts(r.get('completed_at')),
                parse_ts(r.get('created_at')),
            )
            inserted += 1
        except Exception as e:
            print(f'    ! skip onboarding {r.get("id")}: {e}')
    print(f'  onboarding migrated: {inserted}/{len(rows)}')
    return inserted


async def migrate_packet_shares(mongo, pg) -> int:
    print('\n[11a/11] PACKET_SHARES')
    rows = await mongo.packet_shares.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.packet_shares(id, token, recipient_name, recipient_role,
                                                category, recipient_email, recipient_phone,
                                                created_by, created_at, viewed_at, completed_at, signed_ids)
               values($1,$2,$3,$4,$5,$6,$7,$8, coalesce($9, now()), $10, $11, $12)
               on conflict (id) do update set viewed_at=excluded.viewed_at,
                                              completed_at=excluded.completed_at,
                                              signed_ids=excluded.signed_ids""",
            r['id'], r['token'],
            r.get('recipient_name') or '', r.get('recipient_role') or '',
            r.get('category'), r.get('recipient_email'), r.get('recipient_phone'),
            r.get('created_by'),
            parse_ts(r.get('created_at')),
            parse_ts(r.get('viewed_at')),
            parse_ts(r.get('completed_at')),
            r.get('signed_ids') or [],
        )
    print(f'  packet_shares migrated: {len(rows)}')
    return len(rows)


async def migrate_policy_acks(mongo, pg) -> int:
    print('\n[11b/11] POLICY_ACKS')
    rows = await mongo.policy_acks.find({}, {'_id': 0}).to_list(length=None)
    for r in rows:
        await pg.execute(
            """insert into public.policy_acks(id, user_id, user_name, policy_id,
                                              policy_title, acknowledged_at)
               values($1,$2,$3,$4,$5, coalesce($6, now()))
               on conflict (id) do update set acknowledged_at=excluded.acknowledged_at""",
            r['id'], r['user_id'], r.get('user_name'),
            r['policy_id'], r.get('policy_title') or '',
            parse_ts(r.get('acknowledged_at')),
        )
    print(f'  policy_acks migrated: {len(rows)}')
    return len(rows)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

async def main():
    mongo_client = AsyncIOMotorClient(MONGO_URL)
    mongo = mongo_client[DB_NAME]
    pg = await asyncpg.connect(DB_URL, timeout=30)
    sb = create_client(SUPABASE_URL, SERVICE_KEY)

    async with httpx.AsyncClient(timeout=30) as http:
        await migrate_users(mongo, pg, http)

    await migrate_clients(mongo, pg)
    await migrate_documents(mongo, pg, sb)
    await migrate_assignments(mongo, pg)
    await migrate_shifts(mongo, pg)
    await migrate_chat_messages(mongo, pg)
    await migrate_chat_dms(mongo, pg)
    await migrate_client_tasks(mongo, pg)
    await migrate_document_views(mongo, pg)
    await migrate_onboarding(mongo, pg)
    await migrate_packet_shares(mongo, pg)
    await migrate_policy_acks(mongo, pg)

    # Verify totals
    print('\n=== Post-migration counts ===')
    tables = ['profiles', 'clients', 'documents', 'assignments', 'shifts',
              'chat_messages', 'chat_dms', 'client_tasks', 'document_views',
              'onboarding', 'packet_shares', 'policy_acks', 'training',
              'training_completions', 'integrations']
    for t in tables:
        n = await pg.fetchval(f'select count(*) from public.{t}')
        print(f'  {t:25} {n}')

    await pg.close()
    mongo_client.close()
    print('\nDONE.')

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print('Interrupted.')
        sys.exit(1)
