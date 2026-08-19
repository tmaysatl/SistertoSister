"""
NOTE: you probably don't need to run this script. seed_templates() in
server.py was fixed (2026-08) to backfill an existing empty policy row the
next time an admin taps "Templates" in the Document Vault screen -- that's
the simplest path and requires no server/shell access. This script does the
same backfill directly against the database, for cases where running it
from the app isn't convenient (e.g. scripted/ops use, or before the admin
UI is redeployed).

One-time backfill: attach a real, generated PDF to any already-seeded policy
document that predates the seed() / POLICY_BODIES fix (2026-08) and is still
sitting with no file attached (the original seed-templates flow created bare
policy rows and never called build_policy_pdf(), which is why caregivers saw
"Read" open a blank/broken screen for policy documents).

Safe to run more than once -- it only touches documents where
category == "policy" and file_base64 is empty/missing, so it will NEVER
overwrite a policy an admin has already uploaded a custom file for. It does
not touch client_onboarding / caregiver_onboarding template stubs -- those
are backfilled by the existing /api/documents/rebuild-fillable endpoint,
which is now also called automatically from the "Templates" button.

This does not modify any schema, does not delete anything, and does not
touch MongoDB connection settings -- it reuses the exact same core.db /
core.supa_data modules the running API server uses, so it must be run in an
environment where those already work (i.e. the deployed backend, or a shell
with the same .env the API server uses). It will NOT work from a sandbox
that doesn't have network access to your MongoDB and Supabase project.

Usage (from the backend/ directory):

    python -m scripts.backfill_policy_pdfs --dry-run   # preview only, writes nothing
    python -m scripts.backfill_policy_pdfs             # actually backfill
"""
import argparse
import asyncio
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db import db  # noqa: E402
from core import supa_data  # noqa: E402
from forms import build_policy_pdf  # noqa: E402


async def main(dry_run: bool = False) -> None:
    cursor = db.documents.find(
        {
            "category": "policy",
            "$or": [{"file_base64": None}, {"file_base64": ""}],
        },
        {"_id": 0},
    )
    docs = await cursor.to_list(500)

    if not docs:
        print("Nothing to backfill -- every policy document already has a file attached.")
        return

    print(f"Found {len(docs)} policy document(s) with no file attached:")
    for d in docs:
        print(f"  - {d.get('title')}  (id={d.get('id')})")

    if dry_run:
        print("\n--dry-run set: no changes written.")
        return

    updated = 0
    for d in docs:
        title = d.get("title", "")
        doc_id = d.get("id")
        if not doc_id:
            print(f"  ! skipped {title!r}: no id field, looks malformed")
            continue
        try:
            pdf_bytes = build_policy_pdf(title)
        except Exception as e:
            print(f"  ! skipped {title!r}: build_policy_pdf failed: {e}")
            continue

        file_b64 = base64.b64encode(pdf_bytes).decode()
        mime_type = "application/pdf"
        notes = "Auto-generated from agency policy library (backfilled)"

        # Same dual-write order as create_document(): Storage upload first,
        # then persist storage_path on Mongo + Postgres so we never end up
        # with a phantom path if the Storage upload actually failed.
        storage_path = supa_data.upload_document_blob_sync(doc_id, file_b64, mime_type)

        await db.documents.update_one(
            {"id": doc_id},
            {"$set": {
                "file_base64": file_b64,
                "mime_type": mime_type,
                "notes": notes,
                "storage_path": storage_path,
            }},
        )

        if storage_path:
            pg_doc = dict(d)
            pg_doc.update({
                "file_base64": file_b64,
                "mime_type": mime_type,
                "notes": notes,
                "storage_path": storage_path,
            })
            try:
                await supa_data.upsert_document(pg_doc)
            except Exception as e:
                print(f"  ! {title!r}: Postgres mirror failed (Mongo write still succeeded): {e}")

        updated += 1
        print(f"  + backfilled {title!r} (storage_path={storage_path!r})")

    print(f"\nDone. {updated}/{len(docs)} document(s) backfilled.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would change without writing anything.",
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
