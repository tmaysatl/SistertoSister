"""Supabase-backed data access layer (Phase 4 cutover).

Thin async helpers wrapping asyncpg. Each function maps to one or two routes
that used to read from MongoDB. Keep the return shapes compatible with the
Pydantic models in `models.py`.
"""
from __future__ import annotations
from typing import Optional, Any
from datetime import datetime
from uuid import UUID
import json
import logging
import asyncpg

from .supabase import get_pg_pool

log = logging.getLogger(__name__)


def _row_to_dict(row: Optional[asyncpg.Record]) -> Optional[dict]:
    if row is None:
        return None
    d = dict(row)
    # Stringify UUID + datetimes so they're JSON-serializable / Pydantic friendly.
    for k, v in list(d.items()):
        if isinstance(v, UUID):
            d[k] = str(v)
        elif isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


# ---------------------------------------------------------------------------
# Profiles (users)
# ---------------------------------------------------------------------------

async def get_user_by_id(user_id: str) -> Optional[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id::text, email, name, role, photo_base64, "
            "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
            "from public.profiles where id = $1::uuid",
            user_id,
        )
    return _row_to_dict(row)


async def get_user_by_email(email: str) -> Optional[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id::text, email, name, role, photo_base64, "
            "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
            "from public.profiles where email = $1",
            email.lower(),
        )
    return _row_to_dict(row)


async def list_users_by_role(role: str) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select id::text, email, name, role, photo_base64, "
            "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
            "from public.profiles where role = $1 order by name nulls last",
            role,
        )
    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

async def list_clients() -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "select id::text, name, coalesce(address,'') as address, "
            "coalesce(phone,'') as phone, coalesce(notes,'') as notes, "
            "photo_base64, "
            "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
            "from public.clients order by created_at desc"
        )
    return [_row_to_dict(r) for r in rows]


async def get_client(client_id: str) -> Optional[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id::text, name, coalesce(address,'') as address, "
            "coalesce(phone,'') as phone, coalesce(notes,'') as notes, "
            "photo_base64, "
            "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
            "from public.clients where id = $1::uuid",
            client_id,
        )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Aggregate counts (for /api/stats)
# ---------------------------------------------------------------------------

async def get_dashboard_counts() -> dict[str, int]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        clients = await conn.fetchval("select count(*) from public.clients")
        caregivers = await conn.fetchval(
            "select count(*) from public.profiles where role = 'caregiver'"
        )
        documents = await conn.fetchval("select count(*) from public.documents")
        trainings = await conn.fetchval("select count(*) from public.training")
        onboarding_total = await conn.fetchval("select count(*) from public.onboarding")
        onboarding_done = await conn.fetchval(
            "select count(*) from public.onboarding where completed = true"
        )
        training_completions = await conn.fetchval(
            "select count(*) from public.training_completions"
        )
    return {
        'clients': int(clients or 0),
        'caregivers': int(caregivers or 0),
        'documents': int(documents or 0),
        'trainings': int(trainings or 0),
        'onboarding_total': int(onboarding_total or 0),
        'onboarding_done': int(onboarding_done or 0),
        'training_completions': int(training_completions or 0),
    }


# ---------------------------------------------------------------------------
# Write helpers (Phase 4 Slice B — dual-write pattern)
#
# Each helper is best-effort: failures are logged but do NOT raise, so that
# MongoDB stays authoritative. Once Slice J flips, we'll make them strict.
# ---------------------------------------------------------------------------

async def _safe(coro, op: str) -> bool:
    """Best-effort wrapper for Postgres writes.

    Catches *operational* errors (network / FK / constraint hiccups) so a
    Postgres outage doesn't block requests that already succeeded against
    MongoDB. Programmer errors (SQL syntax, type mismatch, etc.) bubble up
    so they get caught in development.
    """
    try:
        await coro
        return True
    except (asyncpg.PostgresSyntaxError,
            asyncpg.UndefinedColumnError,
            asyncpg.UndefinedTableError,
            asyncpg.DataError):
        # programmer bugs — re-raise so they're not silently hidden
        raise
    except Exception as e:
        log.warning("[supa-write] %s failed: %s", op, str(e)[:200])
        return False


def _ts(v):
    """Coerce ISO-8601 string -> datetime; pass datetimes through; None stays None."""
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, str) and v:
        try:
            return datetime.fromisoformat(v.replace('Z', '+00:00'))
        except Exception:
            return None
    return None


async def upsert_client(c: dict) -> bool:
    """Insert/update a client row. `c` should follow the Client Pydantic shape."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.clients(id, name, address, phone, notes, photo_base64, created_at)
               values($1::uuid, $2, $3, $4, $5, $6, coalesce($7, now()))
               on conflict (id) do update set name=excluded.name,
                                              address=excluded.address,
                                              phone=excluded.phone,
                                              notes=excluded.notes,
                                              photo_base64=excluded.photo_base64""",
            c['id'], c.get('name', ''), c.get('address') or '',
            c.get('phone') or '', c.get('notes') or '',
            c.get('photo_base64'),
            _ts(c.get('created_at')),
        ), f"upsert_client {c.get('id')}")


async def update_client_photo(client_id: str, photo_base64: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.clients set photo_base64=$2 where id=$1::uuid",
            client_id, photo_base64,
        ), f"update_client_photo {client_id}")


async def delete_client(client_id: str) -> bool:
    """Delete client + cascade assignments and tasks (CASCADE in DDL handles FKs)."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "delete from public.clients where id=$1::uuid",
            client_id,
        ), f"delete_client {client_id}")


async def update_user_photo(user_id: str, photo_base64: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.profiles set photo_base64=$2 where id=$1::uuid",
            user_id, photo_base64,
        ), f"update_user_photo {user_id}")


# --- assignments ---

async def list_assignments(caregiver_id: Optional[str] = None) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        if caregiver_id:
            rows = await conn.fetch(
                "select id::text, caregiver_id::text, client_id::text, "
                "coalesce(schedule,'') as schedule, coalesce(notes,'') as notes, "
                "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
                "from public.assignments where caregiver_id=$1::uuid",
                caregiver_id,
            )
        else:
            rows = await conn.fetch(
                "select id::text, caregiver_id::text, client_id::text, "
                "coalesce(schedule,'') as schedule, coalesce(notes,'') as notes, "
                "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
                "from public.assignments"
            )
    return [_row_to_dict(r) for r in rows]


async def find_assignment(caregiver_id: str, client_id: str) -> Optional[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id::text, caregiver_id::text, client_id::text, "
            "coalesce(schedule,'') as schedule, coalesce(notes,'') as notes, "
            "to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at "
            "from public.assignments where caregiver_id=$1::uuid and client_id=$2::uuid",
            caregiver_id, client_id,
        )
    return _row_to_dict(row)


async def upsert_assignment(a: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.assignments(id, caregiver_id, client_id, schedule, notes, created_at)
               values($1::uuid, $2::uuid, $3::uuid, $4, $5, coalesce($6, now()))
               on conflict (id) do update set schedule=excluded.schedule,
                                              notes=excluded.notes""",
            a['id'], a['caregiver_id'], a['client_id'],
            a.get('schedule') or '', a.get('notes') or '',
            _ts(a.get('created_at')),
        ), f"upsert_assignment {a.get('id')}")


async def delete_assignment(assignment_id: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "delete from public.assignments where id=$1::uuid",
            assignment_id,
        ), f"delete_assignment {assignment_id}")


# --- client tasks ---

async def upsert_client_task(t: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.client_tasks(id, client_id, title, description, seq,
                                               completed, completed_at, created_at)
               values($1::uuid, $2::uuid, $3, $4, $5, $6, $7, coalesce($8, now()))
               on conflict (id) do update set title=excluded.title,
                                              description=excluded.description,
                                              completed=excluded.completed,
                                              completed_at=excluded.completed_at""",
            t['id'], t['client_id'], t.get('title') or '',
            t.get('description') or '', t.get('seq'),
            bool(t.get('completed', False)),
            _ts(t.get('completed_at')),
            _ts(t.get('created_at')),
        ), f"upsert_client_task {t.get('id')}")


async def toggle_client_task(task_id: str, completed: bool, completed_at: Optional[str]) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.client_tasks set completed=$2, completed_at=$3 where id=$1::uuid",
            task_id, completed, _ts(completed_at),
        ), f"toggle_client_task {task_id}")


# ---------------------------------------------------------------------------
# Documents + Supabase Storage (Slice C)
# ---------------------------------------------------------------------------
import base64 as _b64
from .settings import SUPABASE_STORAGE_BUCKET
from .supabase import get_supabase_service


def _doc_storage_path(doc_id: str, mime_type: str = "application/pdf") -> str:
    ext = "pdf" if (mime_type or "").endswith("pdf") else ((mime_type or "bin").split("/")[-1] or "bin")
    return f"documents/{doc_id}.{ext}"


def upload_document_blob_sync(doc_id: str, file_b64: str, mime_type: str) -> Optional[str]:
    """Upload base64 file content to Supabase Storage. Returns storage_path or None."""
    if not file_b64:
        return None
    try:
        # Strip any data:mime;base64, prefix
        b = file_b64.split(",", 1)[-1] if "," in file_b64 else file_b64
        binary = _b64.b64decode(b)
        path = _doc_storage_path(doc_id, mime_type)
        sb = get_supabase_service()
        sb.storage.from_(SUPABASE_STORAGE_BUCKET).upload(
            path=path,
            file=binary,
            file_options={"content-type": mime_type or "application/pdf", "upsert": "true"},
        )
        return path
    except Exception as e:
        log.warning("[supa-storage] upload failed for %s: %s", doc_id, str(e)[:200])
        return None


def delete_document_blob_sync(storage_path: str) -> bool:
    if not storage_path:
        return True
    try:
        sb = get_supabase_service()
        sb.storage.from_(SUPABASE_STORAGE_BUCKET).remove([storage_path])
        return True
    except Exception as e:
        log.warning("[supa-storage] delete failed for %s: %s", storage_path, str(e)[:200])
        return False


def signed_url_for_document(storage_path: str, expires_in_seconds: int = 3600) -> Optional[str]:
    if not storage_path:
        return None
    try:
        sb = get_supabase_service()
        res = sb.storage.from_(SUPABASE_STORAGE_BUCKET).create_signed_url(storage_path, expires_in_seconds)
        # supabase-py returns {'signedURL': '...'} (snake_case key 'signedUrl' on newer versions)
        if isinstance(res, dict):
            return res.get("signedURL") or res.get("signedUrl") or res.get("signed_url")
        return None
    except Exception as e:
        log.warning("[supa-storage] signed_url failed for %s: %s", storage_path, str(e)[:200])
        return None


async def upsert_document(d: dict) -> bool:
    """Insert/update document metadata in Postgres. `d` is the Document dict.

    NOTE: callers are responsible for setting d['storage_path'] from the
    return value of upload_document_blob_sync — we no longer synthesize a
    path here, because doing so leaves PG with a phantom path if the Storage
    upload actually failed.
    """
    pool = await get_pg_pool()
    storage_path = d.get("storage_path")
    meta = {k: d.get(k) for k in (
        "signature_image", "signed_at", "signed_by", "form_data",
        "public_url", "public_token", "watermark",
    ) if d.get(k) is not None}
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.documents(id, title, category, owner_id, owner_type,
                                            storage_path, mime_type, notes, uploaded_by,
                                            uploaded_at, expires_at, seq, is_template, meta)
               values($1::uuid, $2, $3, $4::uuid, $5, $6, $7, $8, $9::uuid,
                      coalesce($10, now()), $11, $12, $13, $14::jsonb)
               on conflict (id) do update set title=excluded.title,
                                              category=excluded.category,
                                              owner_id=excluded.owner_id,
                                              owner_type=excluded.owner_type,
                                              storage_path=excluded.storage_path,
                                              mime_type=excluded.mime_type,
                                              notes=excluded.notes,
                                              meta=excluded.meta""",
            d["id"], d.get("title") or "", d.get("category") or "caregiver",
            d.get("owner_id"), d.get("owner_type") or "agency",
            storage_path,
            d.get("mime_type") or "application/pdf",
            d.get("notes") or "",
            d.get("uploaded_by"),
            _ts(d.get("uploaded_at")),
            _ts(d.get("expires_at")),
            d.get("seq"), bool(d.get("is_template", False)),
            json.dumps(meta),
        ), f"upsert_document {d.get('id')}")


async def delete_document(doc_id: str) -> Optional[str]:
    """Delete the metadata row; return the storage_path so caller can remove the blob."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "delete from public.documents where id=$1::uuid returning storage_path",
                doc_id,
            )
            return row["storage_path"] if row else None
        except Exception as e:
            log.warning("[supa-write] delete_document %s failed: %s", doc_id, str(e)[:200])
            return None


async def get_document_storage_path(doc_id: str) -> Optional[str]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "select storage_path from public.documents where id=$1::uuid", doc_id
        )


# ---------------------------------------------------------------------------
# Shifts (Slice D)
# ---------------------------------------------------------------------------

def _date_or_none(v):
    """Parse a YYYY-MM-DD string to a date; pass dates through; None stays None."""
    if v is None:
        return None
    from datetime import date as _d
    if isinstance(v, _d):
        return v
    if isinstance(v, str) and v:
        try:
            return _d.fromisoformat(v[:10])
        except Exception:
            return None
    return None


def _shift_clock_location(v) -> Optional[str]:
    """The Mongo schema accepts an arbitrary string for clock_location.
    Postgres stores JSONB, so wrap into a JSON value."""
    if v is None:
        return None
    return json.dumps(v if isinstance(v, (dict, list)) else str(v))


async def upsert_shift(s: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.shifts(id, caregiver_id, client_id, kind, date, weekdays,
                                          recurring_until, parent_shift_id, start_time, end_time,
                                          notes, service_type, status, clocked_in_at, clocked_out_at,
                                          clock_location, created_by, created_at, updated_at)
               values($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::uuid, $9, $10,
                      $11, $12, $13, $14, $15, $16::jsonb, $17::uuid,
                      coalesce($18, now()), $19)
               on conflict (id) do update set kind=excluded.kind,
                                              date=excluded.date,
                                              weekdays=excluded.weekdays,
                                              recurring_until=excluded.recurring_until,
                                              start_time=excluded.start_time,
                                              end_time=excluded.end_time,
                                              notes=excluded.notes,
                                              service_type=excluded.service_type,
                                              status=excluded.status,
                                              clocked_in_at=excluded.clocked_in_at,
                                              clocked_out_at=excluded.clocked_out_at,
                                              clock_location=excluded.clock_location,
                                              updated_at=excluded.updated_at""",
            s['id'], s['caregiver_id'], s['client_id'],
            s.get('kind') or 'one_off',
            _date_or_none(s.get('date')),
            s.get('weekdays'),
            _date_or_none(s.get('recurring_until')),
            s.get('parent_shift_id'),
            s.get('start_time') or '', s.get('end_time') or '',
            s.get('notes') or '', s.get('service_type') or '',
            s.get('status') or 'scheduled',
            _ts(s.get('clocked_in_at')),
            _ts(s.get('clocked_out_at')),
            _shift_clock_location(s.get('clock_location')),
            s.get('created_by'),
            _ts(s.get('created_at')),
            _ts(s.get('updated_at')),
        ), f"upsert_shift {s.get('id')}")


async def upsert_shifts_bulk(shifts: list[dict]) -> int:
    """Upsert many shifts using one connection + executemany (single round-trip
    batch). Used for recurring child shifts which can be hundreds of rows for
    a year-long schedule."""
    if not shifts:
        return 0
    pool = await get_pg_pool()
    rows = []
    for s in shifts:
        rows.append((
            s['id'], s['caregiver_id'], s['client_id'],
            s.get('kind') or 'one_off',
            _date_or_none(s.get('date')),
            s.get('weekdays'),
            _date_or_none(s.get('recurring_until')),
            s.get('parent_shift_id'),
            s.get('start_time') or '', s.get('end_time') or '',
            s.get('notes') or '', s.get('service_type') or '',
            s.get('status') or 'scheduled',
            _ts(s.get('clocked_in_at')),
            _ts(s.get('clocked_out_at')),
            _shift_clock_location(s.get('clock_location')),
            s.get('created_by'),
            _ts(s.get('created_at')),
            _ts(s.get('updated_at')),
        ))
    sql = """insert into public.shifts(id, caregiver_id, client_id, kind, date, weekdays,
                                       recurring_until, parent_shift_id, start_time, end_time,
                                       notes, service_type, status, clocked_in_at, clocked_out_at,
                                       clock_location, created_by, created_at, updated_at)
             values($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::uuid, $9, $10,
                    $11, $12, $13, $14, $15, $16::jsonb, $17::uuid,
                    coalesce($18, now()), $19)
             on conflict (id) do update set status=excluded.status,
                                            updated_at=excluded.updated_at"""
    try:
        async with pool.acquire() as conn:
            await conn.executemany(sql, rows)
        return len(rows)
    except Exception as e:
        log.warning("[supa-write] upsert_shifts_bulk(%d) failed: %s", len(rows), str(e)[:200])
        # Fall back to per-row upsert so we don't lose all children if one is bad
        n = 0
        for s in shifts:
            if await upsert_shift(s):
                n += 1
        return n


async def update_shift_fields(shift_id: str, patch: dict) -> bool:
    """Apply a partial patch (only supported scalar fields)."""
    if not patch:
        return True
    pool = await get_pg_pool()
    allowed = {
        'notes': 'text',
        'service_type': 'text',
        'status': 'text',
        'start_time': 'text',
        'end_time': 'text',
        'date': 'date',
        'clocked_in_at': 'timestamptz',
        'clocked_out_at': 'timestamptz',
        'clock_location': 'jsonb',
        'updated_at': 'timestamptz',
    }
    sets = []
    args: list = []
    i = 2
    for k, v in patch.items():
        if k not in allowed:
            continue
        if allowed[k] == 'date':
            v = _date_or_none(v)
        elif allowed[k] == 'timestamptz':
            v = _ts(v)
        elif allowed[k] == 'jsonb':
            v = _shift_clock_location(v)
        sets.append(f"{k}=${i}")
        args.append(v)
        i += 1
    if not sets:
        return True
    sql = f"update public.shifts set {', '.join(sets)} where id=$1::uuid"
    async with pool.acquire() as conn:
        return await _safe(conn.execute(sql, shift_id, *args),
                           f"update_shift_fields {shift_id}")


async def delete_shift(shift_id: str) -> bool:
    """Delete a shift; ON DELETE CASCADE handles children of recurring parents."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "delete from public.shifts where id=$1::uuid",
            shift_id,
        ), f"delete_shift {shift_id}")


async def list_shifts_filtered(
    *,
    caregiver_id: Optional[str] = None,
    client_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    one_off_only: bool = True,
) -> list[dict]:
    pool = await get_pg_pool()
    sql = [
        "select id::text, caregiver_id::text, client_id::text, kind,",
        "  to_char(date, 'YYYY-MM-DD') as date,",
        "  weekdays,",
        "  to_char(recurring_until, 'YYYY-MM-DD') as recurring_until,",
        "  parent_shift_id::text,",
        "  start_time, end_time, coalesce(notes,'') as notes,",
        "  coalesce(service_type,'') as service_type, status,",
        "  to_char(clocked_in_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as clocked_in_at,",
        "  to_char(clocked_out_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as clocked_out_at,",
        "  clock_location::text as clock_location,",
        "  created_by::text,",
        "  to_char(created_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as created_at,",
        "  to_char(updated_at, 'YYYY-MM-DD\"T\"HH24:MI:SS.MS\"+00:00\"') as updated_at",
        "from public.shifts",
    ]
    where: list[str] = []
    args: list = []
    i = 1
    if caregiver_id:
        where.append(f"caregiver_id=${i}::uuid")
        args.append(caregiver_id)
        i += 1
    if client_id:
        where.append(f"client_id=${i}::uuid")
        args.append(client_id)
        i += 1
    if start:
        sd = _date_or_none(start)
        if sd:
            where.append(f"date >= ${i}")
            args.append(sd)
            i += 1
    if end:
        ed = _date_or_none(end)
        if ed:
            where.append(f"date <= ${i}")
            args.append(ed)
            i += 1
    if one_off_only:
        where.append("kind = 'one_off'")
    if where:
        sql.append("where " + " and ".join(where))
    sql.append("order by date asc nulls last, start_time asc")
    sql_text = "\n".join(sql)
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql_text, *args)
    out = []
    for r in rows:
        d = dict(r)
        # clock_location came back as JSON text; convert to original shape
        if d.get('clock_location'):
            try:
                d['clock_location'] = json.loads(d['clock_location'])
            except Exception:
                pass
        out.append(d)
    return out


async def find_shift(shift_id: str) -> Optional[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "select id::text from public.shifts where id=$1::uuid", shift_id
        )
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Chat: DMs (chat_dms) + AI Assistant (chat_messages)  -- Slice E
# ---------------------------------------------------------------------------

async def insert_dm(m: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.chat_dms(id, from_id, from_name, to_id, to_name,
                                           text, read, created_at)
               values($1::uuid, $2::uuid, $3, $4::uuid, $5, $6, $7,
                      coalesce($8, now()))
               on conflict (id) do update set read=excluded.read""",
            m['id'], m['from_id'], m.get('from_name') or '',
            m['to_id'], m.get('to_name') or '',
            m.get('text') or '', bool(m.get('read', False)),
            _ts(m.get('created_at')),
        ), f"insert_dm {m.get('id')}")


async def list_dm_threads(user_id: str) -> list[dict]:
    """Return one entry per conversation partner, with last message + unread count.
    Each entry also includes the partner's photo_base64 and role from profiles.
    """
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        # CTE: classify each message by 'other_party' (the one who isn't `user_id`),
        # rank in reverse chronological order per other_party, take rank=1 as the
        # 'last message' row; also count unread (where to_id=user_id AND read=false).
        rows = await conn.fetch(
            """
            with own as (
              select id, from_id, from_name, to_id, to_name, text, read, created_at,
                     case when from_id = $1::uuid then to_id else from_id end as other_id,
                     case when from_id = $1::uuid then to_name else from_name end as other_name
              from public.chat_dms
              where from_id = $1::uuid or to_id = $1::uuid
            ),
            ranked as (
              select *, row_number() over (partition by other_id order by created_at desc) as rn
              from own
            ),
            unread_cnt as (
              select other_id, count(*) as unread
              from own
              where to_id = $1::uuid and read = false
              group by other_id
            )
            select r.other_id::text   as other_id,
                   r.other_name        as other_name,
                   r.text              as last_message,
                   to_char(r.created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as last_at,
                   coalesce(u.unread, 0) as unread,
                   p.photo_base64,
                   p.role
            from ranked r
            left join unread_cnt u on u.other_id = r.other_id
            left join public.profiles p on p.id = r.other_id
            where r.rn = 1
            order by r.created_at desc
            """,
            user_id,
        )
    return [dict(r) for r in rows]


async def get_dm_conversation(user_a: str, user_b: str) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id::text, from_id::text, from_name, to_id::text, to_name,
                      text, read,
                      to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as created_at
               from public.chat_dms
               where (from_id = $1::uuid and to_id = $2::uuid)
                  or (from_id = $2::uuid and to_id = $1::uuid)
               order by created_at asc
               limit 500""",
            user_a, user_b,
        )
    return [dict(r) for r in rows]


async def mark_dm_read(recipient_id: str, sender_id: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.chat_dms set read=true "
            "where to_id=$1::uuid and from_id=$2::uuid and read=false",
            recipient_id, sender_id,
        ), f"mark_dm_read {recipient_id}/{sender_id}")


# --- AI Assistant chat ---

async def insert_chat_message(m: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.chat_messages(id, session_id, user_id, role, content, created_at)
               values($1::uuid, $2, $3::uuid, $4, $5, coalesce($6, now()))
               on conflict (id) do nothing""",
            m['id'], m['session_id'], m['user_id'],
            m.get('role') or 'user', m.get('content') or '',
            _ts(m.get('created_at')),
        ), f"insert_chat_message {m.get('id')}")


async def list_chat_messages(session_id: str, user_id: str) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id::text, session_id, user_id::text, role, content,
                      to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as created_at
               from public.chat_messages
               where session_id = $1 and user_id = $2::uuid
               order by created_at asc
               limit 500""",
            session_id, user_id,
        )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Onboarding (Slice F)
# ---------------------------------------------------------------------------

async def upsert_onboarding_step(s: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.onboarding(id, caregiver_id, title, description,
                                              completed, completed_at, created_at)
               values($1::uuid, $2::uuid, $3, $4, $5, $6, coalesce($7, now()))
               on conflict (id) do update set title=excluded.title,
                                              description=excluded.description,
                                              completed=excluded.completed,
                                              completed_at=excluded.completed_at""",
            s['id'], s['caregiver_id'], s.get('title') or '',
            s.get('description') or '',
            bool(s.get('completed', False)),
            _ts(s.get('completed_at')),
            _ts(s.get('created_at')),
        ), f"upsert_onboarding_step {s.get('id')}")


async def toggle_onboarding_step(step_id: str, completed: bool,
                                 completed_at: Optional[str]) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.onboarding set completed=$2, completed_at=$3 "
            "where id=$1::uuid",
            step_id, completed, _ts(completed_at),
        ), f"toggle_onboarding_step {step_id}")


async def delete_onboarding_step(step_id: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "delete from public.onboarding where id=$1::uuid",
            step_id,
        ), f"delete_onboarding_step {step_id}")


async def list_onboarding_steps(caregiver_id: Optional[str] = None) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        if caregiver_id:
            rows = await conn.fetch(
                """select id::text, caregiver_id::text, title,
                          coalesce(description,'') as description,
                          completed,
                          to_char(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as completed_at,
                          to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as created_at
                   from public.onboarding
                   where caregiver_id=$1::uuid
                   order by created_at asc""",
                caregiver_id,
            )
        else:
            rows = await conn.fetch(
                """select id::text, caregiver_id::text, title,
                          coalesce(description,'') as description,
                          completed,
                          to_char(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as completed_at,
                          to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as created_at
                   from public.onboarding
                   order by created_at asc"""
            )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Packet shares (Slice G)
# ---------------------------------------------------------------------------

async def upsert_packet(p: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.packet_shares(id, token, recipient_name, recipient_role,
                                                 category, recipient_email, recipient_phone,
                                                 created_by, created_at, viewed_at, completed_at,
                                                 signed_ids)
               values($1::uuid, $2, $3, $4, $5, $6, $7, $8::uuid,
                      coalesce($9, now()), $10, $11, $12::uuid[])
               on conflict (id) do update set viewed_at=excluded.viewed_at,
                                              completed_at=excluded.completed_at,
                                              signed_ids=excluded.signed_ids""",
            p['id'], p['token'],
            p.get('recipient_name') or '', p.get('recipient_role') or '',
            p.get('category'), p.get('recipient_email'), p.get('recipient_phone'),
            p.get('created_by'),
            _ts(p.get('created_at')),
            _ts(p.get('viewed_at')),
            _ts(p.get('completed_at')),
            [s for s in (p.get('signed_ids') or []) if s],
        ), f"upsert_packet {p.get('id')}")


async def get_packet_by_token(token: str) -> Optional[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """select id::text, token, recipient_name, recipient_role,
                      category, recipient_email, recipient_phone,
                      created_by::text,
                      to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as created_at,
                      to_char(viewed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as viewed_at,
                      to_char(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as completed_at,
                      coalesce(array(select x::text from unnest(signed_ids) x), '{}')::text[] as signed_ids
               from public.packet_shares where token=$1""",
            token,
        )
    return dict(row) if row else None


async def mark_packet_viewed(token: str, when: Optional[str] = None) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.packet_shares set viewed_at=coalesce(viewed_at, $2) where token=$1",
            token, _ts(when),
        ), f"mark_packet_viewed {token[:8]}")


async def packet_add_signed_id(token: str, doc_id: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.packet_shares "
            "set signed_ids = (case when $2::uuid = any(signed_ids) then signed_ids "
            "                       else signed_ids || $2::uuid end) "
            "where token=$1",
            token, doc_id,
        ), f"packet_add_signed_id {token[:8]}")


async def mark_packet_completed(token: str, when: Optional[str] = None) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "update public.packet_shares set completed_at=coalesce(completed_at, $2) where token=$1",
            token, _ts(when),
        ), f"mark_packet_completed {token[:8]}")


async def count_packet_docs(category: str) -> int:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "select count(*) from public.documents where category=$1",
            category,
        )
    return int(n or 0)


# ---------------------------------------------------------------------------
# Policy acknowledgments + Training (Slice H)
# ---------------------------------------------------------------------------

async def upsert_policy_ack(a: dict) -> bool:
    """One ack per (policy_id, user_id) — UPSERT on the unique constraint."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.policy_acks(id, user_id, user_name, policy_id,
                                              policy_title, acknowledged_at)
               values($1::uuid, $2::uuid, $3, $4, $5, coalesce($6, now()))
               on conflict (policy_id, user_id) do update set
                  user_name = excluded.user_name,
                  policy_title = excluded.policy_title,
                  acknowledged_at = excluded.acknowledged_at""",
            a['id'], a['user_id'], a.get('user_name'),
            a['policy_id'], a.get('policy_title') or '',
            _ts(a.get('acknowledged_at')),
        ), f"upsert_policy_ack {a.get('policy_id')}")


async def delete_policy_ack(policy_id: str, user_id: str) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "delete from public.policy_acks where policy_id=$1 and user_id=$2::uuid",
            policy_id, user_id,
        ), f"delete_policy_ack {policy_id}/{user_id[:8]}")


async def list_policy_acks(user_id: Optional[str] = None) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                """select id::text, user_id::text, user_name, policy_id, policy_title,
                          to_char(acknowledged_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as acknowledged_at
                   from public.policy_acks where user_id=$1::uuid
                   order by acknowledged_at desc""",
                user_id,
            )
        else:
            rows = await conn.fetch(
                """select id::text, user_id::text, user_name, policy_id, policy_title,
                          to_char(acknowledged_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as acknowledged_at
                   from public.policy_acks
                   order by acknowledged_at desc"""
            )
    return [dict(r) for r in rows]


# --- Training ---

async def upsert_training(t: dict) -> bool:
    """Training table has no file_base64 column; Mongo's file_base64 is uploaded
    separately to Supabase Storage by the caller (similar to documents)."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.training(id, title, description, storage_path,
                                            mime_type, required, created_at)
               values($1::uuid, $2, $3, $4, $5, $6, coalesce($7, now()))
               on conflict (id) do update set title=excluded.title,
                                              description=excluded.description,
                                              storage_path=excluded.storage_path,
                                              mime_type=excluded.mime_type,
                                              required=excluded.required""",
            t['id'], t.get('title') or '', t.get('description') or '',
            t.get('storage_path'),
            t.get('mime_type') or 'video/mp4',
            bool(t.get('required', True)),
            _ts(t.get('created_at')),
        ), f"upsert_training {t.get('id')}")


async def delete_training(training_id: str) -> bool:
    """Delete training; ON DELETE CASCADE clears completions."""
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            "delete from public.training where id=$1::uuid",
            training_id,
        ), f"delete_training {training_id}")


async def list_training_all() -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """select id::text, title, coalesce(description,'') as description,
                      storage_path, mime_type, required,
                      to_char(created_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as created_at
               from public.training
               order by created_at desc"""
        )
    return [dict(r) for r in rows]


async def upsert_training_completion(c: dict) -> bool:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        return await _safe(conn.execute(
            """insert into public.training_completions(id, training_id, caregiver_id, completed_at)
               values($1::uuid, $2::uuid, $3::uuid, coalesce($4, now()))
               on conflict (training_id, caregiver_id) do nothing""",
            c['id'], c['training_id'], c['caregiver_id'],
            _ts(c.get('completed_at')),
        ), f"upsert_training_completion {c.get('training_id')}")


async def list_training_completions(caregiver_id: Optional[str] = None) -> list[dict]:
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        if caregiver_id:
            rows = await conn.fetch(
                """select id::text, training_id::text, caregiver_id::text,
                          to_char(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as completed_at
                   from public.training_completions where caregiver_id=$1::uuid
                   order by completed_at desc""",
                caregiver_id,
            )
        else:
            rows = await conn.fetch(
                """select id::text, training_id::text, caregiver_id::text,
                          to_char(completed_at, 'YYYY-MM-DD"T"HH24:MI:SS.MS"+00:00"') as completed_at
                   from public.training_completions
                   order by completed_at desc"""
            )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# User creation (Slice I): create Supabase Auth user + ensure profiles row
# ---------------------------------------------------------------------------
import httpx as _httpx

from .settings import SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY


async def create_supabase_auth_user(
    *,
    user_id: str,
    email: str,
    password: str,
    name: str,
    role: str,
) -> bool:
    """POST /auth/v1/admin/users — create Supabase Auth user with the same UUID
    so all foreign keys line up. Then upsert the public.profiles row.

    Returns True on success, False otherwise. Never raises so existing Mongo
    write paths aren't blocked by Supabase outages.
    """
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return False
    try:
        async with _httpx.AsyncClient(timeout=15) as http:
            r = await http.post(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers={
                    "apikey": SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                },
                json={
                    "id": user_id,
                    "email": email,
                    "password": password,
                    "email_confirm": True,
                    "user_metadata": {"name": name, "role": role},
                },
            )
            if r.status_code >= 400:
                body = r.text[:200]
                # 422 already exists - just return False so caller treats as "ok"
                if "already been registered" in body.lower() or r.status_code == 422:
                    log.info("[supa-auth] %s already exists", email)
                    return False
                log.warning("[supa-auth] create_user %s: %s %s", email, r.status_code, body)
                return False
    except Exception as e:
        log.warning("[supa-auth] create_user %s failed: %s", email, str(e)[:200])
        return False

    # Upsert profiles row (the on_auth_user_created trigger should handle this,
    # but explicit upsert here is defensive in case the trigger has been altered).
    pool = await get_pg_pool()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                """insert into public.profiles(id, email, name, role, created_at)
                   values($1::uuid, $2, $3, $4, now())
                   on conflict (id) do update set email=excluded.email,
                                                  name=excluded.name,
                                                  role=excluded.role""",
                user_id, email.lower(), name, role,
            )
        except Exception as e:
            log.warning("[supa-auth] profile upsert %s failed: %s", email, str(e)[:200])
    return True


async def supabase_auth_user_exists(email: str) -> bool:
    """Check if a Supabase Auth user with this email already exists."""
    if not (SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY):
        return False
    try:
        async with _httpx.AsyncClient(timeout=10) as http:
            r = await http.get(
                f"{SUPABASE_URL}/auth/v1/admin/users",
                headers={
                    "apikey": SUPABASE_SERVICE_ROLE_KEY,
                    "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
                },
                params={"email": email},
            )
            if r.status_code >= 400:
                return False
            users = r.json().get("users", [])
            return any(u.get("email", "").lower() == email.lower() for u in users)
    except Exception:
        return False
