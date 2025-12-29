"""
Gemini API client for embeddings and chat completions.
Provides a unified interface for Google Gemini AI operations.
"""
import google.generativeai as genai
from typing import List
import os

# Initialize on import
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Model configuration
EMBEDDING_MODEL = "models/text-embedding-004"
CHAT_MODEL = "gemini-2.5-flash"


def get_embeddings(
    texts: List[str],
    task_type: str = "RETRIEVAL_DOCUMENT"
) -> List[List[float]]:
    """
    Generate embeddings for a list of texts using Gemini.

    Args:
        texts: List of strings to embed
        task_type: One of RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY,
                   SEMANTIC_SIMILARITY, CLASSIFICATION, CLUSTERING

    Returns:
        List of embedding vectors (768 dimensions each)
    """
    embeddings = []

    # Batch in groups of 100 (API limit)
    for i in range(0, len(texts), 100):
        batch = texts[i:i+100]
        result = genai.embed_content(
            model=EMBEDDING_MODEL,
            content=batch,
            task_type=task_type
        )
        embeddings.extend(result['embedding'])

    return embeddings


def get_query_embedding(query: str) -> List[float]:
    """Get embedding for a search query using RETRIEVAL_QUERY task type."""
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=query,
        task_type="RETRIEVAL_QUERY"
    )
    return result['embedding']


def get_single_embedding(text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> List[float]:
    """Get embedding for a single text."""
    result = genai.embed_content(
        model=EMBEDDING_MODEL,
        content=text,
        task_type=task_type
    )
    return result['embedding']


def chat_completion(
    query: str,
    context_chunks: List[dict],
    system_prompt: str = None
) -> str:
    """
    Generate a response using retrieved context.

    Args:
        query: User's question
        context_chunks: List of dicts with 'content' and metadata
        system_prompt: Optional system instructions

    Returns:
        Generated response text
    """
    model = genai.GenerativeModel(CHAT_MODEL)

    # Build context string with citation markers
    context_parts = []
    for i, chunk in enumerate(context_chunks):
        content = chunk.get('content', chunk.get('page_content', ''))
        context_parts.append(f"[Source {i+1}]\n{content}")

    context_str = "\n\n".join(context_parts)

    prompt = f"""You are a helpful assistant that answers questions based on Discord chat history.
Use ONLY the provided context to answer. If the answer isn't in the context, say so.
When citing information, reference the source number like [Source 1].

CONTEXT:
{context_str}

QUESTION: {query}

Answer concisely and cite your sources:"""

    if system_prompt:
        prompt = f"{system_prompt}\n\n{prompt}"

    response = model.generate_content(prompt)
    return response.text
