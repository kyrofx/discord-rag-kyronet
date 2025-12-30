from langchain_core.documents import Document
from utils import CustomMongodbLoader
import os
import asyncio
import logging

logger = logging.getLogger(__name__)

document_loader = CustomMongodbLoader(
    connection_string=os.getenv("MONGODB_URL"),
    db_name=os.getenv("MONGODB_DB"),
    collection_name=os.getenv("MONGODB_COLLECTION"),
    field_names=["author.username", "content"],
    metadata_names=["timestamp", "url"],
    include_db_collection_in_metadata=False
)


async def ingest_documents_async() -> list[Document]:
    """Async version that properly uses CustomMongodbLoader.aload() with custom sorting."""
    logger.info("Starting async document ingestion from MongoDB...")
    docs = await document_loader.aload()
    logger.info(f"Loaded {len(docs)} documents from MongoDB")
    return docs


def ingest_documents() -> list[Document]:
    """Sync wrapper for async ingestion. Uses asyncio.run() to call the async method."""
    return asyncio.run(ingest_documents_async())