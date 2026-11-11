import json
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, field_validator


def _strip_nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


NonBlankStr = Annotated[str, AfterValidator(_strip_nonempty)]


def _bounded_metadata(value: dict[str, Any]) -> dict[str, Any]:
    try:
        encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("metadata must be JSON serializable") from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise ValueError("metadata must be at most 16384 bytes")
    return value


class DocumentCreate(BaseModel):
    title: NonBlankStr = Field(max_length=300)
    content: NonBlankStr = Field(max_length=100_000)
    source: NonBlankStr = Field(default="manual", max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_metadata = field_validator("metadata")(_bounded_metadata)


class DocumentRead(BaseModel):
    id: str
    title: str
    source: str
    metadata: dict[str, Any]
    chunk_count: int


class DocumentCreated(DocumentRead):
    pass


class QueryRequest(BaseModel):
    question: NonBlankStr = Field(max_length=4_000)
    top_k: int = Field(default=4, ge=1, le=20)


class RetrievedChunk(BaseModel):
    chunk_id: str
    document_id: str
    text: str
    score: float
    title: str
    source: str
    metadata: dict[str, Any]


class Citation(BaseModel):
    document_id: str
    title: str
    source: str
    chunk_id: str
    metadata: dict[str, Any]


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    retrieved_chunks: list[RetrievedChunk]


class FeedbackCreate(BaseModel):
    query: NonBlankStr = Field(max_length=4_000)
    answer: NonBlankStr = Field(max_length=20_000)
    rating: int = Field(ge=-1, le=1)
    comment: str | None = Field(default=None, max_length=4_000)


class FeedbackRead(BaseModel):
    id: int
    created_at: str


class SupportWebhook(BaseModel):
    ticket_id: NonBlankStr = Field(max_length=200)
    message: NonBlankStr = Field(max_length=20_000)
    customer_email: str | None = Field(default=None, max_length=320)
    metadata: dict[str, Any] = Field(default_factory=dict)

    _validate_metadata = field_validator("metadata")(_bounded_metadata)


class WebhookResponse(BaseModel):
    status: str
    ticket_id: str
    answer: str | None = None
    confidence: float | None = None
    human_review: bool = False
