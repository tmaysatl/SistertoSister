"""Supabase clients (lazy-initialized).

- `get_supabase_service()` -> service-role client (bypasses RLS; backend only)
- `get_supabase_anon()`    -> anon client (RLS applies)
- `get_pg_pool()`          -> asyncpg pool against the Supabase transaction pooler
"""
from __future__ import annotations
import asyncpg
from typing import Optional
from supabase import create_client, Client

from .settings import (
    SUPABASE_URL,
    SUPABASE_ANON_KEY,
    SUPABASE_SERVICE_ROLE_KEY,
    SUPABASE_DB_URL,
    SUPABASE_ENABLED,
)

_service_client: Optional[Client] = None
_anon_client: Optional[Client] = None
_pg_pool: Optional[asyncpg.Pool] = None


def get_supabase_service() -> Client:
    global _service_client
    if not SUPABASE_ENABLED:
        raise RuntimeError('Supabase is not configured')
    if _service_client is None:
        _service_client = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    return _service_client


def get_supabase_anon() -> Client:
    global _anon_client
    if not SUPABASE_ENABLED:
        raise RuntimeError('Supabase is not configured')
    if _anon_client is None:
        _anon_client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)
    return _anon_client


async def get_pg_pool() -> asyncpg.Pool:
    global _pg_pool
    if not SUPABASE_DB_URL:
        raise RuntimeError('SUPABASE_DB_URL not configured')
    if _pg_pool is None:
        # statement_cache_size=0 required for PgBouncer transaction-mode pooler
        _pg_pool = await asyncpg.create_pool(
            SUPABASE_DB_URL,
            min_size=1,
            max_size=5,
            statement_cache_size=0,
            command_timeout=20,
        )
    return _pg_pool


async def close_pg_pool() -> None:
    global _pg_pool
    if _pg_pool is not None:
        await _pg_pool.close()
        _pg_pool = None
