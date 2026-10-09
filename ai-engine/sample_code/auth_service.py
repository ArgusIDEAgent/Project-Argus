import hmac
import os
from datetime import datetime, timedelta, timezone

import jwt  # PyJWT: demo fixture only, intentionally NOT a runtime dependency of the engine


def _secret() -> str:
    key = os.environ.get("ARGUS_DEMO_JWT_SECRET", "")
    if len(key) < 32:
        raise RuntimeError("Set ARGUS_DEMO_JWT_SECRET (>= 32 chars) before using auth_service.")
    return key


def generate_token(user_id: str) -> str:
    """Generates a signed JWT authentication token valid for 1 hour."""
    payload = {"sub": user_id, "exp": datetime.now(timezone.utc) + timedelta(hours=1)}
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_token(token: str) -> dict:
    """Decodes and verifies an incoming JWT token."""
    try:
        return jwt.decode(token, _secret(), algorithms=["HS256"], options={"require": ["exp", "sub"]})
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid authentication token")


def login_user(username: str, password_hash: str) -> str:
    """Validates user credentials and returns a JWT token upon success."""
    expected = os.environ.get("ARGUS_DEMO_ADMIN_HASH", "")
    if expected and username == "admin" and hmac.compare_digest(password_hash.encode(), expected.encode()):
        return generate_token(user_id="user_001")
    raise ValueError("Unauthorized")
