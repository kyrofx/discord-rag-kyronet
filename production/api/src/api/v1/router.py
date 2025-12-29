"""
V1 API router with all endpoints.
"""
import os
import time
import uuid
import json
import redis
from datetime import datetime
from fastapi import APIRouter, Depends, BackgroundTasks
from typing import Optional

from auth import verify_api_key
from errors import NotFoundError, InternalError, ValidationError
from stats import get_stats_tracker
from v1.models import (
    QueryRequest, QueryResponse, Source,
    HealthResponse,
    IngestRequest, IngestResponse,
    IndexResponse,
    MessageRequest, MessageResponse,
    DeleteMessageResponse,
    GuildStatsResponse, DateRange,
    ChannelsResponse, ChannelInfo,
    EmbedRequest, EmbedResponse,
)

router = APIRouter(prefix="/v1", tags=["v1"])

# Redis client for message queue and stats
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
redis_client = redis.from_url(redis_url, decode_responses=True)

# Lazy import to avoid circular imports
_inferencer = None


def get_inferencer():
    global _inferencer
    if _inferencer is None:
        from inference import Inferencer
        _inferencer = Inferencer()
    return _inferencer


# ============== Core Endpoints ==============

@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        model="gemini-2.5-flash",
        version="1.0.0"
    )


@router.post("/query", response_model=QueryResponse)
async def query(
    request: QueryRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Ask a question and get an answer with citations.

    Uses RAG to search Discord chat history and generate an answer
    with clickable Discord message links as sources.
    """
    start_time = time.time()
    tracker = get_stats_tracker()

    # Validate input
    if not request.query or not request.query.strip():
        raise ValidationError("Query cannot be empty")
    
    if len(request.query) > 10000:
        raise ValidationError("Query is too long (max 10000 characters)")

    try:
        inferencer = get_inferencer()
        result = inferencer.infer(request.query)

        query_time_ms = int((time.time() - start_time) * 1000)

        # Convert sources to response format
        sources = []
        for src in result.get("sources", []):
            sources.append(Source(
                source_number=src.get("source_number", 0),
                snippet=src.get("snippet", ""),
                urls=src.get("urls", []),
                channel=src.get("channel"),
                timestamp=str(src.get("timestamp")) if src.get("timestamp") else None
            ))

        # Track stats
        tracker.record_query(query_time_ms, len(sources), success=True)

        return QueryResponse(
            answer=result.get("answer", ""),
            sources=sources,
            query_time_ms=query_time_ms
        )

    except Exception as e:
        query_time_ms = int((time.time() - start_time) * 1000)
        tracker.record_query(query_time_ms, 0, success=False)
        raise InternalError(f"Query failed: {str(e)}")


# ============== Ingestion & Indexing ==============

@router.post("/guilds/{guild_id}/ingest", response_model=IngestResponse)
async def ingest_guild(
    guild_id: str,
    request: IngestRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key)
):
    """
    Trigger backfill of message history for a guild.

    This starts a background job that fetches messages from Discord
    and stores them in the database.
    """
    job_id = str(uuid.uuid4())[:8]

    # Queue the job
    job_data = {
        "job_id": job_id,
        "guild_id": guild_id,
        "channel_ids": request.channel_ids,
        "after": request.after.isoformat() if request.after else None,
        "limit": request.limit,
        "status": "queued",
        "created_at": datetime.utcnow().isoformat()
    }

    redis_client.set(f"discord_rag:jobs:{job_id}", json.dumps(job_data), ex=86400)
    redis_client.lpush("discord_rag:ingest_queue", json.dumps(job_data))

    return IngestResponse(
        status="started",
        job_id=job_id,
        estimated_messages=None  # Would need Discord API to estimate
    )


@router.post("/guilds/{guild_id}/index", response_model=IndexResponse)
async def index_guild(
    guild_id: str,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key)
):
    """
    Rebuild the vector index for a guild.

    This re-chunks and re-embeds all messages for the guild.
    """
    job_id = str(uuid.uuid4())[:8]

    job_data = {
        "job_id": job_id,
        "guild_id": guild_id,
        "type": "index",
        "status": "queued",
        "created_at": datetime.utcnow().isoformat()
    }

    redis_client.set(f"discord_rag:jobs:{job_id}", json.dumps(job_data), ex=86400)
    redis_client.lpush("discord_rag:index_queue", json.dumps(job_data))

    return IndexResponse(
        status="started",
        job_id=job_id,
        chunks_created=None
    )


@router.post("/messages", response_model=MessageResponse)
async def ingest_message(
    request: MessageRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Webhook for real-time message ingestion.

    Call this endpoint when a new message is posted in Discord
    to add it to the index in real-time.
    """
    # Queue message for processing
    message_data = {
        "id": request.id,
        "guild_id": request.guild_id,
        "channel_id": request.channel_id,
        "author_id": request.author_id,
        "author_name": request.author_name,
        "content": request.content,
        "timestamp": request.timestamp.isoformat(),
        "queued_at": datetime.utcnow().isoformat()
    }

    redis_client.lpush("discord_rag:message_queue", json.dumps(message_data))

    # Track message count
    redis_client.hincrby(f"discord_rag:guild:{request.guild_id}:stats", "queued_messages", 1)

    return MessageResponse(status="queued")


@router.delete("/guilds/{guild_id}/messages/{message_id}", response_model=DeleteMessageResponse)
async def delete_message(
    guild_id: str,
    message_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Remove a deleted message from the index.

    Call this when a message is deleted in Discord to remove
    it from search results.
    """
    # Queue deletion
    deletion_data = {
        "guild_id": guild_id,
        "message_id": message_id,
        "deleted_at": datetime.utcnow().isoformat()
    }

    redis_client.lpush("discord_rag:deletion_queue", json.dumps(deletion_data))

    return DeleteMessageResponse(
        status="deleted",
        message_id=message_id
    )


# ============== Stats & Debug ==============

@router.get("/guilds/{guild_id}/stats", response_model=GuildStatsResponse)
async def guild_stats(
    guild_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Get statistics for a guild.

    Returns message counts, date range, and indexing status.
    """
    stats_key = f"discord_rag:guild:{guild_id}:stats"
    stats = redis_client.hgetall(stats_key)

    # Get from MongoDB if available (would need motor client)
    total_messages = int(stats.get("total_messages", 0))
    total_chunks = int(stats.get("total_chunks", 0))
    indexed_channels = int(stats.get("indexed_channels", 0))

    return GuildStatsResponse(
        guild_id=guild_id,
        total_messages=total_messages,
        total_chunks=total_chunks,
        indexed_channels=indexed_channels,
        date_range=DateRange(
            oldest=stats.get("oldest_message"),
            newest=stats.get("newest_message")
        ),
        last_indexed=stats.get("last_indexed")
    )


@router.get("/guilds/{guild_id}/channels", response_model=ChannelsResponse)
async def guild_channels(
    guild_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    List indexed channels for a guild.
    """
    channels_key = f"discord_rag:guild:{guild_id}:channels"
    channels_data = redis_client.hgetall(channels_key)

    channels = []
    for channel_id, data in channels_data.items():
        try:
            info = json.loads(data)
            channels.append(ChannelInfo(
                id=channel_id,
                name=info.get("name"),
                message_count=info.get("message_count", 0)
            ))
        except:
            channels.append(ChannelInfo(
                id=channel_id,
                name=None,
                message_count=int(data) if data.isdigit() else 0
            ))

    return ChannelsResponse(
        guild_id=guild_id,
        channels=channels
    )


@router.post("/debug/embed", response_model=EmbedResponse)
async def debug_embed(
    request: EmbedRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Test embedding a string (development only).

    Returns the embedding vector for the given text.
    """
    from utils.gemini_embeddings import GeminiEmbeddings

    embeddings = GeminiEmbeddings()
    vector = embeddings.embed_query(request.text)

    return EmbedResponse(
        text=request.text,
        embedding=vector,
        dimensions=len(vector),
        model="text-embedding-004"
    )
