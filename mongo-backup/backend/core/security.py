"""JWT, password hashing, and FastAPI auth dependencies."""
from datetime import datetime, timedelta, timezone
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from passlib.context import CryptContext

from .settings import JWT_SECRET, JWT_ALG, JWT_EXP_MIN
from .db import db
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


async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserPublic:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail='Could not validate credentials',
        headers={'WWW-Authenticate': 'Bearer'},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
        user_id = payload.get('sub')
        if not user_id:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    doc = await db.users.find_one(
        {'id': user_id}, {'_id': 0, 'hashed_password': 0}
    )
    if not doc:
        raise credentials_exception
    return UserPublic(**doc)


def require_admin(current: UserPublic = Depends(get_current_user)) -> UserPublic:
    if current.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin only')
    return current
