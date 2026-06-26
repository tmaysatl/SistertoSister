"""Smoke test Slice G: packet share + view + sign dual-write."""
import asyncio, os, httpx, base64
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg


# Tiny PDF for signature target
MINI_PDF = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 300]/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 20>>stream\nBT /F1 12 Tf 10 100 Td (x) Tj ET\nendstream endobj\n"
    b"xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n248\n%%EOF\n"
).decode()

# 1x1 PNG signature image
PNG_1x1 = base64.b64encode(bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4'
    '890000000d49444154789c63f8cf00000000000ff0001a4c1a3d0000000049'
    '454e44ae426082'
)).decode()


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post('http://localhost:8001/api/auth/login',
                         json={'email': 'admin@healthguard.com', 'password': 'Admin@123'})
        tok = r.json()['access_token']
        H = {'Authorization': f'Bearer {tok}'}

        pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]

        # 1. Create a packet share (caregiver_onboarding category)
        r = await c.post('http://localhost:8001/api/packets/share', headers=H, json={
            'recipient_name': 'Smoke G Tester',
            'recipient_role': 'caregiver',
            'category': 'caregiver_onboarding',
            'delivery': 'link',
        })
        assert r.status_code == 200, r.text
        token = r.json()['token']
        print(f'Created packet token {token[:12]}')
        pg_row = await pg.fetchrow(
            "select recipient_name, category, viewed_at, completed_at, array_length(signed_ids,1) as n_signed "
            "from public.packet_shares where token=$1", token)
        assert pg_row['recipient_name'] == 'Smoke G Tester'
        assert pg_row['viewed_at'] is None
        print(f'  -> PG: {dict(pg_row)}')

        # 2. View the packet (public, no auth)
        r = await c.get(f'http://localhost:8001/api/packets/{token}')
        assert r.status_code == 200, r.text
        body = r.json()
        n_docs = len(body['documents'])
        assert body['packet']['viewed_at'] is not None
        print(f'  -> viewed: {n_docs} docs in packet')
        pg_viewed = await pg.fetchval(
            'select viewed_at from public.packet_shares where token=$1', token)
        assert pg_viewed is not None
        print('  -> viewed_at mirrored to PG')

        # 3. Create a one-off document we can sign without polluting (an existing caregiver_onboarding doc)
        # We'll use the first doc in the packet
        if n_docs == 0:
            # Create a test doc
            r = await c.post('http://localhost:8001/api/documents', headers=H, json={
                'title': 'Slice G smoke test doc',
                'category': 'caregiver_onboarding',
                'file_base64': MINI_PDF,
                'mime_type': 'application/pdf',
            })
            test_doc_id = r.json()['id']
        else:
            test_doc_id = body['documents'][0]['id']
        print(f'Signing doc {test_doc_id[:8]}')

        # 4. Submit signature (public endpoint)
        r = await c.post(
            f'http://localhost:8001/api/packets/{token}/sign/{test_doc_id}',
            json={'signature_base64': PNG_1x1},
        )
        assert r.status_code == 200, r.text
        signed_doc_id = r.json()['signed_doc_id']
        print(f'  -> signed_doc {signed_doc_id[:8]}')

        # 5. Verify signed doc in BOTH Mongo AND Postgres + Storage
        pg_signed = await pg.fetchrow(
            'select title, storage_path from public.documents where id=$1::uuid',
            signed_doc_id,
        )
        assert pg_signed is not None
        assert pg_signed['storage_path'] is not None
        print(f'  -> PG signed doc: {dict(pg_signed)}')

        # 6. Verify signed_ids array in PG includes test_doc_id
        pg_ids = await pg.fetchval(
            'select array(select x::text from unnest(signed_ids) x) from public.packet_shares where token=$1',
            token,
        )
        assert test_doc_id in pg_ids
        print(f'  -> signed_ids in PG: {len(pg_ids)} ids; contains target ✓')

        # 7. Cleanup
        from supabase import create_client
        sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
        sb.storage.from_('documents').remove([pg_signed['storage_path']])
        await pg.execute('delete from public.documents where id=$1::uuid', signed_doc_id)
        await pg.execute('delete from public.packet_shares where token=$1', token)
        await mongo.documents.delete_one({'id': signed_doc_id})
        await mongo.packet_shares.delete_one({'token': token})
        print('  -> Cleanup done')

        await pg.close()
        print('\n*** SLICE G SMOKE TEST PASSED ***')

asyncio.run(main())
