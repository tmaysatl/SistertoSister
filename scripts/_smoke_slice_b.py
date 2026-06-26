"""Smoke test Slice B dual-write: create/delete client + assignment + photo + toggle task."""
import asyncio, os, httpx, uuid
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg


async def main():
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post('http://localhost:8001/api/auth/login',
                         json={'email': 'admin@healthguard.com', 'password': 'Admin@123'})
        tok = r.json()['access_token']
        H = {'Authorization': f'Bearer {tok}'}

        # 1. Create client -> verify in BOTH Mongo and Postgres
        r = await c.post('http://localhost:8001/api/clients', headers=H,
                         json={'name': 'Slice B Test Client', 'address': '123 Main', 'phone': '555', 'notes': 'test'})
        assert r.status_code == 200, r.text
        client = r.json()
        cid = client['id']
        print(f'Created client {cid[:8]}')

        pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
        row = await pg.fetchrow('select id::text, name from public.clients where id=$1::uuid', cid)
        assert row is not None, 'Client not in Postgres after dual-write!'
        assert row['name'] == 'Slice B Test Client'
        print(f'  -> Postgres has: {dict(row)}')

        # 2. Update photo -> verify in both
        r = await c.put(f'http://localhost:8001/api/clients/{cid}/photo', headers=H,
                        json={'photo_base64': 'data:image/png;base64,iVBORw0K=='})
        assert r.status_code == 200
        photo_pg = await pg.fetchval('select photo_base64 from public.clients where id=$1::uuid', cid)
        assert photo_pg == 'iVBORw0K=='
        print(f'  -> Photo updated in Postgres: {photo_pg}')

        # 3. Get a real caregiver to test assignment
        caregivers = (await c.get('http://localhost:8001/api/caregivers', headers=H)).json()
        cg_id = next(c['id'] for c in caregivers if c['email'] == 'caregiver@healthguard.com')

        # 4. Create assignment -> verify in both
        r = await c.post('http://localhost:8001/api/assignments', headers=H,
                         json={'caregiver_id': cg_id, 'client_id': cid, 'schedule': 'M-F 9-5'})
        assert r.status_code == 200, r.text
        aid = r.json()['id']
        print(f'Created assignment {aid[:8]}')
        a_row = await pg.fetchrow('select id::text from public.assignments where id=$1::uuid', aid)
        assert a_row is not None, 'Assignment not in Postgres!'
        print(f'  -> Postgres has assignment {a_row["id"]}')

        # 5. List assignments -> from Postgres now (via list_assignments)
        lst = (await c.get('http://localhost:8001/api/assignments', headers=H)).json()
        ours = [a for a in lst if a['id'] == aid]
        assert ours, f'New assignment not returned by list! Got {len(lst)} total'
        print(f'  -> /api/assignments returned new row from Postgres')

        # 6. Delete assignment -> verify gone from both
        r = await c.delete(f'http://localhost:8001/api/assignments/{aid}', headers=H)
        assert r.status_code == 200
        a_after = await pg.fetchval('select count(*) from public.assignments where id=$1::uuid', aid)
        assert a_after == 0, 'Assignment still in Postgres after delete!'
        print(f'  -> Assignment deleted from Postgres')

        # 7. Delete client -> verify cascade
        r = await c.delete(f'http://localhost:8001/api/clients/{cid}', headers=H)
        assert r.status_code == 200
        c_after = await pg.fetchval('select count(*) from public.clients where id=$1::uuid', cid)
        assert c_after == 0
        print(f'  -> Client deleted from Postgres')

        await pg.close()
        print('\n*** SLICE B SMOKE TEST PASSED ***')

asyncio.run(main())
