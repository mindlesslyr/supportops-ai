from abc import ABC, abstractmethod

from app.schemas import RetrievedChunk


class AnswerProvider(ABC):
    @abstractmethod
    def answer(self, question: str, chunks: list[RetrievedChunk]) -> str: ...

class ContextAnswerProvider(AnswerProvider):
    def answer(self, question: str, chunks: list[RetrievedChunk]) -> str:
        if not chunks:
            return "I don't have enough information in the knowledge base to answer that."
        excerpts = ' '.join(chunk.text for chunk in chunks[:3])
        return f"Based on the support knowledge base: {excerpts}"

class OpenAIAnswerProvider(AnswerProvider):
    def __init__(self, api_key: str, model: str, base_url: str | None = None):
        from openai import OpenAI
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def answer(self, question: str, chunks: list[RetrievedChunk]) -> str:
        context = '\n\n'.join(f'[{i+1}] {c.text}' for i,c in enumerate(chunks))
        response = self.client.chat.completions.create(model=self.model, temperature=0, messages=[{'role':'system','content':'Answer only from the supplied context. Say when context is insufficient.'},{'role':'user','content':f'Context:\n{context}\n\nQuestion: {question}'}])
        return response.choices[0].message.content or ''
