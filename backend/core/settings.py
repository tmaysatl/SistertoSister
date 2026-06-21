"""Centralised env / configuration values.

Keep this file dependency-free so any other module can import it.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]  # /app/backend
load_dotenv(ROOT_DIR / '.env')

# --- Core infrastructure ---
MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']

# --- Auth / JWT ---
JWT_SECRET = os.environ['JWT_SECRET_KEY']
JWT_ALG = os.environ.get('JWT_ALGORITHM', 'HS256')
JWT_EXP_MIN = int(os.environ.get('JWT_ACCESS_TOKEN_EXPIRE_MINUTES', '10080'))

# --- 3rd-party integrations ---
EMERGENT_LLM_KEY = os.environ['EMERGENT_LLM_KEY']
EMERGENT_PUSH_KEY = os.environ.get('EMERGENT_PUSH_KEY', 'placeholder')
PUSH_BASE_URL = 'https://integrations.emergentagent.com'

# --- Microsoft 365 / Graph ---
MS_TENANT_ID = os.environ.get('MS_TENANT_ID', '')
MS_CLIENT_ID = os.environ.get('MS_CLIENT_ID', '')
MS_CLIENT_SECRET = os.environ.get('MS_CLIENT_SECRET', '')
MS_REDIRECT_URI = os.environ.get('MS_REDIRECT_URI', '')
MS_SCOPES = os.environ.get(
    'MS_SCOPES', 'Files.ReadWrite Mail.Send User.Read'
).split()
MS_AUTHORITY = (
    f'https://login.microsoftonline.com/{MS_TENANT_ID}' if MS_TENANT_ID else ''
)
MS_BINDER_FOLDER = os.environ.get('MS_BINDER_FOLDER', 'Audit-Binders')
MS_BINDER_TZ = os.environ.get('MS_BINDER_TIMEZONE', 'America/New_York')
