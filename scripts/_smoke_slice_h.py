"""Smoke test Slice H: policies (acknowledgments) + training dual-write."""
import asyncio, os, httpx
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg


async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        a_tok = (await c.post('http://localhost:8001/api/auth/login',
                              json={'email': 'admin@healthguard.com', 'password': 'Admin@123'})).json()['access_token']
        c_tok = (await c.post('http://localhost:8001/api/auth/login',
                              json={'email': 'caregiver@healthguard.com', 'password': 'Caregiver@123'})).json()['access_token']
        AH = {'Authorization': f'Bearer {a_tok}'}
        CH = {'Authorization': f'Bearer {c_tok}'}

        pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]

        cg_id = (await c.get('http://localhost:8001/api/auth/me', headers=CH)).json()['id']

        # ===== POLICIES =====
        # Find a policy doc
        policies = [d for d in (await c.get('http://localhost:8001/api/documents',
                                            headers=AH)).json()
                    if d.get('category') == 'policy']
        if not policies:
            print('No policy docs to ack — creating one')
            r = await c.post('http://localhost:8001/api/documents', headers=AH, json={
                'title': 'Slice H Test Policy', 'category': 'policy',
            })
            policy_id = r.json()['id']
        else:
            policy_id = policies[0]['id']
        print(f'Acknowledging policy {policy_id[:8]} as caregiver {cg_id[:8]}')

        # Baseline acks for this caregiver
        baseline_pg = await pg.fetchval(
            'select count(*) from public.policy_acks where user_id=$1::uuid', cg_id)

        # 1. POST /policies/acknowledge
        r = await c.post('http://localhost:8001/api/policies/acknowledge', headers=CH,
                         json={'policy_id': policy_id})
        assert r.status_code == 200, r.text
        ack = r.json()
        print(f'  ack id={ack["id"][:8]}')
        # Verify in PG (UPSERT — exactly one row for that pair)
        pg_count = await pg.fetchval(
            'select count(*) from public.policy_acks where policy_id=$1 and user_id=$2::uuid',
            policy_id, cg_id)
        assert pg_count == 1
        print(f'  -> PG has 1 ack for (policy, user)')

        # 2. Re-ack -> should still be 1 (UPSERT)
        await c.post('http://localhost:8001/api/policies/acknowledge', headers=CH,
                     json={'policy_id': policy_id})
        pg_count = await pg.fetchval(
            'select count(*) from public.policy_acks where policy_id=$1 and user_id=$2::uuid',
            policy_id, cg_id)
        assert pg_count == 1
        print('  -> Re-ack is idempotent in PG')

        # 3. List acks (read from PG)
        acks = (await c.get('http://localhost:8001/api/policies/acknowledgments',
                            headers=CH)).json()
        assert any(a['policy_id'] == policy_id for a in acks)
        print(f'  -> /policies/acknowledgments returned {len(acks)} acks (read from PG)')

        # 4. Delete the ack
        r = await c.delete(f'http://localhost:8001/api/policies/acknowledge/{policy_id}',
                           headers=CH)
        assert r.status_code == 200
        pg_count = await pg.fetchval(
            'select count(*) from public.policy_acks where policy_id=$1 and user_id=$2::uuid',
            policy_id, cg_id)
        assert pg_count == 0
        print('  -> ack deleted from PG')

        # Parity check
        end_pg = await pg.fetchval(
            'select count(*) from public.policy_acks where user_id=$1::uuid', cg_id)
        assert end_pg == baseline_pg

        # ===== TRAINING =====
        # Create a training item
        r = await c.post('http://localhost:8001/api/training', headers=AH,
                         json={'title': 'Slice H Training', 'description': 'HIPAA basics',
                               'required': True})
        assert r.status_code == 200, r.text
        tid = r.json()['id']
        print(f'\nCreated training {tid[:8]}')
        pg_t = await pg.fetchrow(
            'select title, required from public.training where id=$1::uuid', tid)
        assert pg_t['title'] == 'Slice H Training' and pg_t['required'] is True
        print(f'  -> PG: {dict(pg_t)}')

        # List training (read from PG)
        items = (await c.get('http://localhost:8001/api/training', headers=AH)).json()
        assert any(t['id'] == tid for t in items)
        print(f'  -> /training returned {len(items)} items (read from PG)')

        # Complete training (caregiver)
        r = await c.post(f'http://localhost:8001/api/training/{tid}/complete', headers=CH)
        assert r.status_code == 200
        n_comp = await pg.fetchval(
            'select count(*) from public.training_completions '
            'where training_id=$1::uuid and caregiver_id=$2::uuid', tid, cg_id)
        assert n_comp == 1
        print(f'  -> completion logged in PG')

        # Idempotent: re-completing returns same record
        await c.post(f'http://localhost:8001/api/training/{tid}/complete', headers=CH)
        n_comp = await pg.fetchval(
            'select count(*) from public.training_completions '
            'where training_id=$1::uuid and caregiver_id=$2::uuid', tid, cg_id)
        assert n_comp == 1
        print('  -> re-complete is idempotent')

        # List completions for caregiver
        comps = (await c.get('http://localhost:8001/api/training/completions',
                              headers=CH)).json()
        assert any(comp['training_id'] == tid for comp in comps)
        print(f'  -> /training/completions returned {len(comps)} (read from PG)')

        # Delete training -> CASCADE clears completion
        r = await c.delete(f'http://localhost:8001/api/training/{tid}', headers=AH)
        assert r.status_code == 200
        n_after = await pg.fetchval(
            'select count(*) from public.training where id=$1::uuid', tid)
        n_comp_after = await pg.fetchval(
            'select count(*) from public.training_completions where training_id=$1::uuid', tid)
        assert n_after == 0 and n_comp_after == 0
        print('  -> training + completions cascaded out of PG')

        await pg.close()
        print('\n*** SLICE H SMOKE TEST PASSED ***')

asyncio.run(main())
