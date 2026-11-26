import pytest
from qdrant_client import QdrantClient

from app.rag.answers import ContextAnswerProvider
from app.rag.embeddings import HashEmbeddingProvider
from app.rag.vector_store import VectorStore
from app.repositories import IdempotencyConflictError, SQLiteRepository
from app.schemas import DocumentCreate
from app.services import IngestionError, SupportService


class RecordingVectorStore:
    def __init__(self):
        self.add_calls = 0
        self.deleted: list[str] = []
        self.reconciled: list[str] = []

    @staticmethod
    def chunk_ids(document_id: str, count: int) -> list[str]:
        return [f"{document_id}-{index}" for index in range(count)]

    def add_chunks(self, *args, chunk_ids: list[str], **kwargs) -> list[str]:
        self.add_calls += 1
        return chunk_ids

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        self.deleted.extend(chunk_ids)

    def delete_document(self, document_id: str) -> None:
        self.reconciled.append(document_id)


def build_service(tmp_path) -> tuple[SupportService, RecordingVectorStore]:
    vectors = RecordingVectorStore()
    service = SupportService(
        SQLiteRepository(str(tmp_path / "db.sqlite")),
        vectors,  # type: ignore[arg-type]
        ContextAnswerProvider(),
        20,
        0,
        0.1,
    )
    return service, vectors


def test_service_replay_returns_original_document_without_rewriting_vectors(tmp_path):
    service, vectors = build_service(tmp_path)
    document = DocumentCreate(title="Refunds", content="Refunds take five business days.")

    first = service.ingest(document, "refund-policy-v1")
    replay = service.ingest(document, "refund-policy-v1")

    assert replay == first
    assert vectors.add_calls == 1
    assert len(service.repository.list_documents()) == 1


def test_service_conflict_stops_before_vector_write(tmp_path):
    service, vectors = build_service(tmp_path)
    service.ingest(
        DocumentCreate(title="Refunds", content="Refunds take five business days."),
        "refund-policy-v1",
    )

    with pytest.raises(IdempotencyConflictError):
        service.ingest(
            DocumentCreate(title="Refunds", content="Refunds take ten business days."),
            "refund-policy-v1",
        )

    assert vectors.add_calls == 1
    assert len(service.repository.list_documents()) == 1


def test_uncertain_vector_write_retries_with_original_document_id(tmp_path):
    class WriteThenFailOnceStore(RecordingVectorStore):
        def __init__(self):
            super().__init__()
            self.document_ids: list[str] = []

        def add_chunks(self, document_id, *args, chunk_ids, **kwargs):
            self.add_calls += 1
            self.document_ids.append(document_id)
            if self.add_calls == 1:
                raise OSError("connection dropped after Qdrant accepted the points")
            return chunk_ids

        def delete_chunks(self, chunk_ids):
            raise AssertionError("idempotent retries must retain deterministic points")

    vectors = WriteThenFailOnceStore()
    service = SupportService(
        SQLiteRepository(str(tmp_path / "uncertain.sqlite")),
        vectors,  # type: ignore[arg-type]
        ContextAnswerProvider(),
        20,
        0,
        0.1,
    )
    document = DocumentCreate(title="Refunds", content="Refunds take five business days.")

    with pytest.raises(IngestionError) as exc_info:
        service.ingest(document, "refund-policy-v1")
    assert exc_info.value.code == "vector_persistence_failed"

    retry = service.ingest(document, "refund-policy-v1")

    assert vectors.document_ids == [retry.id, retry.id]
    assert len(service.repository.list_documents()) == 1


def test_retry_reconciles_points_when_chunk_plan_changes(tmp_path):
    class WriteThenFailOnceStore(VectorStore):
        fail_once = True

        def add_chunks(self, *args, **kwargs):
            ids = super().add_chunks(*args, **kwargs)
            if self.fail_once:
                self.fail_once = False
                raise OSError("connection dropped after Qdrant accepted the points")
            return ids

    repository = SQLiteRepository(str(tmp_path / "db.sqlite"))
    qdrant = QdrantClient(path=str(tmp_path / "qdrant"))
    vectors = WriteThenFailOnceStore(qdrant, "test", HashEmbeddingProvider(16))
    document = DocumentCreate(
        title="Refunds",
        content="one two three four five six seven eight",
    )
    first_service = SupportService(repository, vectors, ContextAnswerProvider(), 2, 0, 0.1)

    with pytest.raises(IngestionError):
        first_service.ingest(document, "refund-policy-v1")

    retry_service = SupportService(repository, vectors, ContextAnswerProvider(), 20, 0, 0.1)
    result = retry_service.ingest(document, "refund-policy-v1")

    assert result.chunk_count == 1
    assert qdrant.count("test", exact=True).count == result.chunk_count
    qdrant.close()
