import asyncio
import hashlib
import hmac
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from qdrant_client import QdrantClient, models

from app.config import Settings
from app.main import MaxBodySizeMiddleware, create_app
from app.rag.answers import ContextAnswerProvider
from app.rag.embeddings import HashEmbeddingProvider
from app.rag.vector_store import VectorStore, VectorStoreConfigurationError
from app.repositories import IdempotencyInProgressError, SQLiteRepository
from app.schemas import DocumentCreate
from app.services import IngestionError, SupportService

API_HEADERS = {"X-API-Key": "test-api-key"}


@pytest.fixture
def client(tmp_path):
    app = create_app(
        Settings(
            qdrant_path=str(tmp_path / "qdrant"),
            database_path=str(tmp_path / "db.sqlite"),
            qdrant_collection="test",
            chunk_size=8,
            chunk_overlap=2,
            low_confidence_threshold=0.01,
            api_key="test-api-key",
            allow_unsigned_webhooks_dev=True,
        )
    )
    with TestClient(app) as test_client:
        yield test_client


def test_health_is_public_but_api_requires_key(client):
    assert client.get("/health").status_code == 200
    assert client.get("/documents").status_code == 401
    assert client.get("/documents", headers=API_HEADERS).status_code == 200


def test_unconfigured_api_auth_disables_protected_endpoints(tmp_path):
    app = create_app(
        Settings(qdrant_path=str(tmp_path / "q"), database_path=str(tmp_path / "db"))
    )
    with TestClient(app) as test_client:
        assert test_client.get("/documents").status_code == 503


def test_ingest_list_query_and_citations(client):
    payload = {
        "title": "Password reset",
        "content": "To reset your password open settings and select security then choose reset password.",
        "source": "kb://password",
        "metadata": {"team": "identity"},
    }
    created = client.post("/documents", json=payload, headers=API_HEADERS)
    assert created.status_code == 201
    assert created.json()["chunk_count"] >= 1
    docs = client.get("/documents", headers=API_HEADERS).json()
    assert docs[0]["title"] == "Password reset"
    result = client.post(
        "/query",
        json={"question": "How do I reset my password?", "top_k": 3},
        headers=API_HEADERS,
    )
    assert result.status_code == 200
    body = result.json()
    assert body["citations"][0]["source"] == "kb://password"
    assert body["retrieved_chunks"]
    assert "knowledge base" in body["answer"]


def test_document_ingestion_replays_original_response(client):
    payload = {
        "title": "Refunds",
        "content": "Refunds are available within 30 days.",
        "source": "kb://refunds",
    }
    headers = {**API_HEADERS, "Idempotency-Key": "document-refunds-v1"}

    first = client.post("/documents", json=payload, headers=headers)
    second = client.post("/documents", json=payload, headers=headers)

    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json() == first.json()
    assert len(client.get("/documents", headers=API_HEADERS).json()) == 1
    assert client.app.state.service.vectors.client.count("test", exact=True).count == first.json()[
        "chunk_count"
    ]


def test_document_ingestion_rejects_key_reuse_with_different_payload(client):
    headers = {**API_HEADERS, "Idempotency-Key": "document-policy-v1"}
    first = client.post(
        "/documents",
        json={"title": "Refunds", "content": "Refunds take 5 days."},
        headers=headers,
    )

    conflict = client.post(
        "/documents",
        json={"title": "Refunds", "content": "Refunds take 10 days."},
        headers=headers,
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "idempotency_conflict",
        "message": "Idempotency key was used with a different payload",
    }
    assert len(client.get("/documents", headers=API_HEADERS).json()) == 1


def test_document_ingestion_rejects_blank_idempotency_key(client):
    response = client.post(
        "/documents",
        json={"title": "Refunds", "content": "Refunds take 5 days."},
        headers={**API_HEADERS, "Idempotency-Key": "   "},
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_idempotency_key"
    assert client.get("/documents", headers=API_HEADERS).json() == []


def test_blank_document_and_query_are_rejected(client):
    response = client.post(
        "/documents",
        json={"title": "x", "content": " \n\t ", "source": "manual"},
        headers=API_HEADERS,
    )
    assert response.status_code == 422
    response = client.post(
        "/query", json={"question": "   "}, headers=API_HEADERS
    )
    assert response.status_code == 422


def test_no_or_low_confidence_context_returns_insufficient_answer(client):
    response = client.post(
        "/query", json={"question": "unknown"}, headers=API_HEADERS
    )
    assert response.status_code == 200
    assert "enough information" in response.json()["answer"]
    assert response.json()["citations"] == []


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/documents", {"title": "x", "content": "x" * 100_001}),
        ("/documents", {"title": "x", "content": "ok", "metadata": {"x": "y" * 16_385}}),
        ("/query", {"question": "q" * 4_001}),
        ("/feedback", {"query": "q", "answer": "a" * 20_001, "rating": 1}),
        ("/feedback", {"query": "q", "answer": "a", "rating": 1, "comment": "c" * 4_001}),
    ],
)
def test_oversized_fields_are_rejected(client, path, payload):
    assert client.post(path, json=payload, headers=API_HEADERS).status_code == 422


def test_oversized_request_body_is_rejected(tmp_path):
    app = create_app(
        Settings(
            qdrant_path=str(tmp_path / "q"),
            database_path=str(tmp_path / "db"),
            api_key="test-api-key",
            max_request_body_bytes=1024,
        )
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/documents",
            content=b"x" * 1025,
            headers={**API_HEADERS, "Content-Type": "application/json"},
        )
    assert response.status_code == 413


def test_malformed_content_length_returns_bad_request():
    downstream_called = False
    sent = []

    async def downstream(scope, receive, send):
        nonlocal downstream_called
        downstream_called = True

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    middleware = MaxBodySizeMiddleware(downstream, max_bytes=1024)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/documents",
        "headers": [(b"content-length", b"invalid")],
    }

    asyncio.run(middleware(scope, receive, send))

    assert not downstream_called
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 400


def test_feedback_is_persisted(client):
    response = client.post(
        "/feedback",
        json={"query": "q", "answer": "a", "rating": 1, "comment": "helpful"},
        headers=API_HEADERS,
    )
    assert response.status_code == 201
    assert response.json()["id"] == 1


def test_webhook_is_disabled_without_signing_config(tmp_path):
    app = create_app(
        Settings(
            qdrant_path=str(tmp_path / "q"),
            database_path=str(tmp_path / "d.db"),
            api_key="test-api-key",
        )
    )
    with TestClient(app) as test_client:
        response = test_client.post(
            "/webhooks/support",
            json={"ticket_id": "T", "message": "help"},
            headers={**API_HEADERS, "Idempotency-Key": "x"},
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "webhook_disabled"


def test_webhook_replays_result_and_rejects_conflicting_payload(client):
    payload = {"ticket_id": "T-1", "message": "password reset help"}
    headers = {**API_HEADERS, "Idempotency-Key": "event-1"}
    first = client.post("/webhooks/support", json=payload, headers=headers)
    assert first.status_code == 200
    second = client.post("/webhooks/support", json=payload, headers=headers)
    assert second.status_code == 200
    assert second.json() == first.json()
    conflict = client.post(
        "/webhooks/support",
        json={"ticket_id": "T-1", "message": "different"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_webhook_rejects_oversized_idempotency_key(client):
    response = client.post(
        "/webhooks/support",
        json={"ticket_id": "T", "message": "help"},
        headers={**API_HEADERS, "Idempotency-Key": "x" * 257},
    )
    assert response.status_code == 422


def test_failed_webhook_can_be_retried(client, monkeypatch):
    original = client.app.state.service.query
    attempts = 0

    def fail_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporary")
        return original(*args, **kwargs)

    monkeypatch.setattr(client.app.state.service, "query", fail_once)
    headers = {**API_HEADERS, "Idempotency-Key": "retry-key"}
    payload = {"ticket_id": "T-2", "message": "help"}
    with pytest.raises(RuntimeError):
        client.post("/webhooks/support", json=payload, headers=headers)
    assert client.post("/webhooks/support", json=payload, headers=headers).status_code == 200
    assert attempts == 2


def test_stale_webhook_worker_cannot_return_uncommitted_response(client, monkeypatch):
    monkeypatch.setattr(
        client.app.state.repository,
        "complete_webhook",
        lambda *args, **kwargs: False,
    )

    response = client.post(
        "/webhooks/support",
        json={"ticket_id": "T-stale", "message": "help"},
        headers={**API_HEADERS, "Idempotency-Key": "stale-worker"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_webhook_lease"


def test_webhook_hmac_auth(tmp_path):
    app = create_app(
        Settings(
            qdrant_path=str(tmp_path / "q"),
            database_path=str(tmp_path / "d.db"),
            qdrant_collection="auth",
            webhook_secret="secret",
            api_key="test-api-key",
        )
    )
    with TestClient(app) as test_client:
        body = json.dumps(
            {"ticket_id": "T-2", "message": "help"}, separators=(",", ":")
        ).encode()
        headers = {
            **API_HEADERS,
            "Content-Type": "application/json",
            "Idempotency-Key": "x",
        }
        assert test_client.post("/webhooks/support", content=body, headers=headers).status_code == 401
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        response = test_client.post(
            "/webhooks/support",
            content=body,
            headers={**headers, "X-Signature-256": signature},
        )
        assert response.status_code == 200


def test_ingestion_deletes_vectors_when_metadata_write_fails(tmp_path):
    class FailingRepository:
        def add_document(self, *args, **kwargs):
            raise OSError("disk full")

    qdrant = QdrantClient(path=str(tmp_path / "q"))
    vectors = VectorStore(qdrant, "test", HashEmbeddingProvider(16))
    service = SupportService(
        FailingRepository(), vectors, ContextAnswerProvider(), 20, 0, 0.1
    )
    with pytest.raises(IngestionError) as exc_info:
        service.ingest(DocumentCreate(title="x", content="valid content", source="s"))
    assert exc_info.value.code == "metadata_persistence_failed"
    assert qdrant.count("test", exact=True).count == 0


def test_ingestion_compensates_when_vector_write_commits_then_raises():
    class WritingThenFailingStore:
        def __init__(self):
            self.written = []
            self.deleted = []

        def chunk_ids(self, document_id, count):
            return [f"{document_id}-{index}" for index in range(count)]

        def add_chunks(self, *args, chunk_ids, **kwargs):
            self.written.extend(chunk_ids)
            raise OSError("connection dropped after commit")

        def delete_chunks(self, chunk_ids):
            self.deleted.extend(chunk_ids)

    store = WritingThenFailingStore()
    service = SupportService(object(), store, ContextAnswerProvider(), 20, 0, 0.1)
    with pytest.raises(IngestionError) as exc_info:
        service.ingest(DocumentCreate(title="x", content="valid content", source="s"))
    assert exc_info.value.code == "vector_persistence_failed"
    assert store.deleted == store.written


def test_idempotency_claim_is_atomic_under_concurrency(tmp_path):
    repository = SQLiteRepository(str(tmp_path / "db.sqlite"))
    barrier = threading.Barrier(8)
    outcomes = []
    errors = []

    def claim():
        try:
            barrier.wait()
            outcomes.append(repository.begin_webhook("same", "T", "digest", 30))
        except IdempotencyInProgressError:
            outcomes.append("in_progress")
        except Exception as exc:  # noqa: BLE001 - collect unexpected thread failures
            errors.append(exc)

    threads = [threading.Thread(target=claim) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert sum(outcome != "in_progress" for outcome in outcomes) == 1


def test_stale_webhook_lease_is_reclaimed_without_old_worker_overwrite(tmp_path):
    repository = SQLiteRepository(str(tmp_path / "db.sqlite"))
    first = repository.begin_webhook("key", "T", "digest", 0.01)
    time.sleep(0.02)
    second = repository.begin_webhook("key", "T", "digest", 30)
    assert first.lease_token is not None
    assert second.lease_token is not None
    assert first.lease_token != second.lease_token
    assert repository.complete_webhook("key", first.lease_token, {"status": "old"}) is False
    assert repository.complete_webhook("key", second.lease_token, {"status": "new"}) is True
    replay = repository.begin_webhook("key", "T", "digest", 30)
    assert replay.response == {"status": "new"}


def test_stale_ingestion_claim_reuses_document_id_without_old_worker_commit(tmp_path):
    repository = SQLiteRepository(str(tmp_path / "db.sqlite"))
    first = repository.begin_document_ingestion("key", "digest", 0.01)
    time.sleep(0.02)
    second = repository.begin_document_ingestion("key", "digest", 30)

    assert first.document_id == second.document_id
    assert first.lease_token is not None
    assert second.lease_token is not None
    assert first.lease_token != second.lease_token

    old_response = {
        "id": first.document_id,
        "title": "Old",
        "source": "manual",
        "metadata": {},
        "chunk_count": 1,
    }
    new_response = {**old_response, "title": "Canonical"}
    assert (
        repository.complete_document_ingestion(
            "key",
            first.lease_token,
            first.document_id,
            "Old",
            "manual",
            {},
            1,
            old_response,
        )
        is False
    )
    assert (
        repository.complete_document_ingestion(
            "key",
            second.lease_token,
            second.document_id,
            "Canonical",
            "manual",
            {},
            1,
            new_response,
        )
        is True
    )
    replay = repository.begin_document_ingestion("key", "digest", 30)
    assert replay.response == new_response
    assert repository.list_documents()[0]["title"] == "Canonical"


def test_production_disables_docs(tmp_path):
    app = create_app(
        Settings(
            qdrant_path=str(tmp_path / "q"),
            database_path=str(tmp_path / "db"),
            app_environment="production",
        )
    )
    with TestClient(app) as test_client:
        assert test_client.get("/docs").status_code == 404
        assert test_client.get("/redoc").status_code == 404
        assert test_client.get("/openapi.json").status_code == 404


def test_existing_qdrant_collection_mismatch_fails_clearly(tmp_path):
    client = QdrantClient(path=str(tmp_path / "q"))
    client.create_collection(
        "mismatch",
        vectors_config=models.VectorParams(size=8, distance=models.Distance.DOT),
    )
    with pytest.raises(VectorStoreConfigurationError, match="expected size=16"):
        VectorStore(client, "mismatch", HashEmbeddingProvider(16))


def test_production_rejects_unsigned_webhook_bypass():
    with pytest.raises(ValidationError, match="ALLOW_UNSIGNED_WEBHOOKS_DEV"):
        Settings(
            app_environment="production",
            allow_unsigned_webhooks_dev=True,
        )


def test_openai_limits_are_validated():
    with pytest.raises(ValidationError):
        Settings(openai_timeout_seconds=0)
    with pytest.raises(ValidationError):
        Settings(openai_max_retries=-1)
    with pytest.raises(ValidationError):
        Settings(openai_max_output_tokens=0)


def test_ingestion_lease_must_be_positive():
    with pytest.raises(ValidationError):
        Settings(ingestion_lease_seconds=0)


def test_provider_settings_are_validated():
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(answer_provider="openai")
    with pytest.raises(ValidationError):
        Settings(embedding_backend="unknown")
    with pytest.raises(ValidationError, match="CHUNK_OVERLAP"):
        Settings(chunk_size=10, chunk_overlap=10)
