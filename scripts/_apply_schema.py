"""One-off: apply supabase_schema.sql via direct asyncpg connection."""
import os, asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / 'backend' / '.env')
import asyncpg

async def main():
    url = os.environ['SUPABASE_DIRECT_URL']
    conn = await asyncpg.connect(url, timeout=30)
    sql = (Path(__file__).parent / 'supabase_schema.sql').read_text()
    print(f'Applying {len(sql)} bytes of DDL...')
    try:
        await conn.execute(sql)
        print('DDL applied OK.')
    except Exception as e:
        print('DDL FAILED:', repr(e))
        await conn.close()
        return

    rows = await conn.fetch(
        "select table_name from information_schema.tables "
        "where table_schema='public' order by table_name"
    )
    print('Tables created:', len(rows))
    for r in rows:
        print('  -', r['table_name'])

    rlsrows = await conn.fetch(
        "select tablename, rowsecurity from pg_tables "
        "where schemaname='public' order by tablename"
    )
    print('RLS status:')
    for r in rlsrows:
        flag = 'ON' if r['rowsecurity'] else 'OFF'
        print(f"  - {r['tablename']:30} {flag}")

    polrows = await conn.fetch(
        "select tablename, count(*) as n from pg_policies "
        "where schemaname='public' group by tablename order by tablename"
    )
    print('Policy counts:')
    for r in polrows:
        print(f"  - {r['tablename']:30} {r['n']}")

    await conn.close()

asyncio.run(main())
