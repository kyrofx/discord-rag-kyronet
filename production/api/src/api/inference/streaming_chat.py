"""
Streaming chat inferencer with chain-of-thought visibility.

This module provides a streaming chat interface that exposes the agent's
thinking process, tool calls, and final response as Server-Sent Events.
"""
import os
import re
import json
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Generator, List, Dict, Any, Optional, Tuple
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
from google.protobuf.struct_pb2 import Struct
from utils.vector_store import get_vector_store, check_index_status
from inference.citations import generate_citations_for_documents
from langchain_core.documents import Document
from api.dashboard import get_current_model, get_current_thinking

# Import shared tools and cache from agentic_inference
from inference.agentic_inference import (
    tools,
    AGENT_SYSTEM_PROMPT,
    _get_cache_key,
    _get_cached_results,
    _cache_results,
)

logger = logging.getLogger(__name__)

genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))

CHAT_SYSTEM_PROMPT = AGENT_SYSTEM_PROMPT + """

CONVERSATION CONTEXT:
You are in a multi-turn conversation. Use the previous messages to understand context and avoid repeating searches you've already done."""


def create_sse_event(event_type: str, data: Dict[str, Any]) -> str:
    """Create a Server-Sent Event string."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


class StreamingChatInferencer:
    """
    A streaming chat inferencer that yields SSE events for chain-of-thought visibility.

    Events yielded:
    - thinking: Agent's reasoning process
    - tool_call: When the agent calls a tool
    - tool_result: Results from a tool call
    - content: Final answer content (streamed in chunks)
    - sources: Citation sources
    - done: Completion event with metadata
    - error: Error event
    """

    def __init__(self):
        self.vector_store = get_vector_store()

        # Check index status on init
        index_status = check_index_status()
        if not index_status["exists"]:
            logger.error(f"Vector index does not exist: {index_status.get('error')}")
        elif index_status["num_docs"] == 0:
            logger.warning("Vector index is empty")
        else:
            logger.info(f"Vector index ready with {index_status['num_docs']} documents")

    def _create_model(self, model_id: Optional[str] = None):
        """Create a GenerativeModel with current settings."""
        model_name = model_id or get_current_model()
        thinking_level = get_current_thinking()

        logger.info(f"Creating model: {model_name} with thinking: {thinking_level}")

        return genai.GenerativeModel(
            model_name,
            tools=[tools],
            system_instruction=CHAT_SYSTEM_PROMPT
        )

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

        try:
            return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except ValueError:
            pass

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
        expanded_results = min(num_results * 5, 100)

        cache_key = _get_cache_key(query, username=username, num_results=num_results)
        cached = _get_cached_results(cache_key)
        if cached:
            return cached

        try:
            docs = self.vector_store.similarity_search(query, k=expanded_results)
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
            docs = self.vector_store.similarity_search("", k=200)
            docs_with_ts = [(doc, doc.metadata.get('timestamp', 0)) for doc in docs]
            docs_with_ts.sort(key=lambda x: float(x[1]) if x[1] else 0)

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
            docs = self.vector_store.similarity_search(username, k=100)
            username_lower = username.lower()
            user_docs = [
                doc for doc in docs
                if username_lower in (self._extract_username_from_content(doc.page_content) or "").lower()
            ]

            if not user_docs:
                return {"username": username, "message_count": 0, "found": False}

            timestamps = [doc.metadata.get('timestamp') for doc in user_docs if doc.metadata.get('timestamp')]
            timestamps = [float(ts) for ts in timestamps if ts]

            all_words = []
            for doc in user_docs:
                content = doc.page_content
                words = content.split()[1:]
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
            docs = self.vector_store.similarity_search(term, k=100)
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
            docs = self.vector_store.similarity_search("", k=200)
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

    def _format_search_results(self, docs: List[Document], source_offset: int = 0) -> List[tuple]:
        """Format search results for the agent to read.

        Returns list of (source_num, doc, formatted_text) tuples.
        """
        if not docs:
            return []

        results = []
        for i, doc in enumerate(docs):
            source_num = source_offset + i + 1
            timestamp = doc.metadata.get('timestamp', 'unknown')
            formatted = f"[Source {source_num}] (timestamp: {timestamp})\n{doc.page_content}"
            results.append((source_num, doc, formatted))

        return results

    def _results_to_text(self, results: List[tuple]) -> str:
        """Convert results tuples to text for the agent."""
        if not results:
            return "No results found for this search."
        return "\n\n---\n\n".join(r[2] for r in results)

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

    def _build_conversation_context(self, history: List[Dict[str, str]], current_message: str) -> str:
        """Build conversation context from history."""
        if not history:
            return f"User: {current_message}"

        context_parts = []
        for msg in history[-10:]:
            role = "User" if msg["role"] == "user" else "Assistant"
            context_parts.append(f"{role}: {msg['content']}")

        context_parts.append(f"User: {current_message}")
        return "\n\n".join(context_parts)

    def _handle_tool_call(
        self, fc_name: str, args: Dict[str, Any], all_sources: List[tuple]
    ) -> Tuple[str, List[tuple]]:
        """Handle a single tool call and return (result_text, new_source_tuples)."""
        new_sources = []

        if fc_name == "search_messages":
            query = args.get("query", "")
            num_results = args.get("num_results", 8)
            docs = self._search_messages(query, num_results)
            new_sources = self._format_search_results(docs, len(all_sources))
            result_text = self._results_to_text(new_sources)

        elif fc_name == "search_by_user":
            query = args.get("query", "")
            username = args.get("username", "")
            num_results = args.get("num_results", 8)
            docs = self._search_by_user(query, username, num_results)
            new_sources = self._format_search_results(docs, len(all_sources))
            result_text = self._results_to_text(new_sources)

        elif fc_name == "search_by_date_range":
            query = args.get("query", "")
            start_date = args.get("start_date", "")
            end_date = args.get("end_date")
            num_results = args.get("num_results", 8)
            docs = self._search_by_date_range(query, start_date, end_date, num_results)
            new_sources = self._format_search_results(docs, len(all_sources))
            result_text = self._results_to_text(new_sources)

        elif fc_name == "get_surrounding_messages":
            timestamp = args.get("timestamp", 0)
            before = args.get("before", 5)
            after = args.get("after", 5)
            docs = self._get_surrounding_messages(timestamp, before, after)
            new_sources = self._format_search_results(docs, len(all_sources))
            result_text = self._results_to_text(new_sources)

        elif fc_name == "get_user_activity":
            username = args.get("username", "")
            activity = self._get_user_activity(username)
            result_text = json.dumps(activity, indent=2, default=str)

        elif fc_name == "count_mentions":
            term = args.get("term", "")
            counts = self._count_mentions(term)
            result_text = json.dumps(counts, indent=2)

        elif fc_name == "get_recent_messages":
            num_results = args.get("num_results", 20)
            docs = self._get_recent_messages(num_results)
            new_sources = self._format_search_results(docs, len(all_sources))
            result_text = self._results_to_text(new_sources)

        elif fc_name == "evaluate_answer":
            question = args.get("question", "")
            findings = args.get("current_findings", "")
            confidence = args.get("confidence", "medium")
            evaluation = self._evaluate_answer(question, findings, confidence)
            result_text = json.dumps(evaluation, indent=2)

        else:
            result_text = f"Unknown tool: {fc_name}"

        return result_text, new_sources

    def chat_stream(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        max_iterations: int = 15,
        model_override: Optional[str] = None
    ) -> Generator[str, None, None]:
        """
        Stream a chat response with chain-of-thought visibility.

        Yields SSE-formatted strings for each event.
        """
        history = history or []
        all_sources: List[tuple] = []  # List of (source_num, doc, formatted_text)
        tool_calls_log: List[Dict[str, Any]] = []

        try:
            # Create model with current settings (or override)
            model = self._create_model(model_override)

            # Build conversation context
            conversation_context = self._build_conversation_context(history, message)

            # Initial prompt with ReAct framing
            initial_prompt = f"""Conversation:
{conversation_context}

Follow the ReAct framework to answer the latest question:

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

Start by analyzing the question and choosing the best tools."""

            logger.info(f"Starting streaming chat for: {message[:100]}...")

            # Yield thinking event
            yield create_sse_event("thinking", {
                "content": "Analyzing the question and planning search strategy..."
            })

            chat = model.start_chat()
            response = chat.send_message(initial_prompt)

            iteration = 0
            while iteration < max_iterations:
                iteration += 1

                # Check for function calls
                function_calls = []
                for part in response.parts:
                    if hasattr(part, 'function_call') and part.function_call:
                        function_calls.append(part.function_call)

                if not function_calls:
                    # No function calls - check if we have any text
                    has_text = any(hasattr(p, 'text') and p.text for p in response.parts)
                    if not has_text:
                        logger.warning(f"Empty response from model, no function calls or text")
                        yield create_sse_event("thinking", {
                            "content": "Model didn't respond, retrying with explicit instruction..."
                        })
                        response = chat.send_message(
                            "Please use one of the available tools to find relevant information before answering."
                        )
                        continue
                    break

                # Process each function call
                function_response_parts = []
                for fc in function_calls:
                    args = dict(fc.args)

                    # Yield tool_call event with all args
                    yield create_sse_event("tool_call", {
                        "tool": fc.name,
                        "args": args,
                        "iteration": iteration
                    })

                    logger.info(f"Agent tool call #{iteration}: {fc.name}({args})")

                    # Handle the tool call using our unified handler
                    result_text, new_sources = self._handle_tool_call(fc.name, args, all_sources)
                    all_sources.extend(new_sources)

                    # Log the tool call
                    tool_calls_log.append({
                        "iteration": iteration,
                        "tool": fc.name,
                        "args": args,
                        "results_count": len(new_sources)
                    })

                    # Yield tool_result event with preview
                    yield create_sse_event("tool_result", {
                        "tool": fc.name,
                        "results_count": len(new_sources),
                        "preview": result_text[:500] + "..." if len(result_text) > 500 else result_text
                    })

                    # Create function response
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

                # Send function results back
                if function_response_parts:
                    yield create_sse_event("thinking", {
                        "content": f"Processing results... ({len(tool_calls_log)} tool calls so far)"
                    })
                    response = chat.send_message(function_response_parts)

            # Extract and stream the final answer
            final_answer = ""
            for part in response.parts:
                if hasattr(part, 'text') and part.text:
                    final_answer += part.text

            # Stream content in chunks for a nice streaming effect
            chunk_size = 50
            for i in range(0, len(final_answer), chunk_size):
                chunk = final_answer[i:i + chunk_size]
                yield create_sse_event("content", {"text": chunk})

            # Extract which source numbers the AI actually referenced
            # Match various formats including comma-separated: [Source 1, 2, 3], (Source 1), Source 1, [1], etc.
            referenced_nums = set()

            # First, find bracketed source references that may contain comma-separated numbers
            # e.g., [Source 1, 2, 3] or [Source 6, 12, 19]
            bracket_pattern = r'\[Source[s]?\s*([\d,\s]+)\]'
            for match in re.finditer(bracket_pattern, final_answer, re.IGNORECASE):
                nums_str = match.group(1)
                referenced_nums.update(int(n.strip()) for n in re.findall(r'\d+', nums_str))

            # Also match parenthesized source references: (Source 1, 2) or (Source 1)
            paren_pattern = r'\(Source[s]?\s*([\d,\s]+)\)'
            for match in re.finditer(paren_pattern, final_answer, re.IGNORECASE):
                nums_str = match.group(1)
                referenced_nums.update(int(n.strip()) for n in re.findall(r'\d+', nums_str))

            # Match standalone "Source N" references
            standalone_pattern = r'Source\s+(\d+)'
            referenced_nums.update(int(m) for m in re.findall(standalone_pattern, final_answer, re.IGNORECASE))

            # Match simple bracketed numbers like [1] or [1, 2, 3]
            simple_bracket_pattern = r'\[([\d,\s]+)\]'
            for match in re.finditer(simple_bracket_pattern, final_answer):
                nums_str = match.group(1)
                # Only count if it looks like source refs (not other bracketed numbers)
                nums = [int(n.strip()) for n in re.findall(r'\d+', nums_str)]
                # Assume it's a source ref if numbers are in our source range
                for n in nums:
                    if any(s[0] == n for s in all_sources):
                        referenced_nums.add(n)

            # Build sources dict preserving original numbers, deduplicating by content
            seen_content = set()
            sources = []
            for source_num, doc, _ in all_sources:
                if source_num not in referenced_nums:
                    continue
                content_hash = hash(doc.page_content)
                if content_hash in seen_content:
                    continue
                seen_content.add(content_hash)

                metadata = doc.metadata or {}
                url = metadata.get('url', '')
                content = doc.page_content or ''
                snippet = content[:300] + "..." if len(content) > 300 else content

                sources.append({
                    'source_number': source_num,
                    'snippet': snippet,
                    'urls': [url] if url else [],
                    'timestamp': metadata.get('timestamp'),
                    'channel': metadata.get('channel')
                })

            # Sort by source number for consistent display
            sources.sort(key=lambda x: x['source_number'])

            # Yield sources
            if sources:
                yield create_sse_event("sources", {"sources": sources})

            # Yield completion event
            yield create_sse_event("done", {
                "iterations": iteration,
                "total_docs_retrieved": len(all_sources),
                "unique_sources_cited": len(sources),
                "tool_calls": len(tool_calls_log),
                "tools_used": list(set(tc["tool"] for tc in tool_calls_log))
            })

            logger.info(f"Streaming chat complete. {iteration} iterations, {len(tool_calls_log)} tool calls, {len(sources)} sources cited")

        except Exception as e:
            logger.error(f"Streaming chat error: {e}", exc_info=True)
            yield create_sse_event("error", {"message": str(e)})


# Singleton instance
_streaming_inferencer = None

def get_streaming_inferencer() -> StreamingChatInferencer:
    global _streaming_inferencer
    if _streaming_inferencer is None:
        _streaming_inferencer = StreamingChatInferencer()
    return _streaming_inferencer
