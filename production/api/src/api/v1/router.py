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
from fastapi.responses import StreamingResponse
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
    IndexStatusResponse,
    UserImportRequest, UserImportStartResponse, UserImportStatusResponse,
    ChatRequest,
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
        from inference.agentic_inference import AgenticInferencer
        _inferencer = AgenticInferencer()
    return _inferencer


# ============== Core Endpoints ==============

@router.get("/health", response_model=HealthResponse)
async def health():
    """Health check endpoint."""
    return HealthResponse(
        status="ok",
        model="gemini-3-flash-preview",
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
            query_time_ms=query_time_ms,
            search_iterations=result.get("iterations"),
            total_docs_retrieved=result.get("total_docs_retrieved"),
            unique_docs=result.get("unique_docs")
        )

    except Exception as e:
        query_time_ms = int((time.time() - start_time) * 1000)
        tracker.record_query(query_time_ms, 0, success=False)
        raise InternalError(f"Query failed: {str(e)}")


@router.post("/chat")
async def chat(
    request: ChatRequest,
    api_key: str = Depends(verify_api_key)
):
    """
    Chat endpoint with streaming chain-of-thought.

    Returns a Server-Sent Events stream with the following event types:
    - thinking: Agent's reasoning process
    - tool_call: When the agent calls a search tool
    - tool_result: Results from the search
    - content: Final answer content (streamed in chunks)
    - sources: Citation sources
    - done: Completion event with metadata
    - error: Error event

    Use EventSource or fetch with stream reading to consume this endpoint.
    """
    from inference.streaming_chat import get_streaming_inferencer

    inferencer = get_streaming_inferencer()

    # Convert history to list of dicts
    history = [{"role": msg.role, "content": msg.content} for msg in request.history]

    def generate():
        for event in inferencer.chat_stream(request.message, history):
            yield event

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


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
        model="gemini-embedding-001"
    )


@router.get("/debug/index-status", response_model=IndexStatusResponse)
async def debug_index_status(
    api_key: str = Depends(verify_api_key)
):
    """
    Check the status of the vector index.

    Returns whether the index exists and how many documents it contains.
    Use this to diagnose issues with empty query results.
    """
    from utils.vector_store import check_index_status, INDEX_NAME

    status = check_index_status()

    return IndexStatusResponse(
        index_name=INDEX_NAME,
        exists=status["exists"],
        num_docs=status["num_docs"],
        error=status.get("error")
    )


# ============== User Token Import ==============

async def _run_import_job(
    job_id: str,
    user_token: str,
    channel_id: str,
    max_messages: Optional[int],
    guild_id: Optional[str] = None,
    full_history: bool = True
):
    """Background task to run the import job."""
    from user_import import run_import
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    logger.info(f"Starting import job {job_id} for channel {channel_id}, full_history={full_history}")

    try:
        # Update status to running
        redis_client.hset(f"discord_rag:import:{job_id}", mapping={
            "status": "running",
            "channel_id": channel_id,
            "messages_imported": 0,
            "messages_skipped": 0,
        })

        result = await run_import(
            user_token=user_token,
            channel_id=channel_id,
            max_messages=max_messages,
            guild_id_override=guild_id,
            full_history=full_history
        )

        logger.info(f"Import job {job_id} completed: {result['messages_imported']} imported, {result['messages_skipped']} skipped")

        # Update with final results
        redis_client.hset(f"discord_rag:import:{job_id}", mapping={
            "status": "completed",
            "channel_id": result["channel_id"],
            "channel_type": result["channel_type"],
            "channel_name": result.get("channel_name") or "",
            "messages_imported": result["messages_imported"],
            "messages_skipped": result["messages_skipped"],
            "resumed_from": result.get("resumed_from") or "",
            "completed_at": datetime.utcnow().isoformat(),
        })

    except Exception as e:
        logger.error(f"Import job {job_id} failed: {str(e)}", exc_info=True)
        redis_client.hset(f"discord_rag:import:{job_id}", mapping={
            "status": "failed",
            "error": str(e),
            "completed_at": datetime.utcnow().isoformat(),
        })


@router.post("/import/user-token", response_model=UserImportStartResponse)
async def import_with_user_token(
    request: UserImportRequest,
    background_tasks: BackgroundTasks,
    api_key: str = Depends(verify_api_key)
):
    """
    Start importing messages from a Discord channel using a user account token.

    This runs as a background job - use GET /v1/import/{job_id} to check status.

    WARNING: Using user tokens violates Discord's Terms of Service.
    """
    import asyncio

    job_id = str(uuid.uuid4())[:8]

    # Store initial job state
    redis_client.hset(f"discord_rag:import:{job_id}", mapping={
        "status": "starting",
        "channel_id": request.channel_id,
        "started_at": datetime.utcnow().isoformat(),
        "messages_imported": 0,
        "messages_skipped": 0,
    })
    redis_client.expire(f"discord_rag:import:{job_id}", 86400)  # 24h TTL

    # Start background task
    background_tasks.add_task(
        _run_import_job,
        job_id,
        request.user_token,
        request.channel_id,
        request.max_messages,
        request.guild_id,
        request.full_history
    )

    return UserImportStartResponse(
        status="started",
        job_id=job_id,
        channel_id=request.channel_id,
        message="Import started. Check status at GET /v1/import/{job_id}"
    )


@router.get("/import/{job_id}", response_model=UserImportStatusResponse)
async def get_import_status(
    job_id: str,
    api_key: str = Depends(verify_api_key)
):
    """
    Check the status of an import job.
    """
    job_data = redis_client.hgetall(f"discord_rag:import:{job_id}")

    if not job_data:
        raise NotFoundError(f"Import job {job_id} not found")

    return UserImportStatusResponse(
        job_id=job_id,
        status=job_data.get("status", "unknown"),
        channel_id=job_data.get("channel_id"),
        channel_type=job_data.get("channel_type"),
        channel_name=job_data.get("channel_name") or None,
        messages_imported=int(job_data.get("messages_imported", 0)),
        messages_skipped=int(job_data.get("messages_skipped", 0)),
        resumed_from=job_data.get("resumed_from") or None,
        error=job_data.get("error"),
        started_at=job_data.get("started_at"),
        completed_at=job_data.get("completed_at"),
    )
