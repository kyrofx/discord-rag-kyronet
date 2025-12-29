"""
API authentication middleware.
"""
import os
import hmac
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional

security = HTTPBearer(auto_error=False)

# API key from environment
API_KEY = os.getenv("API_KEY", "")


class AuthError(HTTPException):
    """Authentication error with standardized format."""
    def __init__(self, code: str, message: str, status_code: int = 401):
        super().__init__(
            status_code=status_code,
            detail={"error": {"code": code, "message": message}}
        )


async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> str:
    """
    Verify the API key from Authorization header.

    Returns the API key if valid, raises AuthError otherwise.
    """
    if not API_KEY:
        # No API key configured - allow all requests (dev mode)
        return "dev"

    if not credentials:
        raise AuthError(
            code="unauthorized",
            message="Missing Authorization header. Use: Authorization: Bearer <API_KEY>"
        )

    if credentials.scheme.lower() != "bearer":
        raise AuthError(
            code="unauthorized",
            message="Invalid authorization scheme. Use: Authorization: Bearer <API_KEY>"
        )

    # Use constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(credentials.credentials, API_KEY):
        raise AuthError(
            code="unauthorized",
            message="Invalid API key"
        )

    return credentials.credentials


async def optional_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)
) -> Optional[str]:
    """
    Optional API key verification - returns None if not provided.
    Useful for endpoints that work with or without auth.
    """
    if not credentials:
        return None

    try:
        return await verify_api_key(credentials)
    except AuthError:
        return None
