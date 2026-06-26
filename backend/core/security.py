"""JWT, password hashing, and FastAPI auth dependencies.

PHASE 4 cutover: profile lookups now go to Supabase Postgres (the dual-mode
JWT verification still supports both legacy HS256 and Supabase ES256 tokens).
Password hashing still lives in Mongo until /api/auth/login is converted.
"""
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

from .settings import JWT_SECRET, JWT_ALG, JWT_EXP_MIN, SUPABASE_ENABLED
from .db import db
from .supabase_auth import verify_supabase_jwt
from . import supa_data
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
    # Phase 4: read profile from Supabase Postgres first.
    try:
        prof = await supa_data.get_user_by_id(user_id)
        if prof:
            return UserPublic(**prof)
    except Exception:
        pass
    # Fallback to Mongo while migration is in flight.
    doc = await db.users.find_one(
        {'id': user_id}, {'_id': 0, 'hashed_password': 0}
    )
    return UserPublic(**doc) if doc else None


async def _user_from_supabase_jwt(token: str) -> UserPublic | None:
    if not SUPABASE_ENABLED:
        return None
    payload = verify_supabase_jwt(token)
    if not payload:
        return None
    email = payload.get('email') or (payload.get('user_metadata') or {}).get('email')
    user_id = payload.get('sub')
    if user_id:
        try:
            prof = await supa_data.get_user_by_id(user_id)
            if prof:
                return UserPublic(**prof)
        except Exception:
            pass
    if email:
        email = email.lower().strip()
        try:
            prof = await supa_data.get_user_by_email(email)
            if prof:
                return UserPublic(**prof)
        except Exception:
            pass
        # Last resort: Mongo by email
        doc = await db.users.find_one({'email': email}, {'_id': 0, 'hashed_password': 0})
        if doc:
            return UserPublic(**doc)
    return None


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    user = await _user_from_legacy_jwt(token)
    if user:
        return user
    user = await _user_from_supabase_jwt(token)
    if user:
        return user
    raise credentials_exception


def require_admin(current: UserPublic = Depends(get_current_user)) -> UserPublic:
    if current.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin only')
    return current
