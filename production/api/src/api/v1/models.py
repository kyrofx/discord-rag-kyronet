"""
Pydantic models for v1 API requests and responses.
"""
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


# ============== Query ==============

class QueryRequest(BaseModel):
    query: str = Field(..., description="The question to answer")
    guild_id: str = Field(..., description="Discord guild/server ID")
    channel_ids: Optional[List[str]] = Field(None, description="Filter to specific channels")
    top_k: int = Field(5, ge=1, le=20, description="Number of sources to retrieve")
    before: Optional[datetime] = Field(None, description="Only search messages before this time")
    after: Optional[datetime] = Field(None, description="Only search messages after this time")


class Source(BaseModel):
    source_number: int
    snippet: str
    urls: List[str]
    channel: Optional[str] = None
    timestamp: Optional[str] = None


class QueryResponse(BaseModel):
    answer: str
    sources: List[Source]
    query_time_ms: int


# ============== Health ==============

class HealthResponse(BaseModel):
    status: str
    model: str
    version: str


# ============== Ingestion ==============

class IngestRequest(BaseModel):
    channel_ids: Optional[List[str]] = Field(None, description="Channels to ingest (all if not specified)")
    after: Optional[datetime] = Field(None, description="Only ingest messages after this time")
    limit: Optional[int] = Field(None, ge=1, le=100000, description="Max messages to ingest")


class IngestResponse(BaseModel):
    status: str
    job_id: str
    estimated_messages: Optional[int] = None


class IndexResponse(BaseModel):
    status: str
    job_id: str
    chunks_created: Optional[int] = None


class MessageRequest(BaseModel):
    id: str = Field(..., description="Discord message ID")
    guild_id: str = Field(..., description="Discord guild ID")
    channel_id: str = Field(..., description="Discord channel ID")
    author_id: str = Field(..., description="Author's Discord user ID")
    author_name: str = Field(..., description="Author's display name")
    content: str = Field(..., description="Message content")
    timestamp: datetime = Field(..., description="Message timestamp")


class MessageResponse(BaseModel):
    status: str


class DeleteMessageResponse(BaseModel):
    status: str
    message_id: str


# ============== Stats ==============

class DateRange(BaseModel):
    oldest: Optional[str] = None
    newest: Optional[str] = None


class GuildStatsResponse(BaseModel):
    guild_id: str
    total_messages: int
    total_chunks: int
    indexed_channels: int
    date_range: DateRange
    last_indexed: Optional[str] = None


class ChannelInfo(BaseModel):
    id: str
    name: Optional[str] = None
    message_count: int


class ChannelsResponse(BaseModel):
    guild_id: str
    channels: List[ChannelInfo]


# ============== Debug ==============

class EmbedRequest(BaseModel):
    text: str = Field(..., description="Text to embed")


class EmbedResponse(BaseModel):
    text: str
    embedding: List[float]
    dimensions: int
    model: str


class IndexStatusResponse(BaseModel):
    index_name: str
    exists: bool
    num_docs: int
    error: Optional[str] = None


# ============== User Token Import ==============

class UserImportRequest(BaseModel):
    user_token: str = Field(..., description="Discord user account token (NOT a bot token)")
    channel_id: str = Field(..., description="Discord channel ID (DM, Group DM, or server channel)")
    max_messages: Optional[int] = Field(None, ge=1, le=100000, description="Maximum messages to import")
    guild_id: Optional[str] = Field(None, description="Override guild ID for stats tracking (useful for group DMs)")
    full_history: bool = Field(True, description="If true, fetch all historical messages. If false, only fetch new ones since last import.")


class UserImportStartResponse(BaseModel):
    status: str  # "started"
    job_id: str
    channel_id: str
    message: str


class UserImportStatusResponse(BaseModel):
    job_id: str
    status: str  # "running", "completed", "failed"
    channel_id: Optional[str] = None
    channel_type: Optional[str] = None
    channel_name: Optional[str] = None
    messages_imported: int = 0
    messages_skipped: int = 0
    resumed_from: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
