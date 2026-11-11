import time
import uuid
from typing import Any, cast

from qdrant_client import QdrantClient, models

from app.rag.embeddings import EmbeddingProvider
from app.schemas import RetrievedChunk


class VectorStoreConfigurationError(RuntimeError):
    pass


class VectorStore:
    def __init__(
        self,
        client: QdrantClient,
        collection: str,
        embeddings: EmbeddingProvider,
        startup_attempts: int = 1,
        retry_seconds: float = 0,
    ):
        self.client = client
        self.collection = collection
        self.embeddings = embeddings
        self._ensure_collection(startup_attempts, retry_seconds)

    def _ensure_collection(self, attempts: int, delay: float) -> None:
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                if not self.client.collection_exists(self.collection):
                    self.client.create_collection(
                        collection_name=self.collection,
                        vectors_config=models.VectorParams(
                            size=self.embeddings.dimensions, distance=models.Distance.COSINE
                        ),
                    )
                info = self.client.get_collection(self.collection)
                vectors = info.config.params.vectors
                if not isinstance(vectors, models.VectorParams):
                    raise VectorStoreConfigurationError(
                        f"Qdrant collection '{self.collection}' must use one unnamed vector"
                    )
                distance = getattr(vectors.distance, "value", vectors.distance)
                if vectors.size != self.embeddings.dimensions or str(distance).lower() != "cosine":
                    raise VectorStoreConfigurationError(
                        f"Qdrant collection '{self.collection}' mismatch: expected size="
                        f"{self.embeddings.dimensions}, distance=Cosine; got size={vectors.size}, "
                        f"distance={distance}"
                    )
                return
            except VectorStoreConfigurationError:
                raise
            except Exception as exc:  # noqa: BLE001 - client failures are provider-specific
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(delay * (2**attempt))
        raise RuntimeError(
            f"Qdrant was not ready after {attempts} attempt(s): {last_error}"
        ) from last_error

    def add_chunks(
        self,
        document_id: str,
        title: str,
        source: str,
        metadata: dict,
        texts: list[str],
        chunk_ids: list[str] | None = None,
    ) -> list[str]:
        vectors = self.embeddings.embed(texts)
        points = []
        ids = chunk_ids or self.chunk_ids(document_id, len(texts))
        for index, (text, vector) in enumerate(zip(texts, vectors, strict=True)):
            chunk_id = ids[index]
            points.append(
                models.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload={
                        "document_id": document_id,
                        "chunk_id": chunk_id,
                        "text": text,
                        "title": title,
                        "source": source,
                        "metadata": metadata,
                    },
                )
            )
        if points:
            self.client.upsert(collection_name=self.collection, points=points, wait=True)
        return ids

    @staticmethod
    def chunk_ids(document_id: str, count: int) -> list[str]:
        return [str(uuid.uuid5(uuid.UUID(document_id), str(index))) for index in range(count)]

    def check_ready(self) -> None:
        self.client.get_collection(self.collection)

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if chunk_ids:
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=list(chunk_ids)),  # type: ignore[arg-type]
                wait=True,
            )

    def search(self, query: str, limit: int) -> list[RetrievedChunk]:
        result = self.client.query_points(
            collection_name=self.collection,
            query=self.embeddings.embed([query])[0],
            limit=limit,
            with_payload=True,
        ).points
        chunks = []
        for point in result:
            payload = cast(dict[str, Any], point.payload)
            chunks.append(
                RetrievedChunk(
                    chunk_id=str(payload["chunk_id"]),
                    document_id=str(payload["document_id"]),
                    text=str(payload["text"]),
                    score=max(0.0, float(point.score)),
                    title=str(payload["title"]),
                    source=str(payload["source"]),
                    metadata=dict(payload.get("metadata") or {}),
                )
            )
        return chunks
