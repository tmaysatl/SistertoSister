"""Create storage bucket + seed admin user."""
import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / '.env')
from supabase import create_client

url = os.environ['SUPABASE_URL']
key = os.environ['SUPABASE_SERVICE_ROLE_KEY']
bucket = os.environ.get('SUPABASE_STORAGE_BUCKET', 'documents')

sb = create_client(url, key)

# --- Create storage bucket (private) ---
existing = [b.name for b in sb.storage.list_buckets()]
if bucket in existing:
    print(f'Bucket "{bucket}" already exists.')
else:
    sb.storage.create_bucket(bucket, options={'public': False})
    print(f'Bucket "{bucket}" created (private).')

# --- Create admin user via service-role admin API ---
ADMIN_EMAIL = 'admin@healthguard.com'
ADMIN_PASSWORD = 'AdminPassword123!'
ADMIN_NAME = 'PHCP Admin'

CAREGIVER_EMAIL = 'caregiver@healthguard.com'
CAREGIVER_PASSWORD = 'Caregiver123!'
CAREGIVER_NAME = 'Demo Caregiver'

users_list = sb.auth.admin.list_users()
existing_emails = [u.email for u in users_list]

# --- ADMIN ---
if ADMIN_EMAIL in existing_emails:
    print(f'Admin user {ADMIN_EMAIL} already exists.')
    admin_user = next(u for u in users_list if u.email == ADMIN_EMAIL)
else:
    resp = sb.auth.admin.create_user({
        'email': ADMIN_EMAIL,
        'password': ADMIN_PASSWORD,
        'email_confirm': True,
        'user_metadata': {'name': ADMIN_NAME, 'role': 'admin'},
    })
    admin_user = resp.user
    print(f'Admin user created: {admin_user.email} (id={admin_user.id})')

sb.table('profiles').upsert({
    'id': admin_user.id,
    'email': admin_user.email,
    'name': ADMIN_NAME,
    'role': 'admin',
}).execute()
print('Admin profile role set.')

# --- CAREGIVER ---
if CAREGIVER_EMAIL in existing_emails:
    print(f'Caregiver user {CAREGIVER_EMAIL} already exists.')
    cg_user = next(u for u in users_list if u.email == CAREGIVER_EMAIL)
else:
    resp = sb.auth.admin.create_user({
        'email': CAREGIVER_EMAIL,
        'password': CAREGIVER_PASSWORD,
        'email_confirm': True,
        'user_metadata': {'name': CAREGIVER_NAME, 'role': 'caregiver'},
    })
    cg_user = resp.user
    print(f'Caregiver user created: {cg_user.email} (id={cg_user.id})')

sb.table('profiles').upsert({
    'id': cg_user.id,
    'email': cg_user.email,
    'name': CAREGIVER_NAME,
    'role': 'caregiver',
}).execute()
print('Caregiver profile role set.')

# --- Verify ---
prof = sb.table('profiles').select('*').execute()
print(f'Total profiles: {len(prof.data)}')
for p in prof.data:
    print(f"  - {p['email']} ({p['role']})")

