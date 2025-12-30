import os
import logging
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
from google.protobuf.struct_pb2 import Struct
from utils.vector_store import get_vector_store, check_index_status
from inference.citations import generate_citations_for_documents
from langchain_core.documents import Document
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Define the search tool for the agent
search_messages_func = FunctionDeclaration(
    name="search_messages",
    description="""Search the Discord message history for relevant messages.
    Use this to find specific conversations, mentions of people, topics, or events.
    You can call this multiple times with different queries to gather comprehensive context.

    Tips for effective searching:
    - Search for specific names when asked about people (e.g., "Alice", "Bob")
    - Search for interactions between people (e.g., "Alice talking to Bob", "Alice and Bob")
    - Search for opinions and sentiments (e.g., "Alice thinks", "Alice feels about")
    - Search for specific topics or events mentioned in the question
    - Try multiple phrasings if initial results aren't sufficient""",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query. Be specific and try variations."
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results to return (1-20, default 8)"
            }
        },
        "required": ["query"]
    }
)

tools = Tool(function_declarations=[search_messages_func])

AGENT_SYSTEM_PROMPT = """You are a research assistant that answers questions about Discord chat history.

Your job is to search the message database to find relevant context, then provide a comprehensive answer.

IMPORTANT INSTRUCTIONS:
1. You have access to a search_messages tool that performs semantic search over Discord messages.
2. For complex questions (especially about relationships between people, opinions, or patterns), you should search MULTIPLE times with different queries.
3. Keep searching until you have enough context to answer thoroughly.
4. After gathering sufficient context, provide your final answer.

For questions like "what does X think about Y":
- Search for "X mentions Y"
- Search for "X and Y conversation"
- Search for "X opinion about Y"
- Search for just "X" and just "Y" to understand each person
- Look for patterns across multiple messages

When you have enough context, provide your answer with citations like [Source 1], [Source 2], etc.
If you truly cannot find relevant information after thorough searching, say so honestly."""


class AgenticInferencer:
    def __init__(self):
        self.vector_store = get_vector_store()
        self.model = genai.GenerativeModel(
            "gemini-2.0-flash",
            tools=[tools],
            system_instruction=AGENT_SYSTEM_PROMPT
        )

        # Check index status on init
        index_status = check_index_status()
        if not index_status["exists"]:
            logger.error(f"Vector index does not exist: {index_status.get('error')}")
        elif index_status["num_docs"] == 0:
            logger.warning("Vector index is empty")
        else:
            logger.info(f"Vector index ready with {index_status['num_docs']} documents")

    def _search_messages(self, query: str, num_results: int = 8) -> List[Document]:
        """Execute a semantic search against the vector store."""
        num_results = min(max(num_results, 1), 20)  # Clamp between 1 and 20

        try:
            docs = self.vector_store.similarity_search(query, k=num_results)
            logger.info(f"Search '{query[:50]}...' returned {len(docs)} results")
            return docs
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    def _format_search_results(self, docs: List[Document], source_offset: int = 0) -> str:
        """Format search results for the agent to read."""
        if not docs:
            return "No results found for this search."

        parts = []
        for i, doc in enumerate(docs):
            source_num = source_offset + i + 1
            timestamp = doc.metadata.get('timestamp', 'unknown')
            parts.append(f"[Source {source_num}] (timestamp: {timestamp})\n{doc.page_content}")

        return "\n\n---\n\n".join(parts)

    def _deduplicate_docs(self, docs: List[Document]) -> List[Document]:
        """Remove duplicate documents based on content."""
        seen = set()
        unique = []
        for doc in docs:
            content_hash = hash(doc.page_content)
            if content_hash not in seen:
                seen.add(content_hash)
                unique.append(doc)
        return unique

    def infer(self, question: str, max_iterations: int = 10) -> Dict[str, Any]:
        """
        Run the agentic inference loop.

        The agent will:
        1. Analyze the question
        2. Make multiple search calls to gather context
        3. Synthesize a final answer
        """
        all_docs: List[Document] = []
        chat = self.model.start_chat()

        # Initial prompt to the agent
        initial_prompt = f"""Question from user: {question}

Search the Discord message database to find relevant context to answer this question.
Make multiple searches with different queries to gather comprehensive information.
When you have enough context, provide your final answer with source citations."""

        logger.info(f"Starting agentic inference for: {question[:100]}...")

        response = chat.send_message(initial_prompt)

        iteration = 0
        while iteration < max_iterations:
            iteration += 1

            # Check if the model wants to call a function
            function_calls = []
            for part in response.parts:
                if hasattr(part, 'function_call') and part.function_call:
                    function_calls.append(part.function_call)

            if not function_calls:
                # No function calls - model is done searching and has provided answer
                break

            # Process each function call
            function_response_parts = []
            for fc in function_calls:
                if fc.name == "search_messages":
                    args = dict(fc.args)
                    query = args.get("query", "")
                    num_results = args.get("num_results", 8)

                    logger.info(f"Agent search #{iteration}: '{query}' (k={num_results})")

                    docs = self._search_messages(query, num_results)
                    all_docs.extend(docs)

                    # Format results for the agent
                    results_text = self._format_search_results(docs, len(all_docs) - len(docs))

                    # Create function response part
                    response_struct = Struct()
                    response_struct.update({"results": results_text})

                    function_response_parts.append(
                        genai.protos.Part(
                            function_response=genai.protos.FunctionResponse(
                                name=fc.name,
                                response=response_struct
                            )
                        )
                    )

            # Send function results back to the model
            if function_response_parts:
                response = chat.send_message(function_response_parts)

        # Extract final answer from the last response
        final_answer = ""
        for part in response.parts:
            if hasattr(part, 'text') and part.text:
                final_answer += part.text

        # Deduplicate and sort collected documents
        unique_docs = self._deduplicate_docs(all_docs)
        sorted_docs = sorted(unique_docs, key=lambda x: x.metadata.get("timestamp", 0))

        # Generate citations
        sources = generate_citations_for_documents(sorted_docs)

        logger.info(f"Agentic inference complete. {iteration} iterations, {len(unique_docs)} unique docs")

        return {
            "question": question,
            "context": [doc.page_content for doc in sorted_docs],
            "answer": final_answer,
            "sources": sources,
            "iterations": iteration,
            "total_docs_retrieved": len(all_docs),
            "unique_docs": len(unique_docs)
        }
