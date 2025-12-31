import os
import time
import asyncio
import uvicorn
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional
from contextlib import asynccontextmanager

from dashboard import router as dashboard_router
from v1 import router as v1_router
from errors import APIError, api_error_handler, http_exception_handler, generic_exception_handler
from stats import get_stats_tracker

# Check if platform mode is enabled
PLATFORM_ENABLED = os.getenv("ENABLE_PLATFORM", "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown."""
    # Startup
    if PLATFORM_ENABLED:
        from platform_app.database import setup_admin_user, get_database
        # Initialize database connection and setup admin
        await get_database()
        await setup_admin_user()
    yield
    # Shutdown (nothing to do)


app = FastAPI(
    title="Discord RAG API",
    description="RAG-powered Discord chat history search with Gemini",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# Register error handlers
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Include routers based on mode
if PLATFORM_ENABLED:
    # Platform mode: Include platform routers
    from platform_app.router import router as platform_api_router
    from platform_app.frontend import router as platform_ui_router

    app.include_router(platform_api_router)
    app.include_router(platform_ui_router)
    # Also include v1 router for API access
    app.include_router(v1_router)
else:
    # Standard mode: Include dashboard and v1 routers
    app.include_router(dashboard_router)
    app.include_router(v1_router)

# Lazy inferencer for legacy endpoint
_inferencer = None


def get_inferencer():
    global _inferencer
    if _inferencer is None:
        from inference import Inferencer
        _inferencer = Inferencer()
    return _inferencer


# Legacy models (kept for backward compatibility)
class Source(BaseModel):
    source_number: int
    snippet: str
    urls: List[str]
    timestamp: Optional[float] = None
    channel: Optional[str] = None


class InferenceResponse(BaseModel):
    question: str
    context: List[str]
    answer: str
    sources: List[Source]


@app.get("/")
async def root():
    """Redirect root to appropriate page based on mode."""
    if PLATFORM_ENABLED:
        return RedirectResponse(url="/chat")
    return RedirectResponse(url="/dashboard")


@app.get("/health")
async def health():
    """Health check endpoint (legacy, use /v1/health)."""
    return {"status": "ok", "model": "gemini-2.5-flash", "version": "1.0.0"}


@app.post("/infer", response_model=InferenceResponse)
async def infer(text: str = Form(...)):
    """
    Perform RAG inference (legacy endpoint, use /v1/query instead).

    - **text**: The question to answer based on Discord chat history
    """
    start_time = time.time()
    tracker = get_stats_tracker()

    try:
        inferencer = get_inferencer()
        result = inferencer.infer(text)
        response_time_ms = (time.time() - start_time) * 1000
        sources_count = len(result.get("sources", []))

        # Record successful query
        tracker.record_query(response_time_ms, sources_count, success=True)

        return result

    except Exception as e:
        response_time_ms = (time.time() - start_time) * 1000
        tracker.record_query(response_time_ms, 0, success=False)
        raise


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
