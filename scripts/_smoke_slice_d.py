"""Smoke test Slice D: shift CRUD + clock-in/out dual-write to Mongo + Postgres."""
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

        # Need a caregiver + client for FKs
        cg = (await c.get('http://localhost:8001/api/caregivers', headers=H)).json()
        cg_id = next(x['id'] for x in cg if x['email'] == 'caregiver@healthguard.com')
        clients = (await c.get('http://localhost:8001/api/clients', headers=H)).json()
        client_id = clients[0]['id']
        print(f'Caregiver {cg_id[:8]}, Client {client_id[:8]}')

        # 1. Create one-off shift
        r = await c.post('http://localhost:8001/api/shifts', headers=H, json={
            'caregiver_id': cg_id, 'client_id': client_id,
            'kind': 'one_off', 'date': '2026-08-15',
            'start_time': '09:00', 'end_time': '17:00',
            'notes': 'smoke test one_off',
        })
        assert r.status_code == 200, r.text
        sid_one = r.json()['id']
        print(f'Created one_off {sid_one[:8]}')
        pg_row = await pg.fetchrow(
            "select status, to_char(date,'YYYY-MM-DD') as d, start_time, notes "
            "from public.shifts where id=$1::uuid", sid_one)
        assert pg_row is not None and pg_row['d'] == '2026-08-15'
        print(f'  -> PG: {dict(pg_row)}')

        # 2. Update notes
        r = await c.put(f'http://localhost:8001/api/shifts/{sid_one}', headers=H,
                        json={'notes': 'updated by smoke'})
        assert r.status_code == 200
        new_notes = await pg.fetchval('select notes from public.shifts where id=$1::uuid', sid_one)
        assert new_notes == 'updated by smoke'
        print(f'  -> PG notes updated')

        # 3. Clock in
        r = await c.post(f'http://localhost:8001/api/shifts/{sid_one}/clock-in', headers=H,
                         json={'location': '37.7749,-122.4194'})
        assert r.status_code == 200, r.text
        cin = await pg.fetchrow(
            "select status, clocked_in_at, clock_location::text as loc "
            "from public.shifts where id=$1::uuid", sid_one)
        assert cin['status'] == 'in_progress'
        assert cin['clocked_in_at'] is not None
        print(f"  -> clock_in: status={cin['status']}, loc={cin['loc']}")

        # 4. Clock out
        r = await c.post(f'http://localhost:8001/api/shifts/{sid_one}/clock-out', headers=H,
                         json={})
        assert r.status_code == 200
        cout = await pg.fetchval('select status from public.shifts where id=$1::uuid', sid_one)
        assert cout == 'completed'
        print(f'  -> clock_out: status={cout}')

        # 5. Create RECURRING shift -> expect parent + children in PG
        r = await c.post('http://localhost:8001/api/shifts', headers=H, json={
            'caregiver_id': cg_id, 'client_id': client_id,
            'kind': 'recurring', 'date': '2026-09-01',
            'weekdays': ['MON', 'WED', 'FRI'],
            'recurring_until': '2026-09-30',
            'start_time': '08:00', 'end_time': '12:00',
            'notes': 'smoke recurring',
        })
        assert r.status_code == 200, r.text
        sid_rec = r.json()['id']
        kids = await pg.fetchval(
            'select count(*) from public.shifts where parent_shift_id=$1::uuid', sid_rec
        )
        print(f'Recurring parent {sid_rec[:8]} -> {kids} child shifts in PG')
        assert kids > 0, 'Recurring children not in Postgres!'

        # 6. List shifts -> should include the new one_off + recurring children
        r = await c.get(
            f'http://localhost:8001/api/shifts?caregiver_id={cg_id}',
            headers=H,
        )
        assert r.status_code == 200
        lst = r.json()
        # Recurring children should appear; parent (recurring kind) should not
        kinds = {x['kind'] for x in lst}
        assert 'recurring' not in kinds, f'Recurring parent leaked: {kinds}'
        new_ones = [x for x in lst if x['id'] == sid_one]
        assert new_ones, 'Updated one_off not returned by /shifts'
        print(f'  -> /shifts returned {len(lst)} one_off rows; recurring parent excluded')

        # 7. Delete recurring -> cascades to children
        r = await c.delete(f'http://localhost:8001/api/shifts/{sid_rec}', headers=H)
        assert r.status_code == 200
        after = await pg.fetchval(
            'select count(*) from public.shifts where id=$1::uuid or parent_shift_id=$1::uuid',
            sid_rec,
        )
        assert after == 0, f'Cascade failed, still {after} rows'
        print('  -> Recurring + children cleared from PG')

        # 8. Delete the one_off
        r = await c.delete(f'http://localhost:8001/api/shifts/{sid_one}', headers=H)
        assert r.status_code == 200
        one_after = await pg.fetchval('select count(*) from public.shifts where id=$1::uuid', sid_one)
        assert one_after == 0
        print('  -> One-off cleared from PG')

        await pg.close()
        print('\n*** SLICE D SMOKE TEST PASSED ***')

asyncio.run(main())
