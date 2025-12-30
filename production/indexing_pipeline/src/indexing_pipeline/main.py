from utils.ingestion import ingest_documents
from utils.preprocessing import preprocess_documents
from utils.chunking import chunk_documents
from utils.vector_store import index_documents_to_redis, check_index_status
import logging

from tqdm import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

BATCH_SIZE = 10


def main():
    logger.info("Starting indexing pipeline.")

    # Check index status before starting
    index_status = check_index_status()
    logger.info(f"Index status before indexing: exists={index_status['exists']}, num_docs={index_status['num_docs']}")

    logger.info("Ingesting documents from MongoDB...")
    documents = ingest_documents()

    if len(documents) == 0:
        logger.warning("No documents found in the database. Check MONGODB_URL, MONGODB_DB, and MONGODB_COLLECTION env vars.")
        return

    logger.info(f"Found {len(documents)} documents in the database.")

    # Log sample of first document for debugging
    if documents:
        sample = documents[0]
        logger.info(f"Sample document - content length: {len(sample.page_content)}, metadata keys: {list(sample.metadata.keys())}")
        logger.debug(f"Sample content preview: {sample.page_content[:200]}...")

    logger.info("Preprocessing documents (grouping by conversation windows)...")
    preprocessed_documents = preprocess_documents(documents)
    logger.info(f"Preprocessing complete. {len(preprocessed_documents)} conversation chunks created from {len(documents)} messages.")

    if len(preprocessed_documents) == 0:
        logger.error("No documents left after preprocessing! Check if messages have empty content.")
        return

    total_chunks_indexed = 0
    n_chunk_err = 0
    n_index_err = 0

    for i in tqdm(range(0, len(preprocessed_documents), BATCH_SIZE), desc="Chunking and indexing"):
        current_documents = preprocessed_documents[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        try:
            logger.debug(f'Batch {batch_num}: Chunking {len(current_documents)} documents')
            chunks = chunk_documents(current_documents)
            logger.debug(f'Batch {batch_num}: Created {len(chunks)} chunks')
        except Exception as e:
            n_chunk_err += 1
            logger.error(f'Batch {batch_num}: Error during chunking: {e}')
            logger.exception('Chunking error details:')
            continue

        if not chunks:
            logger.warning(f'Batch {batch_num}: SemanticChunker returned 0 chunks')
            continue

        try:
            logger.debug(f'Batch {batch_num}: Indexing {len(chunks)} chunks to Redis')
            index_documents_to_redis(chunks)
            total_chunks_indexed += len(chunks)
            logger.debug(f'Batch {batch_num}: Successfully indexed {len(chunks)} chunks')
        except Exception as e:
            n_index_err += 1
            logger.error(f'Batch {batch_num}: Error during indexing: {e}')
            logger.exception('Indexing error details:')

    # Check index status after indexing
    index_status = check_index_status()
    logger.info(f"Index status after indexing: exists={index_status['exists']}, num_docs={index_status['num_docs']}")

    logger.info(
        f'Indexing pipeline complete. '
        f'Chunks indexed: {total_chunks_indexed}, '
        f'Chunking errors: {n_chunk_err}, '
        f'Indexing errors: {n_index_err}'
    )


if __name__ == "__main__":
    main()