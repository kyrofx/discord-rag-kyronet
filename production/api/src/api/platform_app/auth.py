"""
Authentication for platform mode.

Provides session-based authentication for the platform UI.
"""
import logging
from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from platform_app.database import (
    get_user_by_id,
    get_session_by_token,
    verify_password,
    create_session,
    delete_session,
    update_last_login,
    get_user_by_username,
)
from platform_app.models import UserRole, UserStatus

logger = logging.getLogger(__name__)

# HTTP Bearer for API access
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user_from_cookie(request: Request) -> Optional[dict]:
    """Get the current user from the session cookie."""
    session_token = request.cookies.get("platform_session")
    if not session_token:
        return None

    session = await get_session_by_token(session_token)
    if not session:
        return None

    user = await get_user_by_id(session["user_id"])
    if not user:
        return None

    if user["status"] != UserStatus.ACTIVE.value:
        return None

    return user


async def get_current_user_from_bearer(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> Optional[dict]:
    """Get the current user from the Bearer token."""
    if not credentials:
        return None

    session = await get_session_by_token(credentials.credentials)
    if not session:
        return None

    user = await get_user_by_id(session["user_id"])
    if not user:
        return None

    if user["status"] != UserStatus.ACTIVE.value:
        return None

    return user


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> Optional[dict]:
    """Get the current user from either cookie or bearer token."""
    # Try bearer token first (for API calls)
    user = await get_current_user_from_bearer(credentials)
    if user:
        return user

    # Fall back to cookie (for browser sessions)
    return await get_current_user_from_cookie(request)


async def require_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> dict:
    """Require an authenticated user."""
    user = await get_current_user(request, credentials)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return user


async def require_admin(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme)
) -> dict:
    """Require an authenticated admin user."""
    user = await require_user(request, credentials)
    if user["role"] != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=403,
            detail="Admin privileges required"
        )
    return user


async def authenticate_user(username: str, password: str) -> Optional[dict]:
    """Authenticate a user by username and password."""
    user = await get_user_by_username(username)
    if not user:
        return None

    if not verify_password(password, user["password_hash"], user["password_salt"]):
        return None

    if user["status"] != UserStatus.ACTIVE.value:
        return None

    return user


async def login_user(username: str, password: str) -> Optional[dict]:
    """Authenticate and create a session for a user."""
    user = await authenticate_user(username, password)
    if not user:
        return None

    # Update last login
    await update_last_login(str(user["_id"]))

    # Create session
    session = await create_session(str(user["_id"]))

    return {
        "user": user,
        "session": session
    }


async def logout_user(token: str) -> bool:
    """Log out a user by deleting their session."""
    return await delete_session(token)


def user_to_response(user: dict) -> dict:
    """Convert a user document to a response dict."""
    created_at = user["created_at"]
    last_login = user.get("last_login")
    return {
        "id": str(user["_id"]),
        "username": user["username"],
        "email": user["email"],
        "role": user["role"],
        "status": user["status"],
        "created_at": created_at.isoformat() if created_at else None,
        "last_login": last_login.isoformat() if last_login else None,
    }
