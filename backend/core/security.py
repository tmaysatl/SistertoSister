"""JWT, password hashing, and FastAPI auth dependencies.

DUAL-MODE: validates legacy backend JWTs first, then Supabase JWTs.
When a Supabase JWT is presented, we look up the matching MongoDB user by
email so the rest of the app keeps using its existing user IDs unchanged.
"""
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

from .settings import JWT_SECRET, JWT_ALG, JWT_EXP_MIN, SUPABASE_ENABLED
from .db import db
from .supabase_auth import verify_supabase_jwt
from models import UserPublic

pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')
oauth2_scheme = OAuth2PasswordBearer(tokenUrl='/api/auth/login')


def hash_password(pw: str) -> str:
    return pwd_context.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    return pwd_context.verify(pw, hashed)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXP_MIN)
    to_encode.update({'exp': expire})
    return jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALG)


async def _user_from_legacy_jwt(token: str) -> UserPublic | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except JWTError:
        return None
    user_id = payload.get('sub')
    if not user_id:
        return None
    doc = await db.users.find_one(
        {'id': user_id}, {'_id': 0, 'hashed_password': 0}
    )
    return UserPublic(**doc) if doc else None


async def _user_from_supabase_jwt(token: str) -> UserPublic | None:
    """Resolve a Supabase JWT to the MongoDB user record (by email).

    If no matching Mongo user exists, we lazily create one so that
    Supabase-only accounts (e.g. seeded admin) can still drive the app.
    """
    if not SUPABASE_ENABLED:
        return None
    payload = verify_supabase_jwt(token)
    if not payload:
        return None
    email = payload.get('email') or (payload.get('user_metadata') or {}).get('email')
    if not email:
        return None
    email = email.lower().strip()
    doc = await db.users.find_one({'email': email}, {'_id': 0, 'hashed_password': 0})
    if doc:
        return UserPublic(**doc)
    # Lazy-create a Mongo user record so existing endpoints keep working.
    meta = payload.get('user_metadata') or {}
    role = meta.get('role') or 'caregiver'
    name = meta.get('name') or email.split('@')[0]
    new_user = {
        'id': payload.get('sub') or email,
        'email': email,
        'name': name,
        'role': role if role in ('admin', 'caregiver') else 'caregiver',
        'created_at': datetime.now(timezone.utc).isoformat(),
    }
    try:
        await db.users.insert_one({**new_user, 'hashed_password': ''})
    except Exception:
        pass
    return UserPublic(**new_user)


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    # Try legacy JWT first (fast path, unchanged behaviour).
    user = await _user_from_legacy_jwt(token)
    if user:
        return user
    # Fall back to Supabase JWT.
    user = await _user_from_supabase_jwt(token)
    if user:
        return user
    raise credentials_exception


def require_admin(current: UserPublic = Depends(get_current_user)) -> UserPublic:
    if current.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin only')
    return current
