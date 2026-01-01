import os
import re
import logging
import hashlib
from datetime import datetime, timedelta
from collections import defaultdict
from typing import List, Dict, Any, Optional, Tuple
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
from google.protobuf.struct_pb2 import Struct
from utils.vector_store import get_vector_store, check_index_status
from inference.citations import generate_citations_for_documents
from langchain_core.documents import Document

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

# Query cache for efficiency
_query_cache: Dict[str, Tuple[List[Document], float]] = {}
CACHE_TTL_SECONDS = 300  # 5 minutes


def _get_cache_key(query: str, **kwargs) -> str:
    """Generate a cache key for a query."""
    key_data = f"{query}:{sorted(kwargs.items())}"
    return hashlib.md5(key_data.encode()).hexdigest()


def _get_cached_results(cache_key: str) -> Optional[List[Document]]:
    """Get cached results if they exist and are not expired."""
    if cache_key in _query_cache:
        docs, cached_at = _query_cache[cache_key]
        if datetime.now().timestamp() - cached_at < CACHE_TTL_SECONDS:
            return docs
        del _query_cache[cache_key]
    return None


def _cache_results(cache_key: str, docs: List[Document]):
    """Cache query results."""
    _query_cache[cache_key] = (docs, datetime.now().timestamp())
    # Limit cache size
    if len(_query_cache) > 100:
        oldest_key = min(_query_cache.keys(), key=lambda k: _query_cache[k][1])
        del _query_cache[oldest_key]


# ============== Tool Definitions ==============

search_messages_func = FunctionDeclaration(
    name="search_messages",
    description="""Semantic search over Discord message history.
    Use this for general queries about topics, conversations, or events.
    Returns messages ranked by semantic similarity to your query.""",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The semantic search query. Be specific."
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results (1-20, default 8)"
            }
        },
        "required": ["query"]
    }
)

search_by_user_func = FunctionDeclaration(
    name="search_by_user",
    description="""Search messages from a specific user.
    Combines semantic search with username filtering.
    Use this when you need to find what a specific person said about a topic.""",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The semantic search query"
            },
            "username": {
                "type": "string",
                "description": "Username to filter by (case-insensitive partial match)"
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results (1-20, default 8)"
            }
        },
        "required": ["query", "username"]
    }
)

search_by_date_range_func = FunctionDeclaration(
    name="search_by_date_range",
    description="""Search messages within a specific date range.
    Use this for questions about events during a particular time period.
    Dates should be in ISO format (YYYY-MM-DD) or relative like 'last week', 'yesterday'.""",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The semantic search query"
            },
            "start_date": {
                "type": "string",
                "description": "Start date (ISO format or relative: 'yesterday', 'last week', '7 days ago')"
            },
            "end_date": {
                "type": "string",
                "description": "End date (ISO format or relative, default: now)"
            },
            "num_results": {
                "type": "integer",
                "description": "Number of results (1-20, default 8)"
            }
        },
        "required": ["query", "start_date"]
    }
)

get_surrounding_messages_func = FunctionDeclaration(
    name="get_surrounding_messages",
    description="""Get messages around a specific message to see the full conversation context.
    Use this after finding a relevant message to understand the discussion around it.
    Provide the timestamp of the message you want context for.""",
    parameters={
        "type": "object",
        "properties": {
            "timestamp": {
                "type": "number",
                "description": "Unix timestamp of the target message"
            },
            "before": {
                "type": "integer",
                "description": "Number of messages to retrieve before (default 5)"
            },
            "after": {
                "type": "integer",
                "description": "Number of messages to retrieve after (default 5)"
            }
        },
        "required": ["timestamp"]
    }
)

get_user_activity_func = FunctionDeclaration(
    name="get_user_activity",
    description="""Get activity summary for a specific user.
    Returns message count, active time period, and topics they discuss.
    Use this to understand someone's participation patterns.""",
    parameters={
        "type": "object",
        "properties": {
            "username": {
                "type": "string",
                "description": "Username to analyze"
            }
        },
        "required": ["username"]
    }
)

count_mentions_func = FunctionDeclaration(
    name="count_mentions",
    description="""Count how many times a term or topic is mentioned.
    Use this for questions like 'how often is X discussed?' or 'is Y a common topic?'""",
    parameters={
        "type": "object",
        "properties": {
            "term": {
                "type": "string",
                "description": "The term or phrase to count mentions of"
            }
        },
        "required": ["term"]
    }
)

get_recent_messages_func = FunctionDeclaration(
    name="get_recent_messages",
    description="""Get the most recent messages (no semantic search, just chronological).
    Use this for questions like 'what's been happening lately?' or 'what was discussed recently?'""",
    parameters={
        "type": "object",
        "properties": {
            "num_results": {
                "type": "integer",
                "description": "Number of recent messages to get (1-50, default 20)"
            }
        },
        "required": []
    }
)

evaluate_answer_func = FunctionDeclaration(
    name="evaluate_answer",
    description="""Evaluate if you have gathered enough information to answer the question.
    Call this before providing your final answer to check if you need more searches.
    Be honest about gaps in your knowledge.""",
    parameters={
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "The original user question"
            },
            "current_findings": {
                "type": "string",
                "description": "Summary of what you've found so far"
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Your confidence in being able to answer"
            }
        },
        "required": ["question", "current_findings", "confidence"]
    }
)

# Combine all tools
all_tool_declarations = [
    search_messages_func,
    search_by_user_func,
    search_by_date_range_func,
    get_surrounding_messages_func,
    get_user_activity_func,
    count_mentions_func,
    get_recent_messages_func,
    evaluate_answer_func,
]

tools = Tool(function_declarations=all_tool_declarations)

AGENT_SYSTEM_PROMPT = """You are an intelligent research assistant that answers questions about Discord chat history.

## Your Capabilities
You have access to multiple specialized tools:
- **search_messages**: Semantic search for topics, conversations, events
- **search_by_user**: Find what a specific person said about a topic
- **search_by_date_range**: Search within a time period
- **get_surrounding_messages**: Get conversation context around a message
- **get_user_activity**: Analyze a user's participation patterns
- **count_mentions**: Count how often something is discussed
- **get_recent_messages**: Get latest messages chronologically
- **evaluate_answer**: Check if you have enough info before answering

## ReAct Framework
For each question, follow the Thought-Action-Observation loop:

1. **Thought**: Analyze what information you need
2. **Action**: Choose the right tool(s) to gather that information
3. **Observation**: Review results and determine if more searches are needed
4. Repeat until you have sufficient context

## Planning Complex Queries
For complex questions, first create a search plan:

Example - "What does Alice think about the new API?":
- Plan: Search for Alice's API opinions, Alice-API conversations, Alice's general views
- Execute: search_by_user("API", "Alice"), search_messages("Alice API opinion")
- Evaluate: Check if findings are comprehensive before answering

## Best Practices
- Use **search_by_user** when the question is about a specific person
- Use **search_by_date_range** for time-based questions ("last week", "recently")
- Use **get_surrounding_messages** when you need conversation context
- Use **get_recent_messages** for "what's happening lately" questions
- Use **count_mentions** for frequency questions
- Call **evaluate_answer** before your final response for complex questions

## Self-Reflection
Before providing your final answer:
1. Consider: Do I have enough evidence to answer confidently?
2. Are there gaps in my knowledge that another search could fill?
3. Am I making unsupported assumptions?

If confidence is low, do more targeted searches before answering.

## Citation Format
Always cite sources: [Source 1], [Source 2], etc.
Only cite sources you actually retrieved and used.
If you cannot find relevant information after thorough searching, say so honestly."""


class AgenticInferencer:
    def __init__(self):
        self.vector_store = get_vector_store()
        self.model = genai.GenerativeModel(
            "gemini-3-flash-preview",
            tools=[tools],
            system_instruction=AGENT_SYSTEM_PROMPT
        )
        self._all_indexed_docs: Optional[List[Document]] = None

        # Check index status on init
        index_status = check_index_status()
        if not index_status["exists"]:
            logger.error(f"Vector index does not exist: {index_status.get('error')}")
        elif index_status["num_docs"] == 0:
            logger.warning("Vector index is empty")
        else:
            logger.info(f"Vector index ready with {index_status['num_docs']} documents")

    def _parse_relative_date(self, date_str: str) -> Optional[datetime]:
        """Parse relative date strings like 'yesterday', 'last week', '7 days ago'."""
        date_str = date_str.lower().strip()
        now = datetime.now()

        if date_str == "now" or date_str == "today":
            return now
        if date_str == "yesterday":
            return now - timedelta(days=1)
        if date_str == "last week":
            return now - timedelta(weeks=1)
        if date_str == "last month":
            return now - timedelta(days=30)

        # Parse "N days ago", "N weeks ago", etc.
        match = re.match(r'(\d+)\s*(day|days|week|weeks|month|months)\s*ago', date_str)
        if match:
            num = int(match.group(1))
            unit = match.group(2)
            if 'day' in unit:
                return now - timedelta(days=num)
            elif 'week' in unit:
                return now - timedelta(weeks=num)
            elif 'month' in unit:
                return now - timedelta(days=num * 30)

        # Try ISO format
        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except ValueError:
            pass

        # Try common formats
        for fmt in ['%Y-%m-%d', '%m/%d/%Y', '%d/%m/%Y']:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue

        return None

    def _extract_username_from_content(self, content: str) -> Optional[str]:
        """Extract username from page_content (format: 'username message content')."""
        if not content:
            return None
        parts = content.split(' ', 1)
        return parts[0] if parts else None

    def _search_messages(self, query: str, num_results: int = 8) -> List[Document]:
        """Execute a semantic search against the vector store."""
        num_results = min(max(num_results, 1), 20)

        cache_key = _get_cache_key(query, num_results=num_results)
        cached = _get_cached_results(cache_key)
        if cached:
            logger.info(f"Cache hit for query: '{query[:50]}...'")
            return cached

        try:
            docs = self.vector_store.similarity_search(query, k=num_results)
            logger.info(f"Search '{query[:50]}...' returned {len(docs)} results")
            _cache_results(cache_key, docs)
            return docs
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []

    def _search_by_user(self, query: str, username: str, num_results: int = 8) -> List[Document]:
        """Search messages filtered by username."""
        num_results = min(max(num_results, 1), 20)

        # Get more results and filter by username
        expanded_results = min(num_results * 5, 100)

        cache_key = _get_cache_key(query, username=username, num_results=num_results)
        cached = _get_cached_results(cache_key)
        if cached:
            return cached

        try:
            docs = self.vector_store.similarity_search(query, k=expanded_results)
            # Filter by username (case-insensitive partial match)
            username_lower = username.lower()
            filtered = [
                doc for doc in docs
                if username_lower in (self._extract_username_from_content(doc.page_content) or "").lower()
            ]
            result = filtered[:num_results]
            logger.info(f"Search by user '{username}' for '{query[:30]}...' returned {len(result)} results")
            _cache_results(cache_key, result)
            return result
        except Exception as e:
            logger.error(f"Search by user error: {e}")
            return []

    def _search_by_date_range(
        self, query: str, start_date: str, end_date: Optional[str] = None, num_results: int = 8
    ) -> List[Document]:
        """Search messages within a date range."""
        num_results = min(max(num_results, 1), 20)

        start_dt = self._parse_relative_date(start_date)
        end_dt = self._parse_relative_date(end_date) if end_date else datetime.now()

        if not start_dt:
            logger.warning(f"Could not parse start_date: {start_date}")
            return self._search_messages(query, num_results)

        start_ts = start_dt.timestamp()
        end_ts = end_dt.timestamp() if end_dt else datetime.now().timestamp()

        # Get more results and filter by date
        expanded_results = min(num_results * 5, 100)

        try:
            docs = self.vector_store.similarity_search(query, k=expanded_results)
            filtered = []
            for doc in docs:
                ts = doc.metadata.get('timestamp')
                if ts and start_ts <= float(ts) <= end_ts:
                    filtered.append(doc)
            result = filtered[:num_results]
            logger.info(f"Date range search '{query[:30]}...' ({start_date} to {end_date}) returned {len(result)} results")
            return result
        except Exception as e:
            logger.error(f"Date range search error: {e}")
            return []

    def _get_surrounding_messages(
        self, timestamp: float, before: int = 5, after: int = 5
    ) -> List[Document]:
        """Get messages around a specific timestamp."""
        before = min(max(before, 1), 20)
        after = min(max(after, 1), 20)

        try:
            # Search for messages near the timestamp
            # We'll get a broader set and filter by timestamp proximity
            docs = self.vector_store.similarity_search("", k=200)

            # Sort by timestamp
            docs_with_ts = [(doc, doc.metadata.get('timestamp', 0)) for doc in docs]
            docs_with_ts.sort(key=lambda x: float(x[1]) if x[1] else 0)

            # Find the target message and get surrounding
            target_idx = None
            min_diff = float('inf')
            for i, (doc, ts) in enumerate(docs_with_ts):
                if ts:
                    diff = abs(float(ts) - timestamp)
                    if diff < min_diff:
                        min_diff = diff
                        target_idx = i

            if target_idx is None:
                return []

            start_idx = max(0, target_idx - before)
            end_idx = min(len(docs_with_ts), target_idx + after + 1)

            result = [doc for doc, _ in docs_with_ts[start_idx:end_idx]]
            logger.info(f"Got {len(result)} surrounding messages for timestamp {timestamp}")
            return result
        except Exception as e:
            logger.error(f"Get surrounding messages error: {e}")
            return []

    def _get_user_activity(self, username: str) -> Dict[str, Any]:
        """Get activity summary for a user."""
        try:
            # Search for messages by this user
            docs = self.vector_store.similarity_search(username, k=100)

            username_lower = username.lower()
            user_docs = [
                doc for doc in docs
                if username_lower in (self._extract_username_from_content(doc.page_content) or "").lower()
            ]

            if not user_docs:
                return {
                    "username": username,
                    "message_count": 0,
                    "found": False
                }

            # Analyze activity
            timestamps = [doc.metadata.get('timestamp') for doc in user_docs if doc.metadata.get('timestamp')]
            timestamps = [float(ts) for ts in timestamps if ts]

            # Extract topics/keywords (simple word frequency)
            all_words = []
            for doc in user_docs:
                content = doc.page_content
                # Remove username from content
                words = content.split()[1:]  # Skip username
                all_words.extend([w.lower() for w in words if len(w) > 3])

            word_freq = defaultdict(int)
            for word in all_words:
                word_freq[word] += 1
            top_topics = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]

            return {
                "username": username,
                "message_count": len(user_docs),
                "found": True,
                "first_seen": datetime.fromtimestamp(min(timestamps)).isoformat() if timestamps else None,
                "last_seen": datetime.fromtimestamp(max(timestamps)).isoformat() if timestamps else None,
                "top_topics": [{"word": w, "count": c} for w, c in top_topics],
                "sample_messages": [doc.page_content[:200] for doc in user_docs[:3]]
            }
        except Exception as e:
            logger.error(f"Get user activity error: {e}")
            return {"username": username, "error": str(e)}

    def _count_mentions(self, term: str) -> Dict[str, Any]:
        """Count mentions of a term in the message history."""
        try:
            # Search for the term
            docs = self.vector_store.similarity_search(term, k=100)

            # Count exact matches (case-insensitive)
            term_lower = term.lower()
            exact_count = 0
            partial_count = 0

            for doc in docs:
                content_lower = doc.page_content.lower()
                if term_lower in content_lower:
                    exact_count += content_lower.count(term_lower)
                    partial_count += 1

            return {
                "term": term,
                "exact_mentions": exact_count,
                "messages_containing": partial_count,
                "messages_searched": len(docs)
            }
        except Exception as e:
            logger.error(f"Count mentions error: {e}")
            return {"term": term, "error": str(e)}

    def _get_recent_messages(self, num_results: int = 20) -> List[Document]:
        """Get the most recent messages by timestamp."""
        num_results = min(max(num_results, 1), 50)

        try:
            # Get a broad sample and sort by timestamp
            docs = self.vector_store.similarity_search("", k=200)

            # Sort by timestamp descending (most recent first)
            docs_with_ts = [(doc, doc.metadata.get('timestamp', 0)) for doc in docs]
            docs_with_ts.sort(key=lambda x: float(x[1]) if x[1] else 0, reverse=True)

            result = [doc for doc, _ in docs_with_ts[:num_results]]
            logger.info(f"Got {len(result)} recent messages")
            return result
        except Exception as e:
            logger.error(f"Get recent messages error: {e}")
            return []

    def _evaluate_answer(self, question: str, current_findings: str, confidence: str) -> Dict[str, Any]:
        """Self-reflection on whether we have enough information."""
        # This is more of a prompt to the model than actual evaluation
        # The model will use this to reflect on its findings
        suggestions = []

        if confidence == "low":
            suggestions.append("Consider more targeted searches with different phrasings")
            suggestions.append("Try searching for related topics or people")
            suggestions.append("Use search_by_user if the question is about a specific person")
        elif confidence == "medium":
            suggestions.append("Consider one or two more searches to fill gaps")
            suggestions.append("Use get_surrounding_messages for more context on key findings")

        return {
            "question": question,
            "confidence": confidence,
            "recommendation": "proceed" if confidence == "high" else "search_more",
            "suggestions": suggestions
        }

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

    def _handle_tool_call(
        self, fc_name: str, args: Dict[str, Any], all_docs: List[Document]
    ) -> Tuple[str, List[Document]]:
        """Handle a single tool call and return (result_text, new_docs)."""
        new_docs = []

        if fc_name == "search_messages":
            query = args.get("query", "")
            num_results = args.get("num_results", 8)
            docs = self._search_messages(query, num_results)
            new_docs = docs
            result_text = self._format_search_results(docs, len(all_docs))

        elif fc_name == "search_by_user":
            query = args.get("query", "")
            username = args.get("username", "")
            num_results = args.get("num_results", 8)
            docs = self._search_by_user(query, username, num_results)
            new_docs = docs
            result_text = self._format_search_results(docs, len(all_docs))

        elif fc_name == "search_by_date_range":
            query = args.get("query", "")
            start_date = args.get("start_date", "")
            end_date = args.get("end_date")
            num_results = args.get("num_results", 8)
            docs = self._search_by_date_range(query, start_date, end_date, num_results)
            new_docs = docs
            result_text = self._format_search_results(docs, len(all_docs))

        elif fc_name == "get_surrounding_messages":
            timestamp = args.get("timestamp", 0)
            before = args.get("before", 5)
            after = args.get("after", 5)
            docs = self._get_surrounding_messages(timestamp, before, after)
            new_docs = docs
            result_text = self._format_search_results(docs, len(all_docs))

        elif fc_name == "get_user_activity":
            username = args.get("username", "")
            activity = self._get_user_activity(username)
            import json
            result_text = json.dumps(activity, indent=2, default=str)

        elif fc_name == "count_mentions":
            term = args.get("term", "")
            counts = self._count_mentions(term)
            import json
            result_text = json.dumps(counts, indent=2)

        elif fc_name == "get_recent_messages":
            num_results = args.get("num_results", 20)
            docs = self._get_recent_messages(num_results)
            new_docs = docs
            result_text = self._format_search_results(docs, len(all_docs))

        elif fc_name == "evaluate_answer":
            question = args.get("question", "")
            findings = args.get("current_findings", "")
            confidence = args.get("confidence", "medium")
            evaluation = self._evaluate_answer(question, findings, confidence)
            import json
            result_text = json.dumps(evaluation, indent=2)

        else:
            result_text = f"Unknown tool: {fc_name}"

        return result_text, new_docs

    def infer(self, question: str, max_iterations: int = 15) -> Dict[str, Any]:
        """
        Run the agentic inference loop with ReAct pattern.

        The agent will:
        1. Plan its approach using available tools
        2. Execute searches using the most appropriate tools
        3. Self-evaluate if more information is needed
        4. Synthesize a final answer with citations
        """
        all_docs: List[Document] = []
        tool_calls_log: List[Dict[str, Any]] = []
        chat = self.model.start_chat()

        # Initial prompt with ReAct framing
        initial_prompt = f"""Question from user: {question}

Follow the ReAct framework to answer this question:

1. **Think**: What information do I need? Which tools are most appropriate?
2. **Act**: Use the right tools to gather information
3. **Observe**: Review results and decide if more searches are needed
4. **Repeat** until you have comprehensive context

Available tools:
- search_messages: General semantic search
- search_by_user: Find what a specific person said
- search_by_date_range: Search within a time period
- get_surrounding_messages: Get conversation context
- get_user_activity: Analyze a user's participation
- count_mentions: Count topic frequency
- get_recent_messages: Get latest messages
- evaluate_answer: Self-check before final answer

Start by analyzing the question and planning your search strategy."""

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
                args = dict(fc.args)
                logger.info(f"Agent tool call #{iteration}: {fc.name}({args})")

                # Handle the tool call
                result_text, new_docs = self._handle_tool_call(fc.name, args, all_docs)
                all_docs.extend(new_docs)

                # Log the tool call
                tool_calls_log.append({
                    "iteration": iteration,
                    "tool": fc.name,
                    "args": args,
                    "results_count": len(new_docs) if new_docs else 0
                })

                # Create function response part
                response_struct = Struct()
                response_struct.update({"results": result_text})

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

        logger.info(f"Agentic inference complete. {iteration} iterations, {len(unique_docs)} unique docs, {len(tool_calls_log)} tool calls")

        return {
            "question": question,
            "context": [doc.page_content for doc in sorted_docs],
            "answer": final_answer,
            "sources": sources,
            "iterations": iteration,
            "total_docs_retrieved": len(all_docs),
            "unique_docs": len(unique_docs),
            "tool_calls": tool_calls_log
        }
