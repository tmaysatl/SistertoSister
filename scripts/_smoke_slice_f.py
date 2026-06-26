"""Smoke test Slice F: onboarding step create/toggle/delete dual-write + reads from PG."""
import asyncio, os, httpx
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg


async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post('http://localhost:8001/api/auth/login',
                         json={'email': 'admin@healthguard.com', 'password': 'Admin@123'})
        tok = r.json()['access_token']
        H = {'Authorization': f'Bearer {tok}'}

        pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]

        # Get a caregiver to attach steps to
        cg = (await c.get('http://localhost:8001/api/caregivers', headers=H)).json()
        cg_id = next(x['id'] for x in cg if x['email'] == 'caregiver@healthguard.com')
        baseline_pg = await pg.fetchval(
            'select count(*) from public.onboarding where caregiver_id=$1::uuid', cg_id
        )
        baseline_mongo = await mongo.onboarding.count_documents({'caregiver_id': cg_id})
        print(f'Baseline: pg={baseline_pg} mongo={baseline_mongo} for {cg_id[:8]}')

        # 1. Create a new step
        r = await c.post('http://localhost:8001/api/onboarding', headers=H, json={
            'caregiver_id': cg_id, 'title': 'Slice F Test Step',
            'description': 'verify dual-write', 'completed': False,
        })
        assert r.status_code == 200, r.text
        step = r.json()
        sid = step['id']
        print(f'Created step {sid[:8]}')
        pg_row = await pg.fetchrow(
            'select title, completed from public.onboarding where id=$1::uuid', sid)
        assert pg_row['title'] == 'Slice F Test Step' and pg_row['completed'] is False
        print(f'  -> PG: {dict(pg_row)}')

        # 2. List steps (READ from PG) — should include new one
        steps = (await c.get(f'http://localhost:8001/api/onboarding?caregiver_id={cg_id}',
                              headers=H)).json()
        found = next((s for s in steps if s['id'] == sid), None)
        assert found, f'Step not in /api/onboarding list ({len(steps)} returned)'
        print(f'  -> /onboarding returned {len(steps)} steps; new one included')

        # 3. Toggle: complete it
        r = await c.post(f'http://localhost:8001/api/onboarding/{sid}/toggle', headers=H)
        assert r.status_code == 200
        comp_pg = await pg.fetchval(
            'select completed from public.onboarding where id=$1::uuid', sid)
        assert comp_pg is True
        comp_mongo = (await mongo.onboarding.find_one({'id': sid}))['completed']
        assert comp_mongo is True
        print('  -> toggle completed=true in BOTH DBs')

        # 4. Toggle again -> uncomplete
        r = await c.post(f'http://localhost:8001/api/onboarding/{sid}/toggle', headers=H)
        assert r.status_code == 200
        comp_pg = await pg.fetchval(
            'select completed from public.onboarding where id=$1::uuid', sid)
        assert comp_pg is False
        print('  -> toggle completed=false in BOTH DBs')

        # 5. Delete
        r = await c.delete(f'http://localhost:8001/api/onboarding/{sid}', headers=H)
        assert r.status_code == 200
        after_pg = await pg.fetchval(
            'select count(*) from public.onboarding where id=$1::uuid', sid)
        after_mongo = await mongo.onboarding.count_documents({'id': sid})
        assert after_pg == 0 and after_mongo == 0
        print('  -> Step deleted from BOTH DBs')

        # 6. Parity check
        end_pg = await pg.fetchval(
            'select count(*) from public.onboarding where caregiver_id=$1::uuid', cg_id)
        end_mongo = await mongo.onboarding.count_documents({'caregiver_id': cg_id})
        assert end_pg == baseline_pg, f'PG drift: {baseline_pg} -> {end_pg}'
        assert end_mongo == baseline_mongo, f'Mongo drift: {baseline_mongo} -> {end_mongo}'
        print(f'  -> Final parity OK: pg={end_pg} mongo={end_mongo}')

        await pg.close()
        print('\n*** SLICE F SMOKE TEST PASSED ***')

asyncio.run(main())
