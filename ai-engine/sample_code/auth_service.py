import jwt
from datetime import datetime, timedelta

SECRET_KEY = "super-secret-argus-key"

def generate_token(user_id: str) -> str:
    """Generates a signed JWT authentication token valid for 1 hour."""
    payload = {
        "sub": user_id,
        "exp": datetime.utcnow() + timedelta(hours=1)
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")

def verify_token(token: str) -> dict:
    """Decodes and verifies an incoming JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid authentication token")

def login_user(username: str, password_hash: str) -> str:
    """Validates user credentials and returns a JWT token upon success."""
    # In a real app, validate hash against database
    if username == "admin":
        return generate_token(user_id="user_001")
    raise ValueError("Unauthorized")
