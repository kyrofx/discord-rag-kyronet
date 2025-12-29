from langchain_core.prompts import PromptTemplate

TEMPLATE = """You are a helpful assistant that answers questions based on Discord chat history.
Use ONLY the provided context to answer. If the answer isn't in the context, say so.
When citing information, reference the source number like [Source 1].

The following sentences are messages from a Discord conversation.
There are multiple participants, and the context messages are in chronological order.
Respond using the most recent context elements if relevant.

Context: {context}

Question: {question}

Answer concisely and cite your sources:"""

def get_prompt_template():
    return PromptTemplate.from_template(TEMPLATE)
