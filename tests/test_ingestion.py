import pytest

from app.rag.answers import ContextAnswerProvider
from app.repositories import IdempotencyConflictError, SQLiteRepository
from app.schemas import DocumentCreate
from app.services import SupportService


class RecordingVectorStore:
    def __init__(self):
        self.add_calls = 0
        self.deleted: list[str] = []

    @staticmethod
    def chunk_ids(document_id: str, count: int) -> list[str]:
        return [f"{document_id}-{index}" for index in range(count)]

    def add_chunks(self, *args, chunk_ids: list[str], **kwargs) -> list[str]:
        self.add_calls += 1
        return chunk_ids

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        self.deleted.extend(chunk_ids)


def build_service(tmp_path) -> tuple[SupportService, RecordingVectorStore]:
    vectors = RecordingVectorStore()
    service = SupportService(
        SQLiteRepository(str(tmp_path / "db.sqlite")),
        vectors,  # type: ignore[arg-type]
        ContextAnswerProvider(),
        20,
        0,
        0.1,
        30,
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
