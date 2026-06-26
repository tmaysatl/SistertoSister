"""Supabase-backed data access layer (Phase 4 cutover).

Thin async helpers wrapping asyncpg. Each function maps to one or two routes
that used to read from MongoDB. Keep the return shapes compatible with the
Pydantic models in `models.py`.
"""
from __future__ import annotations
from typing import Optional, Any
from datetime import datetime
from uuid import UUID
import asyncpg

from .supabase import get_pg_pool


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
