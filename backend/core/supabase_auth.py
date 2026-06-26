"""Supabase JWT verification helpers.

Supports both new asymmetric JWTs (ES256 / RS256 via JWKS) and the legacy
HS256 shared-secret scheme. The PyJWKClient transparently fetches and caches
the public keys from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`.
"""
from __future__ import annotations
from typing import Optional, Dict, Any
import jwt as _jwt
from jwt import PyJWKClient

from .settings import (
    SUPABASE_JWT_SECRET,
    SUPABASE_ENABLED,
    SUPABASE_URL,
)

SUPABASE_JWT_AUDIENCE = 'authenticated'
SUPABASE_ASYMM_ALGORITHMS = ['ES256', 'RS256']
SUPABASE_LEGACY_ALG = 'HS256'

_jwks_client: Optional[PyJWKClient] = None


def _get_jwks_client() -> Optional[PyJWKClient]:
    global _jwks_client
    if _jwks_client is None and SUPABASE_URL:
        jwks_url = f"{SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(jwks_url, cache_jwk_set=True, lifespan=3600)
    return _jwks_client


def verify_supabase_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Return the decoded payload if valid; None otherwise.

    Never raises — returning None lets the dual-mode dependency fall through.
    """
    if not SUPABASE_ENABLED:
        return None

    # Inspect header to choose verification strategy.
    try:
        header = _jwt.get_unverified_header(token)
    except Exception:
        return None
    alg = header.get('alg', '')

    # --- Asymmetric (new Supabase JWT signing keys) ---
    if alg in SUPABASE_ASYMM_ALGORITHMS:
        client = _get_jwks_client()
        if client is None:
            return None
        try:
            signing_key = client.get_signing_key_from_jwt(token).key
            payload = _jwt.decode(
                token,
                signing_key,
                algorithms=SUPABASE_ASYMM_ALGORITHMS,
                audience=SUPABASE_JWT_AUDIENCE,
            )
            return payload
        except _jwt.PyJWTError:
            return None
        except Exception:
            return None

    # --- Legacy HS256 shared-secret ---
    if alg == SUPABASE_LEGACY_ALG and SUPABASE_JWT_SECRET:
        try:
            payload = _jwt.decode(
                token,
                SUPABASE_JWT_SECRET,
                algorithms=[SUPABASE_LEGACY_ALG],
                audience=SUPABASE_JWT_AUDIENCE,
            )
            return payload
        except _jwt.PyJWTError:
            return None
        except Exception:
            return None

    return None
