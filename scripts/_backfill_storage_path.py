"""Backfill storage_path on Mongo + Postgres for documents that have a
file_base64 blob but no Supabase Storage object yet.

Idempotent — safe to re-run. Skips docs that already have a valid storage_path
in Mongo. Counts: scanned, uploaded, mongo_updated, pg_updated, skipped.
"""
import asyncio, os, sys
from pathlib import Path
sys.path.insert(0, '/app/backend')
from dotenv import load_dotenv
load_dotenv(Path('/app/backend/.env'))

from motor.motor_asyncio import AsyncIOMotorClient
from core import supa_data


async def main():
    mongo = AsyncIOMotorClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
    pool = await supa_data.get_pg_pool()

    scanned = uploaded = mongo_upd = pg_upd = skipped = no_blob = 0

    cursor = mongo.documents.find({}, {'_id': 0})
    async for d in cursor:
        scanned += 1
        doc_id = d.get('id')
        if not doc_id:
            continue
        if d.get('storage_path'):
            skipped += 1
            continue
        b64 = d.get('file_base64')
        if not b64:
            no_blob += 1
            continue
        mime = d.get('mime_type') or 'application/pdf'
        path = supa_data.upload_document_blob_sync(doc_id, b64, mime)
        if not path:
            print(f'  ! upload FAILED for {doc_id} ({d.get("title")})')
            continue
        uploaded += 1
        # Mongo update
        await mongo.documents.update_one(
            {'id': doc_id}, {'$set': {'storage_path': path}}
        )
        mongo_upd += 1
        # Postgres update (just patch storage_path; do not rewrite metadata)
        async with pool.acquire() as conn:
            r = await conn.execute(
                'update public.documents set storage_path=$1 where id=$2',
                path, doc_id,
            )
            if r and r.startswith('UPDATE 1'):
                pg_upd += 1

    print('\n=== BACKFILL SUMMARY ===')
    print(f'Scanned:        {scanned}')
    print(f'Already had:    {skipped}')
    print(f'No blob:        {no_blob}')
    print(f'Uploaded:       {uploaded}')
    print(f'Mongo updated:  {mongo_upd}')
    print(f'PG updated:     {pg_upd}')


asyncio.run(main())
