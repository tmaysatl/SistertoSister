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
    try:
        await coro
        return True
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
