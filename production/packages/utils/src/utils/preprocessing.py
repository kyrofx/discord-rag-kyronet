from langchain_core.documents import Document

# Time gap (in milliseconds) that indicates a conversation break
# 30 minutes of silence = new conversation
CONVERSATION_GAP_MS = 30 * 60 * 1000

# Maximum messages per chunk to prevent overly large chunks
MAX_MESSAGES_PER_CHUNK = 100

# Minimum messages to form a chunk (smaller groups get merged with next)
MIN_MESSAGES_PER_CHUNK = 5


def remove_empty_documents(documents: list[Document]) -> list[Document]:
    return [doc for doc in documents if doc.page_content]


def add_separator_between_author_and_text(documents: list[Document]) -> list[Document]:
    for doc in documents:
        doc.page_content = doc.page_content.replace(" ", ": ", 1)
    return documents


def merge_documents_by_conversation_windows(documents: list[Document]) -> list[Document]:
    """
    Group messages into conversation chunks based on time gaps.

    A new conversation starts when there's a gap of CONVERSATION_GAP_MS (30 min)
    between consecutive messages. This creates semantically coherent chunks
    that represent actual conversations rather than arbitrary message counts.
    """
    if not documents:
        return []

    chunks = []
    current_chunk = [documents[0]]

    for i in range(1, len(documents)):
        current_doc = documents[i]
        prev_doc = documents[i - 1]

        current_ts = current_doc.metadata.get('timestamp', 0)
        prev_ts = prev_doc.metadata.get('timestamp', 0)
        time_gap = current_ts - prev_ts

        # Start new chunk if:
        # 1. Time gap exceeds threshold (new conversation), OR
        # 2. Current chunk is at max capacity
        should_split = (
            time_gap > CONVERSATION_GAP_MS or
            len(current_chunk) >= MAX_MESSAGES_PER_CHUNK
        )

        if should_split and len(current_chunk) >= MIN_MESSAGES_PER_CHUNK:
            chunks.append(_create_chunk_document(current_chunk))
            current_chunk = [current_doc]
        else:
            current_chunk.append(current_doc)

    # Don't forget the last chunk
    if current_chunk:
        chunks.append(_create_chunk_document(current_chunk))

    return chunks


def _create_chunk_document(messages: list[Document]) -> Document:
    """Create a single Document from a list of message Documents."""
    # Collect all URLs from messages in this chunk
    urls = [msg.metadata.get('url') for msg in messages if msg.metadata.get('url')]

    return Document(
        page_content="\n<MESSAGE_SEP>".join(msg.page_content for msg in messages),
        metadata={
            'timestamp': messages[0].metadata.get('timestamp', 0),
            'timestamp_end': messages[-1].metadata.get('timestamp', 0),
            'url': urls[0] if urls else '',  # Primary URL (first message)
            'urls': urls,  # All message URLs for comprehensive citations
            'message_count': len(messages)
        }
    )


def preprocess_documents(documents: list[Document]) -> list[Document]:
    documents = remove_empty_documents(documents)
    documents = add_separator_between_author_and_text(documents)
    return merge_documents_by_conversation_windows(documents)