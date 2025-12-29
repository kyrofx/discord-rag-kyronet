import time
import uvicorn
from fastapi import FastAPI, Form
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import List, Optional

from inference import Inferencer
from dashboard import router as dashboard_router
from stats import get_stats_tracker

app = FastAPI(
    title="Discord RAG API",
    description="RAG-powered Discord chat history search with Gemini",
    version="1.0.0"
)

# Include dashboard routes
app.include_router(dashboard_router)

inferencer = Inferencer()


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
    """Health check endpoint."""
    return {"status": "ok", "model": "gemini-2.5-flash"}


@app.post("/infer", response_model=InferenceResponse)
async def infer(text: str = Form(...)):
    """
    Perform RAG inference on the given text query.

    - **text**: The question to answer based on Discord chat history
    """
    start_time = time.time()
    tracker = get_stats_tracker()

    try:
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
