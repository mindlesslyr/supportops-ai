import hashlib
import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from qdrant_client import QdrantClient

from app.config import AnswerProviderName, EmbeddingBackend, Settings
from app.rag.answers import ContextAnswerProvider, OpenAIAnswerProvider
from app.rag.embeddings import FastEmbedProvider, HashEmbeddingProvider
from app.rag.vector_store import VectorStore
from app.repositories import (
    IdempotencyConflictError,
    IdempotencyInProgressError,
    SQLiteRepository,
)
from app.schemas import (
    DocumentCreate,
    DocumentRead,
    FeedbackCreate,
    FeedbackRead,
    QueryRequest,
    QueryResponse,
    SupportWebhook,
    WebhookResponse,
)
from app.services import IngestionError, SupportService


class RequestBodyTooLarge(Exception):
    pass


class MaxBodySizeMiddleware:
    def __init__(self, app, max_bytes: int):
        self.app, self.max_bytes = app, max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length and int(content_length) > self.max_bytes:
            await JSONResponse(
                status_code=413,
                content={"detail": {"code": "request_too_large", "message": "Request body is too large"}},
            )(scope, receive, send)
            return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLarge:
            await JSONResponse(
                status_code=413,
                content={"detail": {"code": "request_too_large", "message": "Request body is too large"}},
            )(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    config = settings or Settings()
    config.ensure_data_dir()
    embeddings = (
        FastEmbedProvider(config.fastembed_model)
        if config.embedding_backend == EmbeddingBackend.FASTEMBED
        else HashEmbeddingProvider(config.embedding_dimensions)
    )
    qdrant = (
        QdrantClient(url=config.qdrant_url)
        if config.qdrant_url
        else QdrantClient(path=config.qdrant_path)
    )
    repository = SQLiteRepository(config.database_path)
    answer_provider = (
        OpenAIAnswerProvider(
            config.openai_api_key or "", config.openai_model, config.openai_base_url
        )
        if config.answer_provider == AnswerProviderName.OPENAI
        else ContextAnswerProvider()
    )
    vectors = VectorStore(
        qdrant,
        config.qdrant_collection,
        embeddings,
        config.qdrant_startup_attempts,
        config.qdrant_retry_seconds,
    )
    service = SupportService(
        repository,
        vectors,
        answer_provider,
        config.chunk_size,
        config.chunk_overlap,
        config.low_confidence_threshold,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        qdrant.close()

    docs_enabled = config.app_environment != "production" and config.enable_api_docs
    api = FastAPI(
        title="SupportOps AI",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    api.add_middleware(MaxBodySizeMiddleware, max_bytes=config.max_request_body_bytes)
    api.state.settings, api.state.repository, api.state.service = config, repository, service

    def require_api_key(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
        if not config.api_key:
            raise HTTPException(
                status_code=503,
                detail={"code": "api_auth_not_configured", "message": "API access is disabled"},
            )
        if not x_api_key or not hmac.compare_digest(x_api_key, config.api_key):
            raise HTTPException(
                status_code=401,
                detail={"code": "invalid_api_key", "message": "A valid X-API-Key is required"},
            )

    @api.exception_handler(IngestionError)
    async def ingestion_error_handler(_: Request, exc: IngestionError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=500, content={"detail": {"code": exc.code, "message": str(exc)}}
        )

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.get("/ready")
    def ready():
        checks = {"sqlite": "ok", "qdrant": "ok"}
        try:
            repository.check_ready()
        except Exception:  # noqa: BLE001 - readiness must report provider failures
            checks["sqlite"] = "failed"
        try:
            vectors.check_ready()
        except Exception:  # noqa: BLE001 - readiness must report provider failures
            checks["qdrant"] = "failed"
        if "failed" in checks.values():
            return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
        return {"status": "ready", "checks": checks}

    protected = [Depends(require_api_key)]

    @api.post(
        "/documents",
        response_model=DocumentRead,
        status_code=status.HTTP_201_CREATED,
        dependencies=protected,
    )
    def create_document(body: DocumentCreate):
        return service.ingest(body)

    @api.get("/documents", response_model=list[DocumentRead], dependencies=protected)
    def list_documents():
        return repository.list_documents()

    @api.post("/query", response_model=QueryResponse, dependencies=protected)
    def query(body: QueryRequest):
        return service.query(body.question, body.top_k)

    @api.post(
        "/feedback",
        response_model=FeedbackRead,
        status_code=status.HTTP_201_CREATED,
        dependencies=protected,
    )
    def feedback(body: FeedbackCreate):
        return repository.add_feedback(
            body.query, body.answer, body.rating, body.comment
        )

    @api.post(
        "/webhooks/support", response_model=WebhookResponse, dependencies=protected
    )
    async def support_webhook(
        request: Request,
        x_signature_256: str | None = Header(default=None, alias="X-Signature-256"),
        idempotency_key: str | None = Header(
            default=None, alias="Idempotency-Key", max_length=256
        ),
    ):
        raw = await request.body()
        if not config.webhook_secret and not config.allow_unsigned_webhooks_dev:
            raise HTTPException(
                status_code=503,
                detail={"code": "webhook_disabled", "message": "Webhook signing is not configured"},
            )
        if config.webhook_secret:
            expected = "sha256=" + hmac.new(
                config.webhook_secret.encode(), raw, hashlib.sha256
            ).hexdigest()
            if not x_signature_256 or not hmac.compare_digest(expected, x_signature_256):
                raise HTTPException(
                    status_code=401,
                    detail={"code": "invalid_signature", "message": "Webhook signature is invalid"},
                )
        if not idempotency_key:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "missing_idempotency_key",
                    "message": "Idempotency-Key header is required",
                },
            )
        try:
            event = SupportWebhook.model_validate_json(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_payload", "message": str(exc)},
            ) from exc
        digest = hashlib.sha256(raw).hexdigest()
        try:
            claim = repository.begin_webhook(
                idempotency_key, event.ticket_id, digest, config.webhook_lease_seconds
            )
        except IdempotencyConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "idempotency_conflict",
                    "message": "Idempotency key was used with a different payload",
                },
            ) from exc
        except IdempotencyInProgressError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "request_in_progress", "message": "Request is still processing"},
            ) from exc
        if claim.response is not None:
            return WebhookResponse.model_validate(claim.response)
        if claim.lease_token is None:  # defensive invariant
            raise RuntimeError("Webhook claim did not include a lease token")
        try:
            result = service.query(event.message, 4)
            review = result.confidence < config.low_confidence_threshold
            response = WebhookResponse(
                status="human_review" if review else "answered",
                ticket_id=event.ticket_id,
                answer=None if review else result.answer,
                confidence=result.confidence,
                human_review=review,
            )
            repository.complete_webhook(
                idempotency_key, claim.lease_token, response.model_dump()
            )
            return response
        except Exception:
            repository.fail_webhook(idempotency_key, claim.lease_token)
            raise

    return api


app = create_app()
