"""
Standardized error handling for the API.
"""
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from typing import Optional


class APIError(HTTPException):
    """Base API error with standardized format."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 500,
        details: Optional[dict] = None
    ):
        self.code = code
        self.message = message
        self.details = details
        super().__init__(
            status_code=status_code,
            detail=self.to_dict()
        )

    def to_dict(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class UnauthorizedError(APIError):
    """401 Unauthorized."""
    def __init__(self, message: str = "Invalid or missing API key"):
        super().__init__("unauthorized", message, 401)


class ForbiddenError(APIError):
    """403 Forbidden."""
    def __init__(self, message: str = "Access denied"):
        super().__init__("forbidden", message, 403)


class NotFoundError(APIError):
    """404 Not Found."""
    def __init__(self, message: str = "Resource not found"):
        super().__init__("not_found", message, 404)


class RateLimitedError(APIError):
    """429 Too Many Requests."""
    def __init__(self, message: str = "Too many requests"):
        super().__init__("rate_limited", message, 429)


class InternalError(APIError):
    """500 Internal Server Error."""
    def __init__(self, message: str = "Internal server error"):
        super().__init__("internal_error", message, 500)


class ValidationError(APIError):
    """422 Validation Error."""
    def __init__(self, message: str, details: Optional[dict] = None):
        super().__init__("validation_error", message, 422, details)


async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Handle APIError exceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict()
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Handle generic HTTPException and convert to standard format."""
    # If it's already in our format, return as-is
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    # Convert to standard format
    code = "error"
    if exc.status_code == 401:
        code = "unauthorized"
    elif exc.status_code == 403:
        code = "forbidden"
    elif exc.status_code == 404:
        code = "not_found"
    elif exc.status_code == 429:
        code = "rate_limited"
    elif exc.status_code >= 500:
        code = "internal_error"

    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": str(exc.detail)}}
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "An unexpected error occurred"}}
    )
