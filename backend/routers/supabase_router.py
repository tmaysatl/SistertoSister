"""Supabase status & diagnostic endpoints.

These are bridge endpoints used during the MongoDB -> Supabase migration so
the frontend can:
  - check that the Supabase configuration is live (`GET /api/supabase/status`)
  - fetch the current user's profile from the Supabase `profiles` table when
    they log in via Supabase Auth (`GET /api/supabase/me`)
"""
from fastapi import APIRouter, Depends, HTTPException

from core.settings import SUPABASE_ENABLED
from core.supabase import get_supabase_service, get_pg_pool
from core.security import get_current_user
from models import UserPublic

router = APIRouter(prefix='/supabase', tags=['supabase'])


@router.get('/status')
async def supabase_status():
    """Lightweight liveness check for the Supabase migration layer."""
    if not SUPABASE_ENABLED:
        return {'enabled': False}
    info: dict = {'enabled': True}
    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            tables = await conn.fetchval(
                "select count(*) from information_schema.tables "
                "where table_schema='public'"
            )
            profiles = await conn.fetchval('select count(*) from public.profiles')
        info.update({'db': 'ok', 'tables': int(tables), 'profiles': int(profiles)})
    except Exception as e:
        info.update({'db': 'error', 'error': str(e)[:200]})

    try:
        sb = get_supabase_service()
        buckets = [b.name for b in sb.storage.list_buckets()]
        info['storage_buckets'] = buckets
    except Exception as e:
        info['storage_error'] = str(e)[:200]
    return info


@router.get('/me')
async def supabase_me(current: UserPublic = Depends(get_current_user)):
    """Returns the caller's profile row from Supabase Postgres (if one exists)
    plus the resolved MongoDB UserPublic for parity checking.
    """
    out = {'user': current.model_dump()}
    if not SUPABASE_ENABLED:
        out['supabase_profile'] = None
        return out
    try:
        pool = await get_pg_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                'select id::text as id, email, name, role, created_at '
                'from public.profiles where email = $1',
                current.email,
            )
            out['supabase_profile'] = dict(row) if row else None
            if row and row['created_at'] is not None:
                out['supabase_profile']['created_at'] = row['created_at'].isoformat()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'Supabase profile lookup failed: {e}')
    return out
