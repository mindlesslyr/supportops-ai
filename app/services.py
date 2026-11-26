import hashlib
import json
import uuid

from app.rag.answers import AnswerProvider
from app.rag.chunking import chunk_text
from app.rag.vector_store import VectorStore
from app.repositories import SQLiteRepository
from app.schemas import Citation, DocumentCreate, DocumentRead, QueryResponse

INSUFFICIENT_CONTEXT = "I don't have enough information in the knowledge base to answer that."


class IngestionError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class SupportService:
    def __init__(
        self,
        repository: SQLiteRepository,
        vectors: VectorStore,
        answers: AnswerProvider,
        chunk_size: int,
        chunk_overlap: int,
        confidence_threshold: float = 0.35,
        ingestion_lease_seconds: float = 30,
    ):
        self.repository, self.vectors, self.answers = repository, vectors, answers
        self.chunk_size, self.chunk_overlap = chunk_size, chunk_overlap
        self.confidence_threshold = confidence_threshold
        self.ingestion_lease_seconds = ingestion_lease_seconds

    def ingest(self, document: DocumentCreate, idempotency_key: str | None = None) -> DocumentRead:
        claim = None
        lease_token = None
        if idempotency_key:
            encoded = json.dumps(
                document.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            claim = self.repository.begin_document_ingestion(
                idempotency_key,
                hashlib.sha256(encoded).hexdigest(),
                self.ingestion_lease_seconds,
            )
            if claim.response is not None:
                return DocumentRead.model_validate(claim.response)
            if claim.lease_token is None:
                raise RuntimeError("Document ingestion claim did not include a lease token")
            lease_token = claim.lease_token
        doc_id = claim.document_id if claim else str(uuid.uuid4())
        chunks = chunk_text(document.content.strip(), self.chunk_size, self.chunk_overlap)
        if not chunks:
            if idempotency_key and lease_token:
                self.repository.fail_document_ingestion(idempotency_key, lease_token)
            raise IngestionError("zero_chunks", "Document content produced no chunks")
        chunk_ids = self.vectors.chunk_ids(doc_id, len(chunks))
        try:
            self.vectors.add_chunks(
                doc_id,
                document.title,
                document.source,
                document.metadata,
                [chunk.text for chunk in chunks],
                chunk_ids=chunk_ids,
            )
        except Exception as exc:
            owns_claim = not idempotency_key or (
                claim is not None
                and lease_token is not None
                and self.repository.fail_document_ingestion(idempotency_key, lease_token)
            )
            if owns_claim:
                try:
                    self.vectors.delete_chunks(chunk_ids)
                except Exception as compensation_exc:  # noqa: BLE001
                    raise IngestionError(
                        "vector_persistence_failed_compensation_failed",
                        f"Vector persistence was uncertain and cleanup failed: {compensation_exc}",
                    ) from exc
            raise IngestionError(
                "vector_persistence_failed",
                "Vector persistence failed; deterministic point IDs were cleaned up",
            ) from exc
        response = DocumentRead(
            id=doc_id,
            title=document.title,
            source=document.source,
            metadata=document.metadata,
            chunk_count=len(chunks),
        )
        try:
            if idempotency_key:
                if lease_token is None:
                    raise RuntimeError("Document ingestion lease token was lost")
                committed = self.repository.complete_document_ingestion(
                    idempotency_key,
                    lease_token,
                    doc_id,
                    document.title,
                    document.source,
                    document.metadata,
                    len(chunks),
                    response.model_dump(mode="json"),
                )
                if not committed:
                    raise IngestionError(
                        "stale_ingestion_lease",
                        "Document ingestion lease expired; retry the request",
                    )
            else:
                self.repository.add_document(
                    doc_id,
                    document.title,
                    document.source,
                    document.metadata,
                    len(chunks),
                )
        except Exception as exc:
            owns_claim = not idempotency_key or (
                claim is not None
                and lease_token is not None
                and self.repository.fail_document_ingestion(idempotency_key, lease_token)
            )
            if owns_claim:
                try:
                    self.vectors.delete_chunks(chunk_ids)
                except Exception as compensation_exc:  # noqa: BLE001 - preserve both failures
                    raise IngestionError(
                        "metadata_persistence_failed_compensation_failed",
                        f"Metadata persistence failed and vector cleanup failed: {compensation_exc}",
                    ) from exc
            if isinstance(exc, IngestionError):
                raise
            raise IngestionError(
                "metadata_persistence_failed",
                "Metadata persistence failed; inserted vectors were removed",
            ) from exc
        return response

    def query(self, question: str, top_k: int) -> QueryResponse:
        question = question.strip()
        chunks = self.vectors.search(question, top_k)
        confidence = round(max((chunk.score for chunk in chunks), default=0.0), 4)
        if not chunks or confidence < self.confidence_threshold:
            return QueryResponse(
                answer=INSUFFICIENT_CONTEXT,
                citations=[],
                confidence=confidence,
                retrieved_chunks=chunks,
            )
        citations = [
            Citation(
                document_id=chunk.document_id,
                title=chunk.title,
                source=chunk.source,
                chunk_id=chunk.chunk_id,
                metadata=chunk.metadata,
            )
            for chunk in chunks
        ]
        return QueryResponse(
            answer=self.answers.answer(question, chunks),
            citations=citations,
            confidence=confidence,
            retrieved_chunks=chunks,
        )
