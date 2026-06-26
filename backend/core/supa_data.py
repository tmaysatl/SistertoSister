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


def lookup_storage_path_sync(doc_id: str, mime_type: str = "application/pdf") -> Optional[str]:
    """Best-effort: produce the path we'd expect for a doc (no DB hit)."""
    return _doc_storage_path(doc_id, mime_type)


async def upsert_document(d: dict) -> bool:
    """Insert/update document metadata in Postgres. `d` is the Document dict."""
    pool = await get_pg_pool()
    storage_path = d.get("storage_path") or (
        _doc_storage_path(d["id"], d.get("mime_type") or "application/pdf")
        if d.get("file_base64") else None
    )
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
