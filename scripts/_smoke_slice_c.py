"""Smoke test Slice C: document upload + storage + signed URL + delete dual-write."""
import asyncio, os, httpx, base64
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))
import asyncpg


# Tiny valid PDF (1 page, "hi" text) so we have something to upload
MINI_PDF = base64.b64encode(
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R>>endobj\n"
    b"4 0 obj<</Length 24>>stream\nBT /F1 12 Tf 10 100 Td (hi) Tj ET\nendstream endobj\n"
    b"xref\n0 5\n0000000000 65535 f\n0000000009 00000 n\n"
    b"trailer<</Size 5/Root 1 0 R>>\nstartxref\n248\n%%EOF\n"
).decode()


async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post('http://localhost:8001/api/auth/login',
                         json={'email': 'admin@healthguard.com', 'password': 'Admin@123'})
        tok = r.json()['access_token']
        H = {'Authorization': f'Bearer {tok}'}

        # 1. Create document with PDF blob
        r = await c.post('http://localhost:8001/api/documents', headers=H,
                         json={
                             'title': 'Slice C Smoke Doc',
                             'category': 'caregiver',
                             'file_base64': MINI_PDF,
                             'mime_type': 'application/pdf',
                             'notes': 'test',
                         })
        assert r.status_code == 200, r.text
        doc = r.json()
        did = doc['id']
        print(f'Created doc {did[:8]}')

        # 2. Verify in Postgres + storage_path is set
        pg = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'])
        row = await pg.fetchrow('select title, storage_path from public.documents where id=$1::uuid', did)
        assert row is not None, 'Doc not in Postgres!'
        assert row['storage_path'] == f'documents/{did}.pdf', f"Wrong path: {row['storage_path']}"
        print(f'  -> PG row: {dict(row)}')

        # 3. Verify file in Supabase Storage
        from supabase import create_client
        sb = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_ROLE_KEY'])
        files = sb.storage.from_('documents').list('documents', {'limit': 1000})
        names = {f['name'] for f in files}
        assert f'{did}.pdf' in names, f'Storage missing {did}.pdf'
        print(f'  -> Storage has {did}.pdf')

        # 4. Get signed URL via new endpoint
        r = await c.get(f'http://localhost:8001/api/documents/{did}/url', headers=H)
        assert r.status_code == 200, r.text
        signed = r.json()
        print(f"  -> signed URL: {signed['url'][:80]}...  expires_in={signed['expires_in']}")
        # Verify the URL actually works (no auth needed for signed URLs)
        head = await c.get(signed['url'], follow_redirects=True)
        assert head.status_code == 200, f"signed URL did not return PDF: {head.status_code}"
        assert head.headers.get('content-type', '').startswith('application/pdf')
        print(f'  -> signed URL fetch OK, content-type={head.headers.get("content-type")}')

        # 5. Delete the document -> verify cleanup in BOTH DBs and Storage
        r = await c.delete(f'http://localhost:8001/api/documents/{did}', headers=H)
        assert r.status_code == 200
        c_pg = await pg.fetchval('select count(*) from public.documents where id=$1::uuid', did)
        assert c_pg == 0
        files_after = sb.storage.from_('documents').list('documents', {'limit': 1000})
        names_after = {f['name'] for f in files_after}
        assert f'{did}.pdf' not in names_after, f'Storage still has {did}.pdf!'
        print(f'  -> Cleanup confirmed: PG row gone, Storage file gone')

        await pg.close()
        print('\n*** SLICE C SMOKE TEST PASSED ***')

asyncio.run(main())
