from types import SimpleNamespace

import openai

from app.rag.answers import OpenAIAnswerProvider
from app.schemas import RetrievedChunk


def test_openai_provider_sets_limits(monkeypatch):
    client_options = {}
    request_options = {}

    class FakeCompletions:
        def create(self, **kwargs):
            request_options.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="answer"))]
            )

    class FakeClient:
        chat = SimpleNamespace(completions=FakeCompletions())

    def fake_openai(**kwargs):
        client_options.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(openai, "OpenAI", fake_openai)
    provider = OpenAIAnswerProvider(
        api_key="test-key",
        model="test-model",
        timeout_seconds=12,
        max_retries=1,
        max_output_tokens=300,
    )
    chunk = RetrievedChunk(
        chunk_id="chunk-1",
        document_id="doc-1",
        text="Refunds are available within 30 days.",
        score=0.9,
        title="Refund policy",
        source="kb://refunds",
        metadata={},
    )

    assert provider.answer("Can I get a refund?", [chunk]) == "answer"
    assert client_options["timeout"] == 12
    assert client_options["max_retries"] == 1
    assert request_options["max_completion_tokens"] == 300
