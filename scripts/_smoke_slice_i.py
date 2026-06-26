"""Smoke test Slice I: /api/auth/register dual-writes Mongo + Supabase Auth + profiles."""
import asyncio, os, httpx, uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg


async def main():
    # Use a unique email so re-running the test is idempotent
    suffix = uuid.uuid4().hex[:8]
    email = f"slicei-{suffix}@phcp-smoke.io"
    password = "SliceIPass123!"

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post('http://localhost:8001/api/auth/register', json={
            'email': email, 'password': password,
            'name': 'Slice I Test User', 'role': 'caregiver',
        })
        assert r.status_code == 200, r.text
        data = r.json()
        uid = data['user']['id']
        print(f'Registered {email} -> uid {uid[:8]}')

        # Verify in Mongo
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
        m = await mongo.users.find_one({'email': email})
        assert m and m['id'] == uid
        print(f'  -> Mongo user: id={m["id"][:8]} role={m["role"]}')

        # Verify Supabase Auth user with same UUID
        async with httpx.AsyncClient(timeout=15) as h:
            r2 = await h.get(
                f"{os.environ['SUPABASE_URL']}/auth/v1/admin/users",
                headers={'apikey': os.environ['SUPABASE_SERVICE_ROLE_KEY'],
                         'Authorization': f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}"},
                params={'email': email},
            )
            users = r2.json().get('users', [])
            target = next((u for u in users if u['email'].lower() == email.lower()), None)
            assert target is not None, f'Supabase Auth user not found! got {users}'
            assert target['id'] == uid, f'UUID mismatch: mongo={uid}, supabase={target["id"]}'
            print(f'  -> Supabase Auth user: id={target["id"][:8]} matches Mongo UUID')

        # Verify Supabase profiles row
        pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
        prof = await pg.fetchrow(
            'select email, name, role from public.profiles where id=$1::uuid', uid)
        assert prof and prof['email'] == email and prof['role'] == 'caregiver'
        print(f'  -> Postgres profile: {dict(prof)}')

        # Verify the new user can immediately log in via SUPABASE auth
        async with httpx.AsyncClient(timeout=15) as h:
            r3 = await h.post(
                f"{os.environ['SUPABASE_URL']}/auth/v1/token?grant_type=password",
                json={'email': email, 'password': password},
                headers={'apikey': os.environ['SUPABASE_ANON_KEY'],
                         'Content-Type': 'application/json'},
            )
            assert r3.status_code == 200, r3.text
            sb_token = r3.json()['access_token']
            print(f'  -> Supabase login OK (token len {len(sb_token)})')

            # And that the backend accepts the Supabase token for /api/auth/me
            r4 = await c.get('http://localhost:8001/api/auth/me',
                             headers={'Authorization': f'Bearer {sb_token}'})
            assert r4.status_code == 200
            me = r4.json()
            assert me['id'] == uid and me['email'] == email
            print(f'  -> Backend /auth/me via Supabase JWT: id matches Mongo UUID')

        # Verify duplicate-email rejected
        r5 = await c.post('http://localhost:8001/api/auth/register', json={
            'email': email, 'password': 'X', 'name': 'x', 'role': 'caregiver'
        })
        assert r5.status_code == 400
        print('  -> Duplicate email rejected (400)')

        # Cleanup: remove from Mongo + Supabase Auth + Postgres profile
        async with httpx.AsyncClient(timeout=15) as h:
            await h.delete(
                f"{os.environ['SUPABASE_URL']}/auth/v1/admin/users/{uid}",
                headers={'apikey': os.environ['SUPABASE_SERVICE_ROLE_KEY'],
                         'Authorization': f"Bearer {os.environ['SUPABASE_SERVICE_ROLE_KEY']}"},
            )
        await mongo.users.delete_one({'id': uid})
        # profiles row gets removed automatically by FK ON DELETE CASCADE
        prof_after = await pg.fetchval(
            'select count(*) from public.profiles where id=$1::uuid', uid)
        assert prof_after == 0
        print(f'  -> Cleanup complete (cascade removed profile row)')

        await pg.close()
        print('\n*** SLICE I SMOKE TEST PASSED ***')


asyncio.run(main())
