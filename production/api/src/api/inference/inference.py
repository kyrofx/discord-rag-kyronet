import os
import logging
import google.generativeai as genai
from inference.prompting import get_prompt_template
from inference.citations import generate_citations_for_documents
from utils.vector_store import get_vector_store, check_index_status
from langgraph.graph import START, StateGraph
from inference import State

# Configure logging
logger = logging.getLogger(__name__)

# Configure Gemini
genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))


class Inferencer:
    def __init__(self):
        self.vector_store = get_vector_store()
        self.model = genai.GenerativeModel("gemini-3-flash-preview")
        self.prompt_template = get_prompt_template()
        self.graph = self.create_graph()

        # Check and log index status on initialization
        index_status = check_index_status()
        if not index_status["exists"]:
            logger.error(
                f"Vector index does not exist or is inaccessible: {index_status.get('error')}. "
                "Run the indexing pipeline to create and populate the index."
            )
        elif index_status["num_docs"] == 0:
            logger.warning(
                "Vector index exists but is empty. "
                "Run the indexing pipeline to populate it with documents."
            )
        else:
            logger.info(f"Vector index ready with {index_status['num_docs']} documents")

    def retrieve(self, state: State):
        question = state["question"]
        logger.info(f"Retrieving context for question: {question[:100]}...")

        try:
            retrieved_docs = self.vector_store.similarity_search(question, k=6)
            logger.info(f"Retrieved {len(retrieved_docs)} documents from vector store")

            if not retrieved_docs:
                logger.warning(
                    "No documents retrieved from vector store. "
                    "This could indicate: 1) Empty index, 2) Index doesn't exist, "
                    "3) No relevant documents found. Check if the indexing pipeline has been run."
                )
        except Exception as e:
            logger.error(f"Error during similarity search: {e}", exc_info=True)
            retrieved_docs = []

        return {"context": retrieved_docs}

    def generate(self, state: State):
        sorted_context = sorted(state["context"], key=lambda x: x.metadata.get("timestamp", 0))

        # Build context with source markers for citations
        context_parts = []
        for i, doc in enumerate(sorted_context):
            context_parts.append(f"[Source {i+1}]\n{doc.page_content}")
        docs_content = "\n\n".join(context_parts)

        # Build the prompt
        prompt_text = self.prompt_template.format(
            question=state["question"],
            context=docs_content
        )

        # Generate response with Gemini
        response = self.model.generate_content(prompt_text)

        # Generate citations
        sources = generate_citations_for_documents(sorted_context)

        return {
            "answer": response.text,
            "sources": sources
        }

    def create_graph(self):
        graph_builder = StateGraph(State).add_sequence([self.retrieve, self.generate])
        graph_builder.add_edge(START, "retrieve")
        return graph_builder.compile()

    def infer(self, prompt: str):
        result = self.graph.invoke({"question": prompt, "sources": []})
        return {
            "question": result["question"],
            "context": [doc.page_content for doc in result["context"]],
            "answer": result["answer"],
            "sources": result.get("sources", [])
        }
