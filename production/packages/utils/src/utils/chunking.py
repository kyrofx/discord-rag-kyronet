from langchain_experimental.text_splitter import SemanticChunker
from langchain_core.documents import Document
from utils.gemini_embeddings import GeminiEmbeddings

chunker = SemanticChunker(
    embeddings=GeminiEmbeddings(
        model="models/text-embedding-004"
    ),
    sentence_split_regex=r"<MESSAGE_SEP>",
    add_start_index=False
)

def chunk_documents(documents: list[Document]) -> list[Document]:
    return chunker.split_documents(documents)
