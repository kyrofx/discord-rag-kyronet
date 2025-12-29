"""
LangChain-compatible Gemini embeddings wrapper.
This allows using Gemini embeddings with existing LangChain infrastructure.
"""
import google.generativeai as genai
from langchain_core.embeddings import Embeddings
from typing import List
import os


class GeminiEmbeddings(Embeddings):
    """
    LangChain-compatible wrapper for Google Gemini embeddings.

    Uses gemini-embedding-001 model with 3072-dimensional vectors.
    """

    model: str = "models/gemini-embedding-001"
    task_type_document: str = "RETRIEVAL_DOCUMENT"
    task_type_query: str = "RETRIEVAL_QUERY"
    batch_size: int = 100

    def __init__(
        self,
        model: str = "models/gemini-embedding-001",
        api_key: str = None,
        **kwargs
    ):
        """
        Initialize Gemini embeddings.

        Args:
            model: The Gemini embedding model to use
            api_key: Google API key (defaults to GOOGLE_API_KEY env var)
        """
        super().__init__(**kwargs)
        self.model = model
        api_key = api_key or os.getenv("GOOGLE_API_KEY")
        if api_key:
            genai.configure(api_key=api_key)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        Embed a list of documents.

        Args:
            texts: List of document strings to embed

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        embeddings = []

        # Batch in groups to avoid API limits
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i:i+self.batch_size]
            result = genai.embed_content(
                model=self.model,
                content=batch,
                task_type=self.task_type_document
            )
            # Handle both single and batch responses
            if isinstance(result['embedding'][0], list):
                embeddings.extend(result['embedding'])
            else:
                embeddings.append(result['embedding'])

        return embeddings

    def embed_query(self, text: str) -> List[float]:
        """
        Embed a single query text.

        Args:
            text: Query string to embed

        Returns:
            Embedding vector
        """
        result = genai.embed_content(
            model=self.model,
            content=text,
            task_type=self.task_type_query
        )
        return result['embedding']
