import uvicorn
from fastapi import FastAPI, Form
from pydantic import BaseModel
from typing import List, Optional
from inference import Inferencer

app = FastAPI()
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


@app.get("/health")
async def health():
    return {"status": "ok", "model": "gemini-2.5-flash"}


@app.post("/infer", response_model=InferenceResponse)
async def infer(text: str = Form()):
    return inferencer.infer(text)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
