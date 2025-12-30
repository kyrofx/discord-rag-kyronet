from langchain_redis import RedisConfig, RedisVectorStore
from langchain_core.documents import Document
from utils.gemini_embeddings import GeminiEmbeddings
import os
import logging
import redis

logger = logging.getLogger(__name__)

INDEX_NAME = "discord_rag_semantic_index"

redis_config = RedisConfig(
    index_name=INDEX_NAME,
    redis_url=os.getenv("REDIS_URL"),
    metadata_schema=[
        {"name": "timestamp", "type": "numeric"},
        {"name": "url", "type": "text"}
    ]
)

vector_store = RedisVectorStore(
    embeddings=GeminiEmbeddings(
        model="models/gemini-embedding-001"
    ),
    config=redis_config
)


def index_documents_to_redis(documents: list[Document]):
    vector_store.add_documents(documents)


def get_vector_store():
    return vector_store


def check_index_status() -> dict:
    """Check the status of the Redis vector index.

    Returns a dict with:
    - exists: bool - whether the index exists
    - num_docs: int - number of documents in the index (0 if doesn't exist)
    - error: str | None - error message if any
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        return {"exists": False, "num_docs": 0, "error": "REDIS_URL not set"}

    try:
        r = redis.from_url(redis_url)
        # Try to get index info using FT.INFO command
        info = r.execute_command("FT.INFO", INDEX_NAME)

        # Parse the info response (it's a flat list of key-value pairs)
        info_dict = {}
        for i in range(0, len(info), 2):
            key = info[i].decode() if isinstance(info[i], bytes) else info[i]
            value = info[i + 1]
            if isinstance(value, bytes):
                value = value.decode()
            info_dict[key] = value

        num_docs = int(info_dict.get("num_docs", 0))
        logger.info(f"Index '{INDEX_NAME}' exists with {num_docs} documents")
        return {"exists": True, "num_docs": num_docs, "error": None}

    except redis.exceptions.ResponseError as e:
        error_msg = str(e)
        if "Unknown index name" in error_msg or "no such index" in error_msg.lower():
            logger.warning(f"Index '{INDEX_NAME}' does not exist")
            return {"exists": False, "num_docs": 0, "error": "Index does not exist"}
        logger.error(f"Redis error checking index: {e}")
        return {"exists": False, "num_docs": 0, "error": error_msg}
    except Exception as e:
        logger.error(f"Error checking index status: {e}")
        return {"exists": False, "num_docs": 0, "error": str(e)}
