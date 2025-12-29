from langchain_redis import RedisConfig, RedisVectorStore
from langchain_core.documents import Document
from utils.gemini_embeddings import GeminiEmbeddings
import os


redis_config = RedisConfig(
    index_name="discord_rag_semantic_index",
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
