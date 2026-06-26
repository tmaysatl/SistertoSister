"""Smoke test Slice J: MS Graph integrations dual-write (Mongo + Postgres).

Verifies that POST /api/ms/email-recipients and POST /api/ms/disconnect
write to both MongoDB (db.integrations) AND Postgres (public.integrations).
"""
import asyncio, os, sys, httpx
from pathlib import Path
sys.path.insert(0, '/app/backend')
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg
from motor.motor_asyncio import AsyncIOMotorClient


BASE = 'http://localhost:8001'
ADMIN_EMAIL = 'admin@healthguard.com'
ADMIN_LEGACY_PW = 'Admin@123'
PROVIDER = 'microsoft_graph'
MS_DOC = 'ms_connection'


async def login() -> str:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f'{BASE}/api/auth/login', json={
            'email': ADMIN_EMAIL, 'password': ADMIN_LEGACY_PW,
        })
        assert r.status_code == 200, f'login failed: {r.text}'
        return r.json()['access_token']


async def main():
    token = await login()
    headers = {'Authorization': f'Bearer {token}'}
    mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
    pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
    print(f'Logged in as {ADMIN_EMAIL}')

    # Clean slate so we can assert "newly created" rows
    await mongo.integrations.delete_one({'_id': MS_DOC})
    await pg.execute('delete from public.integrations where provider=$1', PROVIDER)

    # --- Step 1: email-recipients writes to both stores ---
    target_email = 'audit-binder-test@example.org'
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f'{BASE}/api/ms/email-recipients',
                         json={'email_to': target_email},
                         headers=headers)
        assert r.status_code == 200, r.text
    print('  -> POST /api/ms/email-recipients OK')

    # Mongo
    m = await mongo.integrations.find_one({'_id': MS_DOC})
    assert m and m.get('email_to') == target_email
    print(f'  -> Mongo: email_to={m["email_to"]}')

    # Postgres
    row = await pg.fetchrow(
        "select tokens, config from public.integrations where provider=$1", PROVIDER)
    assert row is not None, 'Postgres integrations row missing'
    import json
    cfg = row['config'] if isinstance(row['config'], dict) else json.loads(row['config'])
    assert cfg.get('email_to') == target_email, f'PG config mismatch: {cfg}'
    print(f'  -> Postgres: config.email_to={cfg["email_to"]}')

    # --- Step 2: Simulate a refresh-token save (via internal helper) ---
    from routers.ms_graph import _ms_save_tokens
    fake_tokens = {
        'refresh_token': 'fake-rt-' + os.urandom(4).hex(),
        'scope': 'offline_access Mail.Send Files.ReadWrite',
    }
    await _ms_save_tokens(fake_tokens, user_email='ownerbox@example.com')
    m2 = await mongo.integrations.find_one({'_id': MS_DOC})
    assert m2.get('refresh_token') == fake_tokens['refresh_token']
    assert m2.get('connected_email') == 'ownerbox@example.com'
    print(f'  -> Mongo refresh_token updated')

    row2 = await pg.fetchrow(
        "select tokens, config from public.integrations where provider=$1", PROVIDER)
    tok = row2['tokens'] if isinstance(row2['tokens'], dict) else json.loads(row2['tokens'])
    cfg2 = row2['config'] if isinstance(row2['config'], dict) else json.loads(row2['config'])
    assert tok.get('refresh_token') == fake_tokens['refresh_token']
    assert cfg2.get('connected_email') == 'ownerbox@example.com'
    # email_to should still be present from Step 1 (JSONB merge)
    assert cfg2.get('email_to') == target_email
    print(f'  -> Postgres tokens.refresh_token + config.connected_email merged')

    # --- Step 3: disconnect deletes from both ---
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(f'{BASE}/api/ms/disconnect', headers=headers)
        assert r.status_code == 200, r.text
    m3 = await mongo.integrations.find_one({'_id': MS_DOC})
    assert m3 is None, f'Mongo not cleared: {m3}'
    n_pg = await pg.fetchval(
        "select count(*) from public.integrations where provider=$1", PROVIDER)
    assert n_pg == 0, f'PG integrations row still present (count={n_pg})'
    print('  -> Both Mongo + Postgres integrations cleared on disconnect')

    await pg.close()
    print('\n*** SLICE J SMOKE TEST PASSED ***')


asyncio.run(main())
