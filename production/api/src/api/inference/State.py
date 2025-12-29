from langchain_core.documents import Document
from typing_extensions import List, TypedDict, Optional


class Source(TypedDict):
    source_number: int
    snippet: str
    urls: List[str]
    timestamp: Optional[float]
    channel: Optional[str]


class State(TypedDict):
    question: str
    context: List[Document]
    answer: str
    sources: List[Source]
