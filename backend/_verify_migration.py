"""Sanity checks on migrated Supabase data."""
import os, asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')
import asyncpg


async def main():
    conn = await asyncpg.connect(os.environ['SUPABASE_DIRECT_URL'], timeout=20)

    # Admin profile
    admin = await conn.fetchrow(
        "select id::text, email, role from public.profiles where email=$1",
        'admin@healthguard.com',
    )
    print('Admin profile:', dict(admin))

    # Documents with storage paths
    docs_with_storage = await conn.fetchval(
        "select count(*) from public.documents where storage_path is not null"
    )
    docs_total = await conn.fetchval("select count(*) from public.documents")
    print(f'Documents with storage_path: {docs_with_storage}/{docs_total}')

    # Assignments
    assignments = await conn.fetch(
        """select p.name as caregiver, c.name as client, a.created_at
           from public.assignments a
           join public.profiles p on p.id = a.caregiver_id
           join public.clients c on c.id = a.client_id"""
    )
    print(f'Assignments ({len(assignments)}):')
    for a in assignments:
        print(f"  - {a['caregiver']} -> {a['client']}")

    # Shifts
    shifts = await conn.fetch(
        """select s.status, s.kind, s.start_time, s.end_time,
                  p.name as caregiver, c.name as client
           from public.shifts s
           join public.profiles p on p.id = s.caregiver_id
           join public.clients c on c.id = s.client_id"""
    )
    print(f'Shifts ({len(shifts)}):')
    for s in shifts:
        print(f"  - {s['caregiver']} -> {s['client']} {s['start_time']}-{s['end_time']} {s['kind']} {s['status']}")

    # Document categories
    cats = await conn.fetch(
        "select category, count(*) as n from public.documents group by category order by n desc"
    )
    print('Document categories:')
    for c in cats:
        print(f"  - {c['category']:25} {c['n']}")

    # Caregiver onboarding progress
    onb = await conn.fetch(
        """select p.name, count(*) filter (where o.completed) as done, count(*) as total
           from public.onboarding o join public.profiles p on p.id = o.caregiver_id
           group by p.name order by total desc"""
    )
    print(f'Onboarding (top 5):')
    for o in onb[:5]:
        print(f"  - {o['name']:30} {o['done']}/{o['total']}")

    await conn.close()


asyncio.run(main())
