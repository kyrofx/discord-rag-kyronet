"""
Generate Discord message links for citations.
"""
from typing import List
from langchain_core.documents import Document


DISCORD_MESSAGE_URL = "https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def extract_ids_from_url(url: str) -> dict:
    """
    Extract guild_id, channel_id, and message_id from a Discord message URL.

    Args:
        url: Discord message URL like https://discord.com/channels/123/456/789

    Returns:
        Dict with guild_id, channel_id, message_id or empty dict if parsing fails
    """
    if not url:
        return {}

    try:
        parts = url.rstrip('/').split('/')
        if len(parts) >= 3:
            return {
                'guild_id': parts[-3],
                'channel_id': parts[-2],
                'message_id': parts[-1]
            }
    except Exception:
        pass

    return {}


def generate_citations_for_documents(documents: List[Document]) -> List[dict]:
    """
    Generate citation information for retrieved documents.

    Args:
        documents: List of LangChain Document objects with metadata

    Returns:
        List of source dicts with citation URLs and snippets
    """
    sources = []

    for i, doc in enumerate(documents):
        metadata = doc.metadata or {}
        url = metadata.get('url', '')

        # Extract snippet (first 200 chars of content)
        content = doc.page_content or ''
        snippet = content[:200]
        if len(content) > 200:
            snippet += "..."

        source = {
            'source_number': i + 1,
            'snippet': snippet,
            'urls': [url] if url else [],
            'timestamp': metadata.get('timestamp'),
            'channel': metadata.get('channel')
        }

        sources.append(source)

    return sources


def format_response_with_citations(
    answer: str,
    documents: List[Document]
) -> dict:
    """
    Format final response with answer and source citations.

    Returns:
        {
            "answer": "Based on the conversation...",
            "sources": [
                {
                    "source_number": 1,
                    "snippet": "alice: let's go to the beach...",
                    "urls": ["https://discord.com/channels/..."],
                    "timestamp": 1234567890,
                    "channel": "general"
                }
            ]
        }
    """
    sources = generate_citations_for_documents(documents)

    return {
        "answer": answer,
        "sources": sources
    }
