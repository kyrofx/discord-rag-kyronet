import time
import uvicorn
from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional

from dashboard import router as dashboard_router
from v1 import router as v1_router
from errors import APIError, api_error_handler, http_exception_handler, generic_exception_handler
from stats import get_stats_tracker

app = FastAPI(
    title="Discord RAG API",
    description="RAG-powered Discord chat history search with Gemini",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

# Register error handlers
app.add_exception_handler(APIError, api_error_handler)
app.add_exception_handler(HTTPException, http_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

# Include routers
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
    """Redirect root to dashboard."""
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
    uvicorn.run(app, host="0.0.0.0", port=8000)
